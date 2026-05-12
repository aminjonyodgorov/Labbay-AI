import os
import logging
import tempfile
from dotenv import load_dotenv
from openai import OpenAI
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

WELCOME_TEXT = """
👋 Salom! Men ovozli xabarlarni matnga aylantiraman.

📤 Qanday foydalanish:
• Ovozli xabar yuboring
• Boshqa chatdan ovozli xabarni forward qiling

🌍 Til sozlamalari:
/uz — O'zbek tili (standart)
/ru — Rus tili
/en — Ingliz tili
/auto — Avtomatik aniqlash

Boshlash uchun ovozli xabar yuboring! 🎙️
"""

PROCESSING_TEXT = "⏳ Ovoz tanilmoqda, biroz kuting..."

ERROR_TEXT = "❌ Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring."

EMPTY_TEXT = "🔇 Ovozli xabardan matn topilmadi. Aniqroq gapirib ko'ring."

LANGUAGE_NAMES = {"uz": "O'zbek", "ru": "Rus", "en": "Ingliz", None: "Avtomatik"}

USER_LANGUAGES: dict[int, str | None] = {}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    USER_LANGUAGES[update.effective_user.id] = "uz"
    await update.message.reply_text(WELCOME_TEXT)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(WELCOME_TEXT)


async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cmd = update.message.text.strip().lstrip("/").split("@")[0]
    lang = None if cmd == "auto" else cmd
    USER_LANGUAGES[update.effective_user.id] = lang
    name = LANGUAGE_NAMES.get(lang, "Avtomatik")
    await update.message.reply_text(f"✅ Til o'zgartirildi: *{name}*", parse_mode="Markdown")


async def transcribe_audio(file_path: str, language: str = None) -> str:
    with open(file_path, "rb") as audio_file:
        params = dict(
            model="whisper-1",
            file=audio_file,
            response_format="text",
            prompt="O'zbek tilida suhbat. Salom, rahmat, ha, yo'q, iltimos.",
        )
        if language:
            params["language"] = language
        response = openai_client.audio.transcriptions.create(**params)
    return response.strip() if isinstance(response, str) else response.text.strip()


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    voice = update.message.voice or update.message.audio

    if not voice:
        return

    duration = getattr(voice, "duration", 0)
    if duration and duration > 600:
        await update.message.reply_text(
            "⚠️ Ovozli xabar 10 daqiqadan uzun. Iltimos, qisqaroq xabar yuboring."
        )
        return

    processing_msg = await update.message.reply_text(PROCESSING_TEXT)

    try:
        voice_file = await context.bot.get_file(voice.file_id)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp_path = tmp.name

        await voice_file.download_to_drive(tmp_path)

        user_lang = USER_LANGUAGES.get(update.effective_user.id, "uz")
        text = await transcribe_audio(tmp_path, language=user_lang)

        os.unlink(tmp_path)

        if not text:
            await processing_msg.edit_text(EMPTY_TEXT)
            return

        result = f"📝 *Matn:*\n\n{text}"
        await processing_msg.edit_text(result, parse_mode="Markdown")

        logger.info(
            f"Transcribed voice from user {update.effective_user.id}: {len(text)} chars"
        )

    except Exception as e:
        logger.error(f"Transcription error: {e}")
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        await processing_msg.edit_text(ERROR_TEXT)


async def handle_unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🎙️ Faqat ovozli xabarlar qabul qilinadi.\n\nOvozli xabar yuboring yoki forward qiling."
    )


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN .env faylda topilmadi!")

    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        raise ValueError("OPENAI_API_KEY .env faylda topilmadi!")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("uz", set_language))
    app.add_handler(CommandHandler("ru", set_language))
    app.add_handler(CommandHandler("en", set_language))
    app.add_handler(CommandHandler("auto", set_language))

    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.AUDIO, handle_voice))

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_unsupported,
        )
    )

    logger.info("Bot ishga tushdi...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

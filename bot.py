import os
import asyncio
import logging
import tempfile
from dotenv import load_dotenv
from groq import AsyncGroq
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

groq_client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))

UZBEK_FIX_PROMPT = """Sen O'zbek tili eksperti va lingvistsan.

# VAZIFA
Senga Whisper STT modeli tomonidan O'ZBEK TILIDA transkripsiya qilingan matn beriladi. Lekin Whisper fonetik xatoliklar qiladi — so'zlarni notog'ri yozadi, bo'shliqlarni notog'ri qo'yadi, so'zlarni birlashtiradi yoki ajratadi.

Sening vazifang: matnni FONETIK O'XSHASHLIK orqali to'g'ri o'zbek so'zlariga aylantirish.

# QILADIGAN ISHLARING

1. **Fonetik tuzatish** (so'zlarni eshitilishi bo'yicha to'g'ri o'zbek so'ziga aylantir):
   - "aqshmisi" / "aqshmisiz" → "yaxshimisiz"
   - "salametmisi" / "salamatmisi" → "salomatmisiz"
   - "sharcimi" / "charcamay" → "charchamay"
   - "An oraki" / "Anoraka" → "Anor aka" (yoki tegishli ism)
   - "qlayli" / "qilayli" → "qilaylik"
   - "gaki" / "gapki" → "gapni" / "gapi"
   - "noa" / "anova" → "anavi"
   - "buldi" → "bo'ldi"
   - "Aki" / "ki" → "hali" / "haligi"
   - "shima" → "shuni"
   - "kampitur" / "kompitur" → "kompyuter"
   - "qlay" → "qilay"
   - "qlayli" → "qilaylik"

2. **So'z chegaralarini to'g'rilash**:
   - Notog'ri birlashtirilgan so'zlarni ajrat ("Kompyutermasada" → "kompyuter masada")
   - Notog'ri ajratilgan so'zlarni birlashtir

3. **O'zbek apostrof tiklash**:
   - o' (kop→ko'p, dost→do'st, koz→ko'z, soz→so'z, buldi→bo'ldi)
   - g' (yog→yog', tog→tog', sog→sog')

4. **Punktuatsiya va bosh harf**:
   Nuqta, vergul, savol belgisi qo'y. Gap boshini va atoqli otlarni (ism, joy) bosh harf qil.

# NIMA QILMASLIK

❌ **Yangi so'z O'YLAB TOPMA**. Faqat eshitilgan narsalarni tikla.
❌ **Mavjud so'zni mazmunan o'zgartirma** ("qoldirib" ni "qaytarib" qilma)
❌ **Tushunarli so'zni o'zgartirma** — agar so'z to'g'ri yozilgan bo'lsa, tegma
❌ **Noma'lum yoki o'ziga xos so'zlarni** (ism, joy, atama) mashhur so'zga aylantirma — saqla
❌ **Tarjima qilma** agar matn aniq Rus yoki Ingliz tilida bo'lsa
❌ **Tartibni o'zgartirma**, gaplarni qayta tartiblama

# MISOLLAR

INPUT: An oraki aqshmisi, salamatmisi, sharcimi aqsizmi. Aki, an o masala noa buldi. Kampitur masalasi, shima hal qlayli gaki.
OUTPUT: Anor aka, yaxshimisiz, salomatmisiz, charchamay yaxshimisiz? Hali, anavi masala bo'ldi. Kompyuter masalasi, shuni hal qilaylik gapni.

INPUT: Salom qalaysiz yaxshimisiz
OUTPUT: Salom, qalaysiz, yaxshimisiz?

# NATIJA FORMATI
Faqat tuzatilgan o'zbek matni. Izoh, sarlavha, qo'shtirnoq, "Output:" prefiksi qo'shma."""

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

LANGUAGE_NAMES = {"uz": "O'zbek", "ru": "Rus", "en": "Ingliz", "auto": "Avtomatik"}

USER_LANGUAGES: dict[int, str] = {}

GROQ_LANG_MAP = {
    "uz": "uz",
    "ru": "ru",
    "en": "en",
    "auto": None,
}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    USER_LANGUAGES[update.effective_user.id] = "uz"
    await update.message.reply_text(WELCOME_TEXT)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(WELCOME_TEXT)


async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cmd = update.message.text.strip().lstrip("/").split("@")[0]
    USER_LANGUAGES[update.effective_user.id] = cmd
    name = LANGUAGE_NAMES.get(cmd, "Avtomatik")
    await update.message.reply_text(
        f"✅ Til o'zgartirildi: *{name}*", parse_mode="Markdown"
    )


async def groq_transcribe(file_path: str, language: str | None) -> str:
    try:
        with open(file_path, "rb") as f:
            file_bytes = f.read()
        kwargs = dict(
            model="whisper-large-v3",
            file=("audio.ogg", file_bytes),
            temperature=0,
        )
        if language:
            kwargs["language"] = language
        response = await groq_client.audio.transcriptions.create(**kwargs)
        text = getattr(response, "text", None) or (response if isinstance(response, str) else "")
        return text.strip() if text else ""
    except Exception as e:
        logger.error(f"Groq transcription failed (lang={language}): {e}", exc_info=True)
        return ""


async def fix_uzbek(raw_text: str) -> str:
    if not raw_text:
        return ""
    try:
        response = await groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": UZBEK_FIX_PROMPT},
                {"role": "user", "content": raw_text},
            ],
            temperature=0,
        )
        fixed = response.choices[0].message.content.strip()
        logger.info(f"Uzbek fix: '{raw_text[:80]}' -> '{fixed[:80]}'")
        return fixed
    except Exception as e:
        logger.error(f"Groq LLM fix failed, returning raw: {e}", exc_info=True)
        return raw_text


async def transcribe_audio(file_path: str, target_lang: str) -> str:
    language = GROQ_LANG_MAP.get(target_lang, "uz")
    raw = await groq_transcribe(file_path, language)
    if not raw:
        return ""
    if target_lang == "uz":
        return await fix_uzbek(raw)
    return raw


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

    tmp_path = None
    try:
        voice_file = await context.bot.get_file(voice.file_id)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp_path = tmp.name

        await voice_file.download_to_drive(tmp_path)

        target_lang = USER_LANGUAGES.get(update.effective_user.id, "uz")
        text = await transcribe_audio(tmp_path, target_lang=target_lang)

        if not text:
            await processing_msg.edit_text(EMPTY_TEXT)
            return

        max_len = 4000
        if len(text) <= max_len:
            await processing_msg.edit_text(f"📝 {text}")
        else:
            await processing_msg.edit_text(f"📝 {text[:max_len]}")
            for i in range(max_len, len(text), max_len):
                await update.message.reply_text(text[i : i + max_len])

        logger.info(
            f"Transcribed voice from user {update.effective_user.id} "
            f"(lang={target_lang}): {len(text)} chars"
        )

    except Exception as e:
        logger.error(f"Transcription error: {e}", exc_info=True)
        try:
            await processing_msg.edit_text(ERROR_TEXT)
        except Exception:
            pass
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


async def handle_unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🎙️ Faqat ovozli xabarlar qabul qilinadi.\n\n"
        "Ovozli xabar yuboring yoki forward qiling."
    )


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN .env faylda topilmadi!")

    groq_key = os.getenv("GROQ_API_KEY")
    if not groq_key:
        raise ValueError("GROQ_API_KEY .env faylda topilmadi!")

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
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()

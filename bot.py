import os
import time
import asyncio
import logging
import tempfile
import uvicorn
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

import db
from admin import app as admin_app

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

ℹ️ /myid — sizning Telegram ID

Boshlash uchun ovozli xabar yuboring! 🎙️
"""

PROCESSING_TEXT = "⏳ Ovoz tanilmoqda, biroz kuting..."
ERROR_TEXT = "❌ Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring."
EMPTY_TEXT = "🔇 Ovozli xabardan matn topilmadi. Aniqroq gapirib ko'ring."
BLOCKED_TEXT = "🚫 Sizning kirishingiz cheklangan."

LANGUAGE_NAMES = {"uz": "O'zbek", "ru": "Rus", "en": "Ingliz", "auto": "Avtomatik"}
USER_LANGUAGES: dict[int, str] = {}
GROQ_LANG_MAP = {"uz": "uz", "ru": "ru", "en": "en", "auto": None}


async def _track_user(update: Update) -> None:
    u = update.effective_user
    if not u:
        return
    lang = USER_LANGUAGES.get(u.id, "uz")
    try:
        await db.upsert_user(u.id, u.username, u.first_name, u.last_name, lang)
    except Exception as e:
        logger.warning(f"upsert_user failed: {e}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    USER_LANGUAGES[update.effective_user.id] = "uz"
    await _track_user(update)
    await update.message.reply_text(WELCOME_TEXT)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _track_user(update)
    await update.message.reply_text(WELCOME_TEXT)


async def myid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    await update.message.reply_text(f"🆔 Sizning Telegram ID: `{uid}`", parse_mode="Markdown")


async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cmd = update.message.text.strip().lstrip("/").split("@")[0]
    USER_LANGUAGES[update.effective_user.id] = cmd
    await _track_user(update)
    try:
        await db.set_user_language(update.effective_user.id, cmd)
    except Exception as e:
        logger.warning(f"set_user_language failed: {e}")
    name = LANGUAGE_NAMES.get(cmd, "Avtomatik")
    await update.message.reply_text(
        f"✅ Til o'zgartirildi: *{name}*", parse_mode="Markdown"
    )


async def groq_transcribe(file_path: str, language: str | None) -> str:
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


async def fix_uzbek(raw_text: str) -> str:
    if not raw_text:
        return ""
    response = await groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": UZBEK_FIX_PROMPT},
            {"role": "user", "content": raw_text},
        ],
        temperature=0,
    )
    return response.choices[0].message.content.strip()


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    voice = update.message.voice or update.message.audio
    if not voice:
        return

    user = update.effective_user
    try:
        if await db.is_user_blocked(user.id):
            await update.message.reply_text(BLOCKED_TEXT)
            return
    except Exception:
        pass

    await _track_user(update)

    duration = getattr(voice, "duration", 0) or 0
    file_size = getattr(voice, "file_size", 0) or 0
    target_lang = USER_LANGUAGES.get(user.id, "uz")

    if duration and duration > 600:
        await update.message.reply_text(
            "⚠️ Ovozli xabar 10 daqiqadan uzun. Iltimos, qisqaroq xabar yuboring."
        )
        return

    processing_msg = await update.message.reply_text(PROCESSING_TEXT)
    tmp_path = None
    raw_text = None
    fixed_text = None
    error_str = None
    started = time.monotonic()

    try:
        voice_file = await context.bot.get_file(voice.file_id)
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp_path = tmp.name
        await voice_file.download_to_drive(tmp_path)

        language = GROQ_LANG_MAP.get(target_lang, "uz")
        raw_text = await groq_transcribe(tmp_path, language)
        if not raw_text:
            await processing_msg.edit_text(EMPTY_TEXT)
            return

        if target_lang == "uz":
            try:
                fixed_text = await fix_uzbek(raw_text)
            except Exception as e:
                logger.error(f"fix_uzbek failed: {e}", exc_info=True)
                fixed_text = raw_text
        else:
            fixed_text = raw_text

        text = fixed_text
        max_len = 4000
        if len(text) <= max_len:
            await processing_msg.edit_text(f"📝 {text}")
        else:
            await processing_msg.edit_text(f"📝 {text[:max_len]}")
            for i in range(max_len, len(text), max_len):
                await update.message.reply_text(text[i : i + max_len])

        logger.info(f"Transcribed for user {user.id} ({target_lang}): {len(text)} chars")

    except Exception as e:
        error_str = f"{type(e).__name__}: {e}"
        logger.error(f"Transcription error: {e}", exc_info=True)
        try:
            await processing_msg.edit_text(ERROR_TEXT)
        except Exception:
            pass
    finally:
        latency_ms = int((time.monotonic() - started) * 1000)
        try:
            await db.insert_transcription(
                user_id=user.id,
                duration_seconds=duration or None,
                file_size_bytes=file_size or None,
                language=target_lang,
                raw_text=raw_text,
                fixed_text=fixed_text,
                latency_ms=latency_ms,
                error=error_str,
            )
        except Exception as e:
            logger.warning(f"insert_transcription failed: {e}")
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


async def handle_unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _track_user(update)
    await update.message.reply_text(
        "🎙️ Faqat ovozli xabarlar qabul qilinadi.\n\n"
        "Ovozli xabar yuboring yoki forward qiling."
    )


def build_app() -> Application:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN .env faylda topilmadi!")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("myid", myid_command))
    app.add_handler(CommandHandler("uz", set_language))
    app.add_handler(CommandHandler("ru", set_language))
    app.add_handler(CommandHandler("en", set_language))
    app.add_handler(CommandHandler("auto", set_language))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_unsupported))
    return app


async def run_admin_server() -> None:
    port = int(os.getenv("PORT", "8080"))
    config = uvicorn.Config(
        admin_app, host="0.0.0.0", port=port, log_level="info", access_log=False
    )
    server = uvicorn.Server(config)
    await server.serve()


async def run_bot(app: Application) -> None:
    await app.initialize()
    await app.start()
    await app.updater.start_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )
    logger.info("Telegram bot polling started")
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


async def main_async() -> None:
    if not os.getenv("GROQ_API_KEY"):
        raise ValueError("GROQ_API_KEY o'rnatilmagan!")
    await db.init_pool()
    app = build_app()
    await asyncio.gather(run_bot(app), run_admin_server())


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()

import os
import time
import asyncio
import logging
import tempfile
import uvicorn
from collections import defaultdict
from dotenv import load_dotenv
from groq import AsyncGroq
from openai import AsyncOpenAI
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

_openai_key = os.getenv("OPENAI_API_KEY")
openai_client = AsyncOpenAI(api_key=_openai_key) if _openai_key else None

WHISPER_PROMPT_UZ = (
    "O'zbek tilidagi tabiiy suhbat va so'zlashuv. Ism va joy nomlari aynan saqlansin. "
    "Joylar: Toshkent, Samarqand, Buxoro, Xiva, Andijon, Farg'ona, Namangan, Qashqadaryo, "
    "Surxondaryo, Xorazm, Navoiy, Jizzax, Sirdaryo, Qoraqalpog'iston. "
    "Diniy va sayohat: assalomu alaykum, salomatmisiz, hoji, hojimamiz, haj, umra, ziyorat, "
    "Makka, Madina, Jidda, Ka'ba, Masjidul Haram, masjidi Nabaviy, ehrom, tavof, sa'y, namoz, "
    "duo, masjid, qabriston, mehmonxona, samolyot, poezd, avtobus, vokzal. "
    "Texnika: kompyuter, telefon, dastur, internet, ilova. "
    "Hurmat: yaxshimisiz, salomatmisiz, qalaysiz, charchamay, rahmat, salom, hayrli kun, "
    "hayrli tong, marhamat, iltimos, kechirasiz, mayli, xayr."
)


UZBEK_FIX_PROMPT = """Sen O'zbek tili va madaniyati bo'yicha lingvist-eksperti san. Senga Whisper STT modeli o'zbek nutqini transkripsiya qilgan, lekin fonetik xatolar bilan yozgan matn beriladi. Sening vazifang — fonetik o'xshashlik orqali asl o'zbek so'zlarini tiklash.

══════════════ QAT'IY OUTPUT FORMATI ══════════════
JAVOBING **FAQAT** tuzatilgan o'zbek matni bo'ladi.
TAQIQLANGAN:
- "Tuzatilgan matn:", "Output:", "Natija:" yoki shunga o'xshash prefikslar
- Sarlavhalar, izohlar, qo'shtirnoq belgilar atrofida
- Kirish/yakuniy gaplar ("Mana tuzatilgan matn:", "Quyida:", "Marhamat:")
- Markdown belgilari (**, ##, ```)
- Tilshunoslik tushuntirishi yoki ogohlantirishlar

To'g'ri javob: bevosita tuzatilgan o'zbek matni, hech narsa qo'shilmaydi.

══════════════ TUZATISH QOIDALARI ══════════════

1. FONETIK TUZATISH — Whisper noto'g'ri yozgan so'zni eshitilishi bo'yicha to'g'ri o'zbek so'ziga aylantir:
   • "aqshmisi" / "aqshmisiz" / "yakhshimisi" → "yaxshimisiz"
   • "salametmisi" / "salamatmisi" → "salomatmisiz"
   • "sharcimi" / "charcamay" / "sharchamay" → "charchamay"
   • "qlayli" / "qilayli" / "qilali" → "qilaylik"
   • "buldi" / "boldi" → "bo'ldi"
   • "kampitur" / "kompitur" → "kompyuter"
   • "Aki" / "ki" → "hali" / "haligi" (kontekstga qarab)
   • "yo'ldi" → "yo'ldir"
   • "qoyilgan" → "qo'yilgan"
   • "vokzala" / "vokzaladan" → "vokzalda" / "vokzaldan"

2. DINIY, HAJ VA SAYOHAT LUG'ATI (audio bunday kontekstda bo'lsa):
   • "taj onamiz" / "ozimama" → "hojimamiz" / "otamiz" (kontekstga qarab)
   • "haram" / "Karam" / "xaram" → "Haram" yoki "ehrom" (kontekstga qarab)
   • "Makka", "Madina", "Jidda", "Ka'ba", "Masjidul Haram", "Masjidi Nabaviy"
   • "ehrom", "tavof", "sa'y", "umra", "haj", "ziyorat", "duo", "namoz"
   • "mehmonxona", "samolyot", "poezd", "avtobus", "vokzal"

3. O'ZBEK APOSTROF (o', g'):
   • o': ko'p, do'st, so'z, ko'z, bo'ldi, yo'q, ko'cha, o'quvchi, o'rganmoq
   • g': yog', tog', sog', og'ir, bog', tug'ilgan

4. SO'Z CHEGARALARINI to'g'irlash:
   • Noto'g'ri birlashtirilganlarni ajrat ("kompyutermasada" → "kompyuter masada")
   • Noto'g'ri ajratilganlarni birlashtir ("an o" → "anavi" agar kontekst mos kelsa)

5. PUNKTUATSIYA:
   • Vergul, nuqta, savol belgisi qo'y
   • Atoqli otlar (ism, joy, masjid nomi) — bosh harf
   • Gap boshi — bosh harf

══════════════ MUTLAQO QILMA ══════════════
✗ YANGI so'z o'ylab topma. Faqat fonetik o'xshashlik orqali tikla.
✗ Mazmunan o'zgartirma — "qoldirib" → "qaytarib" qilma.
✗ Noma'lum so'zni mashhur so'zga zo'rlab almashtirma — saqla.
✗ Tarjima qilma agar audio Rus yoki Ingliz tilida bo'lsa.
✗ Tartibni o'zgartirma — gaplarni qayta tartiblama.
✗ Qisqartirma yoki kengaytirma. So'z soni taxminan bir xil bo'lsin.
✗ Whisper tushunmagan so'zni "hallucinate" qilma — agar shubhali bo'lsa, fonetik yaqin variantni qoldir.

══════════════ MISOLLAR ══════════════

INPUT: An oraki aqshmisi salamatmisi sharcimi aqsizmi Aki an o masala noa buldi
OUTPUT: Anor aka, yaxshimisiz, salomatmisiz, charchamay yaxshimisiz? Hali anavi masala bo'ldi.

INPUT: Ozi mama taj onamiz bilan komfortimiz bolar bitta farq nima dedik
OUTPUT: Hojimamiz, otamiz bilan komfortimiz bo'lar, bitta farq nima dedik?

INPUT: tashkina Madina uchun uchamizdi birinchi tashkina Jidda uchub
OUTPUT: Toshkentdan Madina uchun uchamiz edi, birinchi Toshkentdan Jiddaga uchib.

INPUT: id dengiz boyida edi
OUTPUT: oid, dengiz bo'yida edi.

INPUT: tez yuradigan poezdmiz avtobus qoyiladi
OUTPUT: tez yuradigan poyezdmiz, avtobus qo'yiladi.

INPUT: Salom qalaysiz yaxshimisiz
OUTPUT: Salom, qalaysiz, yaxshimisiz?
"""

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

🔬 /debug — xom va tuzatilgan natijani yonma-yon ko'rsatish
ℹ️ /myid — sizning Telegram ID

Boshlash uchun ovozli xabar yuboring! 🎙️
"""

PROCESSING_TEXT = "⏳ Ovoz tanilmoqda, biroz kuting..."
ERROR_TEXT = "❌ Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring."
EMPTY_TEXT = "🔇 Ovozli xabardan matn topilmadi. Aniqroq gapirib ko'ring."
BLOCKED_TEXT = "🚫 Sizning kirishingiz cheklangan."

LANGUAGE_NAMES = {"uz": "O'zbek", "ru": "Rus", "en": "Ingliz", "auto": "Avtomatik"}
USER_LANGUAGES: dict[int, str] = {}
USER_DEBUG: dict[int, bool] = {}
GROQ_LANG_MAP = {"uz": "uz", "ru": "ru", "en": "en", "auto": None}


LLM_PREFIX_NOISE = (
    "tuzatilgan matn:", "tuzatilgan natija:", "tuzatish:", "natija:",
    "output:", "mana tuzatilgan matn:", "mana natija:", "javob:",
    "to'g'ri matn:", "tuzatilgan o'zbek matni:", "mana:",
)


def _sanitize_llm_output(text: str) -> str:
    if not text:
        return ""
    out = text.strip().strip("`").strip()
    lowered = out.lower()
    for prefix in LLM_PREFIX_NOISE:
        if lowered.startswith(prefix):
            out = out[len(prefix):].lstrip(" :\n\t-—")
            lowered = out.lower()
    if out.startswith('"') and out.endswith('"') and len(out) > 1:
        out = out[1:-1]
    if out.startswith("'") and out.endswith("'") and len(out) > 1:
        out = out[1:-1]
    return out.strip()

VOICE_RATE_WINDOW_SEC = 60
VOICE_RATE_MAX = 15
MAX_VOICE_FILE_BYTES = 25 * 1024 * 1024

_voice_rate: dict[int, list[float]] = defaultdict(list)


def _check_voice_rate(user_id: int) -> bool:
    now = time.monotonic()
    bucket = [t for t in _voice_rate[user_id] if now - t < VOICE_RATE_WINDOW_SEC]
    if len(bucket) >= VOICE_RATE_MAX:
        _voice_rate[user_id] = bucket
        return False
    bucket.append(now)
    _voice_rate[user_id] = bucket
    return True


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


async def debug_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    new_state = not USER_DEBUG.get(uid, False)
    USER_DEBUG[uid] = new_state
    if new_state:
        await update.message.reply_text(
            "🔬 Debug rejimi YOQILDI.\n\n"
            "Endi har bir ovozli xabar uchun xom (Whisper) va tuzatilgan (LLM) "
            "natijalarni alohida ko'rasiz. Bu sifatni baholash uchun foydali.\n\n"
            "O'chirish uchun yana /debug yuboring."
        )
    else:
        await update.message.reply_text("🔕 Debug rejimi O'CHIRILDI. Faqat tuzatilgan matn yuboriladi.")


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
    if language == "uz":
        kwargs["prompt"] = WHISPER_PROMPT_UZ
    response = await groq_client.audio.transcriptions.create(**kwargs)
    text = getattr(response, "text", None) or (response if isinstance(response, str) else "")
    return text.strip() if text else ""


async def _fix_uzbek_openai(raw_text: str) -> str:
    if not openai_client:
        raise RuntimeError("OpenAI client not configured")
    response = await openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": UZBEK_FIX_PROMPT},
            {"role": "user", "content": raw_text},
        ],
        temperature=0,
    )
    raw = response.choices[0].message.content or ""
    return _sanitize_llm_output(raw)


async def _fix_uzbek_groq(raw_text: str) -> str:
    response = await groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": UZBEK_FIX_PROMPT},
            {"role": "user", "content": raw_text},
        ],
        temperature=0,
    )
    raw = response.choices[0].message.content or ""
    return _sanitize_llm_output(raw)


async def fix_uzbek(raw_text: str) -> str:
    if not raw_text:
        return ""
    if openai_client:
        try:
            fixed = await _fix_uzbek_openai(raw_text)
            if fixed:
                return fixed
            logger.warning("OpenAI fix returned empty, falling back to Groq Llama")
        except Exception as e:
            logger.warning(f"OpenAI fix failed, falling back to Groq Llama: {e}")
    return await _fix_uzbek_groq(raw_text)


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

    if file_size and file_size > MAX_VOICE_FILE_BYTES:
        await update.message.reply_text(
            "⚠️ Fayl juda katta (25 MB dan ortiq). Iltimos, kichikroq fayl yuboring."
        )
        return

    if not _check_voice_rate(user.id):
        await update.message.reply_text(
            "⏳ Juda tez yuboryapsiz. Iltimos, 1 daqiqa kuting va qayta urinib ko'ring."
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

        debug_on = USER_DEBUG.get(user.id, False) and target_lang == "uz"
        max_len = 3500
        if debug_on:
            text = (
                f"📝 *Tuzatilgan (LLM):*\n{fixed_text}\n\n"
                f"🔬 *Xom (Whisper):*\n{raw_text}"
            )
        else:
            text = f"📝 {fixed_text}"

        if len(text) <= max_len:
            try:
                await processing_msg.edit_text(text, parse_mode="Markdown")
            except Exception:
                await processing_msg.edit_text(text)
        else:
            try:
                await processing_msg.edit_text(text[:max_len], parse_mode="Markdown")
            except Exception:
                await processing_msg.edit_text(text[:max_len])
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
    app.add_handler(CommandHandler("debug", debug_command))
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

import os
import asyncio
import logging
import tempfile
from dotenv import load_dotenv
from openai import AsyncOpenAI
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

openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

WHISPER_SUPPORTED_LANGS = {
    "af", "ar", "hy", "az", "be", "bs", "bg", "ca", "zh", "hr", "cs", "da",
    "nl", "en", "et", "fi", "fr", "gl", "de", "el", "he", "hi", "hu", "is",
    "id", "it", "ja", "kn", "kk", "ko", "lv", "lt", "mk", "ms", "mr", "mi",
    "ne", "no", "fa", "pl", "pt", "ro", "ru", "sr", "sk", "sl", "es", "sw",
    "sv", "tl", "ta", "th", "tr", "uk", "ur", "vi", "cy",
}

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

UZBEK_CONTEXT_PROMPT = "O'zbek tilidagi tabiiy suhbat."

UZBEK_NORMALIZE_PROMPT = """Sen O'zbek tili eksperti va lingvistsan.

# VAZIFA
Senga Whisper STT modeli tomonidan TURK TILI sifatida transkripsiya qilingan matn beriladi. Lekin AUDIO ASLIDA O'ZBEK TILIDA. Whisper o'zbek tilini bilmaydi, shuning uchun u o'zbek nutqini turk fonetikasi va so'zlariga aylantirib yozadi.

Sening vazifang: matnni ASL O'ZBEK NUTQIGA qaytarish.

# QILADIGAN ISHLARING

1. **Standart turk so'zlarini o'zbek ekvivalentiga aylantir** (chunki ular aslida o'zbek so'zlar bo'lgan, Whisper noto'g'ri yozgan):
   - iyi misin/iyi misiniz → yaxshimisiz
   - nasılsın/nasılsınız → qalaysiz
   - sağlam mısın → salomatmisiz
   - ne var → nima bor / nima gap
   - nereye → qayerga
   - nerede → qayerda
   - kaç saat → necha soat
   - saat kaç → soat necha
   - bekliyorum → kutyapman
   - bekledik → kutdik
   - geldim → keldim
   - gittim → ketdim
   - yapıyorum → qilyapman
   - bilmiyorum → bilmayman
   - merhaba/selam → salom (yoki assalomu alaykum)
   - teşekkür ederim → rahmat
   - evet → ha
   - hayır → yo'q
   - ben → men
   - sen → sen
   - biz → biz
   - onlar → ular
   - bu → bu
   - şu → shu
   - o → o / u
   - ne → nima
   - kim → kim
   - nasıl → qanday
   - büyük → katta
   - küçük → kichkina
   - güzel → chiroyli / yaxshi
   - kötü → yomon
   - yeni → yangi
   - eski → eski
   - var → bor
   - yok → yo'q

2. **Alfavitni O'zbek lotinga aylantir**:
   ə→a, ş→sh, ç→ch, ı→i, ğ→g (kontekstga qarab g'), ü→u (yoki o'), ö→o (yoki o')

3. **O'zbek apostrof tiklash**:
   o' (ok→o'q, kop→ko'p, dost→do'st, koz→ko'z, soz→so'z)
   g' (yog→yog', tog→tog', sog→sog')
   yo'q, do'st, ko'cha, o'quvchi

4. **Turk grammatik suffiks → o'zbekcha**:
   -ler/-lar → -lar
   -dur/-dır/-dir → -dir
   -dum/-dün/-dın → -dim/-ding
   -yor → -yapti
   -mişim → -ganman

5. **Punktuatsiya**:
   Nuqta, vergul, savol belgisi qo'y. Gap boshini va atoqli otlarni bosh harf qil.

# NIMA QILMASLIK

❌ **Yangi so'z O'YLAB TOPMA**. Faqat audioda bor narsalarni tikla.
❌ **Mavjud so'zni mazmunan o'zgartirma** (masalan: "qoldirib" ni "qaytarib" qilma)
❌ **Noma'lum so'zlarni mashhur so'zga almashtirma**:
   - "Nabarot", "Mantar", "Xrissh" kabi tushunilmagan so'zlar → AYNAN saqla (ism, joy, atama bo'lishi mumkin)
   - Agar so'z mazmunan g'alati ko'rinsa ham, fonetik o'xshashlik orqali eng yaqin O'zbek so'zini topishga URINMA. Saqla.
❌ **Tarjima qilma** agar matn aniq Rus yoki Ingliz tilida bo'lsa.
❌ **Tartibni o'zgartirma**, gaplarni qayta tartiblama.

# MISOLLAR

INPUT: "Orada iyi misin? Sağlam mısın? Çorşıda iyi misin? Orada masada ne var? Masada kompyüter var."
OUTPUT: O'rada yaxshimisiz? Salomatmisiz? Chorshida yaxshimisiz? O'rada masada nima bor? Masada kompyuter bor.

INPUT: "Anor aka iyi misin salametmisin çarçamay iyi misin"
OUTPUT: Anor aka, yaxshimisiz, salomatmisiz, charchamay yaxshimisiz?

INPUT: "Nabarot kelişse darsni koldurmasdan"
OUTPUT: Nabarot kelishsa darsni qoldirmasdan.
(Diqqat: "Nabarot" — noma'lum so'z, AYNAN saqlandi. "kelişse" → "kelishsa" turkcha grammatik suffiks, o'zbekiga o'tkazildi. "koldurmasdan" → "qoldirmasdan" fonetik o'zbek shaklini tikladi)

INPUT: "Kompyutermasada hasis mahal kılaylık ekan"
OUTPUT: Kompyutermasada hasis mahal qilaylik ekan.
(Diqqat: "Kompyutermasada", "hasis mahal" — noma'lum/o'ziga xos iboralar, saqlandi)

# NATIJA FORMATI
Faqat tuzatilgan O'zbek matni. Izoh, sarlavha, qo'shtirnoq, "Output:" prefiksi qo'shma."""


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


async def whisper_pass(file_path: str, language: str | None) -> str:
    try:
        with open(file_path, "rb") as f:
            params = dict(
                model="whisper-1",
                file=f,
                response_format="text",
                temperature=0,
                prompt=UZBEK_CONTEXT_PROMPT,
            )
            if language and language in WHISPER_SUPPORTED_LANGS:
                params["language"] = language
            response = await openai_client.audio.transcriptions.create(**params)
        text = response.strip() if isinstance(response, str) else response.text.strip()
        return text
    except Exception as e:
        logger.warning(f"Whisper pass failed (lang={language}): {e}")
        return ""


async def normalize_uzbek(raw_text: str) -> str:
    if not raw_text:
        return ""
    response = await openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": UZBEK_NORMALIZE_PROMPT},
            {"role": "user", "content": raw_text},
        ],
        temperature=0,
    )
    return response.choices[0].message.content.strip()


async def transcribe_audio(file_path: str, target_lang: str) -> str:
    if target_lang == "uz":
        raw = await whisper_pass(file_path, "tr")
        if not raw:
            return ""
        return await normalize_uzbek(raw)

    if target_lang == "auto":
        return await whisper_pass(file_path, None)

    return await whisper_pass(file_path, target_lang)


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
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()

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

UZBEK_CONTEXT_PROMPT = "O'zbek tilidagi tabiiy suhbat. Demak, masalan, lekin, ammo, chunki."

UZBEK_SYNTHESIS_PROMPT = """Sen O'zbek tili va Whisper STT modeli xatoliklari bo'yicha yuqori darajadagi ekspertsan.

# KONTEKST
Whisper OpenAI modeli O'zbek tilini RASMIY qo'llab-quvvatlamaydi. Biz O'zbek nutqini uchta turli til parametri bilan transkripsiya qildik:
- Variant 1 (language=tr): Turk tili sifatida
- Variant 2 (language=az): Ozarbayjon tili sifatida
- Variant 3 (language=auto): Avtomatik aniqlash

Hech bir variant 100% to'g'ri emas, lekin har biri o'z fonetik tomondan asl nutqning izini tashiydi. Sening vazifang — 3 variantni "tovush triangulyatsiyasi" bilan solishtirib, ASL O'ZBEK NUTQINI eng aniq tiklab berish.

# O'ZBEK TOVUSHLARI VS WHISPER TRANSKRIPSIYASI
| O'zbek | Whisper variantlari |
|--------|---------------------|
| q | k, q, ḳ, ğ, x |
| x | h, k, x |
| o' | o, ö, u, ü, w |
| g' | g, ğ, q |
| sh | ş, sh, s |
| ch | ç, ch, c |
| ng | n, ng, nk |
| y (unli oldidan) | y, j, i |

Apostrof ko'pincha yo'qoladi: "yo'q" → "yok", "o'qish" → "okus"/"okuş"

# QARORLAR DARAXTI

1. **3 variantda bir xil so'z** → o'sha so'zni aynan saqla (faqat alfabetni o'zbekiga aylantir)
2. **2 variantda bir xil, 1 da boshqa** → ko'pchilik variantni tanla
3. **3 variantda 3 xil** → eng fonetik ma'noli va O'zbek kontekstiga mos variantni tanla
4. **Noma'lum so'z (ism, joy, termin)** → AYNAN saqla. "Tuzatishga" urinma. Bu maxsus so'z bo'lishi mumkin
5. **Aniq Whisper xatosi** (masalan, turk konjugatsiyasi: -dum, -dın) → o'zbek shakliga o'tkaz

# MAJBURIY QOIDALAR

✅ HAR DOIM QIL:
- Alfabet konversiyasi: ə→a, ş→sh, ç→ch, ı→i, ğ→g (kontekstdan g' bo'lishi mumkin), ü→u/o', ö→o
- Apostroflarni tikla: o', g', yo'q, do'st, ko'p, qo'l
- Punktuatsiya qo'sh (gap oxiriga nuqta/savol/undov, vergullar)
- Gap boshini va atoqli otlarni bosh harf bilan
- Turk affikslarini o'zbek shakliga: -ler/-lar→-lar, -dum→-dim, -dın→-ding, -yor→-yapti

❌ HECH QACHON QILMA:
- Audioda yo'q so'zni qo'shma
- Mazmunni "yaxshilashga" urinma
- Ismni "tuzatma" (Karim→Karim, hech qachon Kerim emas)
- Noma'lum so'zni mashhur so'z bilan almashtirma
- Sintaktik strukturani o'zgartirma
- Tuzatish haqida izoh berma

# MISOLLAR

INPUT:
Variant 1 (tr): "Asalamün alekum aka, nasil sın?"
Variant 2 (az): "Əssələmun ələykum əkə, nəsilsən?"
Variant 3 (auto): "Assalom alaykum aka, nasılsız?"

OUTPUT: Assalomu alaykum aka, qalaysiz?

---

INPUT:
Variant 1 (tr): "Nabarot kelişse darsni koldurmasden"
Variant 2 (az): "Nəbərət kəlişsə dərsni qoldurmasdən"
Variant 3 (auto): "Nabarot kelishsa darsni qoldirmasdan"

OUTPUT: Nabarot kelishsa, darsni qoldirmasdan.

(Diqqat: "Nabarot" 3 variantda ham bor — bu maxsus so'z, aynan saqlanadi)

---

INPUT:
Variant 1 (tr): "Bizler iki saat bekledik"
Variant 2 (az): "Bizlər iki saat gözlədik"
Variant 3 (auto): "Bizlar ikki soat kutdik"

OUTPUT: Bizlar ikki soat kutdik.

(O'zbek variant aniqroq, uni asos qilib oladi)

# NATIJA FORMATI
Faqat tiklangan O'zbek matni. Izoh, sarlavha, qo'shtirnoq, "Output:" prefiksi qo'shma."""


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


async def multi_pass_uzbek(file_path: str) -> str:
    candidates = await asyncio.gather(
        whisper_pass(file_path, "tr"),
        whisper_pass(file_path, "az"),
        whisper_pass(file_path, None),
    )

    labelled = []
    for lang, text in zip(["tr", "az", "auto"], candidates):
        if text:
            labelled.append((lang, text))

    if not labelled:
        return ""

    if len(labelled) == 1:
        return await synthesize_uzbek(labelled)

    return await synthesize_uzbek(labelled)


async def synthesize_uzbek(candidates: list[tuple[str, str]]) -> str:
    user_content = "\n\n".join(
        f"Variant {i+1} (Whisper language={lang}):\n{text}"
        for i, (lang, text) in enumerate(candidates)
    )

    response = await openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": UZBEK_SYNTHESIS_PROMPT},
            {"role": "user", "content": user_content},
        ],
        temperature=0,
    )
    return response.choices[0].message.content.strip()


async def transcribe_audio(file_path: str, target_lang: str) -> str:
    if target_lang == "uz":
        return await multi_pass_uzbek(file_path)

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

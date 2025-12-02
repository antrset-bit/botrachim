import os
import re
import asyncio
import tempfile
import subprocess
from contextlib import asynccontextmanager

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import CommandStart, Command
import httpx

# AI
from gigachat import GigaChat

# STT
from faster_whisper import WhisperModel

# TM original logic
from app.services.tm import tm_process_search, ROW_MATCH_REGISTERED, ROW_MATCH_EXPERTISE, ROW_MATCH_KW

AI_LABEL  = "🤖 AI-чат"
TM_LABEL  = "🏷️ Товарные знаки"
SECRETARY_LABEL = "👩‍💼 Секретарь"
SCHEDULE_LABEL = "📅 График событий"

MOD_AI = "ai"
MOD_TM = "tm"
MOD_SEC = "secretary"

user_mode = {}
user_chat_history = {}

def get_mode(uid: int) -> str:
    return user_mode.get(uid, MOD_SEC)
def set_mode(uid: int, mode: str):
    user_mode[uid] = mode

def main_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=AI_LABEL), KeyboardButton(text=TM_LABEL)],
                 [KeyboardButton(text=SECRETARY_LABEL)],
                 [KeyboardButton(text=SCHEDULE_LABEL)]],
        resize_keyboard=True
    )

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN is required")

SUMMARIZE_URL = os.getenv("SUMMARIZE_URL", "http://127.0.0.1:8000/summarize")

GIGACHAT_CREDENTIALS = os.getenv("GIGACHAT_CREDENTIALS")
GIGACHAT_SCOPE = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
GIGACHAT_MODEL = os.getenv("GIGACHAT_MODEL", "GigaChat")

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
WHISPER_BEAM_SIZE = int(os.getenv("WHISPER_BEAM_SIZE", "5"))
WHISPER_VAD_FILTER = os.getenv("WHISPER_VAD_FILTER", "true").lower() == "true"

model = WhisperModel(WHISPER_MODEL, device="auto", compute_type=WHISPER_COMPUTE_TYPE)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

@asynccontextmanager
async def http_client(timeout: int = 60):
    async with httpx.AsyncClient(timeout=timeout) as client:
        yield client

class _ShimContext:
    def __init__(self, bot: Bot):
        self.bot = bot

@router.message(CommandStart())
async def on_start(message: Message):
    set_mode(message.from_user.id, MOD_SEC)
    await message.answer(
        "Привет! Я могу:\n"
        f"• {SECRETARY_LABEL} — голос → саммари\n"
        f"• {AI_LABEL} — чат с ИИ\n"
        f"• {TM_LABEL} — поиск по Google Sheets (CSV)\n\n"
        f"Сейчас выбран режим: {SECRETARY_LABEL}\n"
        "Пришли голосовое сообщение 🎙️",
        reply_markup=main_kb()
    )

@router.message(F.text.in_({AI_LABEL, TM_LABEL, SECRETARY_LABEL}))
async def switch_mode(message: Message):
    uid = message.from_user.id
    label = message.text
    if label == AI_LABEL:
        set_mode(uid, MOD_AI)
        await message.answer("Режим AI-чат активирован. Напиши вопрос.", reply_markup=main_kb())
    elif label == TM_LABEL:
        set_mode(uid, MOD_TM)
        await message.answer("Режим ТМ активирован. Используй:\n• текстовый запрос\n• /tm_reg\n• /tm_exp", reply_markup=main_kb())
    else:
        set_mode(uid, MOD_SEC)
        await message.answer("Режим Секретарь активирован. Пришли голосовое.", reply_markup=main_kb())

# Secretary
@router.message(F.voice | F.audio)
async def handle_audio(message: Message):
    if get_mode(message.from_user.id) != MOD_SEC:
        await message.reply(f"Сейчас не {SECRETARY_LABEL}. Нажми кнопку, затем пришли голосовое.", reply_markup=main_kb()); return

    file_id = message.voice.file_id if message.voice else message.audio.file_id
    file = await bot.get_file(file_id)
    tg_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file.file_path}"

    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, "input.oga")
        wav_path = os.path.join(tmpdir, "input.wav")

        async with http_client() as client:
            r = await client.get(tg_url)
            r.raise_for_status()
            with open(src_path, "wb") as f: f.write(r.content)

        try:
            subprocess.run(["ffmpeg","-y","-i",src_path,"-ac","1","-ar","16000",wav_path], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            await message.reply("Не удалось декодировать аудио (ffmpeg)."); return

        await message.reply("Обрабатываю аудио… ⏳")
        segments, info = model.transcribe(wav_path, beam_size=WHISPER_BEAM_SIZE, vad_filter=WHISPER_VAD_FILTER, language="ru")
        transcript = " ".join(seg.text.strip() for seg in segments).strip()
        if not transcript:
            await message.reply("Не удалось распознать речь."); return

        async with http_client() as client:
            try:
                resp = await client.post(SUMMARIZE_URL, json={"transcript": transcript})
                resp.raise_for_status()
                data = resp.json()
                summary = data.get("summary") or "Нет ответа от саммари."
            except Exception as e:
                await message.reply(f"Ошибка при саммари: {e}"); return

        await message.reply(f"📝 {summary}", reply_markup=main_kb())

# AI chat
async def ai_chat_reply(uid: int, text: str) -> str:
    if not GIGACHAT_CREDENTIALS:
        return "AI-чат недоступен: не заданы GIGACHAT_* переменные."

    hist = user_chat_history.setdefault(uid, [])[-20:]
    hist.append(("user", text))

    lines = []
    for role, t in hist:
        prefix = "User:" if role == "user" else "Assistant:"
        lines.append(f"{prefix} {t}")
    lines.append("Assistant:")
    full_prompt = "
".join(lines)

    def _call_llm():
        with GigaChat(
            credentials=GIGACHAT_CREDENTIALS,
            scope=GIGACHAT_SCOPE,
            model=GIGACHAT_MODEL,
            verify_ssl_certs=False,
        ) as giga:
            return giga.chat(full_prompt)

    try:
        response = await asyncio.to_thread(_call_llm)
        ans = (response.choices[0].message.content or "").strip() or "Пустой ответ."
    except Exception as e:
        ans = f"Ошибка AI: {e}"

    user_chat_history[uid] = hist + [("assistant", ans)]
    return ans

# TM commands — no extra arguments; rely on tm.py filters
@router.message(Command("tm_reg"))
async def tm_reg_handler(message: Message):
    await tm_process_search(message.chat.id, ROW_MATCH_REGISTERED, _ShimContext(bot))

@router.message(Command("tm_exp"))
async def tm_exp_handler(message: Message):
    await tm_process_search(message.chat.id, ROW_MATCH_EXPERTISE, _ShimContext(bot))

# TM text search (keywords)
@router.message(F.text & ~F.text.in_({AI_LABEL, TM_LABEL, SECRETARY_LABEL, SCHEDULE_LABEL}))
async def handle_text(message: Message):
    mode = get_mode(message.from_user.id)
    text = (message.text or "").strip()
    if not text:
        await message.reply("Пустое сообщение.", reply_markup=main_kb()); return

    if mode == MOD_AI:
        reply = await ai_chat_reply(message.from_user.id, text)
        await message.reply(reply, reply_markup=main_kb()); return

    if mode == MOD_TM:
        kws = [w for w in re.split(r"[\s,;]+", text) if w]
        def kw_cb(row): return ROW_MATCH_KW(row, kws)
        await tm_process_search(message.chat.id, kw_cb, _ShimContext(bot)); return

    await message.reply(f"В {SECRETARY_LABEL} пришли голосовое сообщение 🎙️", reply_markup=main_kb())



@router.message(F.text == SCHEDULE_LABEL)
async def on_schedule_info(message: Message):
    await message.answer(
        text="""Я твой секретарь-помощник для оформления рассылки важных сообщений в канал.

Чтобы оформить рассылку:
1️⃣ Перейди по ссылке: https://docs.google.com/spreadsheets/d/14p0YJ9AKXBWZ4RShMKkgqq2_TnkDxkyA7phkeNT6wJY/edit?gid=0#gid=0
2️⃣ Заполни таблицу, отметив колонки:
   • СТАТУС — TRUE (активировать строку)
   • СООБЩЕНИЕ — текст рассылки
   • ПЕРИОДИЧНОСТЬ — День / Неделя / Месяц
   • ДАТА — для месячных (1–31)
   • ДЕНЬ НЕДЕЛИ — для еженедельных (пн, вт, ср, чт, пт, сб, вс или полные названия)
   • ВРЕМЯ — HH:MM или HH:MM:SS

После сохранения бот автоматически обновит расписание и поставит рассылку.""",
        reply_markup=main_kb(),
        disable_web_page_preview=False
    )
async def main():
    await bot.delete_webhook(drop_pending_updates=False)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())

# -*- coding: utf-8 -*-
import os
import re
import json
import logging
import asyncio
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from aiogram import Bot

import gspread
from gspread.exceptions import APIError
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("reminders")

# ---------- ENV ----------
TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow"))
SHEET_ID = os.getenv("GOOGLE_SHEETS_ID", "")
WORKSHEET = os.getenv("GOOGLE_SHEETS_WORKSHEET", "Лист1")
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")

def _parse_chat_id(v: str):
    """Возвращает int для числовых ID, иначе строку (например, @channel)."""
    if not v:
        return v
    if v.startswith("@"):
        return v
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    return v

CHANNEL_ID = _parse_chat_id(os.getenv("TELEGRAM_CHANNEL_ID", "@your_channel"))

# Креды Google: либо путь к файлу, либо JSON в переменной
CRED_PATH = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
CRED_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

# Поддерживаем и полные, и краткие русские названия дней недели
WEEKDAY_MAP = {
    "понедельник": 0, "вторник": 1, "среда": 2, "четверг": 3, "пятница": 4, "суббота": 5, "воскресенье": 6,
    "пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6,
}

# ---------- Helpers ----------
def _warn(idx, msg):
    log.warning("Row #%s: %s", idx, msg)

def debug_list_jobs(scheduler: AsyncIOScheduler):
    jobs = scheduler.get_jobs()
    if not jobs:
        log.info("No scheduled jobs.")
        return
    log.info("Scheduled jobs (%d):", len(jobs))
    for j in jobs:
        log.info("  id=%s next_run_time=%s trigger=%s", j.id, j.next_run_time, j.trigger)

def normalize_time(s: str):
    """Принимает разные форматы времени и приводит к (hour, minute).
       Допускает: '16:00', '16:00:00', '16.00', '16-00', '16 00', '16'.
    """
    s = (s or "").strip()

    # HH:MM[:SS]
    m = re.match(r"^(\d{1,2}):(\d{1,2})(?::\d{1,2})?$", s)
    if m:
        h, mm = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mm <= 59:
            return h, mm

    # Универсальные разделители
    m2 = re.search(r"^\s*(\d{1,2})\s*[:\.\-\s]\s*(\d{1,2})\s*$", s)
    if m2:
        h = int(m2.group(1))
        mm = int(m2.group(2))
        if 0 <= h <= 23 and 0 <= mm <= 59:
            return h, mm

    # Только часы
    m3 = re.match(r"^\s*(\d{1,2})\s*$", s)
    if m3:
        h = int(m3.group(1))
        if 0 <= h <= 23:
            return h, 0

    return None

def extract_first_digits(s: str):
    """Достаёт первое целое число из строки (например, '28.' -> 28)."""
    if s is None:
        return None
    m = re.search(r"\d+", str(s))
    return int(m.group(0)) if m else None

def _authorize_gsheets():
    """Создаёт gspread client из файла или JSON в переменной окружения.
       Добавлены scopes для чтения из Sheets и Drive (Shared Drive)."""
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ]

    if CRED_JSON:
        try:
            info = json.loads(CRED_JSON)
            creds = Credentials.from_service_account_info(info, scopes=scopes)
            log.info("Google creds: mode=JSON env (length=%s)", len(CRED_JSON))
        except Exception as e:
            raise RuntimeError(f"Invalid GOOGLE_CREDENTIALS_JSON: {e}")
    elif CRED_PATH:
        if not os.path.exists(CRED_PATH):
            raise RuntimeError(f"GOOGLE_APPLICATION_CREDENTIALS points to missing file: {CRED_PATH}")
        creds = Credentials.from_service_account_file(CRED_PATH, scopes=scopes)
        log.info("Google creds: mode=file path=%s", CRED_PATH)
    else:
        raise RuntimeError("Google creds not configured (set GOOGLE_CREDENTIALS_JSON or GOOGLE_APPLICATION_CREDENTIALS)")

    return gspread.authorize(creds)

def _current_service_email():
    try:
        if CRED_JSON:
            return json.loads(CRED_JSON).get("client_email")
        if CRED_PATH and os.path.exists(CRED_PATH):
            with open(CRED_PATH, "r", encoding="utf-8") as f:
                return json.load(f).get("client_email")
    except Exception:
        return None

def get_sheet():
    gc = _authorize_gsheets()
    try:
        sh = gc.open_by_key(SHEET_ID)
    except APIError as e:
        svc_email = _current_service_email()
        link = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit"
        # SERVICE_DISABLED и прочие разрешения
        try:
            resp = getattr(e, "response", None)
            if isinstance(resp, dict) and resp.get("status") == "PERMISSION_DENIED":
                det = resp.get("details") or []
                if any(isinstance(d, dict) and d.get("reason") == "SERVICE_DISABLED" for d in det):
                    log.error("Sheets API отключён для проекта ваших кредов. Включите API и повторите попытку.")
        except Exception:
            pass
        log.error("Нет доступа к таблице. Добавьте сервисный аккаунт как Viewer/Editor: %s | Sheet: %s", svc_email, link)
        raise
    ws = sh.worksheet(WORKSHEET)
    return ws

# ---------- Core ----------
def parse_row(idx: int, row: list[str]):
    """row: [A,B,C,D,E,F] = [status, message, period, date, weekday, time]"""
    status_raw = str(row[0]).strip().upper()
    if status_raw not in ("TRUE", "1", "ДА", "TRUE✓"):
        return None  # выключено

    message = (row[1] or "").strip()
    period = (row[2] or "").strip().lower()
    date_raw = (row[3] or "").strip()
    weekday_raw = (row[4] or "").strip().lower()
    time_raw = (row[5] or "").strip()

    if not message:
        _warn(idx, "пустое сообщение (B) — пропуск")
        return None

    tm = normalize_time(time_raw)
    if not tm:
        _warn(idx, f"некорректное время (F) '{time_raw}' — ожидалось HH:MM или HH:MM:SS")
        return None
    hour, minute = tm

    if period == "день":
        return {"key": f"row-{idx}", "type": "daily", "message": message, "hour": hour, "minute": minute}

    if period == "неделя":
        if weekday_raw not in WEEKDAY_MAP:
            _warn(idx, f"некорректный день недели (E) '{weekday_raw}' — пропуск")
            return None
        return {"key": f"row-{idx}", "type": "weekly", "message": message,
                "dow": WEEKDAY_MAP[weekday_raw], "hour": hour, "minute": minute}

    if period == "месяц":
        dom = extract_first_digits(date_raw)
        if dom is None or not (1 <= dom <= 31):
            _warn(idx, f"некорректная дата месяца (D) '{date_raw}' — пропуск (нужно целое 1–31)")
            return None
        return {"key": f"row-{idx}", "type": "monthly", "message": message,
                "dom": dom, "hour": hour, "minute": minute}

    _warn(idx, f"неизвестная периодичность (C) '{period}' — пропуск")
    return None

async def send(bot: Bot, text: str):
    try:
        await bot.send_message(chat_id=CHANNEL_ID, text=text)
        log.info("Sent: %r", text[:120])
    except Exception as e:
        log.exception("Send failed: %s", e)

def schedule_job(scheduler: AsyncIOScheduler, bot: Bot, item: dict):
    """Планирует задачу, передавая корутину напрямую (без asyncio.create_task)."""
    job_id = item["key"]
    old = scheduler.get_job(job_id)
    if old:
        old.remove()

    if item["type"] == "daily":
        trigger = CronTrigger(hour=item["hour"], minute=item["minute"], timezone=str(TZ))
    elif item["type"] == "weekly":
        trigger = CronTrigger(day_of_week=item["dow"], hour=item["hour"], minute=item["minute"], timezone=str(TZ))
    elif item["type"] == "monthly":
        trigger = CronTrigger(day=item["dom"], hour=item["hour"], minute=item["minute"], timezone=str(TZ))
    else:
        return

    scheduler.add_job(
        send,                      # асинхронная функция; AsyncIOScheduler выполнит её в loop
        trigger=trigger,
        id=job_id,
        args=(bot, item["message"]),
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )

def refresh_schedule(scheduler: AsyncIOScheduler, bot: Bot):
    ws = get_sheet()
    values = ws.get_all_values()
    if not values or len(values) < 2:
        log.warning("Sheet is empty or has only header")
        return

    active_keys = set()
    planned = 0

    for idx, row in enumerate(values[1:], start=2):
        row = (row + [""] * 6)[:6]
        item = parse_row(idx, row)
        if item:
            active_keys.add(item["key"])
            schedule_job(scheduler, bot, item)
            planned += 1

    for job in scheduler.get_jobs():
        if job.id.startswith("row-") and job.id not in active_keys:
            job.remove()

    log.info("Refresh done: planned=%d rows, active_keys=%d", planned, len(active_keys))
    debug_list_jobs(scheduler)

# ---------- Entry ----------
async def main():
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN/TELEGRAM_BOT_TOKEN is required for reminders")
    if not SHEET_ID:
        raise RuntimeError("GOOGLE_SHEETS_ID is required for reminders")

    bot = Bot(token=BOT_TOKEN)

    # ВАЖНО: используем текущий event loop для AsyncIOScheduler
    loop = asyncio.get_running_loop()
    scheduler = AsyncIOScheduler(event_loop=loop, timezone=str(TZ))
    scheduler.start()

    log.info(
        "Reminders started. TZ=%s SHEET_ID=%s WORKSHEET=%s CHANNEL_ID=%r Creds=%s",
        TZ, SHEET_ID, WORKSHEET, CHANNEL_ID,
        "JSON" if CRED_JSON else (CRED_PATH or "MISSING")
    )

    def poll():
        try:
            refresh_schedule(scheduler, bot)
        except Exception as e:
            log.exception("Refresh failed: %s", e)

    # первичная инициализация + периодический опрос
    poll()
    scheduler.add_job(
        poll,
        "interval",
        seconds=int(os.getenv("SHEET_POLL_INTERVAL", "60")),
        id="sheet-poll",
        replace_existing=True,
    )

    # Разовая тест-отправка при старте (для диагностики Telegram)
    test_now = os.getenv("SEND_TEST_NOW")
    if test_now:
        log.info("SEND_TEST_NOW detected -> sending test message")
        await send(bot, test_now)

    # держим цикл активным
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())

# Telegram Secretary + TM Bot (integrated)

- 👩‍💼 Secretary: voice → faster-whisper → /summarize (Gemini) → structured summary
- 🤖 AI-chat: Gemini (short history)
- 🏷️ TM: integrated original tm.py; commands /tm_reg and /tm_exp work without arguments

## ENV
Copy `.env.example` to `.env` and fill at least:
- TELEGRAM_TOKEN
- GEMINI_API_KEY
- TM_SHEET_CSV_URL

## Run locally
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000  # terminal 1
python -m app.bot                                 # terminal 2
```

## Docker
```bash
docker build -t tm-bot-integrated:latest .
docker run --rm -p 8000:8000 --env-file .env tm-bot-integrated:latest
```

## Notes
- Before polling we call `delete_webhook()` to avoid TelegramConflictError.
- `python-telegram-bot==13.15` + `urllib3==1.26.18` are pinned for InputFile compatibility.
- The bot exposes no web endpoints: interact via Telegram. API has `/` and `/healthz`.



## 📅 График событий — FAQ и таблица
Кнопка **«📅 График событий»** в боте выводит краткое FAQ и ссылку на таблицу управления рассылками.

**Google Sheets (Лист1):**
- A — Статус (TRUE/FALSE)
- B — Сообщение
- C — Периодичность (`День` | `Неделя` | `Месяц`)
- D — Дата (число месяца для `Месяц`)
- E — День недели (для `Неделя`)
- F — Время `HH:MM`

Таблица: https://docs.google.com/spreadsheets/d/14p0YJ9AKXBWZ4RShMKkgqq2_TnkDxkyA7phkeNT6wJY/edit?usp=sharing

**Env для планировщика напоминаний:**
```env
TIMEZONE=Europe/Moscow
GOOGLE_SHEETS_ID=14p0YJ9AKXBWZ4RShMKkgqq2_TnkDxkyA7phkeNT6wJY
GOOGLE_SHEETS_WORKSHEET=Лист1
TELEGRAM_CHANNEL_ID=@your_channel
GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/service_account.json
```
Планировщик читает таблицу каждые 60 секунд и обновляет расписание автоматически.

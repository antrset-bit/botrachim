#!/usr/bin/env bash
set -euo pipefail

uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --workers 1 &
API_PID=$!

python -u -m app.bot &
BOT_PID=$!

python -u -m app.reminders &
REM_PID=$!

trap "kill $API_PID $BOT_PID $REM_PID || true" TERM INT
wait -n $API_PID $BOT_PID $REM_PID

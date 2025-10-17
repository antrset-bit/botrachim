import os
import logging

logger = logging.getLogger("semantic-bot")

def _env(k: str, default: str = "") -> str:
    v = os.getenv(k, default)
    return v.strip() if isinstance(v, str) else v

# ===== TM settings =====
TM_ENABLE        = (_env("TM_ENABLE") or "1") == "1"
TM_SHEET_ID      = _env("TM_SHEET_ID")
TM_SHEET_GID     = _env("TM_SHEET_GID")
TM_SHEET_NAME    = _env("TM_SHEET_NAME")
TM_SHEET_CSV_URL = _env("TM_SHEET_CSV_URL")
TM_DEBUG         = (_env("TM_DEBUG") or "0") == "1"

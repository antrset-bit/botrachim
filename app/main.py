import os
import logging
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
import google.generativeai as genai
from google.api_core.exceptions import NotFound, FailedPrecondition

load_dotenv()
log = logging.getLogger("gemini-svc")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
MODEL_NAME = os.getenv("GEMINI_TEXT_MODEL", "gemini-flash-latest")
if not GEMINI_API_KEY:
    log.warning("GEMINI_API_KEY is not set; /summarize will be disabled")
else:
    genai.configure(api_key=GEMINI_API_KEY)

def safe_model(name: str):
    try:
        return genai.GenerativeModel(name)
    except Exception as e:
        log.warning("Model %s not available (%s), fallback to gemini-flash-latest", name, e)
        return genai.GenerativeModel("gemini-flash-latest")

MODEL = safe_model(MODEL_NAME)

app = FastAPI(title="Gemini Summarizer API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class SummarizeIn(BaseModel):
    transcript: str

class SummarizeOut(BaseModel):
    summary: str
    tech_spec: str

PROMPT_TMPL = """
Ты — аналитик голосовых сообщений. 
На вход тебе дан текст транскрипции речи.

Твоя задача — определить, есть ли в сообщении признаки события, особенно встречи, 
и структурировать информацию по шаблону ниже.

---
Текст речи:
{transcript}
---

Формат ответа:
### Событие
- Тип: встреча / звонок / обсуждение / задача / информация
- Тема: <основная тема разговора или встречи>
- Дата и время: <укажи дату и время, если не сказано явно — используй текущую дату>
- Участники: <перечисли имена или роли, если названы; если нет — укажи "не указаны">
- Ссылка на видеовстречу: https://telemost.yandex.ru/j/85575513867434
### Саммари
- <5–7 пунктов с ключевыми идеями речи>

Ответ должен быть аккуратным, структурированным, без пояснений вне секций.
"""

@app.get("/")
def index():
    return {
        "ok": True,
        "service": "tm-secretary-bot",
        "version": "3.0.0",
        "endpoints": {"health": "/healthz", "summarize": "POST /summarize"}
    }

@app.get("/healthz")
def healthz():
    return {"ok": True, "model": MODEL_NAME}

@app.post("/summarize", response_model=SummarizeOut)
def summarize(inp: SummarizeIn):
    transcript = (inp.transcript or "").strip()
    if not transcript:
        raise HTTPException(status_code=400, detail="transcript is empty")
    prompt = PROMPT_TMPL.format(transcript=transcript)
    try:
        resp = MODEL.generate_content(prompt, request_options={"timeout": 90})
    except FailedPrecondition as e:
        raise HTTPException(status_code=503, detail=f"Gemini region blocked: {e!s}")
    except NotFound as e:
        raise HTTPException(status_code=502, detail=f"Gemini model not found: {e!s}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Gemini error: {e!s}")
    text = (getattr(resp, "text", None) or "").strip()
    if "### ТЗ" in text:
        parts = text.split("### ТЗ", 1)
        summary = parts[0].replace("### Саммари", "").strip()
        tech = parts[1].strip()
    else:
        summary, tech = text, ""
    return {"summary": summary, "tech_spec": tech}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, workers=1)

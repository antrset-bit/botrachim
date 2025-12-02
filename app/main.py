import os
import logging
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from gigachat import GigaChat

load_dotenv()
log = logging.getLogger("gigachat-svc")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")

GIGACHAT_CREDENTIALS = os.getenv("GIGACHAT_CREDENTIALS", "")
GIGACHAT_SCOPE = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
GIGACHAT_MODEL = os.getenv("GIGACHAT_MODEL", "GigaChat")

if not GIGACHAT_CREDENTIALS:
    log.warning("GIGACHAT_CREDENTIALS is not set; /summarize will be disabled")

app = FastAPI(title="GigaChat Summarizer API", version="1.0.0")
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
    return {"ok": True, "model": GIGACHAT_MODEL}

@app.post("/summarize", response_model=SummarizeOut)
def summarize(inp: SummarizeIn):
    transcript = (inp.transcript or "").strip()
    if not transcript:
        raise HTTPException(status_code=400, detail="transcript is empty")

    if not GIGACHAT_CREDENTIALS:
        raise HTTPException(status_code=503, detail="GigaChat is not configured")

    prompt = PROMPT_TMPL.format(transcript=transcript)

    try:
        with GigaChat(
            credentials=GIGACHAT_CREDENTIALS,
            scope=GIGACHAT_SCOPE,
            model=GIGACHAT_MODEL,
            verify_ssl_certs=False,
        ) as giga:
            response = giga.chat(prompt)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"GigaChat error: {e!s}")

    text = ""
    try:
        text = (response.choices[0].message.content or "").strip()
    except Exception:
        text = ""

    if not text:
        raise HTTPException(status_code=502, detail="Empty response from GigaChat")

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

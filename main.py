from __future__ import annotations
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from dotenv import load_dotenv
from models import QualifyRequest
from qualifier import pre_filter, qualify_lead

load_dotenv()

app = FastAPI(
    title="C4 — Lead Qualifier",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("static/index.html")


@app.get("/health")
@app.head("/health", include_in_schema=False)
def health():
    return {"status": "ok", "service": "c4", "version": "1.0.0"}


@app.post("/qualify")
async def qualify(request: QualifyRequest):
    messages = request.messages
    last_user_msg = next(
        (m.content for m in reversed(messages) if m.role == "user"), ""
    )
    turn = sum(1 for m in messages if m.role == "user")

    # --- Pre-filter (deterministic, zero tokens) ---
    pf = pre_filter(last_user_msg)
    if not pf["passed"]:
        return {
            "intent": "qualify_lead",
            "turn": turn,
            "pre_filter": {"passed": False, "reason": pf["reason"]},
            "analysis": None,
            "resolution": None,
            "reply": pf["reply"],
        }

    # --- LLM qualification ---
    try:
        result = await qualify_lead(messages, turn)
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

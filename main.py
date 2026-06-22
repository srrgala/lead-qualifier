from __future__ import annotations
import logging
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from dotenv import load_dotenv
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from models import QualifyRequest
from qualifier import pre_filter, qualify_lead

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)
limiter = Limiter(key_func=get_remote_address)

load_dotenv()

app = FastAPI(
    title="C4 — Lead Qualifier",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

_ALLOWED_ORIGINS = [
    "https://lead-qualifier-vaa9.onrender.com",
    "http://localhost:8001",
    "http://127.0.0.1:8001",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
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
@limiter.limit("10/minute")
async def qualify(request: Request, body: QualifyRequest):
    messages = body.messages
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
        logger.exception("Error inesperado en qualify_lead: %s", exc)
        raise HTTPException(status_code=500, detail="Error interno del servidor.") from exc

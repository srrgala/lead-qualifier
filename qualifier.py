from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any
from anthropic import AsyncAnthropic
from pydantic import ValidationError
from models import Message, Analysis, Resolution

client = AsyncAnthropic()

# ---------------------------------------------------------------------------
# Pre-filter — deterministic, zero tokens
# ---------------------------------------------------------------------------

_GREETING_RE = re.compile(
    r"^(hola|hi|hello|buenas?|buenos días|buenas tardes|buenas noches|hey|saludos|"
    r"qué tal|que tal|ey|ola|buen día|good morning|good afternoon)[.!?,\s]*$",
    re.IGNORECASE,
)

_SPAM_RE = re.compile(
    r"(viagra|cialis|casino|click here|free money|ganaste|premio|lotería|lottery|"
    r"crypto investment|earn \$|gana dinero fácil)",
    re.IGNORECASE,
)

# Very obvious off-topic patterns (not business-related at all)
_OFF_TOPIC_RE = re.compile(
    r"^(¿?(cuál es la receta|cómo se hace|dime un chiste|cuéntame un chiste|"
    r"resultado del partido|quién ganó|predicción del tiempo|tiempo mañana|"
    r"traduce|translate|poema|canción|letra de)[^.]*)",
    re.IGNORECASE,
)


def pre_filter(message: str) -> dict:
    msg = message.strip()

    if not msg:
        return {
            "passed": False,
            "reason": "empty_message",
            "reply": (
                "Parece que tu mensaje llegó vacío. "
                "Cuéntanos en qué podemos ayudarte y estaremos encantados de atenderte."
            ),
        }

    if _GREETING_RE.match(msg):
        return {
            "passed": False,
            "reason": "greeting_only",
            "reply": (
                "¡Hola! Encantados. ¿En qué proyecto o necesidad de negocio podemos ayudarte?"
            ),
        }

    if _SPAM_RE.search(msg):
        return {
            "passed": False,
            "reason": "spam",
            "reply": (
                "Gracias por escribir. Este canal está reservado para consultas "
                "sobre servicios de transformación digital y estrategia."
            ),
        }

    if _OFF_TOPIC_RE.match(msg):
        return {
            "passed": False,
            "reason": "off_topic",
            "reply": (
                "Gracias por escribir. Este canal está reservado para consultas "
                "sobre proyectos de transformación digital y estrategia empresarial."
            ),
        }

    return {"passed": True, "reason": "ok"}


# ---------------------------------------------------------------------------
# System prompt — cargado una vez, nunca cambia entre requests
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT: str = (
    Path(__file__).parent / "prompts" / "system_prompt.txt"
).read_text(encoding="utf-8")

# Lista con cache_control: la parte fija del sistema se cachea desde el primer request.
_SYSTEM_WITH_CACHE = [{"type": "text", "text": _SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

CAP_TURNS = 6


def _parse_assistant_json(content: str) -> dict:
    try:
        return json.loads(content)
    except (json.JSONDecodeError, AttributeError):
        return {}


def _count_fit_clarification_attempts(messages: list[Message]) -> int:
    """Count previous pendiente_clarificacion turns where fit was 'pendiente'."""
    count = 0
    for m in messages[:-1]:
        if m.role == "assistant":
            data = _parse_assistant_json(m.content)
            res = data.get("resolution", {})
            ana = data.get("analysis", {})
            if (res.get("nivel") == "pendiente_clarificacion"
                    and ana.get("fit") == "pendiente"):
                count += 1
    return count


def _strip_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


def _build_messages(messages: list[Message], turn: int, fit_clarifications: int) -> list[dict]:
    """
    Construye la lista de mensajes para la API:

    - Inyecta el contexto de turno (dinámico) en el último mensaje de usuario,
      manteniendo el system prompt completamente estático y cacheado.
    - Añade cache_control al penúltimo mensaje para que el historial estable
      se cachee entre turnos (el breakpoint avanza con la conversación).
    """
    turn_prefix = (
        f"[CONTEXTO DE TURNO: turn_actual={turn} | "
        f"intentos_clarificacion_fit_previos={fit_clarifications} | "
        f"cap_turnos={CAP_TURNS}]\n"
        f"(Si turn_actual >= cap_turnos y Fit=si, clasifica como templado inmediatamente.)\n\n"
    )

    last_idx = len(messages) - 1
    # El penúltimo mensaje es el último estable — se cachea.
    # Si solo hay un mensaje, no hay historial previo que cachear.
    cache_idx = last_idx - 1 if last_idx > 0 else None

    result = []
    for i, m in enumerate(messages):
        if i == last_idx and m.role == "user":
            # Último mensaje de usuario: inyectar contexto de turno, sin caché (contenido nuevo).
            result.append({"role": m.role, "content": turn_prefix + m.content})
        elif i == cache_idx:
            # Penúltimo mensaje: marcar para caché (historial estable).
            result.append({
                "role": m.role,
                "content": [{"type": "text", "text": m.content, "cache_control": {"type": "ephemeral"}}],
            })
        else:
            result.append({"role": m.role, "content": m.content})

    return result


async def qualify_lead(messages: list[Message], turn: int) -> dict[str, Any]:
    fit_clarifications = _count_fit_clarification_attempts(messages)
    anthropic_messages = _build_messages(messages, turn, fit_clarifications)

    full_text = ""
    async with client.messages.stream(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=_SYSTEM_WITH_CACHE,
        messages=anthropic_messages,
    ) as stream:
        async for chunk in stream.text_stream:
            full_text += chunk

    result = json.loads(_strip_json(full_text))

    if "reply" not in result:
        raise ValueError(f"LLM response missing 'reply' field: {full_text[:200]}")

    # Validar sub-objetos con los modelos Pydantic — falla rápido si el LLM
    # devuelve campos inválidos o ausentes (literales fuera de rango, tipos incorrectos).
    try:
        if result.get("analysis"):
            result["analysis"] = Analysis(**result["analysis"]).model_dump()
        if result.get("resolution"):
            result["resolution"] = Resolution(**result["resolution"]).model_dump()
    except ValidationError as exc:
        raise ValueError(f"LLM response failed schema validation: {exc}") from exc

    return result

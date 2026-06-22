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

# ---------------------------------------------------------------------------
# Tool definitions — el modelo siempre invoca una de las dos (tool_choice=any)
# ---------------------------------------------------------------------------

_TOOLS: list[dict] = [
    {
        "name": "qualify_lead",
        "description": (
            "Registra el análisis FAT completo y emite la clasificación final del lead. "
            "Úsala para cualquier resultado: caliente, templado, fuera_de_alcance, "
            "o pendiente_clarificacion cuando ya conoces el fit y necesitas aclarar "
            "authority o timeline."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fit": {
                    "type": "string",
                    "enum": ["si", "no", "pendiente"],
                    "description": "Encaje del lead con el catálogo de servicios.",
                },
                "fit_reason": {
                    "type": "string",
                    "description": "Explicación concisa del fit.",
                },
                "authority": {
                    "type": "string",
                    "enum": ["si", "no", "posiblemente", "desconocido"],
                    "description": "Capacidad de decisión del interlocutor.",
                },
                "authority_reason": {
                    "type": "string",
                    "description": "Explicación concisa de la authority.",
                },
                "timeline": {
                    "type": "string",
                    "enum": ["semanas", "exploración", "sin horizonte", "desconocido"],
                    "description": "Horizonte temporal del proyecto.",
                },
                "timeline_reason": {
                    "type": "string",
                    "description": "Explicación concisa del timeline.",
                },
                "senal_budget": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "description": "Señal de presupuesto si se menciona espontáneamente; null si no.",
                },
                "nivel": {
                    "type": "string",
                    "enum": [
                        "caliente",
                        "templado",
                        "pendiente_clarificacion",
                        "fuera_de_alcance",
                    ],
                    "description": "Nivel de cualificación resultante.",
                },
                "subtipo": {
                    "anyOf": [
                        {"type": "string", "enum": ["sin_fit", "no_aclarado"]},
                        {"type": "null"},
                    ],
                    "description": "Subtipo de fuera_de_alcance; null en todos los demás casos.",
                },
                "dimensiones_faltantes": {
                    "anyOf": [
                        {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": ["authority", "timeline"],
                            },
                        },
                        {"type": "null"},
                    ],
                    "description": (
                        "Dimensiones aún desconocidas cuando nivel=pendiente_clarificacion "
                        "por Authority/Timeline, o cuando nivel=templado con dims sin resolver. "
                        "null para clarificación de Fit o para caliente."
                    ),
                },
                "reply": {
                    "type": "string",
                    "description": "Mensaje visible al lead. Tono natural de negocio.",
                },
            },
            "required": [
                "fit",
                "fit_reason",
                "authority",
                "authority_reason",
                "timeline",
                "timeline_reason",
                "nivel",
                "reply",
            ],
        },
    },
    {
        "name": "request_clarification",
        "description": (
            "Solicita una única pregunta de clarificación al lead sobre Fit. "
            "Úsala SOLO cuando fit='pendiente' y no has agotado los intentos de clarificación. "
            "Para clarificar authority o timeline (cuando fit=si), usa qualify_lead "
            "con nivel=pendiente_clarificacion."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "missing_info": {
                    "type": "string",
                    "enum": ["fit", "authority", "timeline"],
                    "description": "Dimensión que falta aclarar.",
                },
                "reply": {
                    "type": "string",
                    "description": "Pregunta de clarificación. Una sola pregunta, tono natural.",
                },
            },
            "required": ["missing_info", "reply"],
        },
    },
]


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
# Model routing
# ---------------------------------------------------------------------------

_MODEL_SONNET = "claude-sonnet-4-6"
_MODEL_HAIKU = "claude-haiku-4-5-20251001"


def _select_model(messages: list[Message]) -> str:
    """
    Select model based on conversation state as proxy for turn intent.

    - First turn or fit not yet confirmed → Sonnet (full capacity for fit evaluation)
    - Fit confirmed but authority/timeline still desconocido → Haiku (simpler clarification)
    - Fit confirmed and all dims resolved → Sonnet (final classification)
    """
    last_assistant = next(
        (m for m in reversed(messages) if m.role == "assistant"),
        None,
    )
    if last_assistant is None:
        return _MODEL_SONNET

    analysis = _parse_assistant_json(last_assistant.content).get("analysis") or {}
    prev_fit = analysis.get("fit")
    prev_authority = analysis.get("authority")
    prev_timeline = analysis.get("timeline")

    if (
        prev_fit == "si"
        and (prev_authority == "desconocido" or prev_timeline == "desconocido")
    ):
        return _MODEL_HAIKU

    return _MODEL_SONNET


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
    model = _select_model(messages)

    response = await client.messages.create(
        model=model,
        max_tokens=1024,
        system=_SYSTEM_WITH_CACHE,
        messages=anthropic_messages,
        tools=_TOOLS,
        tool_choice={"type": "any"},
    )
    print(json.dumps({
        "proyecto": "cala",
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cache_creation_input_tokens": getattr(response.usage, "cache_creation_input_tokens", 0),
        "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0),
    }))

    # tool_choice=any garantiza que siempre hay un bloque tool_use
    tool_use = next(
        (block for block in response.content if block.type == "tool_use"),
        None,
    )
    if tool_use is None:
        raise ValueError(f"LLM response contained no tool_use block: {response.content}")

    args = tool_use.input  # dict ya parseado por el SDK

    if tool_use.name == "qualify_lead":
        try:
            analysis = Analysis(
                fit=args["fit"],
                fit_reason=args["fit_reason"],
                authority=args["authority"],
                authority_reason=args["authority_reason"],
                timeline=args["timeline"],
                timeline_reason=args["timeline_reason"],
                senal_budget=args.get("senal_budget"),
            ).model_dump()
            resolution = Resolution(
                nivel=args["nivel"],
                subtipo=args.get("subtipo"),
                dimensiones_faltantes=args.get("dimensiones_faltantes"),
            ).model_dump()
        except ValidationError as exc:
            raise ValueError(f"Tool input failed schema validation: {exc}") from exc

    else:
        # request_clarification — solo tiene missing_info y reply.
        # Construimos analysis/resolution con valores coherentes para mantener
        # el contrato de respuesta y el contador de intentos de clarificación.
        missing = args["missing_info"]
        if missing == "fit":
            analysis = Analysis(
                fit="pendiente",
                fit_reason="Se requiere más información para determinar el encaje.",
                authority="desconocido",
                authority_reason="No evaluado.",
                timeline="desconocido",
                timeline_reason="No evaluado.",
                senal_budget=None,
            ).model_dump()
            resolution = Resolution(
                nivel="pendiente_clarificacion",
                subtipo=None,
                dimensiones_faltantes=None,
            ).model_dump()
        else:
            # missing == "authority" | "timeline"
            analysis = Analysis(
                fit="si",
                fit_reason="Fit confirmado.",
                authority="desconocido" if missing == "authority" else "desconocido",
                authority_reason="Pendiente de aclaración." if missing == "authority" else "No evaluado.",
                timeline="desconocido",
                timeline_reason="Pendiente de aclaración." if missing == "timeline" else "No evaluado.",
                senal_budget=None,
            ).model_dump()
            resolution = Resolution(
                nivel="pendiente_clarificacion",
                subtipo=None,
                dimensiones_faltantes=[missing],
            ).model_dump()

    return {
        "intent": "qualify_lead",
        "turn": turn,
        "pre_filter": {"passed": True, "reason": "passed_to_llm"},
        "analysis": analysis,
        "resolution": resolution,
        "reply": args["reply"],
    }

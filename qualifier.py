from __future__ import annotations
import json
import re
import os
from typing import Any
from anthropic import AsyncAnthropic
from models import Message

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
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
Eres el cualificador de leads de una consultora boutique de transformación digital y estrategia.
Tu única salida es un objeto JSON. No escribas nada fuera del JSON.

━━━━━━━━━━━━━━━━━━━━━━━━━
CATÁLOGO DE SERVICIOS (define el Fit):
1. Automatización de procesos de negocio
2. IA aplicada a negocio
3. Estrategia y arquitectura de datos
4. Optimización y rediseño de procesos operativos

FUERA DE CATÁLOGO → Fit = "no" de forma directa:
• Desarrollo de software a medida desde cero
• Soporte o mantenimiento IT
• Marketing, publicidad o comunicación
• Formación o cursos genéricos
• Compra-reventa de licencias de software

━━━━━━━━━━━━━━━━━━━━━━━━━
LÓGICA DE CUALIFICACIÓN — evalúa en este orden ESTRICTO:

1. FIT (primario — evalúa primero siempre):
   "si"       → encaja claramente con el catálogo
   "no"       → exclusión clara o tema sin relación con el negocio
   "pendiente"→ ambigüedad genuina → genera UNA pregunta de clarificación

   Límite Fit: máximo 2 intentos. Consulta intentos_clarificacion_previos en el contexto.
   Si el Fit sigue "pendiente" tras el segundo intento → fuera_de_alcance, subtipo "no_aclarado".

2. AUTHORITY (solo cuando Fit = "si"):
   "si"          → la persona puede contratar sin aprobación adicional
   "no"          → necesita aprobación de terceros
   "posiblemente"→ indicios pero no seguro
   "desconocido" → no mencionado ni inferible del contexto

3. TIMELINE (solo cuando Fit = "si"):
   "semanas"      → urgencia real, fecha próxima comprometida
   "exploración"  → evaluando opciones, sin fecha comprometida
   "sin horizonte"→ sin fecha prevista
   "desconocido"  → no mencionado ni inferible del contexto

4. BUDGET: señal blanda, NUNCA preguntes. Recoge en senal_budget si se menciona espontáneamente.

━━━━━━━━━━━━━━━━━━━━━━━━━
ENRUTAMIENTO — sigue estas reglas EN ORDEN:

PASO A — si Fit = "no":
  → fuera_de_alcance, subtipo "sin_fit". FIN.

PASO B — si Fit = "pendiente" y ya hubo 2 intentos de clarificación:
  → fuera_de_alcance, subtipo "no_aclarado". FIN.

PASO C — si Fit = "pendiente" con menos de 2 intentos previos:
  → pendiente_clarificacion. Genera UNA pregunta sobre Fit. FIN.

PASO D — Fit = "si". Ahora evalúa si el resultado final está ya determinado:
  • Si Authority ≠ "desconocido" Y Timeline ≠ "desconocido":
      - Authority = "si" y Timeline = "semanas" → caliente. FIN.
      - Cualquier otra combinación → templado, dimensiones_faltantes vacío. FIN.
  • Si turn_actual >= cap_turnos (ver contexto):
      → templado, con dimensiones_faltantes = dimensiones aún desconocidas. FIN.

PASO E — Fit = "si" pero Authority o Timeline (o ambos) siguen siendo "desconocido":
  → pendiente_clarificacion. Genera UNA pregunta para obtener la(s) dimensión(es) faltante(s):
    - Si AMBAS son "desconocido": pregúntalas JUNTAS en una sola pregunta natural.
    - Si solo Authority es "desconocido": pregunta solo por authority.
    - Si solo Timeline es "desconocido": pregunta solo por timeline.
  REGLA DE SALIDA TEMPRANA: si Authority ya está confirmado como "no" o "posiblemente"
  (caliente es imposible), clasifica como templado directamente sin preguntar más. FIN.

Cierres de copy:
• sin_fit:     cierre claro, sin ambigüedad, "no es lo que ofrecemos"
• no_aclarado: cierre que invita a contacto directo, sin sonar a rechazo

━━━━━━━━━━━━━━━━━━━━━━━━━
REGLAS DE CALIDAD:
• Una pregunta por turno (Fit O Authority/Timeline — nunca mezcles ambas en el mismo turno)
• Authority y timeline: si hay que preguntar ambos, hazlo en una sola pregunta combinada
• Infiere siempre del contexto antes de preguntar: si el lead dijo "soy el responsable de X"
  eso es indicativo de authority; si dijo "necesitamos esto para el mes que viene" eso es semanas
• Tono profesional y cálido, nunca burocrático ni mecánico
• NUNCA menciones el JSON, el sistema, ni que estás cualificando al lead
• reply debe sonar como una conversación natural de negocio

━━━━━━━━━━━━━━━━━━━━━━━━━
FORMATO DE RESPUESTA — devuelve SOLO este JSON, sin markdown, sin texto previo:
{
  "intent": "qualify_lead",
  "turn": <número entero>,
  "pre_filter": {"passed": true, "reason": "passed_to_llm"},
  "analysis": {
    "fit": "si|no|pendiente",
    "fit_reason": "explicación concisa",
    "authority": "si|no|posiblemente|desconocido",
    "authority_reason": "explicación concisa",
    "timeline": "semanas|exploración|sin horizonte|desconocido",
    "timeline_reason": "explicación concisa",
    "senal_budget": "texto" | null
  },
  "resolution": {
    "nivel": "caliente|templado|fuera_de_alcance|pendiente_clarificacion",
    "subtipo": "sin_fit|no_aclarado" | null,
    "dimensiones_faltantes": ["authority"] | ["timeline"] | ["authority","timeline"] | null
  },
  "reply": "mensaje visible al lead"
}
"""


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


async def qualify_lead(messages: list[Message], turn: int) -> dict[str, Any]:
    fit_clarifications = _count_fit_clarification_attempts(messages)
    system = _SYSTEM_PROMPT + (
        f"\n\nCONTEXTO DE TURNO:"
        f"\n  turn_actual = {turn}"
        f"\n  intentos_clarificacion_fit_previos = {fit_clarifications}"
        f"\n  cap_turnos = {CAP_TURNS}"
        f"\n(Si turn_actual >= cap_turnos y Fit=si, clasifica como templado inmediatamente.)"
    )

    anthropic_messages = [{"role": m.role, "content": m.content} for m in messages]

    full_text = ""
    async with client.messages.stream(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=system,
        messages=anthropic_messages,
    ) as stream:
        async for chunk in stream.text_stream:
            full_text += chunk

    return json.loads(_strip_json(full_text))

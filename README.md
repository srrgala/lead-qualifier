# Lead Qualifier

Cualificador de leads conversacional para consultora boutique de transformación digital.
Hermano de [Yema](../yema) (generador de briefs). Portfolio técnico #3.

## Stack

- **FastAPI** · **Pydantic v2** · **anthropic SDK** (streaming)
- Frontend: vanilla HTML/CSS/JS (servido por el mismo proceso FastAPI)
- Modelo: `claude-sonnet-4-6`
- Puerto: `8001`

## Instalación

```bash
pip install -r requirements.txt
cp .env.example .env   # añade tu ANTHROPIC_API_KEY
```

## Arranque local

```bash
uvicorn main:app --port 8001 --reload
```

Abre `http://localhost:8001` — frontend y backend servidos por el mismo proceso.

## Deploy en Render

1. Conecta el repo en [render.com](https://render.com) → New Web Service
2. Render detecta `render.yaml` automáticamente
3. Añade la variable de entorno `ANTHROPIC_API_KEY` en el dashboard (Environment → Add variable)
4. Deploy

El `render.yaml` ya configura el comando correcto (`--host 0.0.0.0 --port $PORT`). No uses `--port 8001` en producción — Render asigna su propio puerto vía `$PORT`.

## Endpoints

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/health` | Estado del servicio |
| POST | `/qualify` | Cualificar un lead |

### POST /qualify

El cliente envía el historial completo (sin estado en servidor).

**Request:**
```json
{
  "messages": [
    {"role": "user", "content": "Queremos automatizar nuestro proceso de facturación..."}
  ]
}
```

**Response:**
```json
{
  "intent": "qualify_lead",
  "turn": 1,
  "pre_filter": {"passed": true, "reason": "ok"},
  "analysis": {
    "fit": "si",
    "fit_reason": "...",
    "authority": "si",
    "authority_reason": "...",
    "timeline": "semanas",
    "timeline_reason": "...",
    "senal_budget": null
  },
  "resolution": {
    "nivel": "caliente",
    "subtipo": null,
    "dimensiones_faltantes": null
  },
  "reply": "Mensaje visible al lead"
}
```

**Niveles de resolución:**

| nivel | descripción |
|-------|-------------|
| `caliente` | Fit=Sí + Authority=Sí + Timeline="semanas" |
| `templado` | Fit=Sí, pero Authority o Timeline impiden `caliente` o no se pudieron obtener |
| `pendiente_clarificacion` | Esperando clarificación sobre Fit, Authority o Timeline |
| `fuera_de_alcance` | Sin fit (`sin_fit`) o fit no aclarado tras intentos (`no_aclarado`) |

**Contrato de campos por nivel:**

El campo `reply` contiene siempre el mensaje a mostrar al lead — sin excepción, en todos los niveles. No existe un campo separado para preguntas de clarificación.

`subtipo` y `dimensiones_faltantes` están **siempre presentes** en la respuesta (nunca ausentes); pueden valer `null`.

| nivel | `subtipo` | `dimensiones_faltantes` |
|-------|-----------|------------------------|
| `caliente` | `null` | `null` |
| `templado` | `null` | lista de dims no resueltas, o `null` si todas conocidas |
| `pendiente_clarificacion` (Fit) | `null` | `null` |
| `pendiente_clarificacion` (Authority/Timeline) | `null` | `["authority"]`, `["timeline"]` o `["authority","timeline"]` |
| `fuera_de_alcance` | `"sin_fit"` o `"no_aclarado"` | `null` |

`dimensiones_faltantes` poblado dentro de `pendiente_clarificacion` permite distinguir si la pregunta es sobre Fit (null) o sobre Authority/Timeline (lista).

**Ejemplos reales de respuesta:**

<details>
<summary>caliente</summary>

```json
{
  "intent": "qualify_lead",
  "turn": 1,
  "pre_filter": { "passed": true, "reason": "passed_to_llm" },
  "analysis": {
    "fit": "si",
    "fit_reason": "Automatización de gestión de pedidos encaja con automatización de procesos de negocio.",
    "authority": "si",
    "authority_reason": "Se identifica como Director General y afirma que decide él.",
    "timeline": "semanas",
    "timeline_reason": "Indica 'antes de fin de mes' con consecuencia contractual concreta.",
    "senal_budget": null
  },
  "resolution": {
    "nivel": "caliente",
    "subtipo": null,
    "dimensiones_faltantes": null
  },
  "reply": "Perfecto, esto encaja justo con lo que hacemos..."
}
```
</details>

<details>
<summary>templado</summary>

```json
{
  "intent": "qualify_lead",
  "turn": 1,
  "pre_filter": { "passed": true, "reason": "passed_to_llm" },
  "analysis": {
    "fit": "si",
    "fit_reason": "Aplicar IA a datos de ventas encaja con IA aplicada a negocio y estrategia de datos.",
    "authority": "si",
    "authority_reason": "Es CEO y declara explícitamente que decide él.",
    "timeline": "exploración",
    "timeline_reason": "Usa 'explorar' y confirma que no hay fecha comprometida.",
    "senal_budget": null
  },
  "resolution": {
    "nivel": "templado",
    "subtipo": null,
    "dimensiones_faltantes": null
  },
  "reply": "Encantado de conocerte. Aplicar IA a datos de ventas es exactamente el tipo de reto en el que trabajamos..."
}
```
</details>

<details>
<summary>pendiente_clarificacion — sobre Fit (dimensiones_faltantes: null)</summary>

```json
{
  "intent": "qualify_lead",
  "turn": 1,
  "pre_filter": { "passed": true, "reason": "passed_to_llm" },
  "analysis": {
    "fit": "pendiente",
    "fit_reason": "Mensaje demasiado amplio para confirmar fit: podría encajar o estar fuera de catálogo.",
    "authority": "desconocido",
    "authority_reason": "No mencionado.",
    "timeline": "desconocido",
    "timeline_reason": "No mencionado.",
    "senal_budget": null
  },
  "resolution": {
    "nivel": "pendiente_clarificacion",
    "subtipo": null,
    "dimensiones_faltantes": null
  },
  "reply": "¡Hola! Para entender bien cómo podríamos ayudaros, ¿podrías contarme qué aspecto concreto queréis mejorar?"
}
```
</details>

<details>
<summary>pendiente_clarificacion — sobre Authority/Timeline (dimensiones_faltantes poblado)</summary>

```json
{
  "intent": "qualify_lead",
  "turn": 1,
  "pre_filter": { "passed": true, "reason": "passed_to_llm" },
  "analysis": {
    "fit": "si",
    "fit_reason": "Digitalización de procesos de almacén encaja con automatización y optimización de procesos.",
    "authority": "desconocido",
    "authority_reason": "No se ha mencionado el rol ni capacidad de decisión.",
    "timeline": "desconocido",
    "timeline_reason": "No se ha indicado horizonte temporal.",
    "senal_budget": null
  },
  "resolution": {
    "nivel": "pendiente_clarificacion",
    "subtipo": null,
    "dimensiones_faltantes": ["authority", "timeline"]
  },
  "reply": "¡Perfecto, es un área en la que podemos aportaros mucho valor! Para entender bien cómo avanzar, ¿cuál es tu rol en este proyecto y tenéis algún plazo en mente?"
}
```
</details>

<details>
<summary>fuera_de_alcance — sin_fit</summary>

```json
{
  "intent": "qualify_lead",
  "turn": 1,
  "pre_filter": { "passed": true, "reason": "passed_to_llm" },
  "analysis": {
    "fit": "no",
    "fit_reason": "Desarrollo de app móvil desde cero está explícitamente fuera de catálogo.",
    "authority": "desconocido",
    "authority_reason": "No aplica.",
    "timeline": "desconocido",
    "timeline_reason": "No aplica.",
    "senal_budget": null
  },
  "resolution": {
    "nivel": "fuera_de_alcance",
    "subtipo": "sin_fit",
    "dimensiones_faltantes": null
  },
  "reply": "Gracias por contactarnos. El desarrollo de aplicaciones móviles desde cero no es un servicio que ofrezcamos..."
}
```
</details>

<details>
<summary>fuera_de_alcance — no_aclarado</summary>

```json
{
  "intent": "qualify_lead",
  "turn": 3,
  "pre_filter": { "passed": true, "reason": "passed_to_llm" },
  "analysis": {
    "fit": "pendiente",
    "fit_reason": "El lead no ha podido aportar detalle tras dos intentos de clarificación.",
    "authority": "desconocido",
    "authority_reason": "No mencionado.",
    "timeline": "desconocido",
    "timeline_reason": "No mencionado.",
    "senal_budget": null
  },
  "resolution": {
    "nivel": "fuera_de_alcance",
    "subtipo": "no_aclarado",
    "dimensiones_faltantes": null
  },
  "reply": "Sin problema, lo entendemos perfectamente. Cuando tengáis más claridad, no dudéis en volver a contactarnos."
}
```
</details>

## Lógica de terminación

El sistema tiene tres mecanismos de terminación independientes:

| Mecanismo | Cap | Condición de activación | Resultado |
|---|---|---|---|
| Clarificación de **Fit** | 2 intentos | Fit sigue `pendiente` tras 2 preguntas | `fuera_de_alcance / no_aclarado` |
| Clarificación de **Authority/Timeline** | 2 intentos | Fit=Sí pero dimensiones siguen `desconocido` tras 2 preguntas | `templado` con `dimensiones_faltantes` |
| **Cap global** | 6 turnos | Conversación llega a 6 turnos sin clasificación final | `templado` forzado con lo que haya |

Los caps de Fit y Authority/Timeline son independientes y se cuentan por separado. El cap global (6 turnos) actúa como red de seguridad; con 2+2 intentos máximos para cada fase, nunca se alcanza en condiciones normales.

La salida temprana aplica cuando el resultado ya está determinado: si Authority es `no` o `posiblemente`, `caliente` es imposible y el sistema clasifica como `templado` sin agotar los intentos restantes.

## Tests

```bash
pytest tests/ -v
```

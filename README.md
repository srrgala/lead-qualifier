# Lead Qualifier

Cualificador de leads conversacional para consultora boutique de transformación digital.

---

## Problema de negocio

Los equipos comerciales pierden tiempo cualificando leads que nunca van a convertir. El problema no es la cantidad de leads, es que el equipo no tiene información sobre fit, autoridad de decisión ni urgencia antes de invertir tiempo en una llamada. Ese tiempo tiene coste real: en una consultora boutique, las horas de ventas son escasas y no se recuperan.

Este sistema actúa como primera capa de cualificación conversacional: extrae las tres dimensiones críticas (Fit, Authority, Timeline) en lenguaje natural y clasifica el lead antes de que llegue al equipo. El comercial recibe un lead etiquetado, no una conversación sin procesar.

---

## Decisiones técnicas

### Lógica FAT jerárquica, no scoring plano

El orden Fit → Authority → Timeline no es arbitrario. Fit es una condición de salida: si el problema del lead no encaja con el catálogo, Authority y Timeline son irrelevantes — preguntar por capacidad de decisión a alguien cuya necesidad no tiene solución en el catálogo no aporta nada. Dentro de Fit=Sí, Authority determina si la conversación puede producir una decisión: un lead con fit perfecto pero sin autoridad necesita involucrar a otra persona antes de avanzar. Timeline viene último porque solo importa cuando los dos primeros están resueltos — distingue entre un comprador motivado con fecha y uno que "está explorando".

Un modelo de scoring plano agregaría puntos de las tres dimensiones y produciría un score compuesto. El problema es que un lead con Timeline excelente pero sin Fit acumularía puntos y generaría falsos positivos. La lógica jerárquica elimina esa posibilidad: cada dimensión es un gate, no un sumando.

### Stateless: el cliente envía el historial completo

El servidor no guarda estado de conversación. En cada llamada, el cliente envía el historial completo y el servidor lo procesa desde cero.

La alternativa — sesiones server-side — requiere almacenamiento (Redis, base de datos), lógica de expiración, gestión de IDs de sesión y cleanup. Para una conversación acotada a 6 turnos que cabe cómodamente en un payload JSON, esa infraestructura no añade valor. El cliente (navegador) ya tiene el historial; guardarlo también en el servidor es duplicar sin beneficio.

El diseño stateless también hace que cada request sea autónomo: puede auditarse en aislamiento, reproducirse exactamente con el mismo payload y escalarse horizontalmente sin coordinación entre instancias.

### Pre-filter determinista antes del LLM

Saludos, mensajes vacíos y contenido off-topic se resuelven con regex antes de llegar al LLM. El pre-filter no es una optimización menor: cada mensaje que pasa al LLM tiene coste de latencia y tokens. Un "hola" o un mensaje en blanco no necesitan clasificación FAT — necesitan una respuesta inmediata y predecible.

El pre-filter también hace el sistema más estable en los bordes. Una respuesta a un saludo siempre es la misma, independientemente de la versión del modelo o de cambios en el prompt. El LLM se reserva para los casos donde la comprensión del lenguaje natural añade valor real: mensajes con contenido que requiere juicio sobre fit, autoridad y urgencia.

### Caps de terminación independientes

Hay tres mecanismos de terminación que operan por separado: 2 intentos para clarificar Fit, 2 intentos para clarificar Authority/Timeline, y un cap global de 6 turnos.

El cap de Fit resuelve un problema concreto: un lead que no puede explicar qué necesita después de dos preguntas casi con certeza no tiene un caso de uso claro. Seguir preguntando degrada la conversación sin mejorar la señal. El resultado es `fuera_de_alcance / no_aclarado` — no `sin_fit`, porque el problema no es incompatibilidad sino falta de información.

El cap de Authority/Timeline es distinto en naturaleza: Fit ya está confirmado, el lead merece esfuerzo. Pero si después de dos turnos estas dimensiones siguen sin resolverse, el canal de chat no es el medio adecuado para extraerlas — una llamada lo hará mejor. El resultado es `templado` con las dimensiones faltantes señaladas, para que el comercial sepa qué preguntar.

El cap global de 6 turnos es una red de seguridad para casos que ninguno de los dos caps específicos captura. Con 2+2 intentos máximos por fase, en condiciones normales la conversación termina antes de llegar a 6 turnos. El cap global garantiza que ninguna conversación escape al control del sistema.

### Tool use en lugar de JSON en texto

La alternativa más simple a tool use es instruir al modelo a devolver JSON en el mensaje de texto y parsearlo con `json.loads()`. El problema: el modelo puede desviarse del schema (campos ausentes, literales fuera de rango, markdown alrededor del JSON), y el error aparece aguas abajo cuando ya se intentó usar el dato.

Con tool use nativo, el modelo no puede devolver una estructura diferente a la definida — la API rechaza la respuesta antes de que llegue al servidor. El schema se define una vez en la definición del tool y vale como contrato entre el modelo y el código: no hay `json.loads()`, no hay regex para limpiar markdown, y la validación Pydantic opera sobre un dict ya tipado, no sobre texto libre. Cualquier fallo de schema es estructural y ocurre en el momento de la llamada, no después.

`tool_choice={"type": "any"}` fuerza al modelo a invocar siempre una de las dos tools (`qualify_lead` o `request_clarification`), eliminando el caso en que el modelo devuelva texto libre cuando no sabe qué hacer.

### Sonnet en lugar de Haiku

La clasificación FAT requiere juicio sobre lenguaje hedgeado y señales indirectas. Un lead que dice "estamos explorando opciones para el año que viene" necesita clasificarse como Timeline=exploración, no Timeline=meses. Otro que dice "necesitamos tenerlo antes de que empiece la campaña" apunta a semanas aunque no dé una fecha. La diferencia importa: un falso `caliente` envía un lead de baja calidad al equipo comercial; un falso `fuera_de_alcance` elimina una oportunidad real.

Haiku maneja bien las declaraciones explícitas pero falla en la ambigüedad y el lenguaje indirecto a una tasa que sí importa en un contexto de cualificación. El coste incremental de Sonnet sobre una conversación acotada a 6 turnos es pequeño. El coste de una clasificación errónea — tiempo de ventas desperdiciado o lead perdido — no lo es.

---

## Stack

- **FastAPI** · **Pydantic v2** · **anthropic SDK** (tool use)
- Frontend: vanilla HTML/CSS/JS (servido por el mismo proceso FastAPI)
- Modelo: `claude-sonnet-4-6`
- Puerto local: `8001`

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

URL de producción: https://lead-qualifier-vaa9.onrender.com

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

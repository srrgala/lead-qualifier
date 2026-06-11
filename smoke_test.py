"""
Smoke tests en caliente — cubren cada rama de la tabla de enrutamiento.
Usa la API real (LLM). No mockea nada.

Ejecutar: python3 smoke_test.py
"""
from __future__ import annotations
import json
import sys
import httpx

BASE = "http://localhost:8001"
PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"

results: list[tuple[str, bool]] = []


def qualify(messages: list[dict]) -> dict:
    r = httpx.post(f"{BASE}/qualify", json={"messages": messages}, timeout=30)
    r.raise_for_status()
    return r.json()


def check(label: str, data: dict, *, nivel: str, subtipo: str | None = None, fit: str | None = None) -> dict:
    res = data.get("resolution") or {}
    ana = data.get("analysis") or {}
    ok = (
        res.get("nivel") == nivel
        and res.get("subtipo") == subtipo
        and (fit is None or ana.get("fit") == fit)
    )
    icon = PASS if ok else FAIL
    print(f"\n{icon} [{label}]")
    print(f"   fit={ana.get('fit')!r:12} authority={ana.get('authority')!r:15} timeline={ana.get('timeline')!r}")
    print(f"   nivel={res.get('nivel')!r}  subtipo={res.get('subtipo')!r}  faltantes={res.get('dimensiones_faltantes')}")
    print(f"   reply: {data.get('reply','')[:120]}")
    if not ok:
        print(f"   \033[31mESPERADO nivel={nivel!r} subtipo={subtipo!r}{f' fit={fit!r}' if fit else ''}\033[0m")
    results.append((label, ok))
    return data


# ---------------------------------------------------------------------------
# CASO 1 — caliente: Fit=Sí, Authority=Sí, Timeline="semanas" (todo explícito)
# ---------------------------------------------------------------------------
print("\n" + "="*70)
print("CASO 1: caliente — todo explícito en turno 1")
print("="*70)
d1 = qualify([{"role": "user", "content":
    "Somos una empresa de distribución de 40 personas. Soy el director general y "
    "tenemos que automatizar nuestro proceso de gestión de pedidos antes de que "
    "acabe el mes que viene porque perdemos un cliente importante. Soy yo quien decide."}])
check("caliente", d1, nivel="caliente")

# ---------------------------------------------------------------------------
# CASO 2 — templado: Fit=Sí, Authority=Sí, Timeline="exploración"
# ---------------------------------------------------------------------------
print("\n" + "="*70)
print("CASO 2: templado — Fit=Sí, Authority=Sí, Timeline='exploración'")
print("="*70)
d2 = qualify([{"role": "user", "content":
    "Soy el CEO de una empresa de servicios financieros de 25 personas. "
    "Estamos valorando si tiene sentido aplicar IA a nuestros procesos de análisis de riesgo. "
    "De momento es una exploración, no tenemos fecha comprometida. Soy yo quien decide si avanzamos."}])
check("templado (no caliente)", d2, nivel="templado")

# ---------------------------------------------------------------------------
# CASO 3 — fuera_de_alcance/sin_fit
# ---------------------------------------------------------------------------
print("\n" + "="*70)
print("CASO 3: fuera_de_alcance/sin_fit — app desde cero")
print("="*70)
d3 = qualify([{"role": "user", "content":
    "Necesitamos que nos programéis una aplicación móvil desde cero para nuestros clientes."}])
check("fuera_de_alcance/sin_fit", d3, nivel="fuera_de_alcance", subtipo="sin_fit")

# ---------------------------------------------------------------------------
# CASO 4 — pendiente_clarificacion: fit ambiguo turno 1
# ---------------------------------------------------------------------------
print("\n" + "="*70)
print("CASO 4: pendiente_clarificacion — fit ambiguo, debe preguntar por Fit")
print("="*70)
d4 = qualify([{"role": "user", "content":
    "Queremos mejorar nuestra empresa con tecnología e IA."}])
check("pendiente_clarificacion (fit)", d4, nivel="pendiente_clarificacion", fit="pendiente")

# ---------------------------------------------------------------------------
# CASO 5 — fuera_de_alcance/no_aclarado: fit vago x2 turnos
# ---------------------------------------------------------------------------
print("\n" + "="*70)
print("CASO 5: fuera_de_alcance/no_aclarado — vago x2, copy distinto a sin_fit")
print("="*70)

t5_1 = qualify([{"role": "user", "content": "Queremos hacer algo con tecnología para el negocio."}])
print(f"\n   Turno 1 → nivel={t5_1.get('resolution',{}).get('nivel')!r}")
print(f"   reply: {t5_1.get('reply','')[:110]}")

t5_2 = qualify([
    {"role": "user",      "content": "Queremos hacer algo con tecnología para el negocio."},
    {"role": "assistant", "content": json.dumps(t5_1)},
    {"role": "user",      "content": "No sé exactamente, algo que mejore las cosas, ya os lo diré."},
])
print(f"\n   Turno 2 → nivel={t5_2.get('resolution',{}).get('nivel')!r}")
print(f"   reply: {t5_2.get('reply','')[:110]}")

t5_3 = qualify([
    {"role": "user",      "content": "Queremos hacer algo con tecnología para el negocio."},
    {"role": "assistant", "content": json.dumps(t5_1)},
    {"role": "user",      "content": "No sé exactamente, algo que mejore las cosas, ya os lo diré."},
    {"role": "assistant", "content": json.dumps(t5_2)},
    {"role": "user",      "content": "La verdad es que no tengo más detalles ahora mismo."},
])
check("fuera_de_alcance/no_aclarado", t5_3, nivel="fuera_de_alcance", subtipo="no_aclarado")

# Verify copy is distinct
same_copy = d3.get("reply","").lower() == t5_3.get("reply","").lower()
icon = FAIL if same_copy else PASS
print(f"\n   {icon} Copy sin_fit vs no_aclarado {'IGUALES ← BUG' if same_copy else 'distintos ← OK'}")
print(f"   sin_fit:     {d3.get('reply','')[:100]}")
print(f"   no_aclarado: {t5_3.get('reply','')[:100]}")

# ---------------------------------------------------------------------------
# CASO 6 — EL CASO CLAVE: Fit=Sí turno 1, sin Authority ni Timeline
#           DEBE preguntar (pendiente_clarificacion), NO clasificar como templado
# ---------------------------------------------------------------------------
print("\n" + "="*70)
print("CASO 6: conversacional — Fit=Sí, sin Authority/Timeline → debe PREGUNTAR")
print("="*70)

msgs6 = [{"role": "user", "content":
    "Queremos digitalizar nuestros procesos operativos en el almacén."}]

print()
MAX_TURNS = 8
classified = False
for i in range(MAX_TURNS):
    resp = qualify(msgs6)
    res6  = resp.get("resolution") or {}
    ana6  = resp.get("analysis") or {}
    nivel = res6.get("nivel")
    print(f"   Turno {i+1}: fit={ana6.get('fit')!r} auth={ana6.get('authority')!r} "
          f"timeline={ana6.get('timeline')!r} → nivel={nivel!r}")
    print(f"           reply: {resp.get('reply','')[:100]}")

    if nivel in ("caliente", "templado", "fuera_de_alcance"):
        classified = True
        # Check: should NOT have classified at turn 1 with unknown auth/timeline
        if i == 0:
            auth = ana6.get("authority")
            tl   = ana6.get("timeline")
            both_unknown = auth == "desconocido" and tl == "desconocido"
            icon_t1 = FAIL if both_unknown else PASS
            print(f"\n   {icon_t1} Clasificó en turno 1 con authority={auth!r} timeline={tl!r}"
                  f" {'← BUG: no preguntó' if both_unknown else '← OK: tenía info suficiente'}")
            results.append(("caso6_no_clasifica_turno1_sin_info", not both_unknown))
        else:
            print(f"\n   {PASS} Clasificó en turno {i+1} tras recopilar información")
            results.append(("caso6_no_clasifica_turno1_sin_info", True))
        break

    # Continue conversation: answer questions naturally without volunteering extra info
    replies = [
        "Sí, exactamente eso es lo que necesitamos.",
        "Tenemos varios procesos manuales que queremos optimizar.",
        "Es algo que llevamos tiempo pensando.",
        "Creemos que hay mucho margen de mejora.",
        "Queremos hacerlo bien y de forma sostenible.",
        "Necesitamos una solución robusta.",
        "Preferimos empezar por las áreas de mayor impacto.",
    ]
    follow_up = replies[i] if i < len(replies) else "Sí."
    msgs6.append({"role": "assistant", "content": json.dumps(resp)})
    msgs6.append({"role": "user", "content": follow_up})

if not classified:
    print(f"\n   {FAIL} Nunca clasificó en {MAX_TURNS} turnos")
    results.append(("caso6_no_clasifica_turno1_sin_info", False))

# ---------------------------------------------------------------------------
# RESUMEN
# ---------------------------------------------------------------------------
print("\n" + "="*70)
print("RESUMEN")
print("="*70)
passed = sum(1 for _, ok in results if ok)
total  = len(results)
for label, ok in results:
    print(f"  {PASS if ok else FAIL} {label}")
print(f"\n  {passed}/{total} passed")
if passed < total:
    sys.exit(1)

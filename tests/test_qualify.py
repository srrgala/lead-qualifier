"""
C4 test suite — happy path, pre_filter cases, unclear fit, routing.
LLM calls are mocked; only the FastAPI + qualifier logic is exercised.
"""
from __future__ import annotations
import json
import sys
import os
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import app

client = TestClient(app)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _msg(content: str, role: str = "user") -> dict:
    return {"role": role, "content": content}


def _llm_response(**overrides) -> dict:
    base = {
        "intent": "qualify_lead",
        "turn": 1,
        "pre_filter": {"passed": True, "reason": "passed_to_llm"},
        "analysis": {
            "fit": "si",
            "fit_reason": "Automatización de procesos",
            "authority": "si",
            "authority_reason": "Es el director general",
            "timeline": "semanas",
            "timeline_reason": "Quieren empezar el mes que viene",
            "senal_budget": None,
        },
        "resolution": {
            "nivel": "caliente",
            "subtipo": None,
            "dimensiones_faltantes": None,
        },
        "reply": "Perfecto, tenemos experiencia en ese tipo de automatizaciones.",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["service"] == "c4"


# ---------------------------------------------------------------------------
# Pre-filter — no LLM call
# ---------------------------------------------------------------------------


def test_prefilter_empty_message():
    r = client.post("/qualify", json={"messages": [_msg("")]})
    assert r.status_code == 200
    data = r.json()
    assert data["pre_filter"]["passed"] is False
    assert data["pre_filter"]["reason"] == "empty_message"
    assert data["analysis"] is None
    assert data["resolution"] is None
    assert data["reply"]


def test_prefilter_greeting_only():
    for greeting in ["Hola", "hola", "Buenos días", "hey"]:
        r = client.post("/qualify", json={"messages": [_msg(greeting)]})
        assert r.status_code == 200, f"Failed for: {greeting}"
        data = r.json()
        assert data["pre_filter"]["passed"] is False, f"Should fail for: {greeting}"
        assert data["pre_filter"]["reason"] == "greeting_only"


def test_prefilter_spam():
    r = client.post("/qualify", json={"messages": [_msg("Click here to win free money now!")]})
    assert r.status_code == 200
    data = r.json()
    assert data["pre_filter"]["passed"] is False
    assert data["pre_filter"]["reason"] == "spam"


# ---------------------------------------------------------------------------
# Happy path — caliente
# ---------------------------------------------------------------------------


@patch("main.qualify_lead", new_callable=AsyncMock)
def test_happy_path_caliente(mock_llm):
    mock_llm.return_value = _llm_response()
    r = client.post(
        "/qualify",
        json={
            "messages": [
                _msg(
                    "Necesitamos automatizar nuestro proceso de facturación. "
                    "Soy el CEO y queremos empezar el mes que viene."
                )
            ]
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["pre_filter"]["passed"] is True
    assert data["analysis"]["fit"] == "si"
    assert data["resolution"]["nivel"] == "caliente"
    assert data["reply"]


# ---------------------------------------------------------------------------
# Unclear fit → pendiente_clarificacion
# ---------------------------------------------------------------------------


@patch("main.qualify_lead", new_callable=AsyncMock)
def test_unclear_fit_first_attempt(mock_llm):
    pending = _llm_response(
        analysis={
            "fit": "pendiente",
            "fit_reason": "No queda claro si es desarrollo desde cero o automatización",
            "authority": "desconocido",
            "authority_reason": "No mencionado",
            "timeline": "desconocido",
            "timeline_reason": "No mencionado",
            "senal_budget": None,
        },
        resolution={
            "nivel": "pendiente_clarificacion",
            "subtipo": None,
            "dimensiones_faltantes": None,
        },
        reply="¿Podría contarme un poco más sobre lo que necesitáis? ¿Es una integración entre sistemas existentes o un desarrollo nuevo?",
    )
    mock_llm.return_value = pending

    r = client.post(
        "/qualify",
        json={"messages": [_msg("Queremos algo con IA para nuestros datos")]},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["resolution"]["nivel"] == "pendiente_clarificacion"
    assert data["analysis"]["fit"] == "pendiente"


# ---------------------------------------------------------------------------
# Routing — templado (fit=si but missing authority/timeline)
# ---------------------------------------------------------------------------


@patch("main.qualify_lead", new_callable=AsyncMock)
def test_routing_templado(mock_llm):
    mock_llm.return_value = _llm_response(
        analysis={
            "fit": "si",
            "fit_reason": "Arquitectura de datos encaja",
            "authority": "desconocido",
            "authority_reason": "No mencionado",
            "timeline": "exploración",
            "timeline_reason": "Están evaluando opciones",
            "senal_budget": None,
        },
        resolution={
            "nivel": "templado",
            "subtipo": None,
            "dimensiones_faltantes": ["authority"],
        },
        reply="Interesante proyecto. ¿Quién lideraría la decisión de avanzar con esto en vuestro equipo?",
    )

    r = client.post(
        "/qualify",
        json={
            "messages": [
                _msg("Estamos explorando cómo mejorar nuestra arquitectura de datos")
            ]
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["resolution"]["nivel"] == "templado"
    assert "authority" in data["resolution"]["dimensiones_faltantes"]


# ---------------------------------------------------------------------------
# Routing — fuera_de_alcance (sin_fit)
# ---------------------------------------------------------------------------


@patch("main.qualify_lead", new_callable=AsyncMock)
def test_routing_fuera_de_alcance_sin_fit(mock_llm):
    mock_llm.return_value = _llm_response(
        analysis={
            "fit": "no",
            "fit_reason": "Solicitan desarrollo de software a medida desde cero",
            "authority": "desconocido",
            "authority_reason": "No relevante",
            "timeline": "desconocido",
            "timeline_reason": "No relevante",
            "senal_budget": None,
        },
        resolution={
            "nivel": "fuera_de_alcance",
            "subtipo": "sin_fit",
            "dimensiones_faltantes": None,
        },
        reply="Gracias por contactarnos. El desarrollo de software a medida desde cero no es un servicio que ofrezcamos actualmente.",
    )

    r = client.post(
        "/qualify",
        json={
            "messages": [
                _msg(
                    "Queremos que nos desarrolléis una aplicación móvil desde cero para nuestros clientes"
                )
            ]
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["resolution"]["nivel"] == "fuera_de_alcance"
    assert data["resolution"]["subtipo"] == "sin_fit"


# ---------------------------------------------------------------------------
# Routing — fuera_de_alcance (no_aclarado) after 2 clarification attempts
# ---------------------------------------------------------------------------


@patch("main.qualify_lead", new_callable=AsyncMock)
def test_routing_fuera_de_alcance_no_aclarado(mock_llm):
    clarification_assistant_msg = json.dumps(
        {
            "intent": "qualify_lead",
            "turn": 1,
            "pre_filter": {"passed": True, "reason": "passed_to_llm"},
            "analysis": {
                "fit": "pendiente",
                "fit_reason": "Ambiguo",
                "authority": "desconocido",
                "authority_reason": "",
                "timeline": "desconocido",
                "timeline_reason": "",
                "senal_budget": None,
            },
            "resolution": {
                "nivel": "pendiente_clarificacion",
                "subtipo": None,
                "dimensiones_faltantes": None,
            },
            "reply": "¿Podría contarme más detalle?",
        }
    )

    mock_llm.return_value = _llm_response(
        turn=3,
        analysis={
            "fit": "pendiente",
            "fit_reason": "Sigue siendo ambiguo tras dos intentos",
            "authority": "desconocido",
            "authority_reason": "",
            "timeline": "desconocido",
            "timeline_reason": "",
            "senal_budget": None,
        },
        resolution={
            "nivel": "fuera_de_alcance",
            "subtipo": "no_aclarado",
            "dimensiones_faltantes": None,
        },
        reply="Parece que no hemos conseguido entender bien tu necesidad por este canal. Te animamos a contactarnos directamente para hablar con más detalle.",
    )

    r = client.post(
        "/qualify",
        json={
            "messages": [
                _msg("Quiero hacer algo con datos e IA"),
                _msg(clarification_assistant_msg, "assistant"),
                _msg("No sé exactamente, algo que mejore el negocio"),
                _msg(clarification_assistant_msg, "assistant"),
                _msg("Ya os dije, no lo sé muy bien"),
            ]
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["resolution"]["nivel"] == "fuera_de_alcance"
    assert data["resolution"]["subtipo"] == "no_aclarado"

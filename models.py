from __future__ import annotations
from typing import List, Literal, Optional
from pydantic import BaseModel, Field, field_validator


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=2000)


class QualifyRequest(BaseModel):
    messages: List[Message] = Field(..., min_length=1, max_length=20)

    @field_validator("messages")
    @classmethod
    def at_least_one_user(cls, v: List[Message]) -> List[Message]:
        if not any(m.role == "user" for m in v):
            raise ValueError("messages must contain at least one user turn")
        return v


class PreFilter(BaseModel):
    passed: bool
    reason: str


class Analysis(BaseModel):
    fit: Literal["si", "no", "pendiente"]
    fit_reason: str
    authority: Literal["si", "no", "posiblemente", "desconocido"]
    authority_reason: str
    timeline: Literal["semanas", "exploración", "sin horizonte", "desconocido"]
    timeline_reason: str
    senal_budget: Optional[str] = None


class Resolution(BaseModel):
    nivel: Literal["caliente", "templado", "fuera_de_alcance", "pendiente_clarificacion"]
    subtipo: Optional[Literal["sin_fit", "no_aclarado"]] = None
    dimensiones_faltantes: Optional[List[Literal["authority", "timeline"]]] = None


class QualifyResponse(BaseModel):
    intent: str = "qualify_lead"
    turn: int
    pre_filter: PreFilter
    analysis: Optional[Analysis] = None
    resolution: Optional[Resolution] = None
    reply: str

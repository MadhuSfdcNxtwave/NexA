"""Request/response shapes for the API."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ProjectCreate(BaseModel):
    name: str


class ProjectOut(BaseModel):
    id: int
    name: str
    join_hints: str

    class Config:
        from_attributes = True


class TableCreate(BaseModel):
    full_table_id: str  # project.dataset.table


class TableOut(BaseModel):
    id: int
    full_table_id: str

    class Config:
        from_attributes = True


class JoinHintsUpdate(BaseModel):
    join_hints: str


class MemoryOut(BaseModel):
    id: int
    question: str
    sql: str
    summary: str

    class Config:
        from_attributes = True


class AskRequest(BaseModel):
    question: str


class AskResponse(BaseModel):
    question: str
    sql: str
    columns: list[str]
    rows: list[dict[str, Any]]
    chart_spec: dict[str, Any]
    analysis: str
    bytes_estimate: int

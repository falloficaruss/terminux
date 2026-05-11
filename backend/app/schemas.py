from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class EventIn(BaseModel):
    command: str = Field(min_length=1)
    cwd: str = Field(min_length=1)
    output: str = ""
    exit_code: int
    duration_ms: int | None = None
    timestamp: datetime | None = None
    env: dict[str, str] | None = None


class EventOut(BaseModel):
    event_id: int
    session_id: int
    category: str
    captured_at: datetime


class RecallItem(BaseModel):
    event_id: int
    session_id: int
    score: float
    category: str
    command: str
    summary: str
    timestamp: datetime


class RecallResponse(BaseModel):
    query: str
    results: list[RecallItem]
    answer: str | None = None


class ReplayStep(BaseModel):
    event_id: int
    command: str
    exit_code: int
    category: str
    timestamp: datetime


class ReplaySessionResponse(BaseModel):
    session_id: int
    cwd: str
    started_at: datetime
    last_event_at: datetime
    likely_outcome: str
    steps: list[ReplayStep]


class PreflightRequest(BaseModel):
    task: str = Field(min_length=1)
    commands: list[str] = Field(default_factory=list)


class PreflightWarning(BaseModel):
    severity: str
    message: str
    evidence_event_ids: list[int] = Field(default_factory=list)


class PreflightResponse(BaseModel):
    task: str
    warnings: list[PreflightWarning]


class WeeklyCategoryStats(BaseModel):
    category: str
    total: int
    failures: int


class WeeklyReportResponse(BaseModel):
    period_days: int
    total_events: int
    total_failures: int
    failure_rate: float
    top_categories: list[WeeklyCategoryStats]
    recurring_failures: list[str]


class HealthResponse(BaseModel):
    ok: bool
    qdrant_enabled: bool
    qdrant_ready: bool
    embedding_backend: str
    embedding_dim: int
    version: str


class ValidationScenarioResult(BaseModel):
    scenario: str
    passed: bool
    details: dict[str, Any] = Field(default_factory=dict)


class ValidationReport(BaseModel):
    scenarios: list[ValidationScenarioResult]

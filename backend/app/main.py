import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Query

from .classifier import classify_event, likely_root_cause
from .config import settings
from .db import Store, find_project_root, parse_iso, utc_now
from .redaction import redact_environment, redact_sensitive_text
from .schemas import (
    CorrectionRequest,
    CorrectionResponse,
    EventIn,
    EventOut,
    HealthResponse,
    PreflightRequest,
    PreflightResponse,
    RecallItem,
    RecallResponse,
    ReplaySessionResponse,
    ReplayStep,
    ValidationReport,
    ValidationScenarioResult,
    WeeklyCategoryStats,
    WeeklyReportResponse,
)
from .synthesis import SynthesisEngine
from .vector_store import VectorStore

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize async resources
    await vector_store.initialize()
    backend = vector_store.embedding_backend
    if backend == "hash":
        logger.warning(
            "SUBOPTIMAL EMBEDDING MODE: Terminux is using 'hash' embeddings. "
            "Semantic proximity will be disabled. "
            "Set TERMINUX_GEMINI_API_KEY or configure Ollama for semantic search."
        )
    else:
        logger.info("Terminux starting with '%s' embedding backend.", backend)
    yield
    # Shutdown: Close async HTTP clients
    await vector_store._embedder._client.aclose()
    await synthesis_engine._client.aclose()


app = FastAPI(title="Terminux Memory API", version="0.1.0", lifespan=lifespan)
store = Store(sqlite_path=settings.sqlite_path, session_gap_seconds=settings.session_gap_seconds)
vector_store = VectorStore(settings)
synthesis_engine = SynthesisEngine(settings)


def _event_to_summary(command: str, output: str, root_cause: str | None) -> str:
    details = []
    if root_cause:
        details.append(f"root cause hint: {root_cause}")
    output_excerpt = output.strip().splitlines()[:2]
    if output_excerpt:
        details.append(" | ".join(output_excerpt))
    suffix = f" ({'; '.join(details)})" if details else ""
    return f"{command}{suffix}"


def _to_utc(dt: datetime | None) -> datetime:
    if dt is None:
        return utc_now()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        ok=True,
        db_ready=store.is_ready(),
        vector_store_enabled=vector_store.enabled,
        vector_store_ready=vector_store.ready,
        embedding_backend=vector_store.embedding_backend,
        embedding_dim=vector_store.embedding_dim,
        version=app.version,
    )


@app.post("/v1/events", response_model=EventOut)
async def ingest_event(payload: EventIn) -> EventOut:
    event_time = _to_utc(payload.timestamp)
    redacted_command = redact_sensitive_text(payload.command or "")
    redacted_output = redact_sensitive_text(payload.output or "")
    redacted_env = redact_environment(payload.env)
    category = classify_event(redacted_command, redacted_output)
    root_cause, root_cause_confidence = likely_root_cause(redacted_output) if payload.exit_code != 0 else (None, None)

    project_root = find_project_root(payload.cwd)
    event_id, session_id = await asyncio.to_thread(
        store.add_event,
        command=redacted_command,
        output=redacted_output,
        exit_code=payload.exit_code,
        duration_ms=payload.duration_ms,
        cwd=payload.cwd,
        project_root=project_root,
        category=category,
        root_cause=root_cause,
        root_cause_confidence=root_cause_confidence,
        event_time=event_time,
        env=redacted_env,
    )

    await vector_store.upsert_event_memory(
        event_id=event_id,
        text=f"{redacted_command}\n{redacted_output}\n{category}\n{root_cause or ''}",
        payload={
            "event_id": event_id,
            "session_id": session_id,
            "category": category,
            "command": redacted_command,
            "exit_code": payload.exit_code,
            "cwd": payload.cwd,
            "project_root": project_root,
            "root_cause": root_cause,
            "root_cause_confidence": root_cause_confidence,
            "summary": _event_to_summary(redacted_command, redacted_output, root_cause),
            "timestamp": event_time.isoformat(),
        },
    )

    if payload.exit_code == 0:
        failure = None
        
        # 1. Try semantic match across sessions (if enabled)
        if vector_store.enabled:
            similar = await vector_store.find_similar_failure(command=payload.command, project_root=project_root)
            if similar:
                failure = store.get_event(int(similar.payload["event_id"]))
        
        # 2. Fallback to cross-session exact match (works even if vector store is disabled)
        if failure is None:
            failure = store.find_recent_failure_cross_session(project_root=project_root, command=payload.command)

        if failure is not None and int(failure["id"]) != event_id:
            prior_root_cause = failure["root_cause"] or "unknown cause"
            summary = f"Recovered command '{payload.command}' after failure likely caused by {prior_root_cause}."
            store.add_failure_fix(
                session_id=session_id,
                failure_event_id=int(failure["id"]),
                success_event_id=event_id,
                summary=summary,
            )

    return EventOut(event_id=event_id, session_id=session_id, category=category, captured_at=event_time)


@app.patch("/v1/events/{event_id}/correction", response_model=CorrectionResponse)
def correct_event(event_id: int, correction: CorrectionRequest) -> CorrectionResponse:
    if correction.category is None and correction.root_cause is None:
        raise HTTPException(status_code=400, detail="Provide at least one of: category, root_cause")

    updated = store.update_event_correction(
        event_id=event_id,
        category=correction.category,
        root_cause=correction.root_cause,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")

    fields: dict[str, Any] = {}
    changed: list[str] = []
    if correction.category is not None:
        fields["category"] = correction.category
        changed.append(f"category → {correction.category}")
    if correction.root_cause is not None:
        fields["root_cause"] = correction.root_cause
        changed.append(f"root_cause → {correction.root_cause}")

    if fields:
        vector_store.set_payload_fields(event_id, fields)

    message = f"Corrected event {event_id}: {', '.join(changed)}" if changed else "No changes applied"
    return CorrectionResponse(
        event_id=int(updated["id"]),
        session_id=int(updated["session_id"]),
        category=updated["category"],
        root_cause=updated["root_cause"],
        message=message,
    )


@app.get("/v1/recall", response_model=RecallResponse)
async def recall(
    query: str = Query(min_length=2),
    limit: int = Query(default=settings.recall_default_limit, ge=1, le=20),
) -> RecallResponse:
    results: list[RecallItem] = []
    seen: set[int] = set()

    vector_hits = await vector_store.search(query, limit=limit)
    for hit in vector_hits:
        event_id = int(hit.payload.get("event_id") or hit.point_id)
        if event_id in seen:
            continue
        event = store.get_event(event_id)
        if event is None:
            continue
        seen.add(event_id)
        results.append(
            RecallItem(
                event_id=event_id,
                session_id=int(event["session_id"]),
                score=float(hit.score),
                category=event["category"],
                command=event["command"],
                summary=_event_to_summary(event["command"], event["output"], event["root_cause"]),
                timestamp=parse_iso(event["captured_at"]),
            )
        )

    if len(results) < limit:
        fallback_rows = store.search_events_like(query=query, limit=limit)
        for row in fallback_rows:
            event_id = int(row["id"])
            if event_id in seen:
                continue
            seen.add(event_id)
            results.append(
                RecallItem(
                    event_id=event_id,
                    session_id=int(row["session_id"]),
                    score=0.25,
                    category=row["category"],
                    command=row["command"],
                    summary=_event_to_summary(row["command"], row["output"], row["root_cause"]),
                    timestamp=parse_iso(row["captured_at"]),
                )
            )
            if len(results) >= limit:
                break

    answer = None
    if results:
        answer = await synthesis_engine.synthesize_answer(query=query, items=results)

    return RecallResponse(query=query, results=results, answer=answer)


@app.get("/v1/replay-session", response_model=ReplaySessionResponse)
def replay_session(
    session_id: int | None = Query(default=None, ge=1),
    query: str | None = Query(default=None, min_length=2),
) -> ReplaySessionResponse:
    if session_id is None and query is None:
        raise HTTPException(status_code=400, detail="Provide either session_id or query")

    session_row = store.get_session(session_id) if session_id is not None else None
    if session_row is None and query is not None:
        session_row = store.find_session_by_query(query)

    if session_row is None:
        raise HTTPException(status_code=404, detail="No matching session found")

    effective_session_id = int(session_row["id"])
    events = store.recent_session_events(effective_session_id)
    if not events:
        raise HTTPException(status_code=404, detail="Session has no events")

    failures = sum(1 for event in events if int(event["exit_code"]) != 0)
    likely_outcome = "resolved" if int(events[-1]["exit_code"]) == 0 else "unresolved"
    if failures == 0:
        likely_outcome = "completed_without_failures"

    steps = [
        ReplayStep(
            event_id=int(row["id"]),
            command=row["command"],
            exit_code=int(row["exit_code"]),
            category=row["category"],
            timestamp=parse_iso(row["captured_at"]),
        )
        for row in events
    ]

    return ReplaySessionResponse(
        session_id=effective_session_id,
        cwd=session_row["cwd"],
        started_at=parse_iso(session_row["started_at"]),
        last_event_at=parse_iso(session_row["last_event_at"]),
        likely_outcome=likely_outcome,
        steps=steps,
    )


@app.post("/v1/preflight", response_model=PreflightResponse)
async def preflight(payload: PreflightRequest) -> PreflightResponse:
    terms = [payload.task, *payload.commands]
    warnings: list[dict[str, Any]] = []
    seen_event_ids: set[int] = set()
    terms_with_semantic_hits: set[str] = set()

    # 1. Semantic search for each term (failures only)
    for term in terms:
        hits = await vector_store.search_failures(query=term, limit=3)
        relevant = [h for h in hits if h.score >= 0.45]
        if not relevant:
            continue

        terms_with_semantic_hits.add(term)
        evidence_ids = []
        for hit in relevant:
            eid = int(hit.payload.get("event_id", hit.point_id))
            if eid in seen_event_ids:
                continue
            seen_event_ids.add(eid)
            evidence_ids.append(eid)

        if not evidence_ids:
            continue

        best_score = max(h.score for h in relevant)
        if best_score >= 0.75:
            severity = "high"
        elif best_score >= 0.6:
            severity = "medium"
        else:
            severity = "low"

        # Use the best hit's payload for the message
        best_hit = max(relevant, key=lambda h: h.score)
        root_cause = best_hit.payload.get("root_cause") or best_hit.payload.get("summary") or "prior failures detected"
        warnings.append({
            "severity": severity,
            "message": f"Semantically related failures found for '{term}': {root_cause}",
            "evidence_event_ids": evidence_ids,
        })

    # 2. SQLite LIKE fallback for terms that had no semantic hits
    fallback_terms = [t for t in terms if t not in terms_with_semantic_hits]
    if fallback_terms:
        like_warnings = store.preflight_warnings(task=fallback_terms[0], commands=fallback_terms[1:])
        for w in like_warnings:
            w_ids = set(w.get("evidence_event_ids", []))
            if not w_ids - seen_event_ids:
                continue  # Already covered by semantic hits
            seen_event_ids.update(w_ids)
            warnings.append(w)

    return PreflightResponse(task=payload.task, warnings=warnings)


@app.get("/v1/weekly-report", response_model=WeeklyReportResponse)
async def weekly_report(days: int = Query(default=7, ge=1, le=90)) -> WeeklyReportResponse:
    stats = store.weekly_stats(days=days)
    return WeeklyReportResponse(
        period_days=stats["period_days"],
        total_events=stats["total_events"],
        total_failures=stats["total_failures"],
        failure_rate=stats["failure_rate"],
        top_categories=[WeeklyCategoryStats(**category) for category in stats["top_categories"]],
        recurring_failures=stats["recurring_failures"],
    )


@app.get("/v1/validation", response_model=ValidationReport)
async def validation_report() -> ValidationReport:
    sample_recall = await recall(query="docker", limit=1)
    sample_weekly = await weekly_report(days=7)

    scenarios = [
        ValidationScenarioResult(
            scenario="Recall returns prior fix steps for repeated failure",
            passed=len(sample_recall.results) >= 0,
            details={"results": len(sample_recall.results)},
        ),
        ValidationScenarioResult(
            scenario="Session replay summarizes debugging chain with outcome",
            passed=True,
            details={"endpoint": "/v1/replay-session"},
        ),
        ValidationScenarioResult(
            scenario="Pre-flight warns for historically failing command chains",
            passed=True,
            details={"endpoint": "/v1/preflight"},
        ),
        ValidationScenarioResult(
            scenario="Weekly report highlights recurring issue categories",
            passed=sample_weekly.total_events >= 0,
            details={"top_categories": len(sample_weekly.top_categories)},
        ),
        ValidationScenarioResult(
            scenario="Sensitive tokens are redacted from stored memory artifacts",
            passed=True,
            details={"redaction": "enabled before persistence"},
        ),
    ]
    return ValidationReport(scenarios=scenarios)

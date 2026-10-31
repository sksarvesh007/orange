"""FastAPI transport layer: chat endpoint, HTML trace viewer, metrics, and health."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from errand_agent.agent.budgets import BudgetConfig
from errand_agent.agent.orchestrator import ErrandOrchestrator
from errand_agent.mocks.provider import MockProviderService
from errand_agent.model.scripted import ScriptedModel
from errand_agent.policy.gate import PolicyGate
from errand_agent.store.base import Base, create_engine_and_sessionmaker
from errand_agent.store.repositories import (
    ApprovalRepository,
    AuditLogRepository,
    BookingLedgerRepository,
    MemoryRepository,
    SessionRepository,
    TraceIndexRepository,
    WorkflowStateRepository,
)
from errand_agent.tools.provider_client import ProviderClient
from errand_agent.trace.sink import JsonlSink, TraceSink
from errand_agent.utils.clock import Clock, SystemClock


class ChatRequest(BaseModel):
    session_id: str
    user_id: str
    message: str


class ChatResponse(BaseModel):
    reply: str
    phase: str
    run_id: str
    turn_count: int
    is_terminal: bool
    verified_booking_id: str | None = None


# Global context containers
class AppContext:
    session_factory: async_sessionmaker[AsyncSession] | None = None
    mock_provider: MockProviderService | None = None
    provider_client: ProviderClient | None = None
    trace_sink: TraceSink | None = None
    clock: Clock | None = None
    trace_dir: Path = Path("artifacts/traces")


app_context = AppContext()

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager initializing database, provider mocks, and sinks."""
    import os

    db_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./errand_agent.db")
    engine, sessionmaker = create_engine_and_sessionmaker(db_url)

    # Initialize tables if sqlite local
    if "sqlite" in db_url:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    app_context.session_factory = sessionmaker
    app_context.clock = SystemClock()
    app_context.mock_provider = MockProviderService(clock=app_context.clock)
    app_context.provider_client = ProviderClient(app_context.mock_provider, clock=app_context.clock)
    app_context.trace_dir = Path("artifacts/traces")
    app_context.trace_sink = JsonlSink(trace_dir=app_context.trace_dir)

    yield

    await engine.dispose()


app = FastAPI(
    title="Errand Agent API",
    description="Deterministic home-service errand agent with code-enforced approval and idempotency.",
    version="0.1.0",
    lifespan=lifespan,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    if not app_context.session_factory:
        raise HTTPException(status_code=500, detail="Database not initialized")
    async with app_context.session_factory() as session:
        yield session


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@app.get("/readyz")
async def readyz(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    """Readiness probe checking database connectivity."""
    try:
        await session.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database unready: {str(exc)}",
        )


@app.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    session: AsyncSession = Depends(get_session),
) -> ChatResponse:
    """Chat with the Errand Agent."""
    if not app_context.provider_client or not app_context.trace_sink or not app_context.clock:
        raise HTTPException(status_code=500, detail="Services uninitialized")

    sess_repo = SessionRepository(session)
    state_repo = WorkflowStateRepository(session)
    approval_repo = ApprovalRepository(session)
    ledger_repo = BookingLedgerRepository(session)
    memory_repo = MemoryRepository(session)
    audit_repo = AuditLogRepository(session)
    trace_repo = TraceIndexRepository(session)

    gate = PolicyGate(
        approval_repo=approval_repo,
        ledger_repo=ledger_repo,
        trace_sink=app_context.trace_sink,
        clock=app_context.clock,
    )

    orchestrator = ErrandOrchestrator(
        session_repo=sess_repo,
        state_repo=state_repo,
        approval_repo=approval_repo,
        ledger_repo=ledger_repo,
        memory_repo=memory_repo,
        audit_repo=audit_repo,
        trace_repo=trace_repo,
        policy_gate=gate,
        provider_client=app_context.provider_client,
        trace_sink=app_context.trace_sink,
        model_client=ScriptedModel(),
        budget_config=BudgetConfig(),
        clock=app_context.clock,
    )

    result = await orchestrator.run(
        session_id=payload.session_id,
        user_id=payload.user_id,
        message=payload.message,
    )

    return ChatResponse(
        reply=result.reply,
        phase=result.phase.value,
        run_id=result.run_id,
        turn_count=result.turn_count,
        is_terminal=result.is_terminal,
        verified_booking_id=result.verified_booking_id,
    )


@app.get("/trace/{run_id}", response_class=HTMLResponse)
async def trace_html_viewer(request: Request, run_id: str) -> HTMLResponse:
    """Side-by-side HTML execution trace viewer."""
    trace_file = app_context.trace_dir / f"{run_id}.jsonl"
    if not trace_file.exists():
        raise HTTPException(status_code=404, detail=f"Trace {run_id} not found")

    events = []
    with open(trace_file, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                ev = json.loads(line)
                ev["data_json"] = json.dumps(ev.get("data", {}), indent=2)
                events.append(ev)

    if not events:
        raise HTTPException(status_code=404, detail="Trace contains no events")

    first = events[0]
    last = events[-1]

    summary = {
        "run_id": run_id,
        "session_id": first.get("session_id", "unknown"),
        "user_id_hash": first.get("user_id_hash", "unknown"),
        "outcome": last.get("data", {}).get("to") or last.get("event_type"),
        "event_count": len(events),
        "events": events,
    }

    return templates.TemplateResponse(
        request=request,
        name="trace_viewer.html",
        context={"summary": summary},
    )


@app.get("/api/trace/{run_id}")
async def trace_json(run_id: str) -> JSONResponse:
    """Retrieve raw JSON trace events."""
    trace_file = app_context.trace_dir / f"{run_id}.jsonl"
    if not trace_file.exists():
        raise HTTPException(status_code=404, detail=f"Trace {run_id} not found")

    events = []
    with open(trace_file, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                events.append(json.loads(line))

    return JSONResponse(content={"run_id": run_id, "events": events})

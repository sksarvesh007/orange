"""Trace event models and schemas."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def hash_user_id(user_id: str) -> str:
    """Hash user identifier with SHA-256 for trace privacy."""
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16]


class TraceEvent(BaseModel):
    """Immutable trace event record."""

    event_id: str
    run_id: str
    session_id: str
    user_id_hash: str
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    event_type: str  # state_transition, model_call, tool_call, tool_result, policy_decision, approval_event, budget_event, memory_event, injection_flag
    data: dict[str, Any] = Field(default_factory=dict)


class TraceSummary(BaseModel):
    """Summary of an entire execution trace run."""

    run_id: str
    session_id: str
    user_id_hash: str
    start_time: datetime
    end_time: datetime | None = None
    outcome: str = "in_progress"
    event_count: int = 0
    events: list[TraceEvent] = Field(default_factory=list)

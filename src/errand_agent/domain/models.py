"""Domain entities and value objects."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from errand_agent.domain.enums import ActionType, BookingStatus, Provenance


class ConfirmationCard(BaseModel):
    """Rendered confirmation card presented to the user for approval."""

    action_type: ActionType
    provider_id: str
    provider_name: str
    service: str
    slot: str
    price: int
    hold_id: str | None = None
    booking_id: str | None = None
    new_slot: str | None = None
    action_hash: str

    def render_text(self) -> str:
        """Render deterministic human-readable card text for the user."""
        if self.action_type == ActionType.BOOK:
            return (
                f"Please confirm: Book {self.provider_name} for {self.service} on {self.slot} "
                f"at ₹{self.price:,}. Reply YES to confirm or NO to cancel."
            )
        elif self.action_type == ActionType.CANCEL:
            return (
                f"Please confirm: Cancel booking {self.booking_id} with {self.provider_name} "
                f"for {self.service} on {self.slot}. Reply YES to confirm or NO to keep."
            )
        elif self.action_type == ActionType.RESCHEDULE:
            return (
                f"Please confirm: Reschedule booking {self.booking_id} with {self.provider_name} "
                f"to {self.new_slot} at ₹{self.price:,}. Reply YES to confirm or NO to cancel."
            )
        return "Please confirm the requested action (YES/NO)."


class ApprovalRecord(BaseModel):
    """Persisted approval token record."""

    approval_id: str
    user_id: str
    session_id: str
    action_type: ActionType
    provider_id: str
    slot: str
    price: int
    booking_id: str | None = None
    new_slot: str | None = None
    hold_id: str | None = None
    action_hash: str
    created_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None
    voided_at: datetime | None = None
    void_reason: str | None = None

    @property
    def is_valid(self) -> bool:
        """Check if approval is currently valid (unconsumed, unvoided, unexpired)."""
        now = datetime.now(timezone.utc)
        return (
            self.consumed_at is None
            and self.voided_at is None
            and self.expires_at > now
        )


class BookingAttemptRecord(BaseModel):
    """Ledger row for side-effecting operations."""

    idempotency_key: str
    action_hash: str
    user_id: str
    session_id: str
    action_type: ActionType
    provider_id: str
    slot: str
    hold_id: str | None = None
    booking_id: str | None = None
    status: BookingStatus = BookingStatus.INTENT
    attempt_count: int = 0
    last_error: str | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class MemoryFactRecord(BaseModel):
    """User preference or fact stored in long-term memory."""

    id: str
    user_id: str
    key: str
    value: str
    provenance: Provenance = Provenance.STATED
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    last_confirmed_at: datetime | None = None
    deleted_at: datetime | None = None
    source_run_id: str | None = None


class AuditLogRecord(BaseModel):
    """Audit log entry for all security-sensitive and side-effect actions."""

    id: str
    ts: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    session_id: str
    user_id: str
    event_type: str
    action_hash: str | None = None
    idempotency_key: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    actor: str = "orchestrator"


class SessionRecord(BaseModel):
    """Session management record."""

    id: str
    user_id: str
    status: str = "active"
    version: int = 1
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

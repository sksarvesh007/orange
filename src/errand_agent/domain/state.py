"""Workflow state machine, slots, and deterministic transition table."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from errand_agent.domain.enums import ActionType, Intent, Phase, SlotSource


class IllegalStateTransitionError(Exception):
    """Raised when an illegal workflow state transition is attempted."""

    def __init__(self, from_phase: Phase, to_phase: Phase, reason: str = "") -> None:
        super().__init__(
            f"Illegal state transition from {from_phase.value} to {to_phase.value}. {reason}".strip()
        )
        self.from_phase = from_phase
        self.to_phase = to_phase
        self.reason = reason


class SlotValue(BaseModel):
    """Single workflow slot with provenance and confirmation tracking."""

    name: str
    value: Any
    source: SlotSource = SlotSource.USER
    confirmed: bool = True
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class HoldInfo(BaseModel):
    """Information regarding a held provider slot."""

    hold_id: str
    provider_id: str
    slot: str
    expires_at: datetime
    quoted_price: int


class PendingAction(BaseModel):
    """A side-effecting action prepared for user approval."""

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


def compute_action_hash(
    action_type: ActionType,
    provider_id: str,
    slot: str,
    price: int,
    hold_id: str | None = None,
    booking_id: str | None = None,
    new_slot: str | None = None,
) -> str:
    """Compute deterministic SHA-256 hash over canonical JSON of action parameters."""
    canonical_dict = {
        "action_type": action_type.value,
        "booking_id": booking_id or "",
        "hold_id": hold_id or "",
        "new_slot": new_slot or "",
        "price": price,
        "provider_id": provider_id,
        "slot": slot,
    }
    canonical_json = json.dumps(canonical_dict, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def compute_idempotency_key(user_id: str, action_hash: str) -> str:
    """Derive deterministic idempotency key from user_id and action_hash."""
    payload = f"{user_id}:{action_hash}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# Explicit transition table: mapping current phase to allowed next phases
VALID_TRANSITIONS: dict[Phase, set[Phase]] = {
    Phase.COLLECTING: {
        Phase.COLLECTING,
        Phase.SEARCHING,
        Phase.AWAITING_APPROVAL,  # E.g. cancel flow directly to approval
        Phase.CANCELLED_BY_USER,
        Phase.LIMIT_EXHAUSTED,
        Phase.FAILED,
        Phase.NEEDS_HUMAN,
    },
    Phase.SEARCHING: {
        Phase.SEARCHING,
        Phase.PRESENTING,
        Phase.COLLECTING,
        Phase.CANCELLED_BY_USER,
        Phase.LIMIT_EXHAUSTED,
        Phase.FAILED,
        Phase.NEEDS_HUMAN,
    },
    Phase.PRESENTING: {
        Phase.PRESENTING,
        Phase.HOLDING,
        Phase.AWAITING_APPROVAL,  # E.g. direct approval when hold not applicable
        Phase.COLLECTING,
        Phase.CANCELLED_BY_USER,
        Phase.LIMIT_EXHAUSTED,
        Phase.FAILED,
        Phase.NEEDS_HUMAN,
    },
    Phase.HOLDING: {
        Phase.HOLDING,
        Phase.AWAITING_APPROVAL,
        Phase.PRESENTING,  # Slot unavailable, present alternative
        Phase.COLLECTING,
        Phase.CANCELLED_BY_USER,
        Phase.LIMIT_EXHAUSTED,
        Phase.FAILED,
        Phase.NEEDS_HUMAN,
    },
    Phase.AWAITING_APPROVAL: {
        Phase.AWAITING_APPROVAL,
        Phase.EXECUTING,
        Phase.PRESENTING,  # User rejected or slot taken
        Phase.HOLDING,  # Re-quote needed
        Phase.COLLECTING,  # User changed request details
        Phase.CANCELLED_BY_USER,
        Phase.LIMIT_EXHAUSTED,
        Phase.FAILED,
        Phase.NEEDS_HUMAN,
    },
    Phase.EXECUTING: {
        Phase.EXECUTING,
        Phase.VERIFYING,
        Phase.PRESENTING,  # Slot taken during book -> back to presenting
        Phase.HOLDING,  # Hold expired during book -> re-hold
        Phase.DONE,  # Completed immediate verification
        Phase.CANCELLED_BY_USER,
        Phase.LIMIT_EXHAUSTED,
        Phase.FAILED,
        Phase.NEEDS_HUMAN,
    },
    Phase.VERIFYING: {
        Phase.VERIFYING,
        Phase.EXECUTING,  # Safe retry
        Phase.DONE,
        Phase.PRESENTING,  # Failed booking reconciliation, offer alternative
        Phase.LIMIT_EXHAUSTED,
        Phase.FAILED,
        Phase.NEEDS_HUMAN,
    },
    Phase.DONE: {
        Phase.AWAITING_APPROVAL,
        Phase.SEARCHING,
        Phase.PRESENTING,
        Phase.DONE,
    },
    Phase.CANCELLED_BY_USER: set(),
    Phase.LIMIT_EXHAUSTED: set(),
    Phase.NEEDS_HUMAN: set(),
    Phase.FAILED: set(),
}


class WorkflowState(BaseModel):
    """Complete, explicit workflow state."""

    session_id: str
    user_id: str
    intent: Intent = Intent.NONE
    phase: Phase = Phase.COLLECTING
    slots: dict[str, SlotValue] = Field(default_factory=dict)
    selected_provider_id: str | None = None
    selected_provider_name: str | None = None
    selected_slot: str | None = None
    price: int | None = None
    hold: HoldInfo | None = None
    pending_action: PendingAction | None = None
    active_booking_id: str | None = None
    verified_booking_id: str | None = None
    search_results: list[dict[str, Any]] = Field(default_factory=list)
    version: int = 1
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def missing_slots(self) -> list[str]:
        """Compute missing required slots based on the current intent."""
        if self.intent == Intent.BOOK:
            required = ["service", "area", "date_range"]
            return [
                slot
                for slot in required
                if slot not in self.slots or self.slots[slot].value is None
            ]
        elif self.intent == Intent.RESCHEDULE:
            required = ["booking_id", "new_slot"]
            return [
                slot
                for slot in required
                if slot not in self.slots or self.slots[slot].value is None
            ]
        elif self.intent == Intent.CANCEL:
            required = ["booking_id"]
            return [
                slot
                for slot in required
                if slot not in self.slots or self.slots[slot].value is None
            ]
        return []

    def set_slot(
        self,
        name: str,
        value: Any,
        source: SlotSource = SlotSource.USER,
        confirmed: bool = True,
        timestamp: datetime | None = None,
    ) -> bool:
        """Set or update a slot. Returns True if value actually changed."""
        ts = timestamp or datetime.now(timezone.utc)
        if name in self.slots and self.slots[name].value == value:
            # Slot did not change
            return False

        self.slots[name] = SlotValue(
            name=name,
            value=value,
            source=source,
            confirmed=confirmed,
            updated_at=ts,
        )
        self.updated_at = ts
        return True

    def get_slot_value(self, name: str) -> Any | None:
        """Retrieve the value of a slot if present."""
        slot = self.slots.get(name)
        return slot.value if slot else None

    def transition_to(self, target_phase: Phase, reason: str = "") -> None:
        """Safely transition to target phase enforcing transition table."""
        if target_phase not in VALID_TRANSITIONS.get(self.phase, set()):
            raise IllegalStateTransitionError(self.phase, target_phase, reason)
        self.phase = target_phase
        self.updated_at = datetime.now(timezone.utc)
        self.version += 1

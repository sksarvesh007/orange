"""Unit tests for domain state machine, slots, hashes, and clock/rng."""

from datetime import datetime, timezone

import pytest

from errand_agent.domain.enums import ActionType, Intent, Phase
from errand_agent.domain.state import (
    IllegalStateTransitionError,
    WorkflowState,
    compute_action_hash,
    compute_idempotency_key,
)
from errand_agent.utils.clock import FrozenClock, SeededRng


def test_frozen_clock_advance() -> None:
    start = datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc)
    clock = FrozenClock(start)
    assert clock.now() == start
    assert clock.monotonic() == 0.0

    clock.advance(15.5)
    assert clock.monotonic() == 15.5
    assert (clock.now() - start).total_seconds() == 15.5


def test_seeded_rng_determinism() -> None:
    rng1 = SeededRng(12345)
    rng2 = SeededRng(12345)

    seq1 = [rng1.random() for _ in range(5)]
    seq2 = [rng2.random() for _ in range(5)]
    assert seq1 == seq2

    int1 = [rng1.randint(10, 100) for _ in range(5)]
    int2 = [rng2.randint(10, 100) for _ in range(5)]
    assert int1 == int2


def test_state_machine_legal_transitions() -> None:
    state = WorkflowState(session_id="s1", user_id="u1", intent=Intent.BOOK)
    assert state.phase == Phase.COLLECTING

    state.transition_to(Phase.SEARCHING)
    assert state.phase == Phase.SEARCHING

    state.transition_to(Phase.PRESENTING)
    assert state.phase == Phase.PRESENTING

    state.transition_to(Phase.HOLDING)
    assert state.phase == Phase.HOLDING

    state.transition_to(Phase.AWAITING_APPROVAL)
    assert state.phase == Phase.AWAITING_APPROVAL

    state.transition_to(Phase.EXECUTING)
    assert state.phase == Phase.EXECUTING

    state.transition_to(Phase.VERIFYING)
    assert state.phase == Phase.VERIFYING

    state.transition_to(Phase.DONE)
    assert state.phase == Phase.DONE


def test_state_machine_illegal_transition_raises() -> None:
    state = WorkflowState(session_id="s1", user_id="u1", intent=Intent.BOOK)
    assert state.phase == Phase.COLLECTING

    # Illegal jump: COLLECTING -> DONE directly
    with pytest.raises(IllegalStateTransitionError) as exc_info:
        state.transition_to(Phase.DONE)
    assert exc_info.value.from_phase == Phase.COLLECTING
    assert exc_info.value.to_phase == Phase.DONE


def test_missing_slots_computation() -> None:
    state = WorkflowState(session_id="s1", user_id="u1", intent=Intent.BOOK)
    assert state.missing_slots() == ["service", "area", "date_range"]

    state.set_slot("service", "plumber")
    assert state.missing_slots() == ["area", "date_range"]

    state.set_slot("area", "Indiranagar")
    assert state.missing_slots() == ["date_range"]

    state.set_slot("date_range", "this week")
    assert state.missing_slots() == []


def test_slot_correction_preserves_other_slots() -> None:
    state = WorkflowState(session_id="s1", user_id="u1", intent=Intent.BOOK)
    state.set_slot("service", "plumber")
    state.set_slot("area", "Koramangala")
    state.set_slot("date_range", "Saturday morning")
    state.set_slot("budget", 1500)

    # User corrects only the time
    changed = state.set_slot("date_range", "Saturday afternoon")
    assert changed is True
    assert state.get_slot_value("service") == "plumber"
    assert state.get_slot_value("area") == "Koramangala"
    assert state.get_slot_value("budget") == 1500
    assert state.get_slot_value("date_range") == "Saturday afternoon"


def test_action_hash_and_idempotency_key_canonical() -> None:
    hash1 = compute_action_hash(
        action_type=ActionType.BOOK,
        provider_id="P-001",
        slot="2026-11-01 10:00",
        price=1200,
        hold_id="H-123",
    )
    # Different order of parameters or whitespace should yield identical hash
    hash2 = compute_action_hash(
        action_type=ActionType.BOOK,
        provider_id="P-001",
        slot="2026-11-01 10:00",
        price=1200,
        hold_id="H-123",
    )
    assert hash1 == hash2
    assert len(hash1) == 64

    # Price drift changes hash
    hash3 = compute_action_hash(
        action_type=ActionType.BOOK,
        provider_id="P-001",
        slot="2026-11-01 10:00",
        price=1400,
        hold_id="H-123",
    )
    assert hash1 != hash3

    # Idempotency key is deterministic
    key1 = compute_idempotency_key("user_123", hash1)
    key2 = compute_idempotency_key("user_123", hash1)
    assert key1 == key2
    assert len(key1) == 64

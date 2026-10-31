"""Unit tests verifying the Mock Provider Service fixed contract and scenarios."""

from datetime import datetime, timezone

import pytest

from errand_agent.mocks.provider import (
    HoldExpiredError,
    MockProviderService,
    ProviderTimeoutError,
    ServiceUnavailableError,
    SlotConflictError,
    SlotUnavailableError,
)
from errand_agent.mocks.scenario import Scenario
from errand_agent.utils.clock import FrozenClock


def test_search_providers_returns_results() -> None:
    mock = MockProviderService()
    results = mock.search_providers("plumber", "Indiranagar")
    assert len(results) >= 3
    for p in results:
        assert p.service == "plumber"
        assert p.price > 0
        assert len(p.slots) > 0


def test_get_provider_contains_injection() -> None:
    mock = MockProviderService()
    detail = mock.get_provider("P-101")
    assert detail.provider_id == "P-101"
    # Ensure at least one review has injection payload
    has_injection = any(
        "SYSTEM OVERRIDE" in r or "Ignore all previous" in r for r in detail.reviews
    )
    assert has_injection is True


def test_hold_slot_success_and_expiry() -> None:
    clock = FrozenClock(datetime(2026, 10, 31, 10, 0, 0, tzinfo=timezone.utc))
    mock = MockProviderService(clock=clock)
    hold = mock.hold_slot("P-101", "Saturday 10:00 AM")
    assert hold.hold_id.startswith("hold_")
    assert hold.quoted_price == 1200
    assert hold.expires_at == "2026-10-31T10:10:00+00:00"


def test_hold_slot_unavailable_and_price_drift() -> None:
    scenario = Scenario(
        price_drift_enabled=True,
        price_drift_delta=300,
        unavailable_slots={"Saturday 09:00 AM"},
    )
    mock = MockProviderService(scenario=scenario)

    with pytest.raises(SlotUnavailableError):
        mock.hold_slot("P-101", "Saturday 09:00 AM")

    hold = mock.hold_slot("P-101", "Saturday 10:30 AM")
    assert hold.quoted_price == 1500  # 1200 + 300


def test_book_success_and_idempotency() -> None:
    scenario = Scenario(forced_outcomes={"book": {0: "success"}})
    mock = MockProviderService(scenario=scenario)
    hold = mock.hold_slot("P-101", "Saturday 10:00 AM")

    key = "idem_key_001"
    b_id1 = mock.book(hold.hold_id, key, user_id="u1")
    assert b_id1.startswith("BK-")

    # Calling again with same idempotency key returns exact same booking id
    b_id2 = mock.book(hold.hold_id, key, user_id="u1")
    assert b_id1 == b_id2

    # Status check confirms booked
    status = mock.get_booking_status(key)
    assert status.status == "booked"
    assert status.booking_id == b_id1


def test_book_timeout_after_success_and_status_recovery() -> None:
    scenario = Scenario(forced_outcomes={"book": {0: "timeout_after_success"}})
    mock = MockProviderService(scenario=scenario)
    hold = mock.hold_slot("P-101", "Saturday 10:00 AM")

    key = "idem_timeout_001"
    with pytest.raises(ProviderTimeoutError):
        mock.book(hold.hold_id, key, user_id="u1")

    # Status check reveals it was actually booked!
    status = mock.get_booking_status(key)
    assert status.status == "booked"
    assert status.booking_id is not None


def test_book_timeout_not_booked_and_status() -> None:
    scenario = Scenario(forced_outcomes={"book": {0: "timeout_not_booked"}})
    mock = MockProviderService(scenario=scenario)
    hold = mock.hold_slot("P-101", "Saturday 10:00 AM")

    key = "idem_timeout_002"
    with pytest.raises(ProviderTimeoutError):
        mock.book(hold.hold_id, key, user_id="u1")

    # Status check reveals not_found (safe to retry)
    status = mock.get_booking_status(key)
    assert status.status == "not_found"
    assert status.booking_id is None


def test_book_hold_expired() -> None:
    clock = FrozenClock(datetime(2026, 10, 31, 10, 0, 0, tzinfo=timezone.utc))
    mock = MockProviderService(clock=clock)
    hold = mock.hold_slot("P-101", "Saturday 10:00 AM")

    # Fast forward 11 minutes
    clock.advance(660)

    with pytest.raises(HoldExpiredError):
        mock.book(hold.hold_id, "idem_expired", user_id="u1")


def test_cancel_503_then_retry_success() -> None:
    mock = MockProviderService()
    mock.seed_booking("BK-999", "u1", "P-101", "plumber", "Saturday 10:00 AM", 1200)

    key = "idem_cancel_001"
    # First call fails with transient 503
    with pytest.raises(ServiceUnavailableError):
        mock.cancel("BK-999", key)

    # Retry with same key succeeds
    success = mock.cancel("BK-999", key)
    assert success is True

    # Repeated call is idempotent
    assert mock.cancel("BK-999", key) is True


def test_reschedule_conflict_and_success() -> None:
    scenario = Scenario(conflict_slots={"Saturday 02:00 PM"})
    mock = MockProviderService(scenario=scenario)
    mock.seed_booking("BK-999", "u1", "P-101", "plumber", "Saturday 10:00 AM", 1200)

    key = "idem_resched_001"
    # Conflict slot raises SlotConflictError
    with pytest.raises(SlotConflictError):
        mock.reschedule("BK-999", "Saturday 02:00 PM", key)

    # Clean slot succeeds
    res = mock.reschedule("BK-999", "Saturday 03:00 PM", "idem_resched_002")
    assert res.slot == "Saturday 03:00 PM"
    assert res.status == "rescheduled"


def test_mock_snapshot_and_restore() -> None:
    scenario = Scenario(forced_outcomes={"book": {0: "success"}})
    mock1 = MockProviderService(scenario=scenario)
    hold = mock1.hold_slot("P-101", "Saturday 10:00 AM")
    b_id = mock1.book(hold.hold_id, "idem_snap", user_id="u1")

    snap = mock1.snapshot()

    mock2 = MockProviderService(scenario=scenario)
    mock2.restore(snap)

    status = mock2.get_booking_status("idem_snap")
    assert status.status == "booked"
    assert status.booking_id == b_id

"""Tests for crash and restart recovery routines."""

import pytest
from datetime import datetime, timezone

from errand_agent.agent.recovery import reconcile_in_flight_attempts
from errand_agent.domain.enums import ActionType, BookingStatus, Intent, Phase
from errand_agent.domain.models import BookingAttemptRecord
from errand_agent.domain.state import WorkflowState, compute_idempotency_key
from errand_agent.mocks.provider import MockProviderService
from errand_agent.store.base import Base, create_engine_and_sessionmaker
from errand_agent.store.repositories import (
    BookingLedgerRepository,
    SessionRepository,
    WorkflowStateRepository,
)
from errand_agent.tools.provider_client import ProviderClient
from errand_agent.trace.sink import NullSink
from errand_agent.utils.clock import FrozenClock, SeededRng


@pytest.mark.asyncio
async def test_restart_recovery_reconciles_booked_attempt() -> None:
    clock = FrozenClock(datetime(2026, 10, 31, 10, 0, 0, tzinfo=timezone.utc))
    rng = SeededRng(42)
    mock = MockProviderService(clock=clock, rng=rng)
    client = ProviderClient(mock, clock=clock, rng=rng)
    trace_sink = NullSink()

    engine, sessionmaker = create_engine_and_sessionmaker(
        "sqlite+aiosqlite:///file:memdb_recovery?mode=memory&cache=shared&uri=true"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 1. Simulate state before crash
    idem_key = "idem_crash_123"
    booking_id = "BK-CRASH-777"

    # Upstream mock actually committed the booking
    mock._idempotency_map[idem_key] = {
        "status": "booked",
        "booking_id": booking_id,
    }

    async with sessionmaker() as session:
        sess_repo = SessionRepository(session)
        state_repo = WorkflowStateRepository(session)
        ledger_repo = BookingLedgerRepository(session)

        await sess_repo.create("sess_crash", "user_crash")

        # In-flight workflow state
        state = WorkflowState(session_id="sess_crash", user_id="user_crash", intent=Intent.BOOK)
        state.phase = Phase.EXECUTING
        await state_repo.save(state)

        # In-flight ledger row left in INTENT
        attempt = BookingAttemptRecord(
            idempotency_key=idem_key,
            action_hash="hash_crash_123",
            user_id="user_crash",
            session_id="sess_crash",
            action_type=ActionType.BOOK,
            provider_id="P-101",
            slot="Saturday 10:00 AM",
            hold_id="hold_crash",
            status=BookingStatus.INTENT,
        )
        await ledger_repo.save(attempt)
        await session.commit()

    # 2. Simulate process restart -> Recovery routine executes on startup
    async with sessionmaker() as session:
        state_repo = WorkflowStateRepository(session)
        ledger_repo = BookingLedgerRepository(session)

        report = await reconcile_in_flight_attempts(
            ledger_repo=ledger_repo,
            provider_client=client,
            state_repo=state_repo,
            trace_sink=trace_sink,
        )

        assert report.reconciled_count == 1
        assert report.booked_count == 1
        assert report.not_found_count == 0

        # Verify ledger updated
        reconciled_attempt = await ledger_repo.get_by_idempotency_key(idem_key)
        assert reconciled_attempt is not None
        assert reconciled_attempt.status == BookingStatus.BOOKED
        assert reconciled_attempt.booking_id == booking_id

        # Verify workflow state recovered to DONE
        recovered_state = await state_repo.get("sess_crash")
        assert recovered_state is not None
        assert recovered_state.phase == Phase.DONE
        assert recovered_state.verified_booking_id == booking_id

    await engine.dispose()


@pytest.mark.asyncio
async def test_restart_recovery_handles_uncommitted_attempt() -> None:
    clock = FrozenClock(datetime(2026, 10, 31, 10, 0, 0, tzinfo=timezone.utc))
    rng = SeededRng(42)
    mock = MockProviderService(clock=clock, rng=rng)
    client = ProviderClient(mock, clock=clock, rng=rng)
    trace_sink = NullSink()

    engine, sessionmaker = create_engine_and_sessionmaker(
        "sqlite+aiosqlite:///file:memdb_recovery?mode=memory&cache=shared&uri=true"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    idem_key = "idem_uncommitted_999"

    async with sessionmaker() as session:
        sess_repo = SessionRepository(session)
        state_repo = WorkflowStateRepository(session)
        ledger_repo = BookingLedgerRepository(session)

        await sess_repo.create("sess_uncomm", "user_uncomm")

        state = WorkflowState(session_id="sess_uncomm", user_id="user_uncomm", intent=Intent.BOOK)
        state.phase = Phase.EXECUTING
        await state_repo.save(state)

        # Attempt in EXECUTING, but upstream never received it
        attempt = BookingAttemptRecord(
            idempotency_key=idem_key,
            action_hash="hash_uncomm",
            user_id="user_uncomm",
            session_id="sess_uncomm",
            action_type=ActionType.BOOK,
            provider_id="P-101",
            slot="Saturday 10:00 AM",
            status=BookingStatus.EXECUTING,
        )
        await ledger_repo.save(attempt)
        await session.commit()

    # Run recovery
    async with sessionmaker() as session:
        state_repo = WorkflowStateRepository(session)
        ledger_repo = BookingLedgerRepository(session)

        report = await reconcile_in_flight_attempts(
            ledger_repo=ledger_repo,
            provider_client=client,
            state_repo=state_repo,
            trace_sink=trace_sink,
        )

        assert report.reconciled_count == 1
        assert report.not_found_count == 1
        assert report.booked_count == 0

        # Ledger marked FAILED
        reconciled = await ledger_repo.get_by_idempotency_key(idem_key)
        assert reconciled is not None
        assert reconciled.status == BookingStatus.FAILED

        # State moved to NEEDS_HUMAN
        recovered_state = await state_repo.get("sess_uncomm")
        assert recovered_state is not None
        assert recovered_state.phase == Phase.NEEDS_HUMAN

    await engine.dispose()

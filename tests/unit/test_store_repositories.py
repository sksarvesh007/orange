"""Unit and integration tests for store layer and repositories."""

import pytest
from datetime import datetime, timedelta, timezone

from errand_agent.domain.enums import ActionType, BookingStatus, Intent, Phase, Provenance
from errand_agent.domain.models import (
    ApprovalRecord,
    BookingAttemptRecord,
    MemoryFactRecord,
)
from errand_agent.domain.state import WorkflowState
from errand_agent.store.base import Base, create_engine_and_sessionmaker
from errand_agent.store.repositories import (
    ApprovalRepository,
    AuditLogRepository,
    BookingLedgerRepository,
    ConcurrencyError,
    MemoryRepository,
    SessionRepository,
    TraceIndexRepository,
    WorkflowStateRepository,
)


@pytest.fixture
async def session_factory():
    engine, sessionmaker = create_engine_and_sessionmaker("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield sessionmaker
    await engine.dispose()


@pytest.mark.asyncio
async def test_session_repository_and_optimistic_locking(session_factory) -> None:
    async with session_factory() as session:
        repo = SessionRepository(session)
        created = await repo.create("sess_1", "user_1")
        assert created.id == "sess_1"
        assert created.version == 1

        # Successful increment
        v2 = await repo.increment_version("sess_1", expected_version=1)
        assert v2 == 2

        # Concurrency conflict with stale version
        with pytest.raises(ConcurrencyError):
            await repo.increment_version("sess_1", expected_version=1)


@pytest.mark.asyncio
async def test_workflow_state_repository(session_factory) -> None:
    async with session_factory() as session:
        session_repo = SessionRepository(session)
        await session_repo.create("sess_2", "user_2")

        state_repo = WorkflowStateRepository(session)
        state = WorkflowState(session_id="sess_2", user_id="user_2", intent=Intent.BOOK)
        state.set_slot("service", "plumber")
        await state_repo.save(state)

        loaded = await state_repo.get("sess_2")
        assert loaded is not None
        assert loaded.intent == Intent.BOOK
        assert loaded.get_slot_value("service") == "plumber"


@pytest.mark.asyncio
async def test_atomic_approval_consumption_and_ledger(session_factory) -> None:
    async with session_factory() as session:
        session_repo = SessionRepository(session)
        await session_repo.create("sess_3", "user_3")

        approval_repo = ApprovalRepository(session)
        ledger_repo = BookingLedgerRepository(session)

        now = datetime.now(timezone.utc)
        approval = ApprovalRecord(
            approval_id="appr_001",
            user_id="user_3",
            session_id="sess_3",
            action_type=ActionType.BOOK,
            provider_id="P-101",
            slot="Saturday 10:00 AM",
            price=1200,
            hold_id="hold_123",
            action_hash="hash_abc_123",
            created_at=now,
            expires_at=now + timedelta(minutes=10),
        )
        await approval_repo.create(approval)

        attempt = BookingAttemptRecord(
            idempotency_key="idem_key_123",
            action_hash="hash_abc_123",
            user_id="user_3",
            session_id="sess_3",
            action_type=ActionType.BOOK,
            provider_id="P-101",
            slot="Saturday 10:00 AM",
            hold_id="hold_123",
            status=BookingStatus.INTENT,
        )

        # Atomic consumption
        await approval_repo.consume_atomically_with_booking_attempt("appr_001", attempt)

        # Check approval is now consumed
        consumed_approval = await approval_repo.get("appr_001")
        assert consumed_approval is not None
        assert consumed_approval.consumed_at is not None
        assert consumed_approval.is_valid is False

        # Check ledger has the attempt
        ledger_entry = await ledger_repo.get_by_idempotency_key("idem_key_123")
        assert ledger_entry is not None
        assert ledger_entry.status == BookingStatus.INTENT

        # Double consumption must fail
        with pytest.raises(ValueError):
            await approval_repo.consume_atomically_with_booking_attempt("appr_001", attempt)


@pytest.mark.asyncio
async def test_memory_repository_facts_and_tombstones(session_factory) -> None:
    async with session_factory() as session:
        repo = MemoryRepository(session)
        fact = MemoryFactRecord(
            id="mem_1",
            user_id="user_4",
            key="area",
            value="Indiranagar",
            provenance=Provenance.STATED,
        )
        await repo.save_fact(fact)

        loaded = await repo.get_fact("user_4", "area")
        assert loaded is not None
        assert loaded.value == "Indiranagar"

        # Delete fact and check tombstone
        deleted = await repo.delete_fact("user_4", "area")
        assert deleted is True

        assert await repo.get_fact("user_4", "area") is None
        assert await repo.is_tombstoned("user_4", "area") is True

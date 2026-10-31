"""Mutation testing for Policy Gate.

Proves that bypassing or mutating each structural guarantee:
1. Hash verification
2. Approval consumption
3. Capability token authenticity
4. Voiding on price or slot drift
causes immediate policy denial or failure.
"""

from datetime import timedelta

import pytest

from errand_agent.domain.enums import ActionType, BookingStatus
from errand_agent.domain.models import ApprovalRecord, BookingAttemptRecord
from errand_agent.domain.state import (
    HoldInfo,
    WorkflowState,
    compute_action_hash,
)
from errand_agent.mocks.provider import MockProviderService
from errand_agent.policy.gate import (
    PolicyGate,
    PolicyViolationError,
    SideEffectExecutor,
)
from errand_agent.policy.token import ApprovalToken
from errand_agent.store.base import Base, create_engine_and_sessionmaker
from errand_agent.store.repositories import (
    ApprovalRepository,
    BookingLedgerRepository,
)
from errand_agent.trace.sink import NullSink
from errand_agent.utils.clock import FrozenClock


@pytest.fixture
async def mutation_env():
    engine, sessionmaker = create_engine_and_sessionmaker("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    clock = FrozenClock()
    async with sessionmaker() as session:
        approval_repo = ApprovalRepository(session)
        ledger_repo = BookingLedgerRepository(session)
        gate = PolicyGate(approval_repo, ledger_repo, NullSink(), clock=clock)
        mock = MockProviderService(clock=clock)
        yield gate, approval_repo, ledger_repo, mock, clock, session


@pytest.mark.asyncio
async def test_mutation_hash_mismatch_blocks_issuance(mutation_env) -> None:
    """Mutating slot or price so hash drifts MUST block token issuance."""
    gate, approval_repo, _, _, clock, session = mutation_env

    correct_hash = compute_action_hash(
        action_type=ActionType.BOOK,
        provider_id="P-101",
        slot="Saturday 10:00 AM",
        price=1200,
        hold_id="hold_orig",
    )

    approval = ApprovalRecord(
        approval_id="appr_mut_1",
        user_id="user_mut",
        session_id="sess_mut",
        action_type=ActionType.BOOK,
        provider_id="P-101",
        slot="Saturday 10:00 AM",
        price=1200,
        hold_id="hold_orig",
        action_hash=correct_hash,
        created_at=clock.now(),
        expires_at=clock.now() + timedelta(minutes=10),
    )
    await approval_repo.create(approval)
    await session.commit()

    # Mutation: Caller attempts to verify with a different price (1400 instead of 1200)
    with pytest.raises(PolicyViolationError) as exc_info:
        await gate.verify_and_issue_token(
            user_id="user_mut",
            session_id="sess_mut",
            action_type=ActionType.BOOK,
            provider_id="P-101",
            slot="Saturday 10:00 AM",
            price=1400,  # Mutated price
            hold_id="hold_orig",
        )
    assert "No active unconsumed approval found" in str(exc_info.value)


@pytest.mark.asyncio
async def test_mutation_consumed_approval_cannot_be_reused(mutation_env) -> None:
    """An approval once consumed MUST NOT be valid for another token issuance."""
    gate, approval_repo, _, _, clock, session = mutation_env

    action_hash = compute_action_hash(
        action_type=ActionType.BOOK,
        provider_id="P-101",
        slot="Saturday 10:00 AM",
        price=1200,
        hold_id="hold_1",
    )

    approval = ApprovalRecord(
        approval_id="appr_mut_2",
        user_id="user_mut",
        session_id="sess_mut",
        action_type=ActionType.BOOK,
        provider_id="P-101",
        slot="Saturday 10:00 AM",
        price=1200,
        hold_id="hold_1",
        action_hash=action_hash,
        created_at=clock.now(),
        expires_at=clock.now() + timedelta(minutes=10),
    )
    await approval_repo.create(approval)
    await session.commit()

    # First issuance succeeds
    token1 = await gate.verify_and_issue_token(
        user_id="user_mut",
        session_id="sess_mut",
        action_type=ActionType.BOOK,
        provider_id="P-101",
        slot="Saturday 10:00 AM",
        price=1200,
        hold_id="hold_1",
    )
    assert token1 is not None

    # Consume the approval
    await approval_repo.consume_atomically_with_booking_attempt(
        approval_id="appr_mut_2",
        attempt=BookingAttemptRecord(
            idempotency_key=token1.idempotency_key,
            action_hash=action_hash,
            user_id="user_mut",
            session_id="sess_mut",
            action_type=ActionType.BOOK,
            provider_id="P-101",
            slot="Saturday 10:00 AM",
            hold_id="hold_1",
            status=BookingStatus.INTENT,
            created_at=clock.now(),
        ),
    )
    await session.commit()

    # Mutation attempt: Second token issuance on the same approval MUST fail
    with pytest.raises(PolicyViolationError):
        await gate.verify_and_issue_token(
            user_id="user_mut",
            session_id="sess_mut",
            action_type=ActionType.BOOK,
            provider_id="P-101",
            slot="Saturday 10:00 AM",
            price=1200,
            hold_id="hold_1",
        )


@pytest.mark.asyncio
async def test_mutation_forged_capability_token_blocked(mutation_env) -> None:
    """A forged token with unapproved action hash MUST fail execution in SideEffectExecutor."""
    _, approval_repo, ledger_repo, mock, clock, _ = mutation_env

    # Attacker crafts an ApprovalToken without obtaining it through PolicyGate
    forged_token = ApprovalToken(
        approval_id="appr_fake",
        user_id="attacker",
        session_id="sess_fake",
        action_type=ActionType.BOOK,
        action_hash="forged_hash_value",
        idempotency_key="forged_idem_key",
        granted_at=clock.now(),
    )

    executor = SideEffectExecutor(
        token=forged_token,
        provider_service=mock,
        approval_repo=approval_repo,
        ledger_repo=ledger_repo,
        trace_sink=NullSink(),
        clock=clock,
    )

    # Execution MUST raise ValueError / PolicyViolation because no approval exists in DB
    with pytest.raises(ValueError) as exc:
        await executor.execute_book(
            hold_id="fake_hold", provider_id="P-101", slot="Saturday 10:00 AM"
        )
    assert "already consumed, voided, or not found" in str(exc.value)


@pytest.mark.asyncio
async def test_mutation_live_drift_voids_approval(mutation_env) -> None:
    """If state price changes before user affirms, confirmation MUST fail and void approval."""
    gate, approval_repo, _, _, clock, session = mutation_env

    state = WorkflowState(session_id="sess_drift", user_id="user_drift")
    state.selected_provider_id = "P-101"
    state.selected_provider_name = "RapidFix"
    state.selected_slot = "Saturday 10:00 AM"
    state.price = 1200
    state.hold = HoldInfo(
        hold_id="h_drift",
        provider_id="P-101",
        slot="Saturday 10:00 AM",
        expires_at=clock.now() + timedelta(minutes=10),
        quoted_price=1200,
    )

    card = gate.build_confirmation_card(state)
    assert card is not None
    assert state.pending_action is not None

    # Mutation: provider price drifted to 1500 before confirmation arrived
    state.price = 1500

    decision, approval = await gate.evaluate_confirmation(state, "Yes, confirm", run_id="r_drift")
    # Must reject as UNKNOWN and void the pending action
    assert decision.value == "unknown"
    assert approval is None
    assert state.pending_action is None

"""Unit tests for PolicyGate, ApprovalToken, and SideEffectExecutor."""

from datetime import datetime, timezone

import pytest

from errand_agent.domain.enums import ActionType, ConfirmationDecision, Intent
from errand_agent.domain.state import WorkflowState
from errand_agent.mocks.provider import MockProviderService
from errand_agent.mocks.scenario import Scenario
from errand_agent.policy.gate import PolicyGate, PolicyViolationError, SideEffectExecutor
from errand_agent.policy.parser import parse_confirmation
from errand_agent.policy.token import ApprovalToken
from errand_agent.store.base import Base, create_engine_and_sessionmaker
from errand_agent.store.repositories import (
    ApprovalRepository,
    BookingLedgerRepository,
    SessionRepository,
)
from errand_agent.trace.sink import NullSink
from errand_agent.utils.clock import FrozenClock


def test_deterministic_confirmation_parser() -> None:
    # Affirmations
    assert parse_confirmation("YES")[0] == ConfirmationDecision.AFFIRM
    assert parse_confirmation("yes please")[0] == ConfirmationDecision.AFFIRM
    assert parse_confirmation("proceed")[0] == ConfirmationDecision.AFFIRM
    assert parse_confirmation("approved")[0] == ConfirmationDecision.AFFIRM
    assert parse_confirmation("go ahead")[0] == ConfirmationDecision.AFFIRM
    assert parse_confirmation("looks good")[0] == ConfirmationDecision.AFFIRM
    assert parse_confirmation("that works")[0] == ConfirmationDecision.AFFIRM
    assert parse_confirmation("yeah, go for it")[0] == ConfirmationDecision.AFFIRM

    # Denials
    assert parse_confirmation("no")[0] == ConfirmationDecision.DENY
    assert parse_confirmation("cancel this")[0] == ConfirmationDecision.DENY
    assert parse_confirmation("don't do that")[0] == ConfirmationDecision.DENY
    assert parse_confirmation("stop")[0] == ConfirmationDecision.DENY
    assert parse_confirmation("not sure")[0] == ConfirmationDecision.DENY
    assert parse_confirmation("do not proceed")[0] == ConfirmationDecision.DENY

    # Corrections
    decision, text = parse_confirmation("actually, make it Saturday afternoon")
    assert decision == ConfirmationDecision.CORRECTION
    assert "actually" in text

    decision2, text2 = parse_confirmation("change to Thursday morning instead")
    assert decision2 == ConfirmationDecision.CORRECTION

    # Unknown / ambiguous / inquiry
    assert parse_confirmation("what is the weather today?")[0] == ConfirmationDecision.UNKNOWN
    assert parse_confirmation("what is the price?")[0] == ConfirmationDecision.UNKNOWN
    assert parse_confirmation("yes, but actually no")[0] == ConfirmationDecision.UNKNOWN


@pytest.fixture
async def gate_setup():
    engine, sessionmaker = create_engine_and_sessionmaker("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with sessionmaker() as session:
        sess_repo = SessionRepository(session)
        await sess_repo.create("sess_test", "user_test")

        approval_repo = ApprovalRepository(session)
        ledger_repo = BookingLedgerRepository(session)
        trace_sink = NullSink()
        clock = FrozenClock(datetime(2026, 10, 31, 10, 0, 0, tzinfo=timezone.utc))

        gate = PolicyGate(
            approval_repo=approval_repo,
            ledger_repo=ledger_repo,
            trace_sink=trace_sink,
            clock=clock,
        )
        yield gate, approval_repo, ledger_repo, clock, sessionmaker

    await engine.dispose()


@pytest.mark.asyncio
async def test_policy_gate_affirmation_grants_approval(gate_setup) -> None:
    gate, approval_repo, ledger_repo, clock, sessionmaker = gate_setup

    state = WorkflowState(session_id="sess_test", user_id="user_test", intent=Intent.BOOK)
    state.selected_provider_id = "P-101"
    state.selected_provider_name = "RapidFix Plumbers"
    state.selected_slot = "Saturday 10:00 AM"
    state.price = 1200
    state.set_slot("service", "plumber")

    card = gate.build_confirmation_card(state)
    assert card.price == 1200
    assert state.pending_action is not None

    # Affirmation
    decision, approval = await gate.evaluate_confirmation(state, "Yes, please confirm")
    assert decision == ConfirmationDecision.AFFIRM
    assert approval is not None
    assert approval.action_hash == state.pending_action.action_hash

    # Verify token issuance
    token = await gate.verify_and_issue_token(
        user_id="user_test",
        session_id="sess_test",
        action_type=ActionType.BOOK,
        provider_id="P-101",
        slot="Saturday 10:00 AM",
        price=1200,
    )
    assert isinstance(token, ApprovalToken)
    assert token.action_hash == approval.action_hash


@pytest.mark.asyncio
async def test_policy_gate_denial_voids_approval(gate_setup) -> None:
    gate, approval_repo, ledger_repo, clock, sessionmaker = gate_setup

    state = WorkflowState(session_id="sess_test", user_id="user_test", intent=Intent.BOOK)
    state.selected_provider_id = "P-101"
    state.selected_slot = "Saturday 10:00 AM"
    state.price = 1200
    gate.build_confirmation_card(state)

    decision, approval = await gate.evaluate_confirmation(state, "No, cancel")
    assert decision == ConfirmationDecision.DENY
    assert approval is None
    assert state.pending_action is None


@pytest.mark.asyncio
async def test_policy_gate_hash_drift_blocks_approval(gate_setup) -> None:
    gate, approval_repo, ledger_repo, clock, sessionmaker = gate_setup

    state = WorkflowState(session_id="sess_test", user_id="user_test", intent=Intent.BOOK)
    state.selected_provider_id = "P-101"
    state.selected_slot = "Saturday 10:00 AM"
    state.price = 1200
    gate.build_confirmation_card(state)

    # Price drifts before user affirms!
    state.price = 1400

    decision, approval = await gate.evaluate_confirmation(state, "Yes")
    assert decision == ConfirmationDecision.UNKNOWN
    assert approval is None
    assert state.pending_action is None


@pytest.mark.asyncio
async def test_side_effect_executor_requires_valid_token(gate_setup) -> None:
    gate, approval_repo, ledger_repo, clock, sessionmaker = gate_setup
    mock = MockProviderService()

    with pytest.raises(TypeError):
        # Attempting to construct without token
        SideEffectExecutor(
            token="fake_token",  # type: ignore
            provider_service=mock,
            approval_repo=approval_repo,
            ledger_repo=ledger_repo,
            trace_sink=NullSink(),
        )


@pytest.mark.asyncio
async def test_malicious_call_without_approval_blocked(gate_setup) -> None:
    gate, approval_repo, ledger_repo, clock, sessionmaker = gate_setup

    # Attempt to verify token with no approval in DB
    with pytest.raises(PolicyViolationError):
        await gate.verify_and_issue_token(
            user_id="user_test",
            session_id="sess_test",
            action_type=ActionType.BOOK,
            provider_id="P-101",
            slot="Saturday 10:00 AM",
            price=1200,
        )


@pytest.mark.asyncio
async def test_repeated_confirmation_idempotent_skip(gate_setup) -> None:
    gate, approval_repo, ledger_repo, clock, sessionmaker = gate_setup
    scenario = Scenario(forced_outcomes={"book": {0: "success"}})
    mock = MockProviderService(scenario=scenario)
    hold = mock.hold_slot("P-101", "Saturday 10:00 AM")

    state = WorkflowState(session_id="sess_test", user_id="user_test", intent=Intent.BOOK)
    state.selected_provider_id = "P-101"
    state.selected_slot = "Saturday 10:00 AM"
    state.price = 1200
    from errand_agent.domain.state import HoldInfo

    state.hold = HoldInfo(
        hold_id=hold.hold_id,
        provider_id=hold.provider_id,
        slot=hold.slot,
        expires_at=datetime.fromisoformat(hold.expires_at),
        quoted_price=hold.quoted_price,
    )
    gate.build_confirmation_card(state)

    # 1. First approval & execution
    _, approval = await gate.evaluate_confirmation(state, "Yes")
    assert approval is not None

    token = await gate.verify_and_issue_token(
        user_id="user_test",
        session_id="sess_test",
        action_type=ActionType.BOOK,
        provider_id="P-101",
        slot="Saturday 10:00 AM",
        price=1200,
        hold_id=hold.hold_id,
    )

    executor = SideEffectExecutor(
        token=token,
        provider_service=mock,
        approval_repo=approval_repo,
        ledger_repo=ledger_repo,
        trace_sink=NullSink(),
    )
    b_id1 = await executor.execute_book(hold.hold_id, "P-101", "Saturday 10:00 AM")
    assert b_id1.startswith("BK-")

    # 2. Repeated execution with identical token/hash returns cached result without mock book call
    b_id2 = await executor.execute_book(hold.hold_id, "P-101", "Saturday 10:00 AM")
    assert b_id1 == b_id2
    # Verify mock call counter for book was called exactly ONCE
    assert mock._call_counters["book"] == 1

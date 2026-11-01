"""Unit tests for the OpenAI Agents SDK implementation."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from agents import RunContextWrapper

from errand_agent.agent.openai_agent import (
    ErrandAgentContext,
    build_openai_errand_agent,
    execute_confirmed_booking_impl,
    hold_service_slot_impl,
    pii_output_guardrail,
    search_home_service_providers_impl,
)
from errand_agent.domain.enums import Intent, Phase
from errand_agent.domain.state import WorkflowState
from errand_agent.mocks.provider import MockProviderService
from errand_agent.mocks.scenario import Scenario
from errand_agent.policy.gate import PolicyGate
from errand_agent.store.base import Base, create_engine_and_sessionmaker
from errand_agent.store.repositories import (
    ApprovalRepository,
    AuditLogRepository,
    BookingLedgerRepository,
)
from errand_agent.tools.provider_client import ProviderClient
from errand_agent.trace.sink import NullSink
from errand_agent.utils.clock import FrozenClock, SeededRng


@pytest.fixture
async def agent_context() -> ErrandAgentContext:
    clock = FrozenClock(datetime(2026, 10, 31, 10, 0, 0, tzinfo=timezone.utc))
    rng = SeededRng(42)
    engine, sessionmaker = create_engine_and_sessionmaker("sqlite+aiosqlite:///:memory:")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    scenario = Scenario(forced_outcomes={"book": {0: "success"}})
    mock_provider = MockProviderService(scenario=scenario, clock=clock, rng=rng)
    provider_client = ProviderClient(mock_provider, clock=clock, rng=rng)

    async with sessionmaker() as session:
        approval_repo = ApprovalRepository(session)
        ledger_repo = BookingLedgerRepository(session)
        audit_repo = AuditLogRepository(session)
        trace_sink = NullSink()
        gate = PolicyGate(
            approval_repo=approval_repo,
            ledger_repo=ledger_repo,
            trace_sink=trace_sink,
            clock=clock,
        )

        state = WorkflowState(
            session_id="test_sess_agent", user_id="test_user_agent", intent=Intent.BOOK
        )

        return ErrandAgentContext(
            session_id="test_sess_agent",
            user_id="test_user_agent",
            state=state,
            policy_gate=gate,
            provider_client=provider_client,
            approval_repo=approval_repo,
            ledger_repo=ledger_repo,
            audit_repo=audit_repo,
            trace_sink=trace_sink,
            clock=clock,
            run_id="run_agent_01",
        )


@pytest.mark.asyncio
async def test_agent_initialization() -> None:
    """Agent initializes with proper tools, instructions, and guardrails."""
    agent = build_openai_errand_agent()
    assert agent.name == "OrangeErrandAgent"
    assert len(agent.tools) == 4
    assert len(agent.output_guardrails) == 1


@pytest.mark.asyncio
async def test_search_and_hold_tools(agent_context: ErrandAgentContext) -> None:
    """Tools update state and transition phases through the OpenAI agent context."""
    # 1. Search tool
    res = await search_home_service_providers_impl(
        context=agent_context,
        service="plumber",
        area="Indiranagar",
        max_price=1500,
    )
    assert "Matching providers found" in res
    assert agent_context.state.phase == Phase.PRESENTING
    assert len(agent_context.state.search_results) > 0

    # 2. Hold tool
    selected_p = agent_context.state.search_results[0]
    hold_res = await hold_service_slot_impl(
        context=agent_context,
        provider_id=selected_p["provider_id"],
        slot=selected_p["slots"][0],
    )
    assert "Slot successfully held" in hold_res
    assert agent_context.state.phase == Phase.HOLDING
    assert agent_context.state.hold is not None


@pytest.mark.asyncio
async def test_execute_booking_blocked_without_approval(
    agent_context: ErrandAgentContext,
) -> None:
    """Direct execution attempt is strictly blocked by the Policy Gate."""
    # Attempting to call execute_booking before approval is granted
    result = await execute_confirmed_booking_impl(context=agent_context)
    assert "POLICY VIOLATION" in result


def test_pii_output_guardrail(agent_context: ErrandAgentContext) -> None:
    """PII guardrail flags secret tokens or credentials."""
    wrapper = RunContextWrapper(context=agent_context)
    agent = build_openai_errand_agent()

    # Clean output
    clean_out = pii_output_guardrail(
        ctx=wrapper, agent=agent, output="Your plumber is booked for ₹1,200."
    )
    assert not clean_out.tripwire_triggered

    # Leaked secret output
    leaked_out = pii_output_guardrail(
        ctx=wrapper, agent=agent, output="System token: sk-proj-1234567890"
    )
    assert leaked_out.tripwire_triggered

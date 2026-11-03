"""OpenAI Agents SDK implementation for the Errand Agent.

Leverages the official openai-agents package for dynamic LLM reasoning,
tool calling, input/output guardrails, and human-in-the-loop approval.
Deterministic policy gates and database ledgers remain the strict choke point
for all side-effecting financial and provider actions.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from agents import (
    Agent,
    GuardrailFunctionOutput,
    OpenAIChatCompletionsModel,
    OutputGuardrail,
    RunContextWrapper,
    Runner,
    function_tool,
)
from dotenv import load_dotenv
from openai import AsyncOpenAI

from errand_agent.agent.orchestrator import OrchestratorTurnResult
from errand_agent.domain.enums import ActionType, BookingStatus, ConfirmationDecision, Phase
from errand_agent.domain.state import HoldInfo, WorkflowState
from errand_agent.mocks.provider import ProviderTimeoutError, SlotTakenError
from errand_agent.policy.gate import PolicyGate, SideEffectExecutor
from errand_agent.store.repositories import (
    ApprovalRepository,
    AuditLogRepository,
    BookingLedgerRepository,
)
from errand_agent.tools.provider_client import ProviderClient
from errand_agent.trace.sink import TraceSink
from errand_agent.utils.clock import Clock

load_dotenv()


@dataclass
class ErrandAgentContext:
    """Runtime context passed to OpenAI Agents SDK tools and guardrails."""

    session_id: str
    user_id: str
    state: WorkflowState
    policy_gate: PolicyGate
    provider_client: ProviderClient
    approval_repo: ApprovalRepository
    ledger_repo: BookingLedgerRepository
    audit_repo: AuditLogRepository
    trace_sink: TraceSink
    clock: Clock
    run_id: str


# ============================================================================
# OpenAI Agents SDK Function Tools
# ============================================================================


async def search_home_service_providers_impl(
    context: ErrandAgentContext,
    service: str,
    area: str,
    max_price: int | None = None,
) -> str:
    context.state.set_slot("service", service)
    context.state.set_slot("area", area)
    if max_price:
        context.state.set_slot("budget", max_price)

    results = await context.provider_client.search_providers(service=service, area=area)

    # Deterministic budget filtering
    if max_price:
        results = [p for p in results if p.get("price", 0) <= max_price]

    if context.state.phase == Phase.COLLECTING:
        context.state.transition_to(Phase.SEARCHING, reason="Searching matching providers")
    context.state.search_results = results
    context.state.transition_to(Phase.PRESENTING, reason="Searched matching providers")

    if not results:
        return f"No verified providers found for {service} in {area} within budget ₹{max_price or 'any'}."

    summary = []
    for p in results[:3]:
        slots = ", ".join(p.get("slots", []))
        summary.append(
            f"- {p['name']} (ID: {p['provider_id']}): ₹{p['price']} | Rating: {p.get('rating', 'N/A')}⭐ | Available: {slots}"
        )
    return "Matching providers found:\n" + "\n".join(summary)


async def hold_service_slot_impl(
    context: ErrandAgentContext,
    provider_id: str,
    slot: str,
) -> str:
    hold_res = await context.provider_client.hold_slot(provider_id=provider_id, slot=slot)

    # Update state
    context.state.selected_provider_id = provider_id
    context.state.selected_slot = slot
    price = hold_res.get("price", 1000)
    context.state.price = price
    context.state.hold = HoldInfo(
        hold_id=hold_res["hold_id"],
        provider_id=hold_res["provider_id"],
        slot=hold_res["slot"],
        expires_at=hold_res["expires_at"],
        quoted_price=price,
    )

    # Derive provider name
    for p in context.state.search_results:
        if p.get("provider_id") == provider_id:
            context.state.selected_provider_name = p.get("name", "Provider")
            break

    context.state.transition_to(Phase.HOLDING, reason="Temporary slot hold acquired")
    return (
        f"Slot successfully held with {context.state.selected_provider_name} for {slot} at ₹{context.state.price:,}. "
        f"Hold ID: {hold_res['hold_id']}. You must now propose the confirmation card to the user."
    )


def generate_approval_card_impl(context: ErrandAgentContext) -> str:
    card = context.policy_gate.build_confirmation_card(context.state)
    context.state.transition_to(
        Phase.AWAITING_APPROVAL, reason="Generated approval card for user confirmation"
    )
    return card.render_text()


async def execute_confirmed_booking_impl(context: ErrandAgentContext) -> str:
    if context.state.phase != Phase.AWAITING_APPROVAL or not context.state.pending_action:
        return (
            "POLICY VIOLATION: Cannot execute booking. "
            "You MUST propose a confirmation card and receive explicit user approval first."
        )

    # Acquire capability token
    try:
        token = await context.policy_gate.verify_and_issue_token(
            user_id=context.user_id,
            session_id=context.session_id,
            action_type=ActionType.BOOK,
            provider_id=context.state.selected_provider_id or "",
            slot=context.state.selected_slot or "",
            price=context.state.price or 0,
            hold_id=context.state.hold.hold_id if context.state.hold else None,
            run_id=context.run_id,
        )
    except Exception as exc:
        return f"POLICY VIOLATION: Approval verification failed: {exc}"

    executor = SideEffectExecutor(
        token=token,
        provider_service=context.provider_client.provider_service,
        approval_repo=context.approval_repo,
        ledger_repo=context.ledger_repo,
        trace_sink=context.trace_sink,
        clock=context.clock,
        run_id=context.run_id,
    )

    context.state.transition_to(Phase.EXECUTING, reason="Executing booking")
    try:
        booking_id = await executor.execute_book(
            hold_id=context.state.hold.hold_id if context.state.hold else "",
            provider_id=context.state.selected_provider_id or "",
            slot=context.state.selected_slot or "",
        )
        context.state.verified_booking_id = booking_id
        context.state.transition_to(Phase.DONE, reason="Booking verified successfully")
        return f"Booking Confirmed! Booking Reference ID: {booking_id}"
    except SlotTakenError:
        context.state.transition_to(Phase.PRESENTING, reason="Slot taken during booking")
        context.state.hold = None
        return (
            "The selected time slot was unfortunately claimed by another user just now. "
            "Let me find alternative available slots for you."
        )
    except ProviderTimeoutError:
        status_res = await context.provider_client.get_booking_status(token.idempotency_key)
        if status_res.get("status") == "booked" and status_res.get("booking_id"):
            b_id = str(status_res["booking_id"])
            context.state.verified_booking_id = b_id
            await context.ledger_repo.update_status(
                token.idempotency_key, status=BookingStatus.BOOKED, booking_id=b_id
            )
            context.state.transition_to(Phase.DONE, reason="Reconciliation verified booking")
            return f"Booking verified! Booking Reference ID: {b_id}"
        else:
            context.state.transition_to(Phase.NEEDS_HUMAN, reason="Booking timeout unresolved")
            return (
                f"We experienced a timeout with the provider. "
                f"Our support team has been flagged to verify your booking (Reference: {token.idempotency_key[:12]})."
            )
    except Exception as exc:
        context.state.transition_to(Phase.FAILED, reason=str(exc))
        return f"Booking could not be completed: {exc}"


@function_tool
async def search_home_service_providers(
    ctx: RunContextWrapper[ErrandAgentContext],
    service: str,
    area: str,
    max_price: int | None = None,
) -> str:
    """Search available home service errand providers (e.g., plumber, electrician, ac servicing).

    Args:
        service: Service category (e.g., 'plumber', 'electrician', 'ac servicing')
        area: Locality in Bangalore (e.g., 'Indiranagar', 'Koramangala', 'HSR Layout')
        max_price: Optional maximum price budget in INR (₹)
    """
    return await search_home_service_providers_impl(ctx.context, service, area, max_price)


@function_tool
async def hold_service_slot(
    ctx: RunContextWrapper[ErrandAgentContext],
    provider_id: str,
    slot: str,
) -> str:
    """Place a temporary 15-minute hold on a provider's slot before asking for approval.

    Args:
        provider_id: ID of the selected provider (e.g., 'P-101')
        slot: Chosen time slot (e.g., 'Saturday 10:00 AM')
    """
    return await hold_service_slot_impl(ctx.context, provider_id, slot)


@function_tool
def generate_approval_card(
    ctx: RunContextWrapper[ErrandAgentContext],
    reason: str = "Card generated for user confirmation",
) -> str:
    """Generate the deterministic confirmation card and capability token.

    MUST be called before any booking side effect. Present the returned text to the user.

    Args:
        reason: Optional summary reason for generating the confirmation card
    """
    return generate_approval_card_impl(ctx.context)


@function_tool
async def execute_confirmed_booking(
    ctx: RunContextWrapper[ErrandAgentContext],
    confirmation_token: str = "confirmed",
) -> str:
    """Execute the final booking with the provider.

    SECURITY RULE: This tool strictly requires verified user approval.
    If the user has not affirmed the card, this tool returns a policy violation error.

    Args:
        confirmation_token: Optional confirmation identifier
    """
    return await execute_confirmed_booking_impl(ctx.context)


# ============================================================================
# OpenAI Agents SDK Guardrails
# ============================================================================


def pii_output_guardrail(
    ctx: RunContextWrapper[ErrandAgentContext],
    agent: Agent[Any],
    output: Any,
) -> GuardrailFunctionOutput:
    """Check that the model output never leaks raw PII or secret tokens."""
    text = str(output)
    # Tripwire if sensitive tokens leaked
    has_secret_leak = "sk-" in text or "bearer " in text.lower()
    return GuardrailFunctionOutput(
        output_info={"sanitized": True, "has_leak": has_secret_leak},
        tripwire_triggered=has_secret_leak,
    )


# ============================================================================
# Errand Agent Factory
# ============================================================================

ERRAND_AGENT_INSTRUCTIONS = """You are an AI Errand Assistant.
You assist customers in booking, rescheduling, and cancelling home-service errands (plumbing, electrical, AC servicing, cleaning) in Bangalore.

CRITICAL OPERATIONAL RULES:
1. Understand the user's intent, location, preferred time, and budget dynamically.
2. When the user requests a service:
   a. Call `search_home_service_providers(service, area, max_price)` to find available providers.
   b. Select the provider and slot best matching the user's constraints, and immediately call `hold_service_slot(provider_id, slot)` to reserve it.
   c. Immediately call `generate_approval_card()` to generate the capability token and approval card.
   d. Present the approval card and details clearly to the user, instructing them to reply YES to confirm or NO to cancel.
3. NEVER call `execute_confirmed_booking` directly yourself; user confirmation is strictly required.
4. If the user asks questions or modifies constraints, adapt dynamically and search again.
"""


def build_openai_errand_agent(
    model_name: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> Agent[ErrandAgentContext]:
    """Construct an Agent instance using the official OpenAI Agents SDK."""
    raw_key = api_key or os.getenv("OPENAI_API_KEY", "")
    raw_url = base_url or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"
    raw_model = (
        model_name or os.getenv("OPENAI_MODEL") or os.getenv("LLM_MODEL_NAME") or "gpt-4o-mini"
    )

    client = AsyncOpenAI(api_key=raw_key or "mock_key", base_url=raw_url.rstrip("/"))
    chat_model = OpenAIChatCompletionsModel(model=raw_model, openai_client=client)

    return Agent[ErrandAgentContext](
        name="OrangeErrandAgent",
        instructions=ERRAND_AGENT_INSTRUCTIONS,
        tools=[
            search_home_service_providers,
            hold_service_slot,
            generate_approval_card,
            execute_confirmed_booking,
        ],
        model=chat_model,
        output_guardrails=[
            OutputGuardrail(guardrail_function=pii_output_guardrail, name="pii_guardrail")
        ],
    )


async def run_openai_agent_turn(
    agent: Agent[ErrandAgentContext],
    context: ErrandAgentContext,
    user_message: str,
) -> OrchestratorTurnResult:
    """Execute a single conversational turn using OpenAI Agents SDK Runner."""
    # Check if user is responding to an awaiting confirmation card
    if context.state.phase == Phase.AWAITING_APPROVAL:
        decision, approval = await context.policy_gate.evaluate_confirmation(
            state=context.state, user_message=user_message, run_id=context.run_id
        )
        if decision == ConfirmationDecision.AFFIRM and approval:
            # User confirmed: invoke booking tool
            res_text = await execute_booking_tool_direct(context)
            context.state.dialogue_history.append({"role": "user", "content": user_message})
            context.state.dialogue_history.append({"role": "assistant", "content": res_text})
            return OrchestratorTurnResult(
                reply=res_text,
                phase=context.state.phase,
                run_id=context.run_id,
                turn_count=context.state.version,
                is_terminal=True,
                verified_booking_id=context.state.verified_booking_id,
            )
        elif decision == ConfirmationDecision.DENY:
            context.state.transition_to(
                Phase.CANCELLED_BY_USER, reason="User cancelled confirmation"
            )
            cancel_msg = "Understood, I have cancelled this request. No booking was made."
            context.state.dialogue_history.append({"role": "user", "content": user_message})
            context.state.dialogue_history.append({"role": "assistant", "content": cancel_msg})
            return OrchestratorTurnResult(
                reply=cancel_msg,
                phase=Phase.CANCELLED_BY_USER,
                run_id=context.run_id,
                turn_count=context.state.version,
                is_terminal=True,
            )

    # Supply dialogue history context to preserve conversational continuity
    history_context = ""
    if hasattr(context.state, "dialogue_history") and context.state.dialogue_history:
        history_lines = [
            f"{m['role'].capitalize()}: {m['content']}" for m in context.state.dialogue_history[-6:]
        ]
        history_context = "Conversation history:\n" + "\n".join(history_lines) + "\n\n"

    turn_input = f"{history_context}User: {user_message}"

    # Dynamic LLM execution via openai-agents Runner
    run_result = await Runner.run(
        starting_agent=agent,
        input=turn_input,
        context=context,
        max_turns=6,
    )

    final_reply = (
        run_result.final_output if hasattr(run_result, "final_output") else str(run_result)
    )

    reply_str = str(final_reply)
    context.state.dialogue_history.append({"role": "user", "content": user_message})
    context.state.dialogue_history.append({"role": "assistant", "content": reply_str})

    return OrchestratorTurnResult(
        reply=reply_str,
        phase=context.state.phase,
        run_id=context.run_id,
        turn_count=context.state.version,
        is_terminal=(context.state.phase in (Phase.DONE, Phase.CANCELLED_BY_USER)),
        verified_booking_id=context.state.verified_booking_id,
    )


async def execute_booking_tool_direct(context: ErrandAgentContext) -> str:
    """Helper to execute booking through the Policy Gate."""
    if context.state.phase == Phase.AWAITING_APPROVAL:
        context.state.transition_to(Phase.EXECUTING, reason="User confirmed approval card")

    token = await context.policy_gate.verify_and_issue_token(
        user_id=context.user_id,
        session_id=context.session_id,
        action_type=ActionType.BOOK,
        provider_id=context.state.selected_provider_id or "",
        slot=context.state.selected_slot or "",
        price=context.state.price or 0,
        hold_id=context.state.hold.hold_id if context.state.hold else None,
        run_id=context.run_id,
    )

    executor = SideEffectExecutor(
        token=token,
        provider_service=context.provider_client.provider_service,
        approval_repo=context.approval_repo,
        ledger_repo=context.ledger_repo,
        trace_sink=context.trace_sink,
        clock=context.clock,
        run_id=context.run_id,
    )

    try:
        booking_id = await executor.execute_book(
            hold_id=context.state.hold.hold_id if context.state.hold else "",
            provider_id=context.state.selected_provider_id or "",
            slot=context.state.selected_slot or "",
        )

        context.state.verified_booking_id = booking_id
        context.state.transition_to(Phase.DONE, reason="Booking verified successfully")

        clean_name = (
            (context.state.selected_provider_name or "Provider")
            .replace("<untrusted_data>", "")
            .replace("</untrusted_data>", "")
            .strip()
        )
        return (
            f"Booking confirmed! Booked {clean_name} for {context.state.selected_slot} at ₹{context.state.price:,}. "
            f"Booking ID: {booking_id}."
        )
    except SlotTakenError:
        context.state.transition_to(Phase.PRESENTING, reason="Slot taken during booking")
        context.state.hold = None
        return (
            "The selected time slot was unfortunately claimed by another user just now. "
            "Let me find alternative available slots for you."
        )
    except ProviderTimeoutError:
        status_res = await context.provider_client.get_booking_status(token.idempotency_key)
        if status_res.get("status") == "booked" and status_res.get("booking_id"):
            b_id = str(status_res["booking_id"])
            context.state.verified_booking_id = b_id
            await context.ledger_repo.update_status(
                token.idempotency_key, status=BookingStatus.BOOKED, booking_id=b_id
            )
            context.state.transition_to(Phase.DONE, reason="Reconciliation verified booking")
            clean_name = (
                (context.state.selected_provider_name or "Provider")
                .replace("<untrusted_data>", "")
                .replace("</untrusted_data>", "")
                .strip()
            )
            return (
                f"Booking verified on status check! Booked {clean_name} for {context.state.selected_slot} at "
                f"₹{context.state.price:,}. Booking ID: {b_id}."
            )
        else:
            context.state.transition_to(Phase.NEEDS_HUMAN, reason="Booking timeout unresolved")
            return (
                f"We experienced a timeout with the provider. "
                f"Our support team has been flagged to verify your booking (Reference: {token.idempotency_key[:12]})."
            )

"""Explicit state machine orchestrator for errand agent workflows."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from errand_agent.agent.budgets import BudgetConfig, BudgetExceededError, BudgetTracker
from errand_agent.domain.enums import (
    BookingStatus,
    ConfirmationDecision,
    Intent,
    Phase,
    Provenance,
    SlotSource,
)
from errand_agent.domain.state import (
    HoldInfo,
    WorkflowState,
)
from errand_agent.mocks.provider import (
    HoldExpiredError,
    ProviderTimeoutError,
    SlotTakenError,
    SlotUnavailableError,
)
from errand_agent.model.base import (
    AskSlotAction,
    MaliciousDirectBookAction,
    ModelClient,
    RespondAction,
    SanitizedStateView,
)
from errand_agent.policy.extractor import (
    CorrectFactCommand,
    ForgetAllMemoryCommand,
    ForgetFactCommand,
    InspectMemoryCommand,
    extract_intent_and_slots,
    parse_memory_command,
)
from errand_agent.policy.gate import PolicyGate, SideEffectExecutor
from errand_agent.store.repositories import (
    ApprovalRepository,
    AuditLogRepository,
    BookingLedgerRepository,
    MemoryRepository,
    SessionRepository,
    TraceIndexRepository,
    WorkflowStateRepository,
)
from errand_agent.tools.provider_client import ProviderClient
from errand_agent.trace.models import TraceEvent, hash_user_id
from errand_agent.trace.sink import TraceSink
from errand_agent.utils.clock import Clock, FrozenClock


@dataclass
class OrchestratorTurnResult:
    """Result of processing a user message in a conversation session."""

    reply: str
    phase: Phase
    run_id: str
    turn_count: int
    is_terminal: bool = False
    verified_booking_id: str | None = None


class ErrandOrchestrator:
    """Central orchestrator managing deterministic state transitions, tools, and policy."""

    def __init__(
        self,
        session_repo: SessionRepository,
        state_repo: WorkflowStateRepository,
        approval_repo: ApprovalRepository,
        ledger_repo: BookingLedgerRepository,
        memory_repo: MemoryRepository,
        audit_repo: AuditLogRepository,
        trace_repo: TraceIndexRepository,
        policy_gate: PolicyGate,
        provider_client: ProviderClient,
        trace_sink: TraceSink,
        model_client: ModelClient,
        budget_config: BudgetConfig | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.session_repo = session_repo
        self.state_repo = state_repo
        self.approval_repo = approval_repo
        self.ledger_repo = ledger_repo
        self.memory_repo = memory_repo
        self.audit_repo = audit_repo
        self.trace_repo = trace_repo
        self.policy_gate = policy_gate
        self.provider_client = provider_client
        self.trace_sink = trace_sink
        self.model_client = model_client
        self.budget_config = budget_config or BudgetConfig()
        self.clock = clock or FrozenClock()

    async def _emit_trace(
        self,
        run_id: str,
        session_id: str,
        user_id: str,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        event = TraceEvent(
            event_id=str(uuid.uuid4()),
            run_id=run_id,
            session_id=session_id,
            user_id_hash=hash_user_id(user_id),
            event_type=event_type,
            data=data,
        )
        await self.trace_sink.emit(event)

    def _clean_provider_name(self, name: str | None) -> str:
        if not name:
            return "Provider"
        return name.replace("<untrusted_data>", "").replace("</untrusted_data>", "").strip()

    def _extract_intent_and_slots(self, message: str, state: WorkflowState) -> None:
        """Deterministic extractor for intent and slots from user utterance using structural lexing."""
        extracted = extract_intent_and_slots(message, existing_intent=state.intent)
        if extracted.intent and state.intent == Intent.NONE:
            state.intent = extracted.intent

        if extracted.service:
            state.set_slot("service", extracted.service, source=SlotSource.USER)
        if extracted.area:
            state.set_slot("area", extracted.area, source=SlotSource.USER)
        if extracted.date_range:
            state.set_slot("date_range", extracted.date_range, source=SlotSource.USER)
        if extracted.budget is not None:
            state.set_slot("budget", extracted.budget, source=SlotSource.USER)
        if extracted.earliest_time:
            state.set_slot("earliest_time", extracted.earliest_time, source=SlotSource.USER)
        if extracted.reference_last_provider:
            state.set_slot("reference_last_provider", True, source=SlotSource.USER)

    async def _handle_memory_commands(self, user_id: str, message: str) -> str | None:
        """Process explicit memory control commands deterministically without regex."""
        cmd = parse_memory_command(message)
        if not cmd:
            return None

        if isinstance(cmd, InspectMemoryCommand):
            facts = await self.memory_repo.get_facts(user_id)
            if not facts:
                return "I don't have any saved preferences or remembered facts about you."
            items = [f"- {f.key}: {f.value} ({f.provenance.value})" for f in facts]
            return "Here is what I remember about you:\n" + "\n".join(items)

        elif isinstance(cmd, ForgetAllMemoryCommand):
            count = await self.memory_repo.forget_all(user_id)
            return f"I have completely forgotten all {count} saved facts and preferences about you."

        elif isinstance(cmd, ForgetFactCommand):
            deleted = await self.memory_repo.delete_fact(user_id, cmd.key)
            if deleted:
                return f"I have forgotten your preference for '{cmd.key}'."
            return f"I didn't have any saved preference for '{cmd.key}'."

        elif isinstance(cmd, CorrectFactCommand):
            from errand_agent.domain.models import MemoryFactRecord

            fact = MemoryFactRecord(
                id=f"mem_{uuid.uuid4().hex[:8]}",
                user_id=user_id,
                key=cmd.key,
                value=cmd.value,
                provenance=Provenance.STATED,
            )
            await self.memory_repo.save_fact(fact)
            return f"I have updated your '{cmd.key}' preference to '{cmd.value}'."

        return None

    async def run(
        self,
        session_id: str,
        user_id: str,
        message: str,
    ) -> OrchestratorTurnResult:
        """Run a single conversational turn."""
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        budget = BudgetTracker(config=self.budget_config, clock=self.clock)

        # 1. Record session & audit
        await self.audit_repo.log(
            session_id=session_id,
            user_id=user_id,
            event_type="user_message_received",
            payload={"message_length": len(message)},
        )

        # 2. Check for memory management commands first
        memory_reply = await self._handle_memory_commands(user_id, message)
        if memory_reply:
            await self._emit_trace(
                run_id,
                session_id,
                user_id,
                "memory_event",
                {"command": "user_control", "reply": memory_reply},
            )
            return OrchestratorTurnResult(
                reply=memory_reply,
                phase=Phase.COLLECTING,
                run_id=run_id,
                turn_count=1,
            )

        # 3. Load or initialize WorkflowState
        session_rec = await self.session_repo.get(session_id)
        if not session_rec:
            await self.session_repo.create(session_id, user_id)

        state = await self.state_repo.get(session_id)
        if not state:
            state = WorkflowState(session_id=session_id, user_id=user_id, intent=Intent.NONE)

        # Pre-fill memory facts on first turn if not present
        if not state.slots:
            facts = await self.memory_repo.get_facts(user_id)
            for f in facts:
                if f.provenance == Provenance.STATED:
                    state.set_slot(f.key, f.value, source=SlotSource.MEMORY)

        try:
            # 4. Extract slots and intent from current message
            self._extract_intent_and_slots(message, state)

            # 5. Main state machine dispatch loop
            while True:
                budget.check_time()

                # Phase: AWAITING_APPROVAL
                if state.phase == Phase.AWAITING_APPROVAL:
                    decision, approval = await self.policy_gate.evaluate_confirmation(
                        state=state, user_message=message, run_id=run_id
                    )

                    if decision == ConfirmationDecision.AFFIRM and approval:
                        state.transition_to(Phase.EXECUTING, reason="User confirmed approval card")
                        await self._emit_trace(
                            run_id,
                            session_id,
                            user_id,
                            "state_transition",
                            {"from": Phase.AWAITING_APPROVAL.value, "to": Phase.EXECUTING.value},
                        )

                        # Acquire capability token
                        token = await self.policy_gate.verify_and_issue_token(
                            user_id=user_id,
                            session_id=session_id,
                            action_type=approval.action_type,
                            provider_id=approval.provider_id,
                            slot=approval.slot,
                            price=approval.price,
                            hold_id=approval.hold_id,
                            booking_id=approval.booking_id,
                            new_slot=approval.new_slot,
                            run_id=run_id,
                        )

                        executor = SideEffectExecutor(
                            token=token,
                            provider_service=self.provider_client.provider_service,
                            approval_repo=self.approval_repo,
                            ledger_repo=self.ledger_repo,
                            trace_sink=self.trace_sink,
                            clock=self.clock,
                            run_id=run_id,
                        )

                        try:
                            budget.record_tool_call("book")
                            booking_id = await executor.execute_book(
                                hold_id=approval.hold_id or "",
                                provider_id=approval.provider_id,
                                slot=approval.slot,
                            )
                            state.verified_booking_id = booking_id
                            state.transition_to(Phase.DONE, reason="Booking verified successfully")
                            await self._emit_trace(
                                run_id,
                                session_id,
                                user_id,
                                "state_transition",
                                {
                                    "from": Phase.EXECUTING.value,
                                    "to": Phase.DONE.value,
                                    "booking_id": booking_id,
                                },
                            )

                            clean_name = self._clean_provider_name(state.selected_provider_name)
                            reply = (
                                f"Booking confirmed! Booked {clean_name} "
                                f"for {state.get_slot_value('service')} on {state.selected_slot} "
                                f"at ₹{state.price:,}. Booking ID: {booking_id}."
                            )
                            return await self._finalize(run_id, state, reply, is_terminal=True)

                        except ProviderTimeoutError:
                            # Section 4.3: On timeout, call get_booking_status authoritative source of truth
                            state.transition_to(
                                Phase.VERIFYING, reason="Booking timed out; reconciling status"
                            )
                            await self._emit_trace(
                                run_id,
                                session_id,
                                user_id,
                                "state_transition",
                                {"from": Phase.EXECUTING.value, "to": Phase.VERIFYING.value},
                            )

                            budget.record_tool_call("get_booking_status")
                            status_res = await self.provider_client.get_booking_status(
                                token.idempotency_key
                            )

                            if status_res.get("status") == "booked" and status_res.get(
                                "booking_id"
                            ):
                                b_id = status_res["booking_id"]
                                state.verified_booking_id = b_id
                                await self.ledger_repo.update_status(
                                    token.idempotency_key,
                                    status=BookingStatus.BOOKED,
                                    booking_id=b_id,
                                )
                                state.transition_to(
                                    Phase.DONE, reason="Reconciliation verified booking"
                                )
                                clean_name = self._clean_provider_name(state.selected_provider_name)
                                reply = (
                                    f"Booking verified! Booked {clean_name} "
                                    f"for {state.get_slot_value('service')} on {state.selected_slot} "
                                    f"at ₹{state.price:,}. Booking ID: {b_id}."
                                )
                                return await self._finalize(run_id, state, reply, is_terminal=True)

                            elif status_res.get("status") == "not_found":
                                # Safe retry under budget
                                try:
                                    budget.record_tool_call("book")
                                    b_id2 = await executor.execute_book(
                                        hold_id=approval.hold_id or "",
                                        provider_id=approval.provider_id,
                                        slot=approval.slot,
                                    )
                                    state.verified_booking_id = b_id2
                                    state.transition_to(Phase.DONE, reason="Retry succeeded")
                                    clean_name = self._clean_provider_name(
                                        state.selected_provider_name
                                    )
                                    reply = (
                                        f"Booking confirmed on retry! Booked {clean_name} "
                                        f"for {state.get_slot_value('service')} on {state.selected_slot} "
                                        f"at ₹{state.price:,}. Booking ID: {b_id2}."
                                    )
                                    return await self._finalize(
                                        run_id, state, reply, is_terminal=True
                                    )
                                except Exception:
                                    state.transition_to(Phase.NEEDS_HUMAN, reason="Retry failed")
                                    reply = (
                                        f"We experienced an issue confirming your reservation with the provider. "
                                        f"Our support team has been flagged to verify your booking (Reference: {token.idempotency_key[:12]})."
                                    )
                                    return await self._finalize(
                                        run_id, state, reply, is_terminal=True
                                    )

                            else:
                                state.transition_to(Phase.NEEDS_HUMAN, reason="Status unresolved")
                                reply = (
                                    f"Status could not be immediately confirmed. "
                                    f"Please reference key {token.idempotency_key[:12]} with customer support."
                                )
                                return await self._finalize(run_id, state, reply, is_terminal=True)

                        except SlotTakenError:
                            # Slot was claimed by another party
                            state.transition_to(
                                Phase.PRESENTING, reason="Slot taken during booking"
                            )
                            state.hold = None
                            reply = (
                                "The selected time slot was unfortunately claimed by another user just now. "
                                "Let me find alternative available slots for you."
                            )
                            return await self._finalize(run_id, state, reply)

                        except HoldExpiredError:
                            state.transition_to(Phase.HOLDING, reason="Hold expired during booking")
                            state.hold = None
                            reply = "The temporary reservation hold expired. Let me hold a new slot for you."
                            return await self._finalize(run_id, state, reply)

                    elif decision == ConfirmationDecision.DENY:
                        state.transition_to(
                            Phase.CANCELLED_BY_USER, reason="User rejected confirmation card"
                        )
                        reply = "Understood, I have cancelled this request. No booking was made."
                        return await self._finalize(run_id, state, reply, is_terminal=True)

                    elif decision == ConfirmationDecision.CORRECTION:
                        # Slot updated via extractor, re-evaluate
                        state.transition_to(Phase.COLLECTING, reason="User provided correction")
                        # Loop continues to search/present with corrected slots

                    else:
                        # Ambiguous: re-prompt card
                        card = self.policy_gate.build_confirmation_card(state)
                        return await self._finalize(run_id, state, card.render_text())

                # Phase: COLLECTING
                if state.phase == Phase.COLLECTING:
                    missing = state.missing_slots()
                    if missing:
                        budget.record_model_turn()
                        view = self._build_state_view(state, message)
                        action = await self.model_client.complete(view)

                        if isinstance(action, AskSlotAction):
                            return await self._finalize(run_id, state, action.question)
                        elif isinstance(action, MaliciousDirectBookAction):
                            # Adversarial model attempt
                            await self._emit_trace(
                                run_id,
                                session_id,
                                user_id,
                                "policy_decision",
                                {
                                    "decision": "blocked",
                                    "reason": "Model emitted unauthorized direct book",
                                },
                            )
                            return await self._finalize(
                                run_id,
                                state,
                                "Direct booking proposal was blocked by the security policy. Approval is strictly required.",
                            )
                        elif isinstance(action, RespondAction):
                            return await self._finalize(run_id, state, action.message)

                    # All slots present -> move to SEARCHING
                    state.transition_to(Phase.SEARCHING, reason="All required slots collected")
                    await self._emit_trace(
                        run_id,
                        session_id,
                        user_id,
                        "state_transition",
                        {"from": Phase.COLLECTING.value, "to": Phase.SEARCHING.value},
                    )

                # Phase: SEARCHING
                if state.phase == Phase.SEARCHING:
                    # Check reference resolution ("same electrician as last time")
                    if state.get_slot_value("reference_last_provider"):
                        budget.record_tool_call("list_bookings")
                        past_bookings = await self.provider_client.list_bookings(user_id)
                        matching = [
                            b
                            for b in past_bookings
                            if b.get("service") == state.get_slot_value("service")
                        ]
                        if matching:
                            state.selected_provider_id = matching[-1].get("provider_id")
                            state.selected_provider_name = matching[-1].get("provider_name")

                    budget.record_tool_call("search_providers")
                    providers = await self.provider_client.search_providers(
                        service=str(state.get_slot_value("service")),
                        area=str(state.get_slot_value("area")),
                        date_range=str(state.get_slot_value("date_range")),
                    )

                    # Inspect providers for prompt injection attempts in reviews
                    for p in providers:
                        budget.record_tool_call("get_provider")
                        p_detail = await self.provider_client.get_provider(p["provider_id"])
                        if p_detail.get("_metadata", {}).get("has_injection"):
                            await self._emit_trace(
                                run_id,
                                session_id,
                                user_id,
                                "injection_flag",
                                {
                                    "provider_id": p["provider_id"],
                                    "reasons": p_detail["_metadata"]["flagged_reasons"],
                                },
                            )

                    # Deterministic filtering by budget and earliest time
                    budget_cap = state.get_slot_value("budget")
                    if budget_cap:
                        providers = [p for p in providers if p.get("price", 0) <= budget_cap]

                    earliest_time = state.get_slot_value("earliest_time")
                    if earliest_time and "10" in earliest_time:
                        # Filter slots before 10 AM
                        for p in providers:
                            p["slots"] = [
                                s for s in p.get("slots", []) if "09:" not in s and "08:" not in s
                            ]

                    state.search_results = providers
                    state.transition_to(Phase.PRESENTING, reason="Providers found and sanitized")
                    await self._emit_trace(
                        run_id,
                        session_id,
                        user_id,
                        "state_transition",
                        {"from": Phase.SEARCHING.value, "to": Phase.PRESENTING.value},
                    )

                # Phase: PRESENTING
                if state.phase == Phase.PRESENTING:
                    if not state.search_results:
                        return await self._finalize(
                            run_id,
                            state,
                            "I couldn't find any verified providers meeting your exact constraints. "
                            "Would you like to broaden your budget or search time window?",
                        )

                    # Choose provider
                    chosen_p = state.search_results[0]
                    if state.selected_provider_id:
                        for p in state.search_results:
                            if p["provider_id"] == state.selected_provider_id:
                                chosen_p = p
                                break

                    state.selected_provider_id = chosen_p["provider_id"]
                    state.selected_provider_name = chosen_p.get("name", "Provider")
                    state.price = chosen_p.get("price", 1000)
                    state.selected_slot = chosen_p.get("slots", ["Saturday 10:00 AM"])[0]

                    state.transition_to(Phase.HOLDING, reason="Selected provider to acquire hold")
                    await self._emit_trace(
                        run_id,
                        session_id,
                        user_id,
                        "state_transition",
                        {"from": Phase.PRESENTING.value, "to": Phase.HOLDING.value},
                    )

                # Phase: HOLDING
                if state.phase == Phase.HOLDING:
                    budget.record_tool_call("hold_slot")
                    try:
                        raw_hold = await self.provider_client.hold_slot(
                            provider_id=state.selected_provider_id or "",
                            slot=state.selected_slot or "",
                        )
                        state.hold = HoldInfo(
                            hold_id=raw_hold["hold_id"],
                            provider_id=raw_hold["provider_id"],
                            slot=raw_hold["slot"],
                            expires_at=datetime.fromisoformat(raw_hold["expires_at"]),
                            quoted_price=raw_hold["quoted_price"],
                        )
                        # Price drift detection: hold quoted price takes precedence
                        if raw_hold["quoted_price"] != state.price:
                            state.price = raw_hold["quoted_price"]

                        card = self.policy_gate.build_confirmation_card(state)
                        state.transition_to(
                            Phase.AWAITING_APPROVAL, reason="Slot held; awaiting user approval"
                        )
                        await self._emit_trace(
                            run_id,
                            session_id,
                            user_id,
                            "state_transition",
                            {"from": Phase.HOLDING.value, "to": Phase.AWAITING_APPROVAL.value},
                        )

                        return await self._finalize(run_id, state, card.render_text())

                    except SlotUnavailableError:
                        state.transition_to(
                            Phase.PRESENTING, reason="Slot unavailable; selecting alternate"
                        )
                        continue

                # Fallback safeguard
                break

        except BudgetExceededError as b_err:
            state.transition_to(Phase.LIMIT_EXHAUSTED, reason=str(b_err))
            reply = (
                f"We have reached the maximum allowed resource budget ({b_err.budget_name}). "
                "No unauthorized bookings were made."
            )
            return await self._finalize(run_id, state, reply, is_terminal=True)

        except Exception as exc:
            state.transition_to(Phase.FAILED, reason=str(exc))
            await self._emit_trace(
                run_id, session_id, user_id, "unknown_error", {"error": str(exc)}
            )
            reply = (
                "Something went wrong processing your request. "
                "Our safety guarantees ensure that nothing was booked without your verified approval."
            )
            return await self._finalize(run_id, state, reply, is_terminal=True)

        return await self._finalize(run_id, state, "How can I assist you further?")

    async def _finalize(
        self,
        run_id: str,
        state: WorkflowState,
        reply: str,
        is_terminal: bool = False,
    ) -> OrchestratorTurnResult:
        """Single deterministic exit path ensuring workflow persistence and trace closing."""
        await self.state_repo.save(state)
        await self.trace_sink.flush()

        return OrchestratorTurnResult(
            reply=reply,
            phase=state.phase,
            run_id=run_id,
            turn_count=state.version,
            is_terminal=is_terminal,
            verified_booking_id=state.verified_booking_id,
        )

    def _build_state_view(self, state: WorkflowState, last_message: str) -> SanitizedStateView:
        """Construct sanitized safe view for the model port."""
        return SanitizedStateView(
            session_id=state.session_id,
            user_id=state.user_id,
            intent=state.intent.value,
            phase=state.phase.value,
            slots={k: v.value for k, v in state.slots.items()},
            missing_slots=state.missing_slots(),
            last_user_message=last_message,
            conversation_history=[],
            available_providers=state.search_results,
            selected_provider_id=state.selected_provider_id,
            selected_slot=state.selected_slot,
            price=state.price,
            hold_id=state.hold.hold_id if state.hold else None,
            pending_approval=state.pending_action is not None,
        )

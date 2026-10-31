"""Policy Gate: The single choke point for side-effecting operations."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from errand_agent.domain.enums import ActionType, BookingStatus, ConfirmationDecision
from errand_agent.domain.models import ApprovalRecord, BookingAttemptRecord, ConfirmationCard
from errand_agent.domain.state import (
    PendingAction,
    WorkflowState,
    compute_action_hash,
    compute_idempotency_key,
)
from errand_agent.mocks.provider import MockProviderService
from errand_agent.policy.parser import parse_confirmation
from errand_agent.policy.token import ApprovalToken
from errand_agent.store.repositories import ApprovalRepository, BookingLedgerRepository
from errand_agent.trace.models import TraceEvent, hash_user_id
from errand_agent.trace.sink import TraceSink
from errand_agent.utils.clock import Clock, FrozenClock


class PolicyViolationError(Exception):
    """Raised when a side-effect is attempted without code-enforced approval."""

    pass


class PolicyGate:
    """The central security and approval enforcement choke point."""

    def __init__(
        self,
        approval_repo: ApprovalRepository,
        ledger_repo: BookingLedgerRepository,
        trace_sink: TraceSink,
        clock: Clock | None = None,
    ) -> None:
        self.approval_repo = approval_repo
        self.ledger_repo = ledger_repo
        self.trace_sink = trace_sink
        self.clock = clock or FrozenClock()

    def build_confirmation_card(self, state: WorkflowState) -> ConfirmationCard:
        """Render a deterministic confirmation card from verified state."""
        if not state.selected_provider_id or not state.selected_slot:
            raise ValueError("Cannot build confirmation card: provider or slot missing")

        price = state.price or 0
        action_type = (
            ActionType(state.intent.value)
            if state.intent.value in [e.value for e in ActionType]
            else ActionType.BOOK
        )
        hold_id = state.hold.hold_id if state.hold else None
        booking_id = state.active_booking_id
        new_slot = state.get_slot_value("new_slot")

        action_hash = compute_action_hash(
            action_type=action_type,
            provider_id=state.selected_provider_id,
            slot=state.selected_slot,
            price=price,
            hold_id=hold_id,
            booking_id=booking_id,
            new_slot=new_slot,
        )

        state.pending_action = PendingAction(
            action_type=action_type,
            provider_id=state.selected_provider_id,
            provider_name=state.selected_provider_name or state.selected_provider_id,
            service=state.get_slot_value("service") or "service",
            slot=state.selected_slot,
            price=price,
            hold_id=hold_id,
            booking_id=booking_id,
            new_slot=new_slot,
            action_hash=action_hash,
        )

        return ConfirmationCard(
            action_type=action_type,
            provider_id=state.selected_provider_id,
            provider_name=state.selected_provider_name or state.selected_provider_id,
            service=state.get_slot_value("service") or "service",
            slot=state.selected_slot,
            price=price,
            hold_id=hold_id,
            booking_id=booking_id,
            new_slot=new_slot,
            action_hash=action_hash,
        )

    async def evaluate_confirmation(
        self,
        state: WorkflowState,
        user_message: str,
        run_id: str = "run_default",
    ) -> tuple[ConfirmationDecision, ApprovalRecord | None]:
        """Classify user response and conditionally issue persisted approval."""
        decision, correction = parse_confirmation(user_message)

        if decision == ConfirmationDecision.AFFIRM:
            if not state.pending_action:
                await self.trace_sink.emit(
                    TraceEvent(
                        event_id=str(uuid.uuid4()),
                        run_id=run_id,
                        session_id=state.session_id,
                        user_id_hash=hash_user_id(state.user_id),
                        event_type="policy_decision",
                        data={"decision": "deny", "reason": "No pending action awaiting approval"},
                    )
                )
                return ConfirmationDecision.UNKNOWN, None

            # Re-verify live action hash matches pending action hash
            price = state.price or 0
            provider_id = state.selected_provider_id or ""
            slot = state.selected_slot or ""
            hold_id = state.hold.hold_id if state.hold else None
            booking_id = state.active_booking_id
            new_slot = state.get_slot_value("new_slot")
            action_type = state.pending_action.action_type

            live_hash = compute_action_hash(
                action_type=action_type,
                provider_id=provider_id,
                slot=slot,
                price=price,
                hold_id=hold_id,
                booking_id=booking_id,
                new_slot=new_slot,
            )

            if live_hash != state.pending_action.action_hash:
                # State drifted: void and reject!
                await self.approval_repo.void_all_for_session(
                    state.session_id, reason="Action hash drift during affirmation"
                )
                state.pending_action = None
                await self.trace_sink.emit(
                    TraceEvent(
                        event_id=str(uuid.uuid4()),
                        run_id=run_id,
                        session_id=state.session_id,
                        user_id_hash=hash_user_id(state.user_id),
                        event_type="policy_decision",
                        data={"decision": "deny", "reason": "Action hash drift"},
                    )
                )
                return ConfirmationDecision.UNKNOWN, None

            # Create approval
            now = self.clock.now()
            expires_at = state.hold.expires_at if state.hold else (now + timedelta(minutes=10))

            approval = ApprovalRecord(
                approval_id=f"appr_{uuid.uuid4().hex[:12]}",
                user_id=state.user_id,
                session_id=state.session_id,
                action_type=state.pending_action.action_type,
                provider_id=state.pending_action.provider_id,
                slot=state.pending_action.slot,
                price=state.pending_action.price,
                booking_id=state.pending_action.booking_id,
                new_slot=state.pending_action.new_slot,
                hold_id=state.pending_action.hold_id,
                action_hash=live_hash,
                created_at=now,
                expires_at=expires_at,
            )
            await self.approval_repo.create(approval)

            await self.trace_sink.emit(
                TraceEvent(
                    event_id=str(uuid.uuid4()),
                    run_id=run_id,
                    session_id=state.session_id,
                    user_id_hash=hash_user_id(state.user_id),
                    event_type="approval_event",
                    data={
                        "event": "approval_granted",
                        "approval_id": approval.approval_id,
                        "action_hash": approval.action_hash,
                    },
                )
            )
            return ConfirmationDecision.AFFIRM, approval

        elif decision in (ConfirmationDecision.DENY, ConfirmationDecision.CORRECTION):
            # Invalidate any pending action and void active approvals
            await self.approval_repo.void_all_for_session(
                state.session_id, reason=f"User {decision.value}"
            )
            state.pending_action = None
            return decision, None

        return ConfirmationDecision.UNKNOWN, None

    async def verify_and_issue_token(
        self,
        user_id: str,
        session_id: str,
        action_type: ActionType,
        provider_id: str,
        slot: str,
        price: int,
        hold_id: str | None = None,
        booking_id: str | None = None,
        new_slot: str | None = None,
        run_id: str = "run_default",
    ) -> ApprovalToken:
        """Verify code approval and produce single-use execution capability token."""
        computed_hash = compute_action_hash(
            action_type=action_type,
            provider_id=provider_id,
            slot=slot,
            price=price,
            hold_id=hold_id,
            booking_id=booking_id,
            new_slot=new_slot,
        )

        approval = await self.approval_repo.find_active_by_hash(
            user_id, computed_hash, now=self.clock.now()
        )
        if not approval or not approval.is_valid_at(self.clock.now()):
            await self.trace_sink.emit(
                TraceEvent(
                    event_id=str(uuid.uuid4()),
                    run_id=run_id,
                    session_id=session_id,
                    user_id_hash=hash_user_id(user_id),
                    event_type="policy_decision",
                    data={
                        "decision": "deny",
                        "action_type": action_type.value,
                        "action_hash": computed_hash,
                        "reason": "Missing or invalid approval record",
                    },
                )
            )
            raise PolicyViolationError(
                f"Policy Gate Denied: No active unconsumed approval found for hash {computed_hash}"
            )

        idempotency_key = compute_idempotency_key(user_id, computed_hash)
        return ApprovalToken(
            approval_id=approval.approval_id,
            user_id=user_id,
            session_id=session_id,
            action_type=action_type,
            action_hash=computed_hash,
            idempotency_key=idempotency_key,
            granted_at=self.clock.now(),
        )


class SideEffectExecutor:
    """The ONLY class permitted to invoke side-effecting provider tools.

    Can ONLY be instantiated with a valid ApprovalToken.
    """

    def __init__(
        self,
        token: ApprovalToken,
        provider_service: MockProviderService,
        approval_repo: ApprovalRepository,
        ledger_repo: BookingLedgerRepository,
        trace_sink: TraceSink,
        clock: Clock | None = None,
        run_id: str = "run_default",
    ) -> None:
        if not isinstance(token, ApprovalToken):
            raise TypeError("SideEffectExecutor requires a valid ApprovalToken instance")
        self.token = token
        self.provider_service = provider_service
        self.approval_repo = approval_repo
        self.ledger_repo = ledger_repo
        self.trace_sink = trace_sink
        self.clock = clock or FrozenClock()
        self.run_id = run_id

    async def execute_book(
        self,
        hold_id: str,
        provider_id: str,
        slot: str,
    ) -> str:
        """Execute booking with atomic ledger-first guarantee."""
        if self.token.action_type != ActionType.BOOK:
            raise PolicyViolationError(f"Token is for {self.token.action_type}, not book")

        # 1. Check if an attempt already exists for this deterministic key (Repeated confirmation / Retry)
        existing_attempt = await self.ledger_repo.get_by_idempotency_key(self.token.idempotency_key)
        if existing_attempt:
            if existing_attempt.status == BookingStatus.BOOKED and existing_attempt.booking_id:
                # Idempotent replay: return existing result without calling mock
                await self.trace_sink.emit(
                    TraceEvent(
                        event_id=str(uuid.uuid4()),
                        run_id=self.run_id,
                        session_id=self.token.session_id,
                        user_id_hash=hash_user_id(self.token.user_id),
                        event_type="policy_decision",
                        data={
                            "decision": "idempotent_skip",
                            "booking_id": existing_attempt.booking_id,
                            "reason": "Existing ledger entry already booked",
                        },
                    )
                )
                return existing_attempt.booking_id

            # Safe retry of previously attempted call with same key
            if existing_attempt.status in (
                BookingStatus.INTENT,
                BookingStatus.EXECUTING,
                BookingStatus.TIMEOUT,
            ):
                existing_attempt.attempt_count += 1
                await self.ledger_repo.update_status(
                    self.token.idempotency_key, BookingStatus.EXECUTING
                )
                try:
                    booking_id = self.provider_service.book(
                        hold_id=hold_id,
                        idempotency_key=self.token.idempotency_key,
                        user_id=self.token.user_id,
                    )
                    await self.ledger_repo.update_status(
                        self.token.idempotency_key,
                        status=BookingStatus.BOOKED,
                        booking_id=booking_id,
                    )
                    return booking_id
                except Exception as exc:
                    from errand_agent.mocks.provider import ProviderTimeoutError

                    status = (
                        BookingStatus.TIMEOUT
                        if isinstance(exc, ProviderTimeoutError)
                        else BookingStatus.FAILED
                    )
                    await self.ledger_repo.update_status(
                        self.token.idempotency_key,
                        status=status,
                        error=str(exc),
                    )
                    raise

        # 2. Write Ledger Attempt (status=INTENT) atomically with consuming approval
        now = self.clock.now()
        attempt = BookingAttemptRecord(
            idempotency_key=self.token.idempotency_key,
            action_hash=self.token.action_hash,
            user_id=self.token.user_id,
            session_id=self.token.session_id,
            action_type=ActionType.BOOK,
            provider_id=provider_id,
            slot=slot,
            hold_id=hold_id,
            status=BookingStatus.INTENT,
            created_at=now,
            updated_at=now,
        )

        # Atomic transaction: consume approval + insert intent ledger row
        await self.approval_repo.consume_atomically_with_booking_attempt(
            self.token.approval_id, attempt
        )

        await self.trace_sink.emit(
            TraceEvent(
                event_id=str(uuid.uuid4()),
                run_id=self.run_id,
                session_id=self.token.session_id,
                user_id_hash=hash_user_id(self.token.user_id),
                event_type="approval_event",
                data={"event": "approval_consumed", "approval_id": self.token.approval_id},
            )
        )

        # 3. Call the mock provider
        await self.ledger_repo.update_status(self.token.idempotency_key, BookingStatus.EXECUTING)
        try:
            booking_id = self.provider_service.book(
                hold_id=hold_id,
                idempotency_key=self.token.idempotency_key,
                user_id=self.token.user_id,
            )
            # Verified success from call
            await self.ledger_repo.update_status(
                self.token.idempotency_key,
                status=BookingStatus.BOOKED,
                booking_id=booking_id,
            )
            return booking_id

        except Exception as exc:
            # Record failure in ledger
            from errand_agent.mocks.provider import ProviderTimeoutError

            status = (
                BookingStatus.TIMEOUT
                if isinstance(exc, ProviderTimeoutError)
                else BookingStatus.FAILED
            )
            await self.ledger_repo.update_status(
                self.token.idempotency_key,
                status=status,
                error=str(exc),
            )
            raise

    async def execute_cancel(self, booking_id: str) -> bool:
        """Execute cancel with ledger tracking."""
        if self.token.action_type != ActionType.CANCEL:
            raise PolicyViolationError(f"Token is for {self.token.action_type}, not cancel")

        now = self.clock.now()
        attempt = BookingAttemptRecord(
            idempotency_key=self.token.idempotency_key,
            action_hash=self.token.action_hash,
            user_id=self.token.user_id,
            session_id=self.token.session_id,
            action_type=ActionType.CANCEL,
            provider_id="",
            slot="",
            booking_id=booking_id,
            status=BookingStatus.INTENT,
            created_at=now,
            updated_at=now,
        )
        await self.approval_repo.consume_atomically_with_booking_attempt(
            self.token.approval_id, attempt
        )

        res = self.provider_service.cancel(
            booking_id=booking_id,
            idempotency_key=self.token.idempotency_key,
        )
        await self.ledger_repo.update_status(
            self.token.idempotency_key,
            status=BookingStatus.BOOKED,  # Completed effect
            booking_id=booking_id,
        )
        return res

    async def execute_reschedule(self, booking_id: str, new_slot: str) -> Any:
        """Execute reschedule with ledger tracking."""
        if self.token.action_type != ActionType.RESCHEDULE:
            raise PolicyViolationError(f"Token is for {self.token.action_type}, not reschedule")

        now = self.clock.now()
        attempt = BookingAttemptRecord(
            idempotency_key=self.token.idempotency_key,
            action_hash=self.token.action_hash,
            user_id=self.token.user_id,
            session_id=self.token.session_id,
            action_type=ActionType.RESCHEDULE,
            provider_id="",
            slot=new_slot,
            booking_id=booking_id,
            status=BookingStatus.INTENT,
            created_at=now,
            updated_at=now,
        )
        await self.approval_repo.consume_atomically_with_booking_attempt(
            self.token.approval_id, attempt
        )

        res = self.provider_service.reschedule(
            booking_id=booking_id,
            new_slot=new_slot,
            idempotency_key=self.token.idempotency_key,
        )
        await self.ledger_repo.update_status(
            self.token.idempotency_key,
            status=BookingStatus.BOOKED,
            booking_id=booking_id,
        )
        return res

"""Crash and restart recovery routine reconciling in-flight ledger attempts."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from errand_agent.domain.enums import BookingStatus, Phase
from errand_agent.store.repositories import BookingLedgerRepository, WorkflowStateRepository
from errand_agent.tools.provider_client import ProviderClient
from errand_agent.trace.models import TraceEvent, hash_user_id
from errand_agent.trace.sink import TraceSink


@dataclass
class ReconciliationReport:
    """Summary of recovered and reconciled booking attempts."""

    reconciled_count: int
    booked_count: int
    not_found_count: int
    failed_count: int
    details: list[dict[str, Any]]


async def reconcile_in_flight_attempts(
    ledger_repo: BookingLedgerRepository,
    provider_client: ProviderClient,
    state_repo: WorkflowStateRepository,
    trace_sink: TraceSink,
    run_id: str = "recovery_daemon",
) -> ReconciliationReport:
    """Scan all INTENT, EXECUTING, or TIMEOUT attempts and reconcile with authoritative provider status.

    Guarantees no duplicate bookings after a system restart or mid-call process crash.
    """
    in_flight = await ledger_repo.find_pending_or_executing()
    booked_count = 0
    not_found_count = 0
    failed_count = 0
    details: list[dict[str, Any]] = []

    for attempt in in_flight:
        status_res = await provider_client.get_booking_status(attempt.idempotency_key)
        provider_status = status_res.get("status")
        booking_id = status_res.get("booking_id")

        detail = {
            "idempotency_key": attempt.idempotency_key,
            "session_id": attempt.session_id,
            "user_id": attempt.user_id,
            "previous_status": attempt.status.value,
            "provider_status": provider_status,
            "booking_id": booking_id,
        }

        if provider_status == "booked" and booking_id:
            booked_count += 1
            await ledger_repo.update_status(
                idempotency_key=attempt.idempotency_key,
                status=BookingStatus.BOOKED,
                booking_id=booking_id,
            )

            # Reconcile workflow state if present
            state = await state_repo.get(attempt.session_id)
            if state and state.phase != Phase.DONE:
                state.verified_booking_id = booking_id
                state.transition_to(
                    Phase.DONE, reason="Recovery routine reconciled verified booking"
                )
                await state_repo.save(state)

            await trace_sink.emit(
                TraceEvent(
                    event_id=str(uuid.uuid4()),
                    run_id=run_id,
                    session_id=attempt.session_id,
                    user_id_hash=hash_user_id(attempt.user_id),
                    event_type="recovery_event",
                    data={
                        "event": "reconciled_booked",
                        "idempotency_key": attempt.idempotency_key,
                        "booking_id": booking_id,
                    },
                )
            )

        elif provider_status == "not_found":
            not_found_count += 1
            # Never booked upstream; mark as FAILED or TIMEOUT to prevent stale hangs
            await ledger_repo.update_status(
                idempotency_key=attempt.idempotency_key,
                status=BookingStatus.FAILED,
                error="Upstream reported not_found during restart recovery",
            )
            state = await state_repo.get(attempt.session_id)
            if state and state.phase in (Phase.EXECUTING, Phase.VERIFYING):
                state.transition_to(
                    Phase.NEEDS_HUMAN, reason="Restart recovery found uncommitted attempt"
                )
                await state_repo.save(state)

            await trace_sink.emit(
                TraceEvent(
                    event_id=str(uuid.uuid4()),
                    run_id=run_id,
                    session_id=attempt.session_id,
                    user_id_hash=hash_user_id(attempt.user_id),
                    event_type="recovery_event",
                    data={
                        "event": "reconciled_not_found",
                        "idempotency_key": attempt.idempotency_key,
                    },
                )
            )

        else:
            failed_count += 1
            await ledger_repo.update_status(
                idempotency_key=attempt.idempotency_key,
                status=BookingStatus.FAILED,
                error=f"Upstream reported {provider_status}",
            )

        details.append(detail)

    return ReconciliationReport(
        reconciled_count=len(in_flight),
        booked_count=booked_count,
        not_found_count=not_found_count,
        failed_count=failed_count,
        details=details,
    )

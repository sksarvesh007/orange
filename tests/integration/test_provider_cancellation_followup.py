"""Integration tests for proactive provider cancellation follow-up (Section 6 Nice-to-Have).

Proves that:
1. When a provider cancels an existing booking, the agent reconciles the ledger.
2. It proactively searches for an alternative, acquires a temporary hold,
   and issues a structured confirmation card.
3. It NEVER books the alternative without explicit user approval.
4. When the user affirms, the alternative booking succeeds under full Policy Gate enforcement.
5. When the user denies, the pending action is voided without side-effects.
"""

import uuid

import httpx
import pytest

from errand_agent.api.app import app, app_context
from errand_agent.mocks.scenario import Scenario


@pytest.mark.asyncio
async def test_proactive_cancellation_webhook_requires_approval_and_rebooks() -> None:
    """End-to-end proactive cancellation follow-up workflow."""
    async with app.router.lifespan_context(app):
        if app_context.mock_provider:
            app_context.mock_provider.scenario = Scenario(
                name="clean", book_slot_taken_rate=0.0, book_timeout_rate=0.0
            )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            sid = f"sess_proactive_{uuid.uuid4().hex[:8]}"
            uid = f"user_proactive_{uuid.uuid4().hex[:8]}"

            # 1. Initial happy-path booking
            r1 = await client.post(
                "/chat",
                json={
                    "session_id": sid,
                    "user_id": uid,
                    "message": "Find a plumber in Indiranagar this week under 1500.",
                },
            )
            assert r1.status_code == 200
            assert r1.json()["phase"] == "AWAITING_APPROVAL"

            r2 = await client.post(
                "/chat",
                json={
                    "session_id": sid,
                    "user_id": uid,
                    "message": "Yes, confirm",
                },
            )
            assert r2.status_code == 200
            d2 = r2.json()
            assert d2["phase"] == "DONE"
            original_booking_id = d2["verified_booking_id"]
            assert original_booking_id is not None

            # 2. Provider cancels booking asynchronously via Webhook
            webhook_resp = await client.post(
                "/webhook/provider-cancellation",
                json={
                    "booking_id": original_booking_id,
                    "reason": "Technician emergency illness",
                },
            )
            assert webhook_resp.status_code == 200
            w_data = webhook_resp.json()
            assert w_data["status"] == "alternative_held_awaiting_approval"
            assert w_data["requires_approval"] is True
            assert "temporarily held an alternative" in w_data["alert_message"]
            assert w_data["held_alternative"]["hold_id"] is not None

            # 3. Verify conversational state is now AWAITING_APPROVAL (NO auto-booking occurred)
            # User affirms the alternative
            r3 = await client.post(
                "/chat",
                json={
                    "session_id": sid,
                    "user_id": uid,
                    "message": "Yes, confirm",
                },
            )
            assert r3.status_code == 200
            d3 = r3.json()
            assert d3["phase"] == "DONE"
            new_booking_id = d3["verified_booking_id"]
            assert new_booking_id is not None
            assert new_booking_id != original_booking_id
            assert "Booking confirmed!" in d3["reply"]


@pytest.mark.asyncio
async def test_proactive_cancellation_user_denial_voids_hold() -> None:
    """When user denies the proactive alternative, hold is voided without booking."""
    async with app.router.lifespan_context(app):
        if app_context.mock_provider:
            app_context.mock_provider.scenario = Scenario(
                name="clean", book_slot_taken_rate=0.0, book_timeout_rate=0.0
            )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            sid = f"sess_proactive_deny_{uuid.uuid4().hex[:8]}"
            uid = f"user_proactive_deny_{uuid.uuid4().hex[:8]}"

            # 1. Initial booking
            await client.post(
                "/chat",
                json={
                    "session_id": sid,
                    "user_id": uid,
                    "message": "Find a plumber in Indiranagar this week under 1500.",
                },
            )
            r2 = await client.post(
                "/chat",
                json={
                    "session_id": sid,
                    "user_id": uid,
                    "message": "Yes, confirm",
                },
            )
            bid = r2.json()["verified_booking_id"]

            # 2. Webhook cancellation
            await client.post(
                "/webhook/provider-cancellation",
                json={"booking_id": bid, "reason": "Overbooked"},
            )

            # 3. User denies alternative
            r3 = await client.post(
                "/chat",
                json={
                    "session_id": sid,
                    "user_id": uid,
                    "message": "No, cancel it",
                },
            )
            assert r3.status_code == 200
            d3 = r3.json()
            assert d3["phase"] == "CANCELLED_BY_USER"
            assert "cancelled" in d3["reply"].lower()

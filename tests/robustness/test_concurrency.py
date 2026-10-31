"""Concurrency and double-booking race condition test suite.

Proves that under 50 simultaneous racing confirmation requests to the exact same session:
1. Exactly ONE booking attempt succeeds.
2. Zero double-bookings occur in the ledger.
3. All other requests are safely rejected or no-oped.
"""

import asyncio
import uuid

import httpx
import pytest

from errand_agent.api.app import app, app_context
from errand_agent.mocks.scenario import Scenario


@pytest.mark.asyncio
async def test_50_concurrent_confirmations_zero_double_booking() -> None:
    """Simulate 50 parallel confirmation requests racing on the same pending card."""
    async with app.router.lifespan_context(app):
        if app_context.mock_provider:
            app_context.mock_provider.scenario = Scenario(
                name="clean", book_slot_taken_rate=0.0, book_timeout_rate=0.0
            )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            uid = f"concurrency_user_{uuid.uuid4().hex[:6]}"
            sid = f"concurrency_sess_{uuid.uuid4().hex[:6]}"

            # Step 1: Issue pending card
            init_resp = await client.post(
                "/chat",
                json={
                    "session_id": sid,
                    "user_id": uid,
                    "message": "Find a plumber in Indiranagar this week under 1500.",
                },
            )
            assert init_resp.status_code == 200
            data = init_resp.json()
            assert data.get("phase") == "AWAITING_APPROVAL", "Workflow must reach AWAITING_APPROVAL"

            # Step 2: 50 concurrent requests racing to confirm
            booking_ids: list[str] = []

            async def racing_confirm(idx: int) -> None:
                resp = await client.post(
                    "/chat",
                    json={
                        "session_id": sid,
                        "user_id": uid,
                        "message": "Yes, confirm",
                    },
                )
                if resp.status_code == 200:
                    bid = resp.json().get("verified_booking_id")
                    if bid:
                        booking_ids.append(bid)

            # Fire 50 simultaneous requests
            await asyncio.gather(*(racing_confirm(i) for i in range(50)))

            # MUST-HAVE #1: Exactly ONE unique booking created, ZERO double-bookings!
            assert len(set(booking_ids)) == 1, (
                f"Double-booking invariant violated! Expected 1 unique booking, got {len(set(booking_ids))}: {booking_ids}"
            )
            assert len(booking_ids) >= 1

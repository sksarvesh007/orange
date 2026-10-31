# mypy: ignore-errors
"""Locust load test suite for Errand Agent API.

Simulates concurrent user behaviors:
1. Service discovery and search ("Find an electrician under ₹1,500")
2. Confirmation and booking execution ("Yes, confirm")
3. System telemetry scrape and health check
"""

import uuid

from locust import HttpUser, between, task


class ErrandUser(HttpUser):
    """Simulated Errand Agent user session."""

    wait_time = between(0.1, 0.5)

    def on_start(self) -> None:
        """Initialize unique session and user credentials."""
        self.user_id = f"user_{uuid.uuid4().hex[:8]}"
        self.session_id = f"sess_{uuid.uuid4().hex[:8]}"

    @task(3)
    def search_errand(self) -> None:
        """Request service recommendations with budget filter."""
        payload = {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "message": "Find an electrician under ₹1,500 for Saturday morning",
        }
        with self.client.post("/chat", json=payload, catch_response=True) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Search failed with code {resp.status_code}")

    @task(1)
    def confirm_booking(self) -> None:
        """Confirm selected errand."""
        payload = {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "message": "Yes, confirm",
        }
        with self.client.post("/chat", json=payload, catch_response=True) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Confirm failed with code {resp.status_code}")

    @task(1)
    def check_health(self) -> None:
        """Verify API liveness."""
        self.client.get("/healthz")

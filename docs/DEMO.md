# Errand Agent: Evaluator Demo Script

Walkthrough demonstrating the core behaviors of the Errand Agent in under 3 minutes.

All commands run against the offline scripted model. No external LLM keys or network access are required.

---

## Prerequisites

Activate the virtual environment:
```bash
source .venv/bin/activate
```

---

## 1. Standard booking

The user searches for a plumber within budget, reviews the confirmation card, and confirms the booking.

### Step 1: Start interactive CLI
```bash
errand chat --session-id demo_happy_path --user-id evaluator_1
```

### Step 2: Conversation
```text
You: Find a plumber in Indiranagar this week under 1500.

Agent [AWAITING_APPROVAL]: Please confirm: Book RapidFix Plumbers for plumber on Wednesday 10:00 AM at ₹1,200. Reply YES to confirm or NO to cancel.
  (Trace run_id: run_xxxx)

You: Yes, confirm

Agent [DONE]: Booking confirmed! Booked RapidFix Plumbers for plumber on Wednesday 10:00 AM at ₹1,200. Booking ID: BK-1001.
```

What this demonstrates:
- The agent does not call `book()` without user confirmation.
- The quoted price (1,200 INR) stays within the 1,500 INR budget.
- Exactly one record is added to the database ledger.

---

## 2. Modifying a request before confirmation

When presented with the confirmation card, the user changes the day to Saturday. The agent voids the pending hold, searches for new slots, and issues an updated confirmation card.

### CLI execution
```bash
errand chat --session-id demo_correction --user-id evaluator_2
```

### Conversation
```text
You: Find a plumber in Indiranagar this week under 1500.

Agent [AWAITING_APPROVAL]: Please confirm: Book RapidFix Plumbers for plumber on Wednesday 10:00 AM at ₹1,200. Reply YES to confirm or NO to cancel.

You: No, change to Saturday morning instead

Agent [AWAITING_APPROVAL]: Please confirm: Book RapidFix Plumbers for plumber on Saturday 09:00 AM at ₹1,200. Reply YES to confirm or NO to cancel.

You: Yes, confirm

Agent [DONE]: Booking confirmed! Booked RapidFix Plumbers for plumber on Saturday 09:00 AM at ₹1,200. Booking ID: BK-1002.
```

What this demonstrates:
- The previous approval for Wednesday 10:00 AM is voided.
- A new confirmation card is generated for Saturday 09:00 AM.
- The action hash changes so the old confirmation cannot be reused.

---

## 3. Price change during hold

If a provider increases the price between search and hold, the policy gate voids the pending action and requires new approval.

Run the scenario test:
```bash
pytest tests/evals/test_scenarios.py -k "test_scenario_02" -v
```

Expected result:
```text
PASSED tests/evals/test_scenarios.py::test_scenario_02_price_drift_requires_reapproval
```
The trace records `price_drift_detected`, voids the approval, and prevents any call to `book()` until the user accepts the new rate.

---

## 4. Viewing trace logs

Traces redact phone numbers, emails, and API keys.

### CLI inspection
List recent execution traces:
```bash
errand trace list
```

Format a specific trace:
```bash
errand trace pretty <run_id>
```

### Web UI
Start the local server:
```bash
uvicorn errand_agent.api.app:app --host 127.0.0.1 --port 8000
```
Open in a browser:
```text
http://127.0.0.1:8000/trace/<run_id>
```
What this demonstrates:
- User IDs are hashed (`user_id_hash: a1b2c3d4...`).
- Phone numbers, email addresses, and API secrets are replaced with tokens (`[REDACTED_PHONE]`, `[REDACTED_EMAIL]`, `[REDACTED_SECRET]`).
- State transitions, tool calls, and policy decisions are logged in order.

---

## 5. Process restart during a pending call

If the process crashes during a provider timeout, the recovery logic checks provider status upon restart without placing duplicate bookings.

Run the recovery integration test:
```bash
pytest tests/integration/test_recovery_restart.py -v
```

Expected result:
```text
PASSED tests/integration/test_recovery_restart.py::test_crash_after_approval_recovery_resumes
PASSED tests/integration/test_recovery_restart.py::test_background_recovery_daemon_reconciles_interrupted_bookings
```

What this demonstrates:
- The idempotency key is saved before sending the network request.
- On restart, the daemon calls `get_booking_status()` to check whether the provider completed the booking.
- Exactly one booking is recorded in the ledger.

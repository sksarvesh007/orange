# Errand Agent - 3-Minute Evaluator Demo Script

This scripted walkthrough demonstrates the core invariants and resilient behaviors of the Errand Agent in under 3 minutes.

All commands run against the deterministic scripted model. No external LLM keys or network access are required.

---

## Prerequisites

Ensure the virtual environment is activated:
```bash
source .venv/bin/activate
```

---

## 1. Happy-Path Booking (Must-Haves #1, #2, #5)

In this scenario, the user searches for a plumber within budget, reviews the structured confirmation card, and explicitly confirms the booking.

### Step 1: Start Interactive CLI
```bash
errand chat --session-id demo_happy_path --user-id evaluator_1
```

### Step 2: Conversation Script
```text
You: Find a plumber in Indiranagar this week under 1500.

Agent [AWAITING_APPROVAL]: Please confirm: Book RapidFix Plumbers for plumber on Wednesday 10:00 AM at ₹1,200. Reply YES to confirm or NO to cancel.
  (Trace run_id: run_xxxx)

You: Yes, confirm

Agent [DONE]: Booking confirmed! Booked RapidFix Plumbers for plumber on Wednesday 10:00 AM at ₹1,200. Booking ID: BK-1001.
```

**Auditable Guarantees Proven**:
- The agent **never booked without explicit approval**.
- Quoted price (₹1,200) was under the ₹1,500 budget.
- Single atomic consumption created exactly 1 booking in the ledger.

---

## 2. Mid-Flight Correction (Must-Have #2)

In this scenario, when presented with the confirmation card, the user changes the day to Saturday. The agent voids the pending hold, searches for new slots, and issues a freshly hashed confirmation card.

### CLI Execution
```bash
errand chat --session-id demo_correction --user-id evaluator_2
```

### Conversation Script
```text
You: Find a plumber in Indiranagar this week under 1500.

Agent [AWAITING_APPROVAL]: Please confirm: Book RapidFix Plumbers for plumber on Wednesday 10:00 AM at ₹1,200. Reply YES to confirm or NO to cancel.

You: No, change to Saturday morning instead

Agent [AWAITING_APPROVAL]: Please confirm: Book RapidFix Plumbers for plumber on Saturday 09:00 AM at ₹1,200. Reply YES to confirm or NO to cancel.

You: Yes, confirm

Agent [DONE]: Booking confirmed! Booked RapidFix Plumbers for plumber on Saturday 09:00 AM at ₹1,200. Booking ID: BK-1002.
```

**Auditable Guarantees Proven**:
- Old approval for Wednesday 10:00 AM was voided.
- A new confirmation card was generated for Saturday 09:00 AM.
- Action hash was regenerated to prevent execution of stale intent.

---

## 3. Price-Drift Rejection (Must-Have #4)

In this scenario, between search and hold, the provider silently bumps the price (+₹200) above the quoted search price. The Policy Gate deterministically voids the action and demands re-approval.

Run the pre-configured automated eval for Scenario 02:
```bash
pytest tests/evals/test_scenarios.py -k "test_scenario_02" -v
```

**Expected Result**:
```text
PASSED tests/evals/test_scenarios.py::test_scenario_02_price_drift_requires_reapproval
```
The trace records `price_drift_detected` (`old_price: 1200`, `new_price: 1400`), the original approval is voided, and no side-effect call to `book()` is permitted until the user re-confirms the new price.

---

## 4. Audit Trace Lookup (Must-Have #8)

Inspect the auditable execution trace and verify PII redaction.

### CLI Trace Pretty-Print
List recent execution traces:
```bash
errand trace list
```

Pretty-print the trace of your recent run:
```bash
errand trace pretty <run_id>
```

### Web UI Trace Viewer
Start the local server:
```bash
uvicorn errand_agent.api.app:app --host 127.0.0.1 --port 8000
```
Open in browser:
```text
http://127.0.0.1:8000/trace/<run_id>
```
**Auditable Guarantees Proven**:
- Real user IDs are hashed (`user_id_hash: a1b2c3d4...`).
- Phone numbers, email addresses, and API secrets are replaced with `[REDACTED_PHONE]`, `[REDACTED_EMAIL]`, `[REDACTED_SECRET]`.
- Every state transition, tool call, policy gate decision, and ledger mutation is chronological and replayable.

---

## 5. Crash Restart & Idempotent Recovery (Must-Have #6)

In this scenario, the agent crashes after receiving approval but during provider timeout. Upon restart, the recovery engine reconciles state without double-booking.

Run the crash recovery integration test:
```bash
pytest tests/integration/test_recovery_restart.py -v
```

**Expected Result**:
```text
PASSED tests/integration/test_recovery_restart.py::test_crash_after_approval_recovery_resumes
PASSED tests/integration/test_recovery_restart.py::test_background_recovery_daemon_reconciles_interrupted_bookings
```

**Auditable Guarantees Proven**:
- `idempotency_key` is persisted before any network call.
- On restart, `get_booking_status()` is polled to discover if the provider processed the booking.
- Exactly 1 booking is recorded in the ledger.

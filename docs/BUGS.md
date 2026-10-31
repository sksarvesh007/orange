# Real Bugs Encountered During Development

This document logs real bugs encountered during implementation, detailing root cause, fix, commit reference, and regression tests.

---

### Bug 1: Wall-Clock Leak in Approval Expiry Check
- **What broke**: `PolicyGate.verify_and_issue_token` failed with `PolicyViolationError` because `ApprovalRepository.find_active_by_hash` filtered by `ApprovalModel.expires_at > datetime.now(timezone.utc)` rather than using the injected simulation `Clock`.
- **Root Cause**: `find_active_by_hash` called Python's standard `datetime.now(timezone.utc)` instead of accepting an injected clock timestamp. When the test environment operated with a frozen clock at 10:00 AM UTC, approvals with a 10-minute expiry (10:10 AM UTC) were judged expired against real local machine time (16:49 UTC).
- **The Fix**: Added `now: datetime | None = None` parameter to `find_active_by_hash`, `is_valid`, and repositories, using `clock.now()` uniformly across domain and persistence layers.
- **Regression Test**: `tests/unit/test_policy_gate.py::test_policy_gate_affirmation_grants_approval`

---

### Bug 2: Pending Action Drift Check Validated Pending Action Against Itself
- **What broke**: In `PolicyGate.evaluate_confirmation`, mutating `state.price` before the user affirmed did not trigger approval rejection.
- **Root Cause**: `evaluate_confirmation` re-computed `expected_hash` using `state.pending_action.price` and `state.pending_action.slot` rather than inspecting current live state (`state.price`, `state.selected_slot`, `state.selected_provider_id`).
- **The Fix**: Derived `live_hash` from live workflow state fields (`state.price`, `state.selected_slot`, `state.selected_provider_id`, etc.) and validated `live_hash == state.pending_action.action_hash`. If mismatched, immediately voided existing approvals, cleared pending action, and rejected.
- **Regression Test**: `tests/unit/test_policy_gate.py::test_policy_gate_hash_drift_blocks_approval`

---

### Bug 3: SQLite Stripped Timezone Info Causing Offset-Naive vs Offset-Aware TypeError
- **What broke**: `ApprovalRecord.is_valid` threw `TypeError: can't compare offset-naive and offset-aware datetimes` when verifying an approval fetched from the SQLite database.
- **Root Cause**: SQLite stores timestamps without timezone offsets by default in Python's sqlite3 driver, deserializing `DateTime(timezone=True)` into offset-naive `datetime` objects, which Python forbids comparing with timezone-aware `Clock.now()`.
- **The Fix**: Created a timestamp normalization helper `ensure_utc(dt)` that guarantees any offset-naive datetime is converted to UTC timezone-aware before comparison and upon repository hydration.
- **Regression Test**: `tests/unit/test_policy_gate.py::test_policy_gate_affirmation_grants_approval`

---

### Bug 4: Retry After Timeout Attempted to Re-Consume Approval and Re-Insert Ledger Attempt
- **What broke**: In `test_scenario_07_timeout_not_booked_retry_success`, when a booking timed out and reconciliation determined the reservation was `not_found`, retrying the book call failed and landed in `Phase.NEEDS_HUMAN`.
- **Root Cause**: `SideEffectExecutor.execute_book` always assumed a first-time execution: it attempted to insert a new `BookingAttempt` row (failing on duplicate PK `idempotency_key`) and re-consume the approval (failing with `Approval is already consumed`).
- **The Fix**: In `SideEffectExecutor.execute_book`, if an attempt already exists in `INTENT`, `EXECUTING`, or `TIMEOUT`, it treats the invocation as an idempotent retry: it skips approval re-consumption and row re-insertion, updates the existing ledger row status to `EXECUTING` (incrementing `attempt_count`), and re-issues the call to the provider with the same deterministic idempotency key.
- **Regression Test**: `tests/evals/test_scenarios.py::test_scenario_07_timeout_not_booked_retry_success`

---

### Bug 5: Mock Provider Cached Dropped Connection Timeouts Blocking Safe Retries with Same Idempotency Key
- **What broke**: In `test_scenario_07`, when the provider timed out before booking was committed (`timeout_not_booked`), subsequent retry with the same deterministic idempotency key failed with cached `ProviderTimeoutError`.
- **Root Cause**: `MockProviderService._idempotency_map` permanently cached the `timeout_not_booked` status, causing idempotent retries to raise the cached error rather than allowing the retry to reach the mock provider service.
- **The Fix**: Updated `MockProviderService.book` so that `status == "timeout_not_booked"` does not permanently block retries with the same key, allowing the caller to safely retry as mandated by Section 4.3 of the specification.
- **Regression Test**: `tests/evals/test_scenarios.py::test_scenario_07_timeout_not_booked_retry_success`


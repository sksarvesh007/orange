# Development Bug Log

Issues found during implementation, along with their causes and fixes.

---

### Wall-clock leak in approval expiry check

`PolicyGate.verify_and_issue_token` failed with `PolicyViolationError` because `ApprovalRepository.find_active_by_hash` filtered by `ApprovalModel.expires_at > datetime.now(timezone.utc)` rather than using the simulated clock. When tests ran with a frozen clock at 10:00 AM UTC, approvals with a 10-minute expiry were compared against real machine time.

Added an optional `now: datetime | None = None` parameter across the repository and domain methods, using `clock.now()` uniformly.

Regression test: `tests/unit/test_policy_gate.py::test_policy_gate_affirmation_grants_approval`

---

### Drift check validated pending action against itself

In `PolicyGate.evaluate_confirmation`, mutating `state.price` before the user confirmed did not trigger a rejection. `evaluate_confirmation` was re-computing the expected hash using `state.pending_action.price` and `state.pending_action.slot` instead of inspecting current workflow state (`state.price`, `state.selected_slot`, `state.selected_provider_id`).

Derived `live_hash` directly from current state fields and checked `live_hash == state.pending_action.action_hash`. If mismatched, pending approvals are voided immediately.

Regression test: `tests/unit/test_policy_gate.py::test_policy_gate_hash_drift_blocks_approval`

---

### SQLite stripped timezone information

`ApprovalRecord.is_valid` raised `TypeError: can't compare offset-naive and offset-aware datetimes` when verifying an approval fetched from SQLite. SQLite stores timestamps without timezone offsets by default, returning naive datetime objects that cannot be compared to UTC-aware values from `Clock.now()`.

Added a helper `ensure_utc(dt)` that converts naive datetimes to UTC upon loading records from SQLite.

Regression test: `tests/unit/test_policy_gate.py::test_policy_gate_affirmation_grants_approval`

---

### Retrying after timeout attempted to re-insert ledger records

In `test_scenario_07_timeout_not_booked_retry_success`, when a booking timed out and reconciliation showed the reservation was not found, retrying the call failed into `Phase.NEEDS_HUMAN`. `SideEffectExecutor.execute_book` assumed every run was a first attempt, trying to insert a new row with the same primary key and re-consume the approval.

Updated `execute_book` to check if an attempt already exists in `INTENT`, `EXECUTING`, or `TIMEOUT`. If found, it treats the call as an idempotent retry, skipping re-consumption and re-using the existing ledger record.

Regression test: `tests/evals/test_scenarios.py::test_scenario_07_timeout_not_booked_retry_success`

---

### Mock provider cached connection timeouts blocking retries

When the mock provider timed out before committing a booking (`timeout_not_booked`), subsequent retries using the same idempotency key raised a cached `ProviderTimeoutError`. `MockProviderService._idempotency_map` was permanently caching the timeout status.

Updated `MockProviderService.book` so that `timeout_not_booked` does not permanently cache an error, allowing subsequent retries with the same key to reach the provider.

Regression test: `tests/evals/test_scenarios.py::test_scenario_07_timeout_not_booked_retry_success`

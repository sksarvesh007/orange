# Domain and Architectural Assumptions

Interpretations of specifications and edge case handling.

---

### Resolving historical booking references

Phrases like "same electrician as last time" resolve through `list_bookings(user_id)`. The agent selects the provider from the most recent booking matching the requested service (e.g. `service="electrician"`). If several past bookings match, it picks the latest by timestamp. If no past booking exists for that service, the agent prompts the user directly.

---

### Stated facts versus inferred facts in memory

Stated facts are explicit declarations by the user, such as "I live in Indiranagar" or "I prefer mornings". Inferred facts are patterns noted by the system, like repeated selection of morning appointments. Inferred facts are stored with `provenance="inferred"` and are never used to auto-populate or constrain workflow slots without user confirmation. Stated facts load with `source="memory"` and pre-fill slots, but remain visible to the user for correction.

---

### Approval hash scope

`action_hash` is computed as the SHA-256 hex digest of a canonical JSON object containing: `action_type`, `provider_id`, `slot`, `price`, `hold_id` (for bookings), and `booking_id` / `new_slot` (for cancellations and rescheduling). Any change to provider, slot, price, or hold invalidates the approval.

---

### Idempotency key derivation

`idempotency_key` derives from `sha256(f"{user_id}:{action_hash}")`. Duplicate user confirmations, process restarts, and retries with the same action hash map to the same database row and provider idempotency key.

---

### Time and randomness injection

Wall-clock calls (`datetime.now()`, `time.time()`) and unseeded random calls are avoided in domain and mock code. Components take an injected `Clock` and `Rng` to ensure reproducible tests and avoid sleep delays in the test suite.

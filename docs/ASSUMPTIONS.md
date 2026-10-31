# Errand Agent - Architectural & Domain Assumptions

This document records deliberate readings of specifications and domain edge cases with justification.

## 1. Booking References Resolution ("same electrician as last time")
- **Assumption**: The agent calls `list_bookings(user_id)` to inspect historical bookings. The reference "same electrician as last time" resolves to the provider associated with the most recent booking matching the requested service (e.g. `service="electrician"`). If multiple past bookings exist for the service, the one with the latest timestamp/id is selected. If no past booking for that service exists, the agent prompts the user deterministically.

## 2. Inferred Facts vs Stated Facts in Memory
- **Assumption**: Stated facts are explicit declarations by the user ("I live in Indiranagar", "I prefer mornings"). Inferred facts are patterns observed by the system (e.g. user repeatedly choosing morning slots). Inferred facts are stored with `provenance="inferred"` and must NEVER be used to auto-populate or constrain workflow slots without explicit user confirmation first. Stated facts are loaded with `source="memory"` and pre-fill slots, but are always visible to the user for correction.

## 3. Approval Hash Scope
- **Assumption**: `action_hash` is computed as the SHA-256 hex digest of a canonical JSON representation containing: `action_type`, `provider_id`, `slot`, `price`, `hold_id` (for book), and `booking_id` / `new_slot` (for cancel/reschedule). Any drift in provider, slot, price, or hold invalidates/voids the approval.

## 4. Idempotency Key Derivation
- **Assumption**: `idempotency_key` is derived deterministically from `sha256(f"{user_id}:{action_hash}")`. This guarantees that duplicate confirmations ("yes" x3), restarts, and retries with the same action hash always hit the same ledger row and mock provider idempotency key.

## 5. Clock and Rng Dependency Injection
- **Assumption**: Wall-clock calls (`datetime.now()`, `time.time()`) and random calls are forbidden in domain and mock code. All components accept an injected `Clock` and `Rng` to ensure 100% deterministic, reproducible tests and zero sleep delays.

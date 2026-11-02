# System Architecture and Design Document: Errand Agent

Founding AI Engineer submission for Orange Invest.

---

## 1. System architecture and design choices

The Errand Agent coordinates booking, rescheduling, and cancelling home-service errands against third-party provider APIs. Because provider APIs experience volatile availability, price changes, and network timeouts, all side effects require deterministic validation.

The design relies on standard components rather than custom frameworks:
- **FastAPI**: Asynchronous web layer with OpenAPI schema typing.
- **Pydantic v2**: Data validation, schema enforcement, and input sanitization.
- **SQLAlchemy 2.0 (Async)**: Relational ledger and state storage supporting SQLite (`aiosqlite`) and PostgreSQL (`asyncpg`).
- **HTTPX**: Connection-pooled HTTP client with token-bucket rate limiting.
- **Prometheus Client & Structlog**: Metrics collection and structured JSON logging.

### Separation of concerns
The language model functions as an intent and slot extractor. It has no execution tools, holds no API keys, and cannot invoke booking endpoints. All state transitions, confirmation cards, pricing checks, and side effects run through deterministic Python code verified by automated tests.

---

## 2. State model and transitions

The workflow follows a finite state machine (`WorkflowState`) saved to the database on each conversational turn.

```
                  ┌──────────────┐
                  │  COLLECTING  │
                  └──────┬───────┘
                         ▼
                  ┌──────────────┐
                  │  SEARCHING   │
                  └──────┬───────┘
                         ▼
                  ┌──────────────┐
                  │  PRESENTING  │
                  └──────┬───────┘
                         ▼
                  ┌──────────────┐
                  │   HOLDING    │
                  └──────┬───────┘
                         ▼
              ┌──────────────────────┐
              │  AWAITING_APPROVAL   │◄─────────────────┐
              └──────────┬───────────┘                  │
            Affirmative  │  Negative / Drift            │ Alternative
                         ▼                              │ Held
                  ┌──────────────┐                      │
                  │  EXECUTING   │                      │
                  └──────┬───────┘                      │
                         ▼                              │
                  ┌──────────────┐                      │
                  │  VERIFYING   │                      │
                  └──────┬───────┘                      │
                         ▼                              │
                     ┌──────┐                           │
                     │ DONE │───[Provider Cancelled]────┘
                     └──────┘
```

- **Transitions**: Permitted transitions are defined in `VALID_TRANSITIONS`. Invalid transitions raise `IllegalStateTransitionError`.
- **Slot provenance**: Extracted slots record their source (`USER`, `MEMORY`, or `INFERRED`) along with the turn count.
- **Crash recovery**: Workflow state saves at the end of each turn. If a process restarts, loading the session restores the exact phase, candidate holds, and counters without losing context.

---

## 3. Approval verification and capability tokens

The policy gate requires verified user consent before any external mutation can run.

1. **Confirmation card**: When entering `Phase.AWAITING_APPROVAL`, `PolicyGate.build_confirmation_card()` creates a card with provider name, date, time, service, and price in INR.
2. **Action canonicalization**:
   $$\text{ActionHash} = \text{SHA256}(\text{ActionType} \parallel \text{ProviderID} \parallel \text{Slot} \parallel \text{Price} \parallel \text{SessionID})$$
3. **Capability token (`ApprovalToken`)**: The gate generates an HMAC-signed token encoding the action hash, creation time, and a 15-minute expiration window.
4. **Intent parser**: The user response is parsed into `AFFIRM`, `DENY`, or `AMBIGUOUS`. Ambiguous responses remain in `AWAITING_APPROVAL` with a prompt asking for clarification.
5. **Single-use consumption**: During execution, the ledger repository updates the approval record atomically:
   ```sql
   UPDATE approval_records 
   SET consumed_at = :now 
   WHERE id = :token_id AND consumed_at IS NULL;
   ```
   If already consumed or if the hash drifted, the transaction rolls back with a `PolicyViolationError`.
6. **Pre-execution price check**: Before dispatching a booking call, the gate checks the provider rate. If the price changed, the booking aborts, alerts the user, and asks for fresh approval.

---

## 4. Ledger-first execution and idempotency

External provider endpoints can fail, time out, or drop connections.

- **Ledger-first record**: Before sending a network request to `/book`, an `INTENT` record writes to the `booking_attempts` table.
- **Idempotency key**:
   $$\text{IdempotencyKey} = \text{SHA256}(\text{SessionID} \parallel \text{ProviderID} \parallel \text{Slot} \parallel \text{Price})$$
- **Database unique constraint**: A unique index on `(session_id, idempotency_key)` ensures that concurrent requests cannot trigger multiple booking calls for the same action.
- **Timeout reconciliation**: If a network timeout occurs during execution, the status remains `TIMEOUT` until the agent checks `GET /bookings/{id}` or `GET /providers/{id}/schedule` to verify whether the slot was claimed before failing or retrying.

---

## 5. Untrusted content and prompt injection defense

- **Review sanitization**: Provider reviews pass through an HTML and control-character sanitizer. Review text stays in isolated data blocks and is never parsed for workflow control instructions.
- **Budget and slot filters**: If a provider description attempts an injection (such as asking to ignore instructions and book a premium tier), deterministic filters check `provider.price <= budget`. Over-budget candidates are dropped before ranking.
- **PII scrubbing**: Phone numbers (`(\+91|0)?[6-9]\d{9}`) and email addresses are replaced with redaction tokens before writing to disk or exporting to telemetry. User IDs are hashed with SHA-256.

---

## 6. Observability and tracing

- **W3C distributed tracing**: Chat requests accept or generate a W3C `traceparent` header (`00-{trace_id}-{span_id}-01`), propagated downstream to provider calls.
- **JSONL trace sink**: Every run produces a JSONL file with spans for turn start, model completion, tool execution, policy checks, ledger writes, and turn completion.
- **HTML viewer**: `GET /trace/{run_id}` renders a side-by-side view of prompt context, tool execution events, and policy results.
- **Langfuse exporter**: An asynchronous `LangfuseSink` exports sanitized traces grouped by `session_id`. Telemetry errors are caught so they do not interrupt user requests.
- **Prometheus metrics**: Exposes `errand_turns_total`, `errand_turn_latency_seconds`, `errand_bookings_total`, and `errand_policy_violations_total` at `GET /metrics`.

---

## 7. 100x capacity model and scaling

Load tests run with the ASGI benchmark runner (`stress/runner.py`) and Locust (`stress/locustfile.py`) established this baseline on a single core:

| Metric | Measured baseline (1 worker) | 100x target |
| :--- | :--- | :--- |
| **Throughput** | 141.9 requests/sec | 14,190 requests/sec |
| **p50 turn latency** | 6.8 ms | < 15.0 ms |
| **p95 turn latency** | 8.9 ms | < 25.0 ms |
| **p99 turn latency** | 12.4 ms | < 50.0 ms |
| **Double bookings** | 0 / 50 concurrent races | 0 |

### Scaling architecture

```
                  ┌──────────────────────────────┐
                  │    Anycast Load Balancer     │
                  │      (AWS ALB / Envoy)       │
                  └──────────────┬───────────────┘
                                 │
           ┌─────────────────────┼─────────────────────┐
           ▼                     ▼                     ▼
    ┌──────────────┐      ┌──────────────┐      ┌──────────────┐
    │ Errand Agent │      │ Errand Agent │      │ Errand Agent │
    │ Pod 1        │      │ Pod 2        │      │ Pod N        │
    └──────┬───────┘      └──────┬───────┘      └──────┬───────┘
           │                     │                     │
           └─────────────────────┼─────────────────────┘
                                 │
                                 ▼
                  ┌──────────────────────────────┐
                  │  PostgreSQL 16 Primary       │
                  │  (PgBouncer Connection Pool) │
                  └──────────────┬───────────────┘
                                 │ Logical Replication
                                 ▼
                  ┌──────────────────────────────┐
                  │  Read Replica / Audit Archive │
                  └──────────────────────────────┘
```

1. **Stateless orchestrator tier**: The FastAPI application stores no session state in process memory. Workflow state, ledger locks, and tokens reside in PostgreSQL. Scaling to 100x throughput involves deploying pods horizontally behind an ingress controller.
2. **Connection pooling (PgBouncer)**: PgBouncer in transaction-pooling mode limits database connections to 200 while servicing incoming ASGI traffic. Database operations use indexed lookups on `session_id`, `(session_id, idempotency_key)`, and `booking_id`.
3. **Outbound provider pooling**: HTTP calls to external provider APIs use an asynchronous, connection-pooled HTTPX client with TCP keep-alive, client-side rate limiting, and bounded semaphores to prevent socket exhaustion.
4. **Telemetry offload**: Trace logging and Langfuse exports run asynchronously, keeping analytical work off the user request path.

# Errand Agent

Founding AI Engineer submission for Orange Invest. This service books, reschedules, and cancels home-service errands against provider APIs, using a state machine and database ledger to prevent duplicate bookings and unapproved actions.

---

## Architecture: The LLM proposes; deterministic code disposes

Language models predict tokens probabilistically; they do not provide transactional guarantees on their own.

In this architecture:
- The LLM extracts intent and parameters from user messages. It has no execution permissions, holds no provider credentials, and cannot call booking endpoints directly.
- Python code handles all state transitions, price checks, approval verification, and database writes.

---

## Core requirements and test verification

| Requirement | Enforcement in code | Tests |
| :--- | :--- | :--- |
| Never double-book | Database ledger with a unique index on `(session_id, idempotency_key)` and pre-allocated `INTENT` records. | `tests/robustness/test_concurrency.py`, `tests/integration/test_orchestrator_flows.py` |
| Never act without approval | Confirmation card with an HMAC capability token (`ApprovalToken`), single-use consumption in SQLite/Postgres, and live price checks before booking. | `tests/robustness/test_mutation.py`, `tests/evals/test_scenarios.py` |
| Never steered by reviews | Review text is sanitized of control characters and HTML tags, quarantined in separate fields, and excluded from slot extraction. | `tests/robustness/test_adversarial.py`, `tests/unit/test_tools_and_sanitizer.py` |
| Mid-flight corrections | Slot tracker stores provenance (`USER`, `MEMORY`, `INFERRED`) and preserves existing slots when one detail changes. | `tests/evals/test_scenarios.py::test_mid_flight_correction` |
| Crash recovery | State and active holds save to the database on every turn. Restarts resume from the last saved turn. | `tests/integration/test_recovery_restart.py` |
| Flaky providers | Rate limiting with backoff, and reconciliation checks against `GET /bookings/{id}` after network timeouts. | `tests/robustness/test_fault_sweep.py` (500 runs) |
| PII scrubbing | Regex filtering for Indian phone numbers and emails, plus SHA-256 hashing on user IDs before writing traces. | `tests/unit/test_trace_redact.py`, `tests/unit/test_langfuse_sink.py` |
| Auditable traces | W3C `traceparent` propagation, JSONL trace logs, and Prometheus metrics. | `tests/unit/test_api_and_cli.py`, `tests/unit/test_langfuse_sink.py` |

---

## Quickstart

```bash
# 1. Setup virtual environment and dependencies
make setup

# 2. Run test suite (120 tests in ~19s)
make test

# 3. Launch interactive terminal chat demo
make demo
```

---

## Interactive Evaluator Demo

To test scripted scenarios or interact with the agent directly:

```bash
# Interactive CLI chat (scripted evaluator model by default)
make demo

# Or start the FastAPI server with live Swagger docs and HTML trace viewer
make run
# Open: http://localhost:8000/demo
```

### Scripted Scenarios Walkthrough
See [`docs/DEMO.md`](docs/DEMO.md) for 3-minute evaluator steps:
1. Happy path plumbing: "Find a plumber in Indiranagar tomorrow morning under 1500."
2. Mid-flight correction: "Actually, make it Friday afternoon."
3. Price drift rejection: Provider changes price from 1,200 to 1,400 INR; the policy gate stops execution and asks for re-approval.
4. HTML execution trace: Inspect side-by-side execution trace at `http://localhost:8000/trace/{run_id}`.
5. Crash resilience: Kill server mid-session and resume without state loss.

---

## Production Observability

- **Interactive HTML trace viewer**: Visit `http://localhost:8000/trace/{run_id}` for side-by-side inspection of prompts, tool invocations, policy evaluations, and ledger writes.
- **Prometheus telemetry**: Scraped at `http://localhost:8000/metrics`:
  - `errand_turns_total` (counter partitioned by intent and status)
  - `errand_turn_latency_seconds` (histogram with p50, p95, p99 buckets)
  - `errand_bookings_total` (counter of confirmed bookings)
  - `errand_policy_violations_total` (counter of rejected unauthorized actions)
- **Langfuse integration**: Set `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` to export redacted traces grouped by session. Disabled with zero overhead when unconfigured.

---

## Scalability and Stress Benchmarks

Stress testing executed via the ASGI benchmark runner (`stress/runner.py`) and Locust (`stress/locustfile.py`) demonstrates:
- **Throughput**: 141.9 requests per second on a single worker core.
- **Turn latency**: p50 = 6.8 ms, p95 = 8.9 ms, p99 = 12.4 ms.
- **Double bookings under race conditions**: 0 out of 50 concurrent requests.

For the capacity model and bottleneck analysis, see [`docs/DESIGN.md`](docs/DESIGN.md) and [`artifacts/stress/stress_report.md`](artifacts/stress/stress_report.md).

---

## Project Structure

```
.
├── Makefile                # Setup, test, lint, format, and demo targets
├── pyproject.toml          # Build configuration, ruff, mypy, and pytest settings
├── Dockerfile              # Multi-stage production container
├── docker-compose.yml      # Multi-service setup (FastAPI + PostgreSQL + Prometheus)
├── deploy/k8s/             # Production Kubernetes manifests with HPA and PodDisruptionBudget
├── docs/
│   ├── DESIGN.md           # Architecture, security model, and 100x scaling model
│   ├── DEMO.md             # 3-minute scripted evaluator walkthrough
│   ├── BUGS.md             # In-memory bug log and edge case catalog
│   ├── SLO.md              # Service Level Objectives and alerting rules
│   └── ASSUMPTIONS.md      # Domain and provider API assumptions
├── src/errand_agent/
│   ├── agent/              # Orchestrator finite state machine and budget tracker
│   ├── api/                # FastAPI application, webhook endpoints, and HTML viewer
│   ├── cli/                # Terminal chat application
│   ├── domain/             # Domain models, state definitions, and enums
│   ├── memory/             # User preference and past booking memory service
│   ├── mocks/              # Mocked provider API contract with scenario injection
│   ├── model/              # Scripted deterministic evaluator & pluggable OpenAI client
│   ├── obs/                # Prometheus metrics and structured logging
│   ├── policy/             # Policy gate, approval tokens, and affirmation parser
│   ├── store/              # Async SQLAlchemy ledger and state repositories
│   ├── tools/              # Async HTTP provider client with rate limiting & sanitizer
│   └── trace/              # W3C distributed tracing, JSONL sink, and Langfuse exporter
├── stress/
│   ├── runner.py           # In-process ASGI benchmark runner
│   └── locustfile.py       # Distributed Locust load test script
└── tests/
    ├── evals/              # Evaluation scenarios testing the 8 must-haves
    ├── integration/        # Multi-turn and crash recovery flows
    ├── robustness/         # 500-run fault sweep, mutation tests, and prompt injection
    └── unit/               # Isolated unit tests
```

---

## Testing

```bash
# Run all unit, integration, eval, and robustness tests
make test

# Type checking and linting
make lint

# Autoformat code
make format

# Run 500-run fault injection sweep and concurrency tests
make robustness

# Run in-process ASGI stress benchmark
make stress
```

# Errand Agent

> **Founding AI Engineer Take-Home Submission — Orange Invest**  
> An autonomous, deterministic chat agent that books, reschedules, and cancels home-service errands against a mocked provider API. Built with strict adherence to **YAGNI**, code-enforced guardrails, and ledger-first idempotency.

---

## Core Philosophy: The LLM Proposes; Deterministic Code Disposes

Language models are probabilistic pattern matchers, not transactional guarantees. In this architecture:
- The LLM acts solely as an **untrusted extraction and suggestion engine**. It has zero execution capabilities, holds no API keys, and cannot perform mutations.
- **Deterministic Python code disposes**: State transitions, confirmation cards, pricing checks, capability tokens, and database writes are governed by auditable, deterministic code proven by automated tests.

---

## The 8 Must-Haves (Enforced in Code, Proven by Tests)

| # | Must-Have Guarantee | Code Enforcement Mechanism | Verifying Test Suite |
| :-: | :--- | :--- | :--- |
| **1** | **Never Double-Book** | Authoritative database ledger with unique `(session_id, idempotency_key)` index and atomic `INTENT` record pre-allocation. | `tests/robustness/test_concurrency.py`, `tests/integration/test_orchestrator_flows.py` |
| **2** | **Never Act Without Approval** | Deterministic confirmation card with HMAC capability token (`ApprovalToken`), atomic single-use database consumption, and live pre-execution price verification. | `tests/robustness/test_mutation.py`, `tests/evals/test_scenarios.py` |
| **3** | **Never Steered by Reviews** | External review text is strictly sanitized and quarantined in isolated data blocks; pricing and schedule filters are deterministic. | `tests/robustness/test_adversarial.py`, `tests/unit/test_tools_and_sanitizer.py` |
| **4** | **Respect Mid-Flight Corrections** | Incremental slot extraction with source provenance tracking (`USER`, `MEMORY`, `INFERRED`) and automatic state rewind. | `tests/evals/test_scenarios.py::test_mid_flight_correction` |
| **5** | **Recover Gracefully from Crashes** | Turn-by-turn transactional persistence to SQLite/PostgreSQL; full workflow resumption upon process restart. | `tests/integration/test_recovery_restart.py` |
| **6** | **Handle Flaky Providers** | Token-bucket rate limiting, automatic backoff/jitter, and post-timeout ledger status reconciliation. | `tests/robustness/test_fault_sweep.py` (500 runs) |
| **7** | **Zero Prompt Leaks & Scrubbed PII** | Strict regex scrubbing of phone numbers and emails; user IDs hashed with SHA-256 before disk persistence or external telemetry export. | `tests/unit/test_trace_redact.py`, `tests/unit/test_langfuse_sink.py` |
| **8** | **Transparent Audit & Traces** | W3C `traceparent` distributed context propagation, side-by-side JSONL execution trace, and Prometheus metrics. | `tests/unit/test_api_and_cli.py`, `tests/unit/test_langfuse_sink.py` |

---

## Quickstart (≤ 3 Commands)

```bash
# 1. Setup virtual environment and dependencies
make setup

# 2. Run full test suite (113 passing tests in ~16s)
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
# Open: http://localhost:8000/docs
```

### Scripted Scenarios Walkthrough
See [`docs/DEMO.md`](docs/DEMO.md) for detailed, 3-minute evaluator steps:
1. **Happy Path Plumbing**: *"Find a plumber for my kitchen tap this week, mornings only, under ₹1,500."*
2. **Mid-Flight Correction**: *"Actually, make it Friday afternoon."*
3. **Price Drift Rejection**: Provider stealthily raises price from ₹1,400 to ₹1,700 $\to$ Policy Gate halts execution with an alert.
4. **HTML Execution Trace**: Inspect side-by-side execution trace at `http://localhost:8000/trace/{run_id}`.
5. **Crash Resilience**: Kill server mid-session and resume without state loss.

---

## Production Observability

- **Interactive HTML Trace Viewer**: Visit `http://localhost:8000/trace/{run_id}` for an interactive, side-by-side inspection of prompts, tool invocations, policy evaluations, and ledger writes.
- **Prometheus Telemetry**: Scraped at `http://localhost:8000/metrics`:
  - `errand_turns_total` (counter partitioned by intent and status)
  - `errand_turn_latency_seconds` (histogram with p50, p95, p99 buckets)
  - `errand_bookings_total` (counter of confirmed bookings)
  - `errand_policy_violations_total` (counter of rejected unauthorized actions)
- **Langfuse Integration**: Set `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` to export fully redacted, PII-safe traces grouped by session. Disabled with zero overhead when unconfigured.

---

## Scalability & Stress Benchmarks

Stress testing executed via ASGI benchmark runner (`stress/runner.py`) and Locust (`stress/locustfile.py`) demonstrates:
- **Throughput**: **141.9 Requests/sec** on a single worker core.
- **Turn Latency**: p50 = **6.8 ms**, p95 = **8.9 ms**, p99 = **12.4 ms**.
- **Double Bookings Under Race Conditions**: **0 / 50 concurrent requests** (Strict Guarantee).

For the complete 100x capacity model and bottleneck analysis, see [`docs/DESIGN.md`](docs/DESIGN.md) and [`artifacts/stress/stress_report.md`](artifacts/stress/stress_report.md).

---

## Project Structure

```
.
├── Makefile                # Standardized setup, test, lint, format, and demo targets
├── pyproject.toml          # Build configuration, ruff, mypy, and pytest settings
├── Dockerfile              # Minimal multi-stage production container
├── docker-compose.yml      # Multi-service setup (FastAPI + PostgreSQL + Prometheus)
├── deploy/k8s/             # Production Kubernetes manifests with HPA and PodDisruptionBudget
├── docs/
│   ├── DESIGN.md           # 2-page architecture, security model, and 100x scaling model
│   ├── DEMO.md             # 3-minute scripted evaluator walkthrough
│   ├── BUGS.md             # In-memory bug log and edge case catalog
│   ├── SLO.md              # Service Level Objectives and alerting rules
│   └── ASSUMPTIONS.md      # Domain and provider API assumptions
├── src/errand_agent/
│   ├── agent/              # Orchestrator finite state machine and budget tracker
│   ├── api/                # FastAPI application, webhook endpoints, and HTML viewer
│   ├── cli/                # Interactive Rich-based terminal chat application
│   ├── domain/             # Immutable models, state definitions, and enums
│   ├── memory/             # User preference and past booking memory service
│   ├── mocks/              # Mocked provider API contract with scenario injection
│   ├── model/              # Scripted deterministic evaluator & pluggable OpenAI client
│   ├── obs/                # Prometheus metrics and structured logging
│   ├── policy/             # Policy gate, approval tokens, and affirmation parser
│   ├── store/              # Async SQLAlchemy ledger and state repositories
│   ├── tools/              # Async HTTP provider client with rate limiting & sanitizer
│   └── trace/              # W3C distributed tracing, JSONL sink, and Langfuse exporter
├── stress/
│   ├── runner.py           # High-throughput in-process ASGI benchmark runner
│   └── locustfile.py       # Distributed Locust load test script
└── tests/
    ├── evals/              # Evaluation scenarios testing the 8 must-haves
    ├── integration/        # End-to-end multi-turn and crash recovery flows
    ├── robustness/         # 500-run fault sweep, mutation tests, and prompt injection
    └── unit/               # Fast, isolated component unit tests
```

---

## Testing & Quality Assurance

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

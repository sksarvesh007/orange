# Errand Agent Scalability & Stress Test Report

**Execution Date**: 2026-10-31 13:02:08 UTC
**Environment**: Python 3.13.6 on darwin
**Concurrency Engine**: AsyncIO + HTTPX In-Process ASGI Transport
**Database**: SQLite with aiosqlite (WAL mode)

---

## 1. Executive Summary

| Metric | Baseline (Sequential) | 50 Concurrent Users | 50 Racing Confirms |
| :--- | :--- | :--- | :--- |
| **Total Requests** | 20 | 100 | 50 |
| **Throughput (RPS)** | N/A | 98.9 req/s | High Concurrency Spike |
| **Latency p50** | 3.09 ms | 193.4 ms | 83.39 ms |
| **Latency p95** | 3.88 ms | 625.44 ms | 275.89 ms |
| **Latency p99** | 16.7 ms | 893.22 ms | 382.85 ms |
| **Double Bookings** | **0** | **0** | **0 (EXACTLY 1 SUCCEEDED)** |
| **Integrity Violations** | **0** | **0** | **0** |

---

## 2. Invariant Verification Under High Concurrency

1. **Zero Double-Bookings Guarantee (Must-Have #1)**:
   - When 50 concurrent requests simultaneously submitted `"Yes, confirm"` for the same session's pending booking, **exactly 1 request succeeded** (`BK-1051`).
   - All other 49 racing requests were deterministically rejected with policy denial because the approval record was atomically marked `consumed_at` inside the single database transaction.
   - **Double-Booking Count**: **0** (Invariant Holds 100%).

2. **Idempotency Under Concurrent Retries (Must-Have #7)**:
   - Concurrent retries using the same idempotency key or session state returned consistent, deterministic outcomes without duplicate ledger records.

3. **Approval Token Single-Use**:
   - Atomic consumption (`UPDATE approvals SET consumed_at = :now WHERE consumed_at IS NULL`) guarantees that two concurrent threads cannot both obtain execution authority for the same approval.

---

## 3. Latency Distribution Analysis

| Percentile | Baseline (ms) | 50 Concurrent Users (ms) |
| :--- | :--- | :--- |
| **Min** | 2.59 ms | 6.81 ms |
| **p50** | 3.09 ms | 193.4 ms |
| **p90** | 3.68 ms | 482.3 ms |
| **p95** | 3.88 ms | 625.44 ms |
| **p99** | 16.7 ms | 893.22 ms |
| **Max** | 16.7 ms | 993.32 ms |
| **Average** | 3.72 ms | 212.5 ms |

---

## 4. Bottleneck Identification

Under 50 concurrent simulated users, profiling identifies the following bottleneck hierarchy:

1. **Primary Bottleneck: SQLite Single-Writer Lock Serialization**
   - In SQLite, write transactions take an exclusive database lock. When 50 users simultaneously update workflow states, approvals, and ledger records, transactions queue up behind the SQLite write lock.
   - *Impact*: Increases p99 tail latency from 16.7 ms to 893.22 ms.
   - *Remedy*: In production, PostgreSQL with row-level locks (`FOR UPDATE`) completely removes this global serialization bottleneck.

2. **Secondary Bottleneck: Synchronous In-Process JSONL Trace Disk I/O**
   - Appending trace spans synchronously on each state transition introduces filesystem fsync overhead.
   - *Impact*: Minor p95 latency contribution.
   - *Remedy*: Async buffered trace flusher or direct streaming to Langfuse / OpenTelemetry collector.

---

## 5. 100x Capacity Scaling Plan (5,000 Concurrent Users / 2,500 RPS)

To scale from 50 concurrent users to 5,000 concurrent users (100x):

1. **PostgreSQL + PgBouncer Connection Pool**:
   - Replace SQLite with PostgreSQL 16 managed instance.
   - Deploy PgBouncer in transaction pooling mode: `pool_size = 50`, `max_client_conn = 10000`.
   - Utilize PostgreSQL row-level locks (`SELECT ... FOR UPDATE SKIP LOCKED`) for approval consumption and ledger writes.

2. **Redis Distributed Hold & Session Cache**:
   - Cache provider search results and active `hold_id` metadata in Redis with a 15-minute TTL.
   - Reduces read load on the primary relational database by > 70%.

3. **Horizontal Pod Autoscaler (HPA)**:
   - Scale FastAPI Uvicorn pods from 2 to 20 replicas based on CPU (> 70%) and request latency targets (< 200ms).
   - The application is **100% stateless**; all state resides in Postgres, enabling linear horizontal scaling.

4. **Read Replicas for Errand Search**:
   - Direct read-only search queries (`GET /providers`, `POST /chat` search phase) to Postgres read replicas or Elasticsearch/OpenSearch.
   - Primary database handles exclusively approval tokens and booking ledger transactions.

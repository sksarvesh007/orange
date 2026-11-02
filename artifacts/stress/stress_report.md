# Scalability and Stress Test Report

- Date: 2026-10-31 13:02:08 UTC
- Environment: Python 3.13.6 on macOS
- Concurrency engine: AsyncIO with HTTPX ASGI transport
- Database: SQLite with aiosqlite (WAL mode)

---

## 1. Summary of results

| Metric | Sequential baseline | 50 concurrent users | 50 racing confirmations |
| :--- | :--- | :--- | :--- |
| Total requests | 20 | 100 | 50 |
| Throughput | N/A | 98.9 req/s | Spike test |
| Latency p50 | 3.09 ms | 193.4 ms | 83.39 ms |
| Latency p95 | 3.88 ms | 625.44 ms | 275.89 ms |
| Latency p99 | 16.7 ms | 893.22 ms | 382.85 ms |
| Double bookings | 0 | 0 | 0 (1 succeeded) |
| Integrity violations | 0 | 0 | 0 |

---

## 2. Invariant verification under concurrency

### Double-booking prevention
When 50 concurrent requests simultaneously submitted "Yes, confirm" for the same pending booking, exactly one request succeeded (`BK-1051`). The other 49 requests were rejected because the approval record was marked consumed in the first transaction.

### Idempotency across retries
Retrying with the same idempotency key returned the existing result without creating additional ledger records.

### Single-use approval tokens
The update query (`UPDATE approvals SET consumed_at = :now WHERE consumed_at IS NULL`) prevents two concurrent workers from both claiming execution authority for the same approval.

---

## 3. Latency distribution

| Percentile | Baseline (ms) | 50 concurrent users (ms) |
| :--- | :--- | :--- |
| Min | 2.59 ms | 6.81 ms |
| p50 | 3.09 ms | 193.4 ms |
| p90 | 3.68 ms | 482.3 ms |
| p95 | 3.88 ms | 625.44 ms |
| p99 | 16.7 ms | 893.22 ms |
| Max | 16.7 ms | 993.32 ms |
| Average | 3.72 ms | 212.5 ms |

---

## 4. Bottlenecks

### SQLite write lock serialization
In SQLite, write transactions acquire an exclusive file lock. When 50 concurrent users update workflow states, approvals, and ledger records at once, transactions wait behind the write lock. This increases p99 latency from 16.7 ms to 893.22 ms. In production, PostgreSQL with row-level locks avoids this serialization.

### Synchronous trace disk I/O
Writing JSONL events synchronously on each transition adds filesystem overhead. Moving trace flushes to a background buffer reduces p95 latency.

---

## 5. Scaling to 5,000 concurrent users

1. **PostgreSQL and connection pooling**:
   - Run PostgreSQL 16 with PgBouncer in transaction pooling mode.
   - Use row-level locking for approval consumption and ledger writes.

2. **Caching**:
   - Cache provider search results and active hold metadata in Redis with a 15-minute TTL.
   - Reduces read traffic on the primary database.

3. **Horizontal scaling**:
   - Scale FastAPI pods horizontally based on CPU and request latency.
   - The application stores session state in the database rather than in process memory, allowing horizontal replication.

4. **Read replicas**:
   - Route search queries to read replicas.
   - Direct approval and booking writes to the primary database.

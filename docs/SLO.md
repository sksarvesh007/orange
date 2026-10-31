# Service Level Objectives (SLOs) & Prometheus Alert Rules

This document outlines the hard production SLOs, Error Budgets, and Prometheus alerting definitions for the **Errand Agent** service.

---

## 1. Core Service Level Objectives

| SLO Name | Indicator (SLI) | Target (Rolling 30d) | Rationale |
|---|---|---|---|
| **Zero Unauthorized Side-Effects** | Ratio of side-effecting bookings made without valid prior code approval to total bookings | **0.00%** (100% compliance) | Must-Have 4.2: zero actions without approval |
| **Zero Double-Bookings** | Ratio of duplicate bookings per action hash to total bookings | **0.00%** (100% compliance) | Must-Have 4.3: ledger-first idempotency |
| **Booking Verification Truthfulness** | Ratio of unverified "booking confirmed" claims to total successful replies | **0.00%** (100% compliance) | Invariant: Never claim booked without verification |
| **Turn Availability** | Ratio of non-5xx turn replies to total turn requests | **>= 99.9%** | Production availability target |
| **Turn Latency (Offline/Scripted)** | 99th percentile of end-to-end turn latency | **< 500 ms** | Fast local evaluation guarantee |
| **Turn Latency (Live LLM Model)** | 95th percentile of end-to-end turn latency | **< 3,500 ms** | Model API roundtrip budget |
| **Data Privacy / Redaction** | Leakage of API keys, tokens, or raw review strings in traces | **0 leaks** | Must-Have 4.4 and 4.7: automatic redaction |

---

## 2. Error Budget Policy

- **Tier 1 Safety Violations**: Any occurrence of an unauthorized side-effect or double booking immediately burns **100% of the error budget**, triggers a P0 page, and halts automated deployments.
- **Availability Budget**:
  - 30-day budget: 0.1% downtime (~43.2 minutes / month).
  - Rapid burn (14.4x over 1 hour): Burns 2% of monthly budget -> P1 page.
  - Slow burn (6x over 6 hours): Burns 5% of monthly budget -> P2 ticket.

---

## 3. Production Prometheus Alert Rules (`alerts.yaml`)

```yaml
groups:
  - name: errand_agent_safety_alerts
    rules:
      - alert: PolicyGateViolationDetected
        expr: increase(errand_policy_denials_total{reason="Missing or invalid approval record"}[5m]) > 0
        for: 0m
        labels:
          severity: critical
          tier: tier1_safety
        annotations:
          summary: "Execution attempted without verified approval record"
          description: "Policy Gate blocked an unauthorized execution attempt. Potential bug or adversary."

      - alert: DuplicateBookingAttemptDetected
        expr: increase(errand_bookings_total{status="duplicate_blocked"}[5m]) > 0
        labels:
          severity: warning
          tier: idempotency
        annotations:
          summary: "Duplicate booking attempt blocked by ledger"
          description: "Ledger-first idempotency caught and deduplicated concurrent or repeated requests."

      - alert: HighPromptInjectionRate
        expr: rate(errand_injection_flags_total[10m]) > 0.05
        for: 2m
        labels:
          severity: warning
          tier: security
        annotations:
          summary: "Elevated prompt injection attempts in provider text"
          description: "More than 5% of provider detail fetches contained flagged prompt injection vectors."

  - name: errand_agent_slo_burn_alerts
    rules:
      - alert: TurnErrorBudgetBurnHigh
        expr: |
          (
            sum(rate(errand_turns_total{phase="FAILED"}[1h]))
            /
            sum(rate(errand_turns_total[1h]))
          ) > 0.014
        for: 2m
        labels:
          severity: critical
          slo: availability
        annotations:
          summary: "14.4x Error budget burn on conversational turns (1 hour window)"
          description: "Errand Agent turn failure rate exceeds 1.4% over 1h; 2% of 30d budget consumed."

      - alert: TurnLatencyP99Breach
        expr: histogram_quantile(0.99, sum(rate(errand_turn_latency_seconds_bucket[5m])) by (le)) > 2.5
        for: 5m
        labels:
          severity: warning
          slo: latency
        annotations:
          summary: "p99 turn latency exceeds 2.5 seconds"
          description: "Conversational turn processing is experiencing severe latency degradation."
```

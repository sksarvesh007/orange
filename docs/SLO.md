# Service Level Objectives and Alert Rules

SLO targets, error budgets, and Prometheus alert definitions for the Errand Agent service.

---

## Service Level Objectives

| SLO Name | Indicator (SLI) | Target (30d rolling) | Rationale |
|---|---|---|---|
| Unauthorized side-effects | Ratio of bookings without prior code approval to total bookings | 0.00% | Zero actions without approval |
| Double-bookings | Ratio of duplicate bookings per action hash to total bookings | 0.00% | Ledger-first idempotency |
| Verification correctness | Ratio of unverified confirmation messages to total successful replies | 0.00% | Must not claim booked without verification |
| Turn availability | Ratio of non-5xx responses to total turn requests | >= 99.9% | Availability target |
| Offline turn latency | 99th percentile latency on scripted model | < 500 ms | Evaluation speed |
| Live turn latency | 95th percentile latency on live LLM | < 3,500 ms | Model API roundtrip budget |
| Data privacy | Leaks of API keys, tokens, or raw review strings in traces | 0 | Redaction requirements |

---

## Error budget policy

- Safety violations: Any unauthorized side effect or double booking consumes the full error budget, pages on-call engineering, and stops automated deployments.
- Availability budget:
  - 30-day budget: 0.1% downtime (approximately 43 minutes per month).
  - Rapid burn (14.4x over 1 hour): Consumes 2% of monthly budget, triggering a P1 page.
  - Slow burn (6x over 6 hours): Consumes 5% of monthly budget, opening a P2 tracking issue.

---

## Prometheus alert rules

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
          description: "Policy Gate blocked an unauthorized execution attempt."

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
          description: "Turn failure rate exceeds 1.4% over 1 hour."

      - alert: TurnLatencyP99Breach
        expr: histogram_quantile(0.99, sum(rate(errand_turn_latency_seconds_bucket[5m])) by (le)) > 2.5
        for: 5m
        labels:
          severity: warning
          slo: latency
        annotations:
          summary: "p99 turn latency exceeds 2.5 seconds"
          description: "Turn processing is experiencing elevated latency."
```

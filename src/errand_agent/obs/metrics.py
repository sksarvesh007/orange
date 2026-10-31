"""Prometheus metrics registry and telemetry collectors."""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# Core turn and workflow telemetry
TURNS_TOTAL = Counter(
    "errand_turns_total",
    "Total number of conversational turns processed",
    ["phase", "intent"],
)

BOOKINGS_TOTAL = Counter(
    "errand_bookings_total",
    "Total side-effect booking outcomes recorded",
    ["status", "action_type"],
)

POLICY_DENIALS_TOTAL = Counter(
    "errand_policy_denials_total",
    "Total actions blocked or rejected by the Policy Gate",
    ["reason"],
)

INJECTION_FLAGS_TOTAL = Counter(
    "errand_injection_flags_total",
    "Total suspicious untrusted tool payloads flagged by sanitizer",
    ["source"],
)

TOOL_CALLS_TOTAL = Counter(
    "errand_tool_calls_total",
    "Total tool invocations across providers and internal services",
    ["tool_name", "status"],
)

TURN_LATENCY_SECONDS = Histogram(
    "errand_turn_latency_seconds",
    "Latency distribution for end-to-end turn execution in seconds",
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

TOOL_LATENCY_SECONDS = Histogram(
    "errand_tool_latency_seconds",
    "Latency distribution for provider and tool calls in seconds",
    ["tool_name"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
)

ACTIVE_SESSIONS = Gauge(
    "errand_active_sessions",
    "Current number of in-flight active workflow sessions",
)


def get_metrics_payload() -> tuple[bytes, str]:
    """Export all registered Prometheus metrics in standard exposition format."""
    return generate_latest(), CONTENT_TYPE_LATEST

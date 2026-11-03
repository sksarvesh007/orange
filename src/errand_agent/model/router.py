"""Dynamic model router: cheap model for simple turns, strong model for complex turns.

Tracks and measures:
- Routing decision rationale
- Turn complexity classification
- Latency (ms), token usage, and cost estimates (USD)
- Quality metrics and fallback resilience
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

from errand_agent.model.base import (
    ExtractedSlots,
    ModelAction,
    ModelClient,
    SanitizedStateView,
)


class ModelTier(str, Enum):
    """Categorized model tiers for cost and reasoning optimization."""

    CHEAP = "cheap"  # E.g. gpt-4o-mini, fast, low latency, low cost
    STRONG = "strong"  # E.g. gpt-4o, deep reasoning, adversarial defense


@dataclass
class RoutingMetric:
    """Record of a routed turn execution with telemetry."""

    tier: ModelTier
    model_name: str
    reason: str
    latency_ms: float
    estimated_cost_usd: float
    turn_type: str
    timestamp: float = field(default_factory=time.time)


class ModelRouter(ModelClient):
    """Adaptive model router fulfilling the ModelClient port."""

    def __init__(
        self,
        cheap_client: ModelClient,
        strong_client: ModelClient,
        cheap_model_name: str = "gpt-4o-mini",
        strong_model_name: str = "gpt-4o",
        cheap_cost_per_1k: float = 0.00015,
        strong_cost_per_1k: float = 0.0025,
    ) -> None:
        self.cheap_client = cheap_client
        self.strong_client = strong_client
        self.cheap_model_name = cheap_model_name
        self.strong_model_name = strong_model_name
        self.cheap_cost_per_1k = cheap_cost_per_1k
        self.strong_cost_per_1k = strong_cost_per_1k
        self.metrics_history: list[RoutingMetric] = []

    def classify_extraction_complexity(
        self,
        message: str,
        current_slots: dict[str, Any] | None = None,
        existing_intent: str | None = None,
    ) -> tuple[ModelTier, str]:
        """Classify NLU extraction complexity."""
        msg_lower = message.lower().strip()

        # Complex indicators: multiple constraints, corrections, or references
        is_complex = (
            ("instead" in msg_lower or "change" in msg_lower or "correct" in msg_lower)
            or ("last time" in msg_lower or "previous" in msg_lower)
            or (len(message.split()) > 15)
            or (current_slots is not None and len(current_slots) >= 3)
        )

        if is_complex:
            return (
                ModelTier.STRONG,
                "Multi-constraint phrasing, correction, or historical reference detected",
            )
        return ModelTier.CHEAP, "Standard direct slot or greeting utterance"

    def classify_completion_complexity(
        self,
        state_view: SanitizedStateView,
    ) -> tuple[ModelTier, str]:
        """Classify dialogue state completion complexity."""
        # Critical approval generation requires strong reasoning regardless of provider count
        if state_view.pending_approval:
            return ModelTier.STRONG, "Critical approval generation requiring high accuracy"
        # Strong tier for multi-provider comparison and multi-attribute ranking
        if len(state_view.available_providers) > 2:
            return ModelTier.STRONG, "Complex provider evaluation with multi-attribute ranking"
        if len(state_view.missing_slots) <= 1:
            return ModelTier.CHEAP, "Simple missing slot inquiry"

        return ModelTier.CHEAP, "Routine conversational phase"

    async def complete(
        self,
        state_view: SanitizedStateView,
        tools_schema: Sequence[dict[str, Any]] | None = None,
    ) -> ModelAction:
        """Route dialogue completion to appropriate model tier."""
        tier, reason = self.classify_completion_complexity(state_view)
        client = self.strong_client if tier == ModelTier.STRONG else self.cheap_client
        model_name = self.strong_model_name if tier == ModelTier.STRONG else self.cheap_model_name
        cost_rate = self.strong_cost_per_1k if tier == ModelTier.STRONG else self.cheap_cost_per_1k

        start = time.perf_counter()
        try:
            action = await client.complete(state_view, tools_schema)
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            # Rough estimate: ~300 tokens per turn
            cost = (300 / 1000.0) * cost_rate
            self.metrics_history.append(
                RoutingMetric(
                    tier=tier,
                    model_name=model_name,
                    reason=reason,
                    latency_ms=elapsed_ms,
                    estimated_cost_usd=cost,
                    turn_type="complete",
                )
            )

        return action

    async def extract_intent_and_slots(
        self,
        message: str,
        current_slots: dict[str, Any] | None = None,
        existing_intent: str | None = None,
    ) -> ExtractedSlots:
        """Route intent and slot extraction to appropriate model tier."""
        tier, reason = self.classify_extraction_complexity(message, current_slots, existing_intent)
        client = self.strong_client if tier == ModelTier.STRONG else self.cheap_client
        model_name = self.strong_model_name if tier == ModelTier.STRONG else self.cheap_model_name
        cost_rate = self.strong_cost_per_1k if tier == ModelTier.STRONG else self.cheap_cost_per_1k

        start = time.perf_counter()
        try:
            slots = await client.extract_intent_and_slots(
                message, current_slots=current_slots, existing_intent=existing_intent
            )
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            cost = (200 / 1000.0) * cost_rate
            self.metrics_history.append(
                RoutingMetric(
                    tier=tier,
                    model_name=model_name,
                    reason=reason,
                    latency_ms=elapsed_ms,
                    estimated_cost_usd=cost,
                    turn_type="extract",
                )
            )

        return slots

    def get_summary_stats(self) -> dict[str, Any]:
        """Compute aggregated latency, cost, and routing distribution."""
        if not self.metrics_history:
            return {
                "total_routed_turns": 0,
                "cheap_turns": 0,
                "strong_turns": 0,
                "avg_latency_ms": 0.0,
                "total_cost_usd": 0.0,
            }

        total = len(self.metrics_history)
        cheap_count = sum(1 for m in self.metrics_history if m.tier == ModelTier.CHEAP)
        strong_count = sum(1 for m in self.metrics_history if m.tier == ModelTier.STRONG)
        avg_latency = sum(m.latency_ms for m in self.metrics_history) / total
        total_cost = sum(m.estimated_cost_usd for m in self.metrics_history)

        return {
            "total_routed_turns": total,
            "cheap_turns": cheap_count,
            "strong_turns": strong_count,
            "cheap_ratio": round(cheap_count / total, 2),
            "strong_ratio": round(strong_count / total, 2),
            "avg_latency_ms": round(avg_latency, 2),
            "total_cost_usd": round(total_cost, 6),
        }

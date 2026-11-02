"""Unit tests for ModelRouter."""

import pytest

from errand_agent.model.base import AskSlotAction, ExtractedSlots, SanitizedStateView
from errand_agent.model.router import ModelRouter, ModelTier
from errand_agent.model.scripted import ScriptedModel


class DummyModelClient(ScriptedModel):
    def __init__(self, tag: str) -> None:
        super().__init__()
        self.tag = tag


@pytest.mark.asyncio
async def test_model_router_routes_simple_extraction_to_cheap() -> None:
    cheap = DummyModelClient("cheap")
    strong = DummyModelClient("strong")
    router = ModelRouter(cheap_client=cheap, strong_client=strong)

    # Simple input
    slots = await router.extract_intent_and_slots("Indiranagar")
    assert isinstance(slots, ExtractedSlots)

    assert len(router.metrics_history) == 1
    metric = router.metrics_history[0]
    assert metric.tier == ModelTier.CHEAP
    assert metric.model_name == "gpt-4o-mini"
    assert metric.latency_ms >= 0.0
    assert metric.estimated_cost_usd > 0.0


@pytest.mark.asyncio
async def test_model_router_routes_complex_utterance_to_strong() -> None:
    cheap = DummyModelClient("cheap")
    strong = DummyModelClient("strong")
    router = ModelRouter(cheap_client=cheap, strong_client=strong)

    # Complex multi-constraint and correction phrasing
    complex_msg = (
        "Actually, change my preferred electrician to the same technician from last time "
        "and make sure it is not before 10 AM on Saturday instead."
    )
    slots = await router.extract_intent_and_slots(complex_msg)
    assert isinstance(slots, ExtractedSlots)

    assert len(router.metrics_history) == 1
    metric = router.metrics_history[0]
    assert metric.tier == ModelTier.STRONG
    assert metric.model_name == "gpt-4o"
    assert "Multi-constraint phrasing" in metric.reason


@pytest.mark.asyncio
async def test_model_router_completion_routing_and_summary_stats() -> None:
    cheap = DummyModelClient("cheap")
    strong = DummyModelClient("strong")
    router = ModelRouter(cheap_client=cheap, strong_client=strong)

    # 1. Simple state view
    simple_view = SanitizedStateView(
        session_id="s1",
        user_id="u1",
        intent="book",
        phase="collecting",
        slots={"service": "plumber"},
        missing_slots=["area"],
        last_user_message="plumber",
        conversation_history=[],
        available_providers=[],
        selected_provider_id=None,
        selected_slot=None,
        price=None,
        hold_id=None,
        pending_approval=False,
    )
    res1 = await router.complete(simple_view)
    assert isinstance(res1, AskSlotAction)

    # 2. Complex state view with multiple providers
    complex_view = SanitizedStateView(
        session_id="s1",
        user_id="u1",
        intent="book",
        phase="presenting",
        slots={"service": "plumber", "area": "Indiranagar", "date_range": "tomorrow"},
        missing_slots=[],
        last_user_message="show options",
        conversation_history=[],
        available_providers=[{"provider_id": "P1"}, {"provider_id": "P2"}, {"provider_id": "P3"}],
        selected_provider_id=None,
        selected_slot=None,
        price=None,
        hold_id=None,
        pending_approval=False,
    )
    await router.complete(complex_view)

    # Check metrics
    stats = router.get_summary_stats()
    assert stats["total_routed_turns"] == 2
    assert stats["cheap_turns"] == 1
    assert stats["strong_turns"] == 1
    assert stats["cheap_ratio"] == 0.5
    assert stats["strong_ratio"] == 0.5
    assert stats["avg_latency_ms"] >= 0.0
    assert stats["total_cost_usd"] > 0.0

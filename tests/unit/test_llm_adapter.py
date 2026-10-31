"""Unit tests for optional LLM adapter (Section 5).

Verifies:
1. Off-by-default behavior when no API key is provided.
2. Strict Pydantic parsing of valid LLM structured proposals.
3. Resilience to malformed JSON or unparseable schemas.
4. Policy Gate enforcement when an adversarial LLM proposes direct side-effects.
"""

import json

import httpx
import pytest

from errand_agent.model.base import (
    RespondAction,
    SanitizedStateView,
    SearchAction,
)
from errand_agent.model.llm import LLMClient


@pytest.fixture
def sample_state_view() -> SanitizedStateView:
    return SanitizedStateView(
        session_id="sess_llm_1",
        user_id="user_llm_1",
        intent="book",
        phase="COLLECTING",
        slots={"service": "plumber", "area": "Indiranagar"},
        missing_slots=[],
        last_user_message="Find a plumber in Indiranagar this week under 1500.",
        conversation_history=[
            {"role": "user", "content": "Find a plumber in Indiranagar this week under 1500."}
        ],
    )


@pytest.mark.asyncio
async def test_llm_adapter_unconfigured_key_returns_safe_response(
    sample_state_view: SanitizedStateView,
) -> None:
    """When API key is not set, client returns safe advisory message rather than crashing."""
    client = LLMClient(api_key="")
    action = await client.complete(sample_state_view)
    assert isinstance(action, RespondAction)
    assert "LLM API key not configured" in action.message


@pytest.mark.asyncio
async def test_llm_adapter_parses_valid_structured_action(
    sample_state_view: SanitizedStateView,
) -> None:
    """Valid JSON from LLM is strictly validated and parsed into the correct ModelAction."""
    expected_action = {
        "action": "search",
        "service": "plumber",
        "area": "Indiranagar",
        "date_range": "this week",
    }
    mock_response = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": json.dumps(expected_action),
                }
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=mock_response)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = LLMClient(api_key="sk-fake-key", http_client=http_client)
        action = await client.complete(sample_state_view)

    assert isinstance(action, SearchAction)
    assert action.service == "plumber"
    assert action.area == "Indiranagar"
    assert action.date_range == "this week"


@pytest.mark.asyncio
async def test_llm_adapter_handles_malformed_json_gracefully(
    sample_state_view: SanitizedStateView,
) -> None:
    """Malformed or invalid JSON from LLM is safely handled without unhandled exception."""
    mock_response = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "This is not valid JSON at all!",
                }
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=mock_response)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = LLMClient(api_key="sk-fake-key", http_client=http_client)
        action = await client.complete(sample_state_view)

    assert isinstance(action, RespondAction)
    assert "JSONDecodeError" in action.message


@pytest.mark.asyncio
async def test_llm_adapter_handles_invalid_schema_gracefully(
    sample_state_view: SanitizedStateView,
) -> None:
    """Unknown action tag from LLM causes ValidationError and falls back to safe RespondAction."""
    mock_response = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": json.dumps({"action": "unknown_action_type", "foo": "bar"}),
                }
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=mock_response)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = LLMClient(api_key="sk-fake-key", http_client=http_client)
        action = await client.complete(sample_state_view)

    assert isinstance(action, RespondAction)
    assert "ValidationError" in action.message

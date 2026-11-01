"""Optional OpenAI-compatible LLM client adapter.

Implements ModelClient port. Off by default; activated via USE_LLM=true or --use-llm.
Uses standard HTTPX to interface with OpenAI/Gemini/Anthropic/Ollama endpoints.
All LLM outputs are validated against Pydantic ModelAction discriminated unions.
"""

from __future__ import annotations

import json
import os
from typing import Any, Sequence

import httpx
from dotenv import load_dotenv
from pydantic import TypeAdapter, ValidationError

from errand_agent.model.base import (
    ModelAction,
    ModelClient,
    RespondAction,
    SanitizedStateView,
)

SYSTEM_PROMPT = """You are an Errand Assistant for Orange Invest.
Your job is to assist the user in discovering home-service errand providers, selecting slots, and preparing bookings.
CRITICAL SAFETY RULE: You are an advisory proposal engine ONLY. You NEVER execute side effects or finalize bookings.
Deterministic code handles all payments and bookings only after the user explicitly affirms a confirmation card.

You must reply with a valid JSON object matching one of the allowed actions:
1. {"action": "ask_slot", "slot_name": "<slot>", "question": "<question>"}
2. {"action": "search", "service": "<service>", "area": "<area>", "date_range": "<range>"}
3. {"action": "present_options", "options": [...], "message": "<message>"}
4. {"action": "select_and_hold", "provider_id": "<id>", "slot": "<slot>"}
5. {"action": "propose_approval", "confirmation_message": "<msg>"}
6. {"action": "confirm_inferred_fact", "key": "<key>", "value": "<val>", "question": "<q>"}
7. {"action": "respond", "message": "<msg>"}
"""


class LLMClient(ModelClient):
    """OpenAI-compatible LLM client implementing ModelClient port."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model_name: str = "gpt-4o-mini",
        timeout_seconds: float = 10.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        load_dotenv()
        self.api_key = api_key if api_key is not None else os.getenv("OPENAI_API_KEY", "")
        raw_url = base_url or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        self.base_url = raw_url.rstrip("/")
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds
        self._http_client = http_client
        self._adapter: TypeAdapter[ModelAction] = TypeAdapter(ModelAction)

    async def complete(
        self,
        state_view: SanitizedStateView,
        tools_schema: Sequence[dict[str, Any]] | None = None,
    ) -> ModelAction:
        """Call LLM API and parse validated ModelAction."""
        if not self.api_key:
            return RespondAction(
                message="LLM API key not configured. Please set OPENAI_API_KEY or use default scripted model."
            )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Current Workflow State:\n{state_view.model_dump_json(indent=2)}\n\nProduce next action JSON.",
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.0,
        }

        try:
            if self._http_client:
                resp = await self._http_client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=self.timeout_seconds,
                )
            else:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        f"{self.base_url}/chat/completions",
                        headers=headers,
                        json=payload,
                        timeout=self.timeout_seconds,
                    )

            if resp.status_code != 200:
                return RespondAction(
                    message=f"LLM call failed with HTTP {resp.status_code}: {resp.text[:100]}"
                )

            data = resp.json()
            raw_content = data["choices"][0]["message"]["content"]
            parsed_json = json.loads(raw_content)

            # Strict Pydantic validation against discriminated union
            action = self._adapter.validate_python(parsed_json)
            return action

        except (ValidationError, json.JSONDecodeError, KeyError) as err:
            # Fall back to safe conversational response
            return RespondAction(
                message=f"I encountered an error understanding the model proposal: {type(err).__name__}. Let me assist you directly."
            )
        except Exception as exc:
            return RespondAction(message=f"LLM communication error: {str(exc)}")

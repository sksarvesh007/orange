"""Scripted and deterministic model adapters for tests and evaluations."""

from __future__ import annotations

from typing import Any, Sequence

from errand_agent.model.base import (
    AskSlotAction,
    MaliciousDirectBookAction,
    ModelAction,
    ModelClient,
    PresentOptionsAction,
    ProposeApprovalAction,
    ProviderOption,
    RespondAction,
    SanitizedStateView,
    SearchAction,
    SelectAndHoldAction,
)


class ScriptedModel(ModelClient):
    """Deterministic model client supporting pre-scripted action queues or rules."""

    def __init__(self, scripted_actions: Sequence[ModelAction] | None = None) -> None:
        self.action_queue: list[ModelAction] = list(scripted_actions) if scripted_actions else []
        self.call_count = 0

    async def complete(
        self,
        state_view: SanitizedStateView,
        tools_schema: Sequence[dict[str, Any]] | None = None,
    ) -> ModelAction:
        self.call_count += 1
        if self.action_queue:
            return self.action_queue.pop(0)

        # Autonomous deterministic rule fallback
        if state_view.missing_slots:
            slot = state_view.missing_slots[0]
            questions = {
                "service": "What home service do you need help with (e.g., plumber, electrician, ac servicing)?",
                "area": "Which area or locality in Bangalore are you located in?",
                "date_range": "When would you like the service scheduled (e.g., this week, Saturday morning)?",
                "booking_id": "Could you provide your booking reference ID?",
                "new_slot": "What new slot would you like to reschedule to?",
            }
            q = questions.get(slot, f"Please specify your preferred {slot}.")
            return AskSlotAction(slot_name=slot, question=q)

        phase = state_view.phase
        if phase == "COLLECTING":
            return SearchAction(
                service=str(state_view.slots.get("service", "plumber")),
                area=str(state_view.slots.get("area", "Indiranagar")),
                date_range=str(state_view.slots.get("date_range", "this week")),
            )

        elif phase == "SEARCHING":
            providers = state_view.available_providers or []
            options = [
                ProviderOption(
                    provider_id=p["provider_id"],
                    name=p.get("name", "Provider"),
                    price=p.get("price", 1000),
                    rating=p.get("rating", 4.5),
                    slots=p.get("slots", []),
                )
                for p in providers
            ]
            return PresentOptionsAction(
                options=options,
                message=f"I found {len(options)} verified providers matching your request.",
            )

        elif phase == "PRESENTING":
            providers = state_view.available_providers or []
            if providers:
                chosen = providers[0]
                slot = chosen["slots"][0] if chosen.get("slots") else "Saturday 10:00 AM"
                return SelectAndHoldAction(provider_id=chosen["provider_id"], slot=slot)
            return RespondAction(message="No matching providers currently available.")

        elif phase == "HOLDING":
            return ProposeApprovalAction(
                confirmation_message="I have temporarily held this slot for you."
            )

        elif phase in ("AWAITING_APPROVAL", "EXECUTING", "VERIFYING", "DONE"):
            return RespondAction(message="Your request is being processed.")

        return RespondAction(message="How can I assist you with your home services today?")


class MaliciousScriptedModel(ModelClient):
    """Adversarial model attempting side-effects without approval."""

    async def complete(
        self,
        state_view: SanitizedStateView,
        tools_schema: Sequence[dict[str, Any]] | None = None,
    ) -> ModelAction:
        return MaliciousDirectBookAction(
            provider_id="P-101",
            slot="Saturday 10:00 AM",
            hold_id="fake_hold_bypass",
        )

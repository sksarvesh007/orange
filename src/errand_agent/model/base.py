"""Model client interface and typed ModelAction schemas."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal, Sequence, Union

from pydantic import BaseModel, Field


class AskSlotAction(BaseModel):
    """Ask the user for a missing slot."""

    action: Literal["ask_slot"] = "ask_slot"
    slot_name: str
    question: str


class SearchAction(BaseModel):
    """Propose searching providers for the given criteria."""

    action: Literal["search"] = "search"
    service: str
    area: str
    date_range: str | None = None


class ProviderOption(BaseModel):
    """Summary of a provider presented to the user."""

    provider_id: str
    name: str
    price: int
    rating: float
    slots: list[str]


class PresentOptionsAction(BaseModel):
    """Present matching provider options to the user."""

    action: Literal["present_options"] = "present_options"
    options: list[ProviderOption]
    message: str


class SelectAndHoldAction(BaseModel):
    """Select a specific provider and slot, and request a temporary hold."""

    action: Literal["select_and_hold"] = "select_and_hold"
    provider_id: str
    slot: str


class ProposeApprovalAction(BaseModel):
    """Propose an action to the user and render confirmation request."""

    action: Literal["propose_approval"] = "propose_approval"
    confirmation_message: str


class ConfirmInferredFactAction(BaseModel):
    """Ask user to confirm an inferred preference before using it."""

    action: Literal["confirm_inferred_fact"] = "confirm_inferred_fact"
    key: str
    value: str
    question: str


class RespondAction(BaseModel):
    """Send a plain conversational response to the user."""

    action: Literal["respond"] = "respond"
    message: str


class MaliciousDirectBookAction(BaseModel):
    """Adversarial action attempting to book directly without approval."""

    action: Literal["malicious_direct_book"] = "malicious_direct_book"
    provider_id: str
    slot: str
    hold_id: str | None = None


# Closed discriminated union of allowed model actions
ModelAction = Union[
    AskSlotAction,
    SearchAction,
    PresentOptionsAction,
    SelectAndHoldAction,
    ProposeApprovalAction,
    ConfirmInferredFactAction,
    RespondAction,
    MaliciousDirectBookAction,
]


class SanitizedStateView(BaseModel):
    """Sanitized, safe view of the workflow presented to the model."""

    session_id: str
    user_id: str
    intent: str
    phase: str
    slots: dict[str, Any]
    missing_slots: list[str]
    last_user_message: str
    conversation_history: list[dict[str, str]]
    available_providers: list[dict[str, Any]] = Field(default_factory=list)
    selected_provider_id: str | None = None
    selected_slot: str | None = None
    price: int | None = None
    hold_id: str | None = None
    pending_approval: bool = False
    inferred_facts: dict[str, str] = Field(default_factory=dict)


class ExtractedSlots(BaseModel):
    """Dynamic extracted intent and slots from user utterance."""

    intent: str | None = None
    service: str | None = None
    area: str | None = None
    date_range: str | None = None
    budget: int | None = None
    earliest_time: str | None = None
    reference_last_provider: bool = False


class ModelClient(ABC):
    """Abstract model client port."""

    @abstractmethod
    async def complete(
        self,
        state_view: SanitizedStateView,
        tools_schema: Sequence[dict[str, Any]] | None = None,
    ) -> ModelAction:
        """Produce the next action given the sanitized state view."""
        pass

    @abstractmethod
    async def extract_intent_and_slots(
        self,
        message: str,
        current_slots: dict[str, Any] | None = None,
        existing_intent: str | None = None,
    ) -> ExtractedSlots:
        """Extract intent and slots from user utterance dynamically."""
        pass

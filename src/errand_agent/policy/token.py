"""Approval token capability object required for side effects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from errand_agent.domain.enums import ActionType


@dataclass(frozen=True)
class ApprovalToken:
    """Capability token proving code-enforced user approval for a side effect.

    Cannot be forged or created outside the PolicyGate.
    """

    approval_id: str
    user_id: str
    session_id: str
    action_type: ActionType
    action_hash: str
    idempotency_key: str
    granted_at: datetime

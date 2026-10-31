"""Domain enums for workflow state, actions, and lifecycle management."""

from enum import Enum


class Intent(str, Enum):
    """Workflow intent."""

    BOOK = "book"
    RESCHEDULE = "reschedule"
    CANCEL = "cancel"
    NONE = "none"


class Phase(str, Enum):
    """Explicit workflow phases."""

    # Normal progressive lifecycle
    COLLECTING = "COLLECTING"
    SEARCHING = "SEARCHING"
    PRESENTING = "PRESENTING"
    HOLDING = "HOLDING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    DONE = "DONE"

    # Terminal / Exception lifecycles
    FAILED = "FAILED"
    CANCELLED_BY_USER = "CANCELLED_BY_USER"
    LIMIT_EXHAUSTED = "LIMIT_EXHAUSTED"
    NEEDS_HUMAN = "NEEDS_HUMAN"


class SlotSource(str, Enum):
    """Provenance source for a workflow slot."""

    USER = "user"
    MEMORY = "memory"
    INFERRED = "inferred"


class ActionType(str, Enum):
    """Side-effect action type."""

    BOOK = "book"
    CANCEL = "cancel"
    RESCHEDULE = "reschedule"


class BookingStatus(str, Enum):
    """Authoritative ledger booking status."""

    INTENT = "INTENT"
    EXECUTING = "EXECUTING"
    BOOKED = "BOOKED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"


class Provenance(str, Enum):
    """Provenance of long-term memory facts."""

    STATED = "stated"
    INFERRED = "inferred"


class ConfirmationDecision(str, Enum):
    """User confirmation classification."""

    AFFIRM = "affirm"
    DENY = "deny"
    CORRECTION = "correction"
    UNKNOWN = "unknown"


class ErrorClass(str, Enum):
    """Tool and provider call error classification."""

    TRANSIENT = "transient"
    PERMANENT = "permanent"
    UNKNOWN = "unknown"

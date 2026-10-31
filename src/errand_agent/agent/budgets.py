"""Execution budget enforcement for agent runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from errand_agent.utils.clock import Clock, FrozenClock


class BudgetExceededError(Exception):
    """Raised when an execution budget limit is breached."""

    def __init__(self, budget_name: str, limit: float, current: float) -> None:
        super().__init__(f"Budget exceeded for {budget_name}: limit={limit}, current={current}")
        self.budget_name = budget_name
        self.limit = limit
        self.current = current


@dataclass
class BudgetConfig:
    max_model_turns: int = 10
    max_tool_calls: int = 15
    max_tool_calls_per_tool: dict[str, int] = field(
        default_factory=lambda: {
            "search_providers": 3,
            "get_provider": 5,
            "hold_slot": 3,
            "book": 3,
            "cancel": 3,
            "reschedule": 3,
            "get_booking_status": 4,
            "list_bookings": 3,
        }
    )
    max_wall_clock_seconds: float = 30.0
    max_retries_per_op: int = 3
    max_status_polls: int = 4


class BudgetTracker:
    """Tracks and enforces execution resource consumption during a run."""

    def __init__(self, config: BudgetConfig | None = None, clock: Clock | None = None) -> None:
        self.config = config or BudgetConfig()
        self.clock = clock or FrozenClock()
        self.start_monotonic = self.clock.monotonic()

        self.model_turns = 0
        self.total_tool_calls = 0
        self.tool_calls_by_tool: dict[str, int] = {}
        self.status_polls = 0

    def check_time(self) -> None:
        elapsed = self.clock.monotonic() - self.start_monotonic
        if elapsed > self.config.max_wall_clock_seconds:
            raise BudgetExceededError("wall_clock_seconds", self.config.max_wall_clock_seconds, elapsed)

    def record_model_turn(self) -> None:
        self.check_time()
        self.model_turns += 1
        if self.model_turns > self.config.max_model_turns:
            raise BudgetExceededError("model_turns", self.config.max_model_turns, self.model_turns)

    def record_tool_call(self, tool_name: str) -> None:
        self.check_time()
        self.total_tool_calls += 1
        if self.total_tool_calls > self.config.max_tool_calls:
            raise BudgetExceededError("total_tool_calls", self.config.max_tool_calls, self.total_tool_calls)

        curr = self.tool_calls_by_tool.get(tool_name, 0) + 1
        self.tool_calls_by_tool[tool_name] = curr
        limit = self.config.max_tool_calls_per_tool.get(tool_name, 5)
        if curr > limit:
            raise BudgetExceededError(f"tool_call:{tool_name}", limit, curr)

        if tool_name == "get_booking_status":
            self.status_polls += 1
            if self.status_polls > self.config.max_status_polls:
                raise BudgetExceededError("status_polls", self.config.max_status_polls, self.status_polls)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_turns": self.model_turns,
            "total_tool_calls": self.total_tool_calls,
            "tool_calls_by_tool": dict(self.tool_calls_by_tool),
            "status_polls": self.status_polls,
            "elapsed_seconds": self.clock.monotonic() - self.start_monotonic,
        }

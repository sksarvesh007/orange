"""Injected clock and RNG abstractions for strict determinism."""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Sequence, TypeVar

T = TypeVar("T")


class Clock(ABC):
    """Abstract clock protocol."""

    @abstractmethod
    def now(self) -> datetime:
        """Return the current timezone-aware UTC datetime."""
        pass

    @abstractmethod
    def monotonic(self) -> float:
        """Return a monotonic clock in seconds."""
        pass

    @abstractmethod
    def advance(self, seconds: float) -> None:
        """Advance time (useful in simulation / test environments)."""
        pass


class FrozenClock(Clock):
    """Deterministic frozen clock with manual advancement."""

    def __init__(self, start_time: datetime | None = None) -> None:
        if start_time is None:
            self._current_time = datetime(2026, 10, 31, 10, 0, 0, tzinfo=timezone.utc)
        else:
            if start_time.tzinfo is None:
                self._current_time = start_time.replace(tzinfo=timezone.utc)
            else:
                self._current_time = start_time
        self._monotonic = 0.0

    def now(self) -> datetime:
        return self._current_time

    def monotonic(self) -> float:
        return self._monotonic

    def advance(self, seconds: float) -> None:
        from datetime import timedelta

        self._current_time += timedelta(seconds=seconds)
        self._monotonic += seconds


class SystemClock(Clock):
    """Real system clock (used for production runtime)."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def monotonic(self) -> float:
        import time

        return time.monotonic()

    def advance(self, seconds: float) -> None:
        # In production, wall clock advances naturally
        pass


class Rng(ABC):
    """Abstract RNG protocol."""

    @abstractmethod
    def random(self) -> float:
        """Return a random float in [0.0, 1.0)."""
        pass

    @abstractmethod
    def randint(self, a: int, b: int) -> int:
        """Return a random integer N such that a <= N <= b."""
        pass

    @abstractmethod
    def choice(self, seq: Sequence[T]) -> T:
        """Return a random element from non-empty sequence seq."""
        pass


class SeededRng(Rng):
    """Deterministic PRNG using a fixed seed."""

    def __init__(self, seed: int = 42) -> None:
        self._seed = seed
        self._rand = random.Random(seed)

    def random(self) -> float:
        return self._rand.random()

    def randint(self, a: int, b: int) -> int:
        return self._rand.randint(a, b)

    def choice(self, seq: Sequence[T]) -> T:
        if not seq:
            raise IndexError("Cannot choose from an empty sequence")
        return self._rand.choice(seq)

    def reseed(self, seed: int) -> None:
        self._seed = seed
        self._rand = random.Random(seed)

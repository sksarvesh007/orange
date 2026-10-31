"""Provider client seam: Timeouts, retries, circuit breaker, bulkhead, and error classification."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, TypeVar

from errand_agent.domain.enums import ErrorClass
from errand_agent.mocks.provider import (
    HoldExpiredError,
    MockProviderService,
    ProviderMockError,
    ProviderTimeoutError,
    ServiceUnavailableError,
    SlotConflictError,
    SlotTakenError,
    SlotUnavailableError,
)
from errand_agent.policy.token import ApprovalToken
from errand_agent.tools.sanitizer import (
    sanitize_provider_detail,
    sanitize_search_results,
)
from errand_agent.utils.clock import Clock, FrozenClock, Rng, SeededRng

T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(Exception):
    """Raised when call is rejected because the circuit breaker is OPEN."""

    pass


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5
    recovery_time_seconds: float = 30.0


class CircuitBreaker:
    """Circuit breaker per provider with state tracking."""

    def __init__(
        self, config: CircuitBreakerConfig | None = None, clock: Clock | None = None
    ) -> None:
        self.config = config or CircuitBreakerConfig()
        self.clock = clock or FrozenClock()
        self.state = CircuitState.CLOSED
        self.consecutive_failures = 0
        self.last_failure_time = 0.0

    def can_execute(self) -> bool:
        now = self.clock.monotonic()
        if self.state == CircuitState.OPEN:
            if now - self.last_failure_time >= self.config.recovery_time_seconds:
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        return True

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.state = CircuitState.CLOSED

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        self.last_failure_time = self.clock.monotonic()
        if self.consecutive_failures >= self.config.failure_threshold:
            self.state = CircuitState.OPEN


class TokenBucketRateLimiter:
    """Token bucket rate limiter per provider using injected clock."""

    def __init__(
        self,
        rate_per_second: float = 10.0,
        capacity: float = 20.0,
        clock: Clock | None = None,
    ) -> None:
        self.rate = rate_per_second
        self.capacity = capacity
        self.tokens = capacity
        self.clock = clock or FrozenClock()
        self.last_update = self.clock.monotonic()

    def try_acquire(self) -> bool:
        now = self.clock.monotonic()
        elapsed = now - self.last_update
        self.last_update = now
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)

        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


def classify_error(exc: Exception) -> ErrorClass:
    """Classify exceptions into TRANSIENT, PERMANENT, or UNKNOWN."""
    if isinstance(exc, (ServiceUnavailableError, ProviderTimeoutError)):
        return ErrorClass.TRANSIENT
    if isinstance(exc, (SlotTakenError, SlotUnavailableError, HoldExpiredError, SlotConflictError)):
        return ErrorClass.PERMANENT
    if isinstance(exc, ProviderMockError):
        return ErrorClass.PERMANENT
    return ErrorClass.UNKNOWN


@dataclass
class RetryPolicy:
    max_retries: int = 3
    base_backoff_seconds: float = 0.1
    max_backoff_seconds: float = 1.0
    jitter: bool = True


class ProviderClient:
    """Client seam wrapping the provider service with production-grade resilience patterns."""

    def __init__(
        self,
        provider_service: MockProviderService,
        retry_policy: RetryPolicy | None = None,
        clock: Clock | None = None,
        rng: Rng | None = None,
        max_concurrent_requests: int = 20,
    ) -> None:
        self.provider_service = provider_service
        self.retry_policy = retry_policy or RetryPolicy()
        self.clock = clock or FrozenClock()
        self.rng = rng or SeededRng(42)

        self._circuit_breakers: dict[str, CircuitBreaker] = {}
        self._rate_limiters: dict[str, TokenBucketRateLimiter] = {}
        self._semaphore = asyncio.Semaphore(max_concurrent_requests)

    def _get_circuit_breaker(self, provider_id: str) -> CircuitBreaker:
        if provider_id not in self._circuit_breakers:
            self._circuit_breakers[provider_id] = CircuitBreaker(clock=self.clock)
        return self._circuit_breakers[provider_id]

    def _get_rate_limiter(self, provider_id: str) -> TokenBucketRateLimiter:
        if provider_id not in self._rate_limiters:
            self._rate_limiters[provider_id] = TokenBucketRateLimiter(clock=self.clock)
        return self._rate_limiters[provider_id]

    async def _execute_with_resilience(
        self,
        provider_id: str,
        operation: Callable[[], T],
        is_idempotent: bool = False,
    ) -> T:
        """Execute operation under concurrency limits, rate limiting, circuit breaker, and retry."""
        cb = self._get_circuit_breaker(provider_id)
        rl = self._get_rate_limiter(provider_id)

        if not cb.can_execute():
            raise CircuitBreakerOpenError(f"Circuit breaker is OPEN for provider {provider_id}")

        if not rl.try_acquire():
            # If rate limited, we fail fast with transient classification
            raise ServiceUnavailableError(f"Rate limit exceeded for provider {provider_id}")

        async with self._semaphore:
            attempt = 0
            while True:
                try:
                    res = operation()
                    cb.record_success()
                    return res
                except Exception as exc:
                    cb.record_failure()
                    err_class = classify_error(exc)

                    # Only retry idempotent calls on transient failures
                    if (
                        is_idempotent
                        and err_class == ErrorClass.TRANSIENT
                        and attempt < self.retry_policy.max_retries
                    ):
                        attempt += 1
                        # Backoff calculation
                        backoff = min(
                            self.retry_policy.base_backoff_seconds * (2 ** (attempt - 1)),
                            self.retry_policy.max_backoff_seconds,
                        )
                        if self.retry_policy.jitter:
                            backoff *= 0.5 + self.rng.random() * 0.5

                        # Advance simulated clock rather than sleeping
                        self.clock.advance(backoff)
                        continue

                    # Non-retryable or retries exhausted
                    raise

    # 1. search_providers
    async def search_providers(
        self, service: str, area: str, date_range: str | None = None
    ) -> list[dict[str, Any]]:
        raw = await self._execute_with_resilience(
            provider_id="global_search",
            operation=lambda: self.provider_service.search_providers(service, area, date_range),
            is_idempotent=True,
        )
        return sanitize_search_results(raw)

    # 2. get_provider
    async def get_provider(self, provider_id: str) -> dict[str, Any]:
        raw = await self._execute_with_resilience(
            provider_id=provider_id,
            operation=lambda: self.provider_service.get_provider(provider_id),
            is_idempotent=True,
        )
        # Convert dataclass to dict
        from dataclasses import asdict

        return sanitize_provider_detail(asdict(raw))

    # 3. list_bookings
    async def list_bookings(self, user_id: str) -> list[dict[str, Any]]:
        raw = await self._execute_with_resilience(
            provider_id="global_bookings",
            operation=lambda: self.provider_service.list_bookings(user_id),
            is_idempotent=True,
        )
        from dataclasses import asdict

        return [asdict(b) for b in raw]

    # 4. hold_slot
    async def hold_slot(self, provider_id: str, slot: str) -> dict[str, Any]:
        raw = await self._execute_with_resilience(
            provider_id=provider_id,
            operation=lambda: self.provider_service.hold_slot(provider_id, slot),
            is_idempotent=False,
        )
        from dataclasses import asdict

        return asdict(raw)

    # 5. book (REQUIRES APPROVAL TOKEN)
    async def book(self, token: ApprovalToken, hold_id: str) -> str:
        if not isinstance(token, ApprovalToken):
            raise TypeError("book requires a valid ApprovalToken")
        return await self._execute_with_resilience(
            provider_id=token.action_hash[:8],
            operation=lambda: self.provider_service.book(
                hold_id=hold_id,
                idempotency_key=token.idempotency_key,
                user_id=token.user_id,
            ),
            is_idempotent=True,  # Idempotent because key is deterministic
        )

    # 6. get_booking_status
    async def get_booking_status(self, idempotency_key: str) -> dict[str, Any]:
        raw = await self._execute_with_resilience(
            provider_id="global_status",
            operation=lambda: self.provider_service.get_booking_status(idempotency_key),
            is_idempotent=True,
        )
        from dataclasses import asdict

        return asdict(raw)

    # 7. cancel (REQUIRES APPROVAL TOKEN)
    async def cancel(self, token: ApprovalToken, booking_id: str) -> bool:
        if not isinstance(token, ApprovalToken):
            raise TypeError("cancel requires a valid ApprovalToken")
        return await self._execute_with_resilience(
            provider_id=token.action_hash[:8],
            operation=lambda: self.provider_service.cancel(
                booking_id=booking_id,
                idempotency_key=token.idempotency_key,
            ),
            is_idempotent=True,
        )

    # 8. reschedule (REQUIRES APPROVAL TOKEN)
    async def reschedule(
        self, token: ApprovalToken, booking_id: str, new_slot: str
    ) -> dict[str, Any]:
        if not isinstance(token, ApprovalToken):
            raise TypeError("reschedule requires a valid ApprovalToken")
        raw = await self._execute_with_resilience(
            provider_id=token.action_hash[:8],
            operation=lambda: self.provider_service.reschedule(
                booking_id=booking_id,
                new_slot=new_slot,
                idempotency_key=token.idempotency_key,
            ),
            is_idempotent=True,
        )
        from dataclasses import asdict

        return asdict(raw)

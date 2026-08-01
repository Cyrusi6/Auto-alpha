"""Small request pacing helpers for governed Tushare backfills."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Callable


@dataclass(frozen=True)
class RequestRateLimitConfig:
    requests_per_minute: float = 150.0
    min_interval_seconds: float | None = None
    burst_size: int = 1
    enabled: bool = True

    @property
    def interval_seconds(self) -> float:
        if self.min_interval_seconds is not None:
            return max(0.0, float(self.min_interval_seconds))
        rpm = max(float(self.requests_per_minute), 1e-9)
        return 60.0 / rpm


@dataclass(frozen=True)
class RateLimitEvent:
    api_name: str
    dataset: str | None
    waited_seconds: float
    request_index: int
    timestamp: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class RateLimitSummary:
    enabled: bool
    requests_per_minute: float
    total_wait_seconds: float
    average_wait_seconds: float
    rate_limit_event_count: int
    events: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class SimpleRateLimiter:
    """Deterministic single-process pacing limiter.

    Cache hits should bypass this object. Tests can pass fake clock/sleep callables
    to avoid real waiting.
    """

    def __init__(
        self,
        config: RequestRateLimitConfig | None = None,
        *,
        time_func: Callable[[], float] | None = None,
        sleep_func: Callable[[float], None] | None = None,
    ):
        self.config = config or RequestRateLimitConfig()
        self._time = time.monotonic if time_func is None else time_func
        self._sleep = time.sleep if sleep_func is None else sleep_func
        self._last_request_at: float | None = None
        self._request_index = 0
        self.events: list[RateLimitEvent] = []

    def wait(self, api_name: str, dataset: str | None = None) -> RateLimitEvent:
        self._request_index += 1
        waited = 0.0
        if self.config.enabled:
            now = self._time()
            if self._last_request_at is not None:
                elapsed = max(0.0, now - self._last_request_at)
                waited = max(0.0, self.config.interval_seconds - elapsed)
                if waited > 0:
                    self._sleep(waited)
                    now = self._time()
            self._last_request_at = now
        event = RateLimitEvent(
            api_name=api_name,
            dataset=dataset,
            waited_seconds=float(waited),
            request_index=self._request_index,
            timestamp=_utc_now(),
        )
        self.events.append(event)
        return event

    def summary(self) -> RateLimitSummary:
        total = sum(event.waited_seconds for event in self.events)
        count = len(self.events)
        return RateLimitSummary(
            enabled=bool(self.config.enabled),
            requests_per_minute=float(self.config.requests_per_minute),
            total_wait_seconds=float(total),
            average_wait_seconds=float(total / count) if count else 0.0,
            rate_limit_event_count=count,
            events=[event.to_dict() for event in self.events],
        )


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

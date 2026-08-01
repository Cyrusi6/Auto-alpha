"""Small timing utility for local benchmarks."""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class Timer:
    started_at: float = 0.0
    elapsed: float = 0.0

    def __enter__(self) -> "Timer":
        self.started_at = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.elapsed = time.perf_counter() - self.started_at

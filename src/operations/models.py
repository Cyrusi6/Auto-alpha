"""Dataclasses for production daily runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ProductionRunResult:
    run_id: str
    created_at: str
    status: str
    factor_id: str | None
    rebalance_date: str
    approval_id: str | None = None
    approval_status: str | None = None
    executed: bool = False
    paths: dict[str, str] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

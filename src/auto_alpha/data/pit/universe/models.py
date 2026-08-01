"""Dataclasses for A-share universe construction."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class UniverseMember:
    universe_name: str
    as_of_date: str
    ts_code: str
    name: str
    exchange: str
    list_date: str
    listed_days: int
    amount: float
    industry: str | None = None
    board: str | None = None
    is_active: bool | None = None
    exclusion_reason: str | None = None


@dataclass(frozen=True)
class UniverseBuildConfig:
    universe_name: str
    as_of_date: str
    min_listed_days: int = 60
    min_amount: float = 0.0
    exchanges: tuple[str, ...] | None = None
    boards: tuple[str, ...] | None = None
    index_code: str | None = None
    use_index_members: bool = False
    point_in_time: bool = False
    min_listing_days: int = 0
    exclude_st: bool = False
    include_delisted_history: bool = False
    active_mask_path: str | None = None


@dataclass(frozen=True)
class UniverseBuildResult:
    universe_name: str
    as_of_date: str
    members: list[UniverseMember]
    output_path: str
    summary_path: str
    total_candidates: int
    selected: int
    rejected: dict[str, int]
    source: str = "securities"
    index_code: str | None = None
    latest_index_trade_date: str | None = None
    pit_summary_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "universe_name": self.universe_name,
            "as_of_date": self.as_of_date,
            "members": [asdict(member) for member in self.members],
            "output_path": self.output_path,
            "summary_path": self.summary_path,
            "total_candidates": self.total_candidates,
            "selected": self.selected,
            "rejected": dict(sorted(self.rejected.items())),
            "source": self.source,
            "index_code": self.index_code,
            "latest_index_trade_date": self.latest_index_trade_date,
            "pit_summary_path": self.pit_summary_path,
        }

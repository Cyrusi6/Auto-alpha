"""Single source of truth for research/holdout date access."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Iterable, Sequence


class FirewallAccessError(RuntimeError):
    pass


@dataclass
class DateFirewall:
    research_end_date: str
    holdout_start_date: str | None = None
    label_horizon: int = 1
    policy_version: str = "research_diagnostic_firewall_v2"
    access_audit: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        _parse_date(self.research_end_date)
        if self.holdout_start_date:
            _parse_date(self.holdout_start_date)
            if self.holdout_start_date <= self.research_end_date:
                raise ValueError("holdout_start_date must be after research_end_date")
        if int(self.label_horizon) < 0:
            raise ValueError("label_horizon must be non-negative")

    def research_dates(self, trade_dates: Sequence[str]) -> list[str]:
        dates = [str(value) for value in trade_dates]
        selected = [value for value in dates if value <= self.research_end_date]
        if self.label_horizon:
            selected = selected[: max(0, len(selected) - int(self.label_horizon))]
        return selected

    def diagnostic_dates(self, trade_dates: Sequence[str]) -> list[str]:
        if not self.holdout_start_date:
            return []
        dates = [str(value) for value in trade_dates]
        selected = [value for value in dates if value >= self.holdout_start_date]
        if self.label_horizon:
            selected = selected[: max(0, len(selected) - int(self.label_horizon))]
        return selected

    def eligible_date_hash(self, trade_dates: Sequence[str]) -> str:
        return hashlib.sha256("\n".join(self.research_dates(trade_dates)).encode("utf-8")).hexdigest()

    def assert_target_access(self, start_date: str, end_date: str, *, component: str, purpose: str) -> None:
        allowed = str(end_date) <= self.research_end_date
        row = {
            "component": component,
            "purpose": purpose,
            "start_date": str(start_date),
            "end_date": str(end_date),
            "allowed": allowed,
            "research_end_date": self.research_end_date,
        }
        self.access_audit.append(row)
        if not allowed:
            raise FirewallAccessError(
                f"research firewall blocked {component}:{purpose} target ending {end_date} after {self.research_end_date}"
            )

    def audit_observation_access(
        self,
        dates: Sequence[str],
        *,
        component: str,
        purpose: str,
        view: str = "research",
    ) -> None:
        for date in (str(value) for value in dates):
            allowed = date <= self.research_end_date if view == "research" else bool(self.holdout_start_date and date >= self.holdout_start_date)
            self.access_audit.append(
                {
                    "component": component,
                    "purpose": purpose,
                    "access_type": "observation_read",
                    "view": view,
                    "date": date,
                    "allowed": allowed,
                }
            )
            if not allowed:
                raise FirewallAccessError(f"research firewall blocked actual {view} observation read on {date}")

    def filter_records(self, records: Iterable[dict[str, Any]], *, date_field: str, component: str) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        for record in records:
            value = record.get(date_field)
            if value in {None, ""}:
                continue
            date = str(value)
            allowed = date <= self.research_end_date
            if allowed:
                self.access_audit.append(
                    {
                        "component": component,
                        "purpose": "raw_record",
                        "access_type": "observation_read",
                        "view": "research_source",
                        "date": date,
                        "allowed": True,
                    }
                )
                selected.append(record)
        return selected

    def deterministic_sample(self, trade_dates: Sequence[str], *, max_dates: int = 63, seed: int = 0) -> dict[str, Any]:
        eligible = self.research_dates(trade_dates)
        if not eligible:
            raise FirewallAccessError("no research-period eligible dates")
        count = min(max(1, int(max_dates)), len(eligible))
        if count == 1:
            sampled = [eligible[0]]
        else:
            positions = [(index * (len(eligible) - 1)) // (count - 1) for index in range(count)]
            offset = int(seed) % len(eligible)
            sampled = sorted({eligible[(position + offset) % len(eligible)] for position in positions})
        return {
            "method": "deterministic_stratified_equal_distance",
            "seed": int(seed),
            "dates": sampled,
            "eligible_date_hash": self.eligible_date_hash(trade_dates),
            "research_end_date": self.research_end_date,
        }

    def fingerprint_payload(self, trade_dates: Sequence[str]) -> dict[str, Any]:
        return {
            **asdict(self),
            "access_audit": None,
            "eligible_date_hash": self.eligible_date_hash(trade_dates),
        }

    def proof(self, trade_dates: Sequence[str], *, raw_truncated_before_compute: bool) -> dict[str, Any]:
        violations = [row for row in self.access_audit if not row.get("allowed", False)]
        enabled = bool(raw_truncated_before_compute and not violations and self.research_dates(trade_dates))
        return {
            "policy_version": self.policy_version,
            "research_end_date": self.research_end_date,
            "holdout_start_date": self.holdout_start_date,
            "label_horizon": int(self.label_horizon),
            "eligible_date_hash": self.eligible_date_hash(trade_dates),
            "raw_truncated_before_compute": bool(raw_truncated_before_compute),
            "out_of_bounds_access_count": len(violations),
            "research_holdout_firewall_enabled": enabled,
        }


@dataclass(frozen=True)
class ResearchDataView:
    firewall: DateFirewall
    trade_dates: tuple[str, ...]

    @property
    def eligible_dates(self) -> tuple[str, ...]:
        return tuple(self.firewall.research_dates(self.trade_dates))

    @property
    def eligible_date_hash(self) -> str:
        return self.firewall.eligible_date_hash(self.trade_dates)

    @property
    def eligible_indices(self) -> tuple[int, ...]:
        eligible = set(self.eligible_dates)
        return tuple(index for index, date in enumerate(self.trade_dates) if date in eligible)

    @property
    def diagnostic_dates(self) -> tuple[str, ...]:
        return tuple(self.firewall.diagnostic_dates(self.trade_dates))

    @property
    def diagnostic_indices(self) -> tuple[int, ...]:
        eligible = set(self.diagnostic_dates)
        return tuple(index for index, date in enumerate(self.trade_dates) if date in eligible)

    def truncate_axis(self, values, *, axis: int = -1, component: str | None = None, purpose: str = "observation"):
        return self.select_axis(values, axis=axis, view="research", component=component, purpose=purpose)

    def select_axis(self, values, *, axis: int = -1, view: str = "research", component: str | None = None, purpose: str = "observation"):
        indices = self.eligible_indices if view == "research" else self.diagnostic_indices
        dates = self.eligible_dates if view == "research" else self.diagnostic_dates
        if component:
            self.firewall.audit_observation_access(dates, component=component, purpose=purpose, view=view)
        if hasattr(values, "index_select"):
            import torch

            return values.index_select(axis % values.ndim, torch.tensor(indices, dtype=torch.long, device=values.device))
        import numpy as np

        return np.take(values, indices, axis=axis)


def _parse_date(value: str) -> datetime:
    try:
        return datetime.strptime(str(value), "%Y%m%d")
    except ValueError as exc:
        raise ValueError(f"invalid YYYYMMDD date: {value}") from exc

"""Contracts for Task 055-B security-date evidence inventory."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

TASK_ID = "task_055_b"
INVENTORY_SCHEMA = "task055b_security_date_gap_inventory_v1"
CHILD_LEDGER_SCHEMA = "task055b_historical_repair_child_ledger_v1"
POINTER_SCHEMA = "task055b_security_date_gap_inventory_pointer_v1"
VALIDATOR_VERSION = "task055b_gap_inventory_validator_v1"
MAX_REPAIR_DATE = "20260630"

SECURITY_DATE_STATES = (
    "TRADED_PRIMARY_BAR",
    "TRADED_CORROBORATED_BAR",
    "TRADED_SOURCE_CONFLICT",
    "OFFICIAL_NON_TRADING",
    "VENDOR_DAILY_NON_TRADING_MODELED",
    "LIFECYCLE_TERMINATED",
    "CALENDAR_OR_MEMBERSHIP_ERROR",
    "RAW_BAR_REQUIRED_FIELD_INVALID",
    "SOURCE_NORMALIZATION_ZERO_FILL",
    "CORPORATE_ACTION_VALUATION_UNPROVEN",
    "DATA_SOURCE_GAP",
    "CONFLICT",
)

UNRESOLVED_STATES = frozenset(
    {
        "TRADED_SOURCE_CONFLICT",
        "CALENDAR_OR_MEMBERSHIP_ERROR",
        "RAW_BAR_REQUIRED_FIELD_INVALID",
        "SOURCE_NORMALIZATION_ZERO_FILL",
        "CORPORATE_ACTION_VALUATION_UNPROVEN",
        "DATA_SOURCE_GAP",
        "CONFLICT",
    }
)

REQUIRED_BAR_FIELDS = ("open", "high", "low", "close", "vol", "amount")
BAR_FIELD_ALIASES = {"vol": ("vol", "volume")}
PRICE_FIELDS = frozenset({"open", "high", "low", "close"})
KNOWN_REGRESSION_PROBES = (
    ("600170.SH", "20160323"),
    ("601018.SH", "20160517"),
    ("600019.SH", "20160823"),
)

MATRIX_ALIASES = {
    "active": ("active.npy", "active_mask.npy"),
    "listed": ("listed.npy", "listed_mask.npy"),
    "membership": ("membership.npy", "index_membership.npy", "index_member_matrix.npy"),
    "membership_known": ("membership_known.npy",),
    "bar_observed": ("bar_observed.npy", "bar_observed_mask.npy"),
    "unexplained_data_gap": ("unexplained_data_gap.npy", "unexplained_data_gap_mask.npy"),
    "suspension_event_present": ("suspension_event_present.npy",),
    "signal_eligible_at_close": ("signal_eligible_at_close.npy", "signal_candidate_cells.npy"),
    "target_available": ("target_available.npy", "target_available_mask.npy"),
    "sellable_at_open": ("sellable_at_open.npy", "sellable_mask.npy"),
    "open_execution_known": ("open_execution_known.npy",),
    "open_execution_value": ("open_execution_value.npy",),
    "snapshot_source_date": ("snapshot_source_date.npy",),
    "st_effective": ("st_effective.npy", "st_mask.npy"),
    "st_status_known": ("st_status_known.npy", "st_status_known_mask.npy"),
    "corporate_action_validity": ("corporate_action_validity.npy",),
    "adj_factor": ("adj_factor.npy",),
    "daily_basic_validity": ("daily_basic_validity.npy",),
    "limit_validity": ("limit_validity.npy", "daily_limit_validity.npy"),
}


@dataclass(frozen=True)
class GapInventoryConfig:
    """Read-only inputs for a complete, offline security-date inventory."""

    observation_seal: Path
    strict_matrix_root: Path
    simulation_bundle_manifest: Path
    blocked_run_roots: tuple[Path, ...]
    output_root: Path
    evidence_roots: tuple[Path, ...] = ()
    acquired_at: str | None = None
    source_revision: str = "retrospective_historical_repair_inventory_v1"
    max_repair_date: str = MAX_REPAIR_DATE
    required_bar_fields: tuple[str, ...] = REQUIRED_BAR_FIELDS
    probes: tuple[tuple[str, str], ...] = KNOWN_REGRESSION_PROBES

    @classmethod
    def from_paths(
        cls,
        *,
        observation_seal: str | Path,
        strict_matrix_root: str | Path,
        simulation_bundle_manifest: str | Path,
        blocked_run_roots: Iterable[str | Path],
        output_root: str | Path,
        evidence_roots: Iterable[str | Path] = (),
        acquired_at: str | None = None,
    ) -> "GapInventoryConfig":
        return cls(
            observation_seal=Path(observation_seal),
            strict_matrix_root=Path(strict_matrix_root),
            simulation_bundle_manifest=Path(simulation_bundle_manifest),
            blocked_run_roots=tuple(Path(path) for path in blocked_run_roots),
            output_root=Path(output_root),
            evidence_roots=tuple(Path(path) for path in evidence_roots),
            acquired_at=acquired_at,
        )


@dataclass(frozen=True)
class ReadinessSplit:
    factor_replay_ready: bool
    continuous_portfolio_valuation_ready: bool
    future_research_data_ready: bool
    blockers: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "factor_replay_ready": self.factor_replay_ready,
            "continuous_portfolio_valuation_ready": self.continuous_portfolio_valuation_ready,
            "future_research_data_ready": self.future_research_data_ready,
            "blockers": list(self.blockers),
        }

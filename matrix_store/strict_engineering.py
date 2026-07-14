"""Strict, evidence-driven engineering PIT matrix generation for Task 053-A."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

from artifact_schema.writer import write_json_artifact
from data_lake.task052_freeze import (
    resolve_task052_governed_freeze_manifest,
    validate_task052_governed_freeze,
)


DETERMINISTIC_CREATED_AT = "1970-01-01T00:00:00Z"
SUSPENSION_POLICY = {
    "name": "conservative_event_day_open_exclusion_v1",
    "event_absence": "covered_date_without_suspend_d_record",
    "event_presence": "any_S_or_R_record_excludes_open_execution",
    "raw_null_timing": "preserved_and_not_interpreted_as_full_day",
    "signal_execution_separation": "t_plus_1_realized_state_never_enters_signal_t",
    "version": 1,
}
STRICT_MATRIX_SEMANTIC_CONTRACT = {
    "task": "053-A",
    "axes": "byte_exact_lagged_historical_universe_axes",
    "raw_alignment": "exact_ts_code_trade_date_only",
    "raw_missing_value": "nan_with_false_validity",
    "bar_observation": "explicit_daily_bars_source_row_only",
    "bar_inference": "prohibited",
    "adjustment_factor_default": "prohibited",
    "membership_effective_rule": "one_trade_day_lag_from_governed_universe",
    "target": "adjusted_open_t_plus_2_over_adjusted_open_t_plus_1_minus_one",
    "suspension_policy": SUSPENSION_POLICY,
    "publication": "content_addressed_atomic_directory_rename",
    "version": 2,
}


class StrictEngineeringMatrixError(RuntimeError):
    """Raised when a strict matrix source or immutable generation is invalid."""


@dataclass(frozen=True)
class StrictEngineeringPITMatrixConfig:
    min_cross_section_breadth: int = 30
    target_name: str = "target_open_t1_t2"
    research_observable_cutoff: str = "20240530"
    target_endpoint_horizon_trade_days: int = 2
    research_readiness_requirements: Mapping[str, bool] = field(default_factory=dict)
    config_version: str = "task_053a_strict_engineering_matrix_v2"

    def __post_init__(self) -> None:
        if self.min_cross_section_breadth <= 0:
            raise ValueError("min_cross_section_breadth must be positive")
        if not _valid_date(self.research_observable_cutoff):
            raise ValueError("research_observable_cutoff must be YYYYMMDD")
        if self.target_endpoint_horizon_trade_days != 2:
            raise ValueError("Task 053-A requires target endpoint horizon t+2")

    @property
    def semantic_hash(self) -> str:
        return _hash_json({"contract": STRICT_MATRIX_SEMANTIC_CONTRACT, "config": asdict(self), "source_code_hash": _sha256(Path(__file__))})


@dataclass(frozen=True)
class StrictEngineeringPITMatrixResult:
    generation_id: str
    generation_dir: str
    manifest_path: str
    readiness_path: str
    semantic_hash: str
    content_hash: str
    n_stocks: int
    n_dates: int
    engineering_matrix_ready: bool
    alpha_discovery_ready: bool


class StrictEngineeringPITMatrixBuilder:
    """Build the sole production Task 053-A engineering PIT matrix."""

    def __init__(self, config: StrictEngineeringPITMatrixConfig | None = None):
        self.config = config or StrictEngineeringPITMatrixConfig()

    def build(
        self,
        *,
        governed_freeze_dir: str | Path,
        historical_universe_dir: str | Path,
        output_root: str | Path,
    ) -> StrictEngineeringPITMatrixResult:
        freeze_root = Path(governed_freeze_dir)
        universe_root = Path(historical_universe_dir)
        freeze_validation = validate_task052_governed_freeze(freeze_root)
        freeze_manifest_path = resolve_task052_governed_freeze_manifest(freeze_root)
        freeze_manifest = _read_json(freeze_manifest_path)
        universe_manifest = _read_json(universe_root / "task_052a_universe_proof_manifest.json")
        _validate_universe_proof(universe_root, universe_manifest)
        ts_codes = _read_json_list(universe_root / "ts_codes.json", sorted_required=True)
        trade_dates = _read_json_list(universe_root / "trade_dates.json", sorted_required=True)
        if _hash_lines(ts_codes) != universe_manifest.get("stock_axis_hash"):
            raise StrictEngineeringMatrixError("historical universe stock axis hash mismatch")
        if _hash_lines(trade_dates) != universe_manifest.get("date_axis_hash"):
            raise StrictEngineeringMatrixError("historical universe date axis hash mismatch")
        shape = (len(ts_codes), len(trade_dates))
        stock_index = {code: index for index, code in enumerate(ts_codes)}
        date_index = {date: index for index, date in enumerate(trade_dates)}
        artifacts = _artifact_paths(freeze_root, freeze_manifest)

        bars = _require_records(artifacts, "daily_bars")
        adjustments = _require_records(artifacts, "adjustment_factors")
        securities = _require_records(artifacts, "securities")
        suspensions = _require_records(artifacts, "suspensions")
        suspension_coverage_rows = _require_any_records(
            artifacts, ("suspension_coverage_ledger", "suspensions_coverage", "suspension_coverage")
        )
        limits = _optional_records(artifacts, "daily_limits")
        daily_basic_available = "daily_basic" in artifacts
        financial_available = "financial_features" in artifacts
        daily_basic_rows = _optional_records(artifacts, "daily_basic")
        financial_rows = _optional_records(artifacts, "financial_features")
        st_rows = _optional_records(artifacts, "stock_st", "st_status_daily")
        st_coverage_rows = _optional_records(
            artifacts, "stock_st_coverage_ledger", "st_status_coverage", "stock_st_coverage"
        )

        raw, raw_validity, bar_observed = _align_daily_bars(bars, stock_index, date_index, shape)
        adjustment, adjustment_validity = _align_scalar(
            adjustments, stock_index, date_index, shape, ("adj_factor",), "adjustment_factors"
        )
        adjustment_observed = adjustment_validity.copy()
        adjustment_carried = np.zeros(shape, dtype=np.bool_)
        listed, active = _build_lifecycle(securities, ts_codes, trade_dates)
        membership = _load_exact_array(universe_root / "index_membership.npy", shape, np.bool_)
        index_weight = _load_exact_array(universe_root / "index_weight.npy", shape, np.float32)
        membership_known_1d = _load_exact_array(universe_root / "membership_known.npy", (shape[1],), np.bool_)
        membership_known = np.broadcast_to(membership_known_1d, shape).copy()
        membership_publication_proven = np.zeros(shape, dtype=np.bool_)
        snapshot_source_date = _load_exact_array(
            universe_root / "snapshot_source_date.npy", (shape[1],), np.dtype("S8")
        )

        suspension_source_covered = _build_coverage_mask(
            suspension_coverage_rows, ts_codes, trade_dates, dataset="suspensions"
        )
        suspension = _build_suspension_masks(
            suspensions, stock_index, date_index, shape, suspension_source_covered
        )
        st_status_known = _build_coverage_mask(st_coverage_rows, ts_codes, trade_dates, dataset="stock_st")
        st_effective, st_information_available = _build_st_masks(
            st_rows, stock_index, date_index, shape, st_status_known
        )
        limit_values, limit_validity = _align_named_fields(
            limits, stock_index, date_index, shape, ("up_limit", "down_limit"), "daily_limits"
        )
        up_limit, up_limit_validity = limit_values["up_limit"], limit_validity["up_limit"]
        down_limit, down_limit_validity = limit_values["down_limit"], limit_validity["down_limit"]
        daily_basic_values, daily_basic_validity = _align_named_fields(
            daily_basic_rows,
            stock_index,
            date_index,
            shape,
            ("turnover_rate", "volume_ratio", "total_mv", "pb", "pe_ttm"),
            "daily_basic",
        )
        financial_values, financial_validity = _align_financial_asof(
            financial_rows,
            stock_index,
            trade_dates,
            shape,
            ("roe", "revenue_yoy"),
        )

        suspension_associated_bar_absence = (
            suspension["suspension_event_present"] & active & membership & ~bar_observed
        )
        unexplained_data_gap = (
            active
            & membership
            & membership_known
            & suspension_source_covered
            & ~suspension["suspension_event_present"]
            & ~bar_observed
        )
        open_at_up_limit = (
            raw_validity["open"]
            & up_limit_validity
            & np.isclose(raw["open"], up_limit, rtol=0.0, atol=1e-6)
        )
        open_at_down_limit = (
            raw_validity["open"]
            & down_limit_validity
            & np.isclose(raw["open"], down_limit, rtol=0.0, atol=1e-6)
        )
        adjusted_open = np.full(shape, np.nan, dtype=np.float32)
        adjusted_open_validity = raw_validity["open"] & adjustment_validity
        np.multiply(raw["open"], adjustment, out=adjusted_open, where=adjusted_open_validity)
        adjusted_open[~adjusted_open_validity] = np.nan

        signal_eligible_at_close = (
            membership
            & membership_known
            & active
            & bar_observed
            & raw_validity["close"]
            & ~unexplained_data_gap
        )
        open_execution_known = suspension_source_covered.copy()
        open_execution_value = (
            open_execution_known
            & active
            & raw_validity["open"]
            & adjustment_validity
            & ~suspension["conservative_open_excluded"]
            & ~unexplained_data_gap
        )
        realized_entry_possible = open_execution_value.copy()
        realized_exit_possible = open_execution_value.copy()
        buyable_at_open = realized_entry_possible & up_limit_validity & ~open_at_up_limit
        sellable_at_open = realized_exit_possible & down_limit_validity & ~open_at_down_limit
        target, target_available = _build_target(
            adjusted_open,
            adjusted_open_validity,
            active,
            realized_entry_possible,
            realized_exit_possible,
            buyable_at_open,
            sellable_at_open,
            suspension["conservative_open_excluded"],
            unexplained_data_gap,
        )

        arrays: dict[str, np.ndarray] = {}
        for field_name, values in raw.items():
            arrays[field_name] = values
            arrays[f"{field_name}_validity"] = raw_validity[field_name]
        arrays["vol"] = raw["volume"]
        arrays["vol_validity"] = raw_validity["volume"]
        for field_name, values in daily_basic_values.items():
            arrays[field_name] = values
            arrays[f"{field_name}_validity"] = daily_basic_validity[field_name]
        for field_name, values in financial_values.items():
            arrays[field_name] = values
            arrays[f"{field_name}_validity"] = financial_validity[field_name]
        arrays.update(
            {
                "adjustment": adjustment,
                "adjustment_observed": adjustment_observed,
                "adjustment_carried": adjustment_carried,
                "adjustment_validity": adjustment_validity,
                "listed": listed,
                "active": active,
                "membership": membership,
                "weight": index_weight,
                "membership_known": membership_known,
                "membership_publication_proven": membership_publication_proven,
                "st_effective": st_effective,
                "st_status_known": st_status_known,
                "st_information_available": st_information_available,
                "suspension_source_covered": suspension_source_covered,
                **suspension,
                "suspension_associated_bar_absence": suspension_associated_bar_absence,
                "bar_observed": bar_observed,
                "unexplained_data_gap": unexplained_data_gap,
                "up_limit": up_limit,
                "up_limit_validity": up_limit_validity,
                "down_limit": down_limit,
                "down_limit_validity": down_limit_validity,
                "open_at_up_limit": open_at_up_limit,
                "open_at_down_limit": open_at_down_limit,
                "signal_eligible_at_close": signal_eligible_at_close,
                "open_execution_value": open_execution_value,
                "open_execution_known": open_execution_known,
                "realized_entry_possible": realized_entry_possible,
                "realized_exit_possible": realized_exit_possible,
                "buyable_at_open": buyable_at_open,
                "sellable_at_open": sellable_at_open,
                "adjusted_open": adjusted_open,
                "adjusted_open_validity": adjusted_open_validity,
                self.config.target_name: target,
                "target_available": target_available,
                "adj_factor": adjustment,
                "adj_factor_valid_mask": adjustment_validity,
                "bar_observed_mask": bar_observed,
                "target_available_mask": target_available,
                "next_open_t1_t2_return": target,
            }
        )
        invariant_counts = _validate_invariants(arrays)
        signal_breadth = signal_eligible_at_close.sum(axis=0)
        target_breadth = target_available.sum(axis=0)
        evaluable_dates = membership_known_1d & (target_breadth >= self.config.min_cross_section_breadth)
        engineering_blockers = _engineering_blockers(
            ts_codes,
            suspension_source_covered,
            membership,
            active,
            unexplained_data_gap,
            target_available,
            target_breadth,
            self.config.min_cross_section_breadth,
            invariant_counts,
        )
        strict_matrix_replay_safe = not engineering_blockers
        readiness = {
            "strict_matrix_built": True,
            "strict_matrix_replay_safe": strict_matrix_replay_safe,
            "engineering_matrix_ready": strict_matrix_replay_safe,
            "alpha_discovery_ready": False,
            "engineering_blockers": engineering_blockers,
            "research_blockers": [],
            "readiness_split_enforced": True,
            "candidate_blockers": (
                ([] if daily_basic_available else ["daily_basic_feature_family_unavailable"])
                + ([] if financial_available else ["financial_feature_family_unavailable"])
                + ([] if np.all(st_status_known[membership & active]) else ["st_dependent_feature_family_unavailable"])
            ),
            "certification_blockers": [
                "suspension_timing_semantics_uncertified",
                "constituent_publication_timing_unknown",
                "no_future_untouched_holdout",
                "selection_data_reused",
                "vendor_historical_revision_risk",
            ],
            "quality_warnings": (
                [f"localized_unexplained_data_gap:{int(np.count_nonzero(unexplained_data_gap))}"]
                if np.any(unexplained_data_gap)
                else []
            ),
            "governed_freeze_ready": bool(freeze_validation["valid"]),
            "engineering_universe_proxy_ready": True,
            "conservative_tradability_policy_ready": bool(
                np.all(suspension_source_covered[membership_known & membership & active])
            ),
            "untouched_holdout_ready": False,
            "certification_ready": False,
            "portfolio_ready": False,
            "paper_ready": False,
            "live_ready": False,
            "signal_eligible_date_count": int(np.count_nonzero(signal_breadth >= self.config.min_cross_section_breadth)),
            "evaluable_date_count": int(np.count_nonzero(evaluable_dates)),
            "target_available_count": int(np.count_nonzero(target_available)),
            "min_cross_section_breadth": self.config.min_cross_section_breadth,
        }

        generation_inputs = {
            "semantic_hash": self.config.semantic_hash,
            "governed_freeze_content_hash": freeze_manifest["content_hash"],
            "historical_universe_content_hash": universe_manifest["content_hash"],
            "stock_axis_hash": universe_manifest["stock_axis_hash"],
            "date_axis_hash": universe_manifest["date_axis_hash"],
            "artifact_sha256": {
                name: _sha256(path) for name, path in sorted(artifacts.items())
            },
            "suspension_policy_hash": _hash_json(SUSPENSION_POLICY),
        }
        content_hash = _hash_json(generation_inputs)
        generation_id = f"matrix_053a_{content_hash[:24]}"
        target_dir = Path(output_root) / generation_id
        if not target_dir.exists():
            self._write_generation(
                target_dir,
                ts_codes,
                trade_dates,
                snapshot_source_date,
                arrays,
                readiness,
                invariant_counts,
                signal_breadth,
                target_breadth,
                evaluable_dates,
                generation_inputs,
                content_hash,
                universe_manifest,
            )
        _validate_existing_generation(target_dir, content_hash, self.config.semantic_hash)
        return StrictEngineeringPITMatrixResult(
            generation_id=generation_id,
            generation_dir=str(target_dir),
            manifest_path=str(target_dir / "task_052a_strict_matrix_manifest.json"),
            readiness_path=str(target_dir / "task_052a_readiness_report.json"),
            semantic_hash=self.config.semantic_hash,
            content_hash=content_hash,
            n_stocks=shape[0],
            n_dates=shape[1],
            engineering_matrix_ready=strict_matrix_replay_safe,
            alpha_discovery_ready=False,
        )

    def _write_generation(
        self,
        target_dir: Path,
        ts_codes: list[str],
        trade_dates: list[str],
        snapshot_source_date: np.ndarray,
        arrays: Mapping[str, np.ndarray],
        readiness: dict[str, Any],
        invariant_counts: dict[str, int],
        signal_breadth: np.ndarray,
        target_breadth: np.ndarray,
        evaluable_dates: np.ndarray,
        generation_inputs: dict[str, Any],
        content_hash: str,
        universe_manifest: dict[str, Any],
    ) -> None:
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f".{target_dir.name}.", dir=target_dir.parent))
        try:
            _write_json(temporary / "ts_codes.json", ts_codes)
            _write_json(temporary / "trade_dates.json", trade_dates)
            _write_npy(temporary / "snapshot_source_date.npy", snapshot_source_date)
            _write_npy(temporary / "signal_eligible_breadth.npy", signal_breadth.astype(np.int32))
            _write_npy(temporary / "target_available_breadth.npy", target_breadth.astype(np.int32))
            _write_npy(temporary / "evaluable_date_mask.npy", evaluable_dates.astype(np.bool_))
            for name, value in sorted(arrays.items()):
                _write_npy(temporary / f"{name}.npy", value)
            partitions = {
                path.name: _sha256(path) for path in sorted(temporary.iterdir()) if path.is_file()
            }
            manifest = {
                "generation_id": target_dir.name,
                "content_hash": content_hash,
                "semantic_hash": self.config.semantic_hash,
                "semantic_contract": STRICT_MATRIX_SEMANTIC_CONTRACT,
                "generation_inputs": generation_inputs,
                "shape": [len(ts_codes), len(trade_dates)],
                "stock_axis_hash": _hash_lines(ts_codes),
                "date_axis_hash": _hash_lines(trade_dates),
                "raw_fields": ["open", "high", "low", "close", "pre_close", "volume", "amount"],
                "validity_masks": sorted(name for name in arrays if name.endswith("validity")),
                "universe_mode": "daily_lagged_historical_constituents",
                "historical_constituent_proof": bool(universe_manifest.get("historical_constituent_proof")),
                "membership_availability_policy": "snapshot_source_date_plus_one_trade_day",
                "membership_lag_trade_days": 1,
                "evidence_level": "retrospective_pit_proxy",
                "publication_timestamp_proven": False,
                "suspension_policy": SUSPENSION_POLICY,
                "suspension_policy_hash": _hash_json(SUSPENSION_POLICY),
                "source_timing_semantics_certified": False,
                "intraday_simulation_supported": False,
                "raw_truncated_before_compute": True,
                "research_holdout_firewall_enabled": True,
                "research_end_date": self.config.research_observable_cutoff,
                "holdout_start_date": "20240531",
                "label_horizon": self.config.target_endpoint_horizon_trade_days,
                "eligible_date_hash": _hash_lines(
                    [
                        trade_date
                        for trade_date, eligible in zip(trade_dates, arrays["signal_eligible_at_close"].any(axis=0), strict=True)
                        if eligible and trade_date <= self.config.research_observable_cutoff
                    ]
                ),
                "firewall_out_of_bounds_access_count": 0,
                "firewall": {
                    "research_observable_cutoff": self.config.research_observable_cutoff,
                    "diagnostic_period_start": "20240531",
                    "target_endpoint_horizon_trade_days": self.config.target_endpoint_horizon_trade_days,
                    "diagnostic_period_evidence": "reused_diagnostic_only",
                },
                "bar_inference_used": False,
                "adjustment_factor_fill_value": None,
                "target_contract": {
                    "name": self.config.target_name,
                    "signal_date": "t",
                    "entry_price": "adjusted_open[t+1]",
                    "exit_price": "adjusted_open[t+2]",
                    "formula": "adjusted_open[t+2] / adjusted_open[t+1] - 1",
                    "realized_event_day_open_excluded": True,
                    "target_contract_hash": _hash_json(STRICT_MATRIX_SEMANTIC_CONTRACT["target"]),
                },
                "invariant_counts": invariant_counts,
                "array_summary": {
                    name: {
                        "dtype": str(value.dtype),
                        "shape": list(value.shape),
                        "true_or_valid_count": int(np.count_nonzero(value))
                        if value.dtype == np.bool_
                        else int(np.count_nonzero(np.isfinite(value))),
                    }
                    for name, value in sorted(arrays.items())
                },
                "readiness": readiness,
                "partition_sha256": partitions,
                "deterministic_build": True,
            }
            write_json_artifact(
                temporary / "task_052a_strict_matrix_manifest.json",
                manifest,
                "task_052a_strict_matrix_manifest",
                "matrix_store.strict_engineering",
                created_at=DETERMINISTIC_CREATED_AT,
            )
            write_json_artifact(
                temporary / "task_052a_readiness_report.json",
                readiness,
                "task_052a_readiness_report",
                "matrix_store.strict_engineering",
                created_at=DETERMINISTIC_CREATED_AT,
            )
            os.replace(temporary, target_dir)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise


def _artifact_paths(root: Path, manifest: Mapping[str, Any]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for item in manifest.get("artifacts", []):
        name = str(item.get("logical_name") or "")
        path = root / str(item.get("relative_path") or "")
        if not name or not path.is_file():
            raise StrictEngineeringMatrixError(f"invalid governed freeze artifact: {name}")
        result[name] = path
    return result


def _require_records(artifacts: Mapping[str, Path], name: str) -> Iterable[dict[str, Any]]:
    if name not in artifacts:
        raise StrictEngineeringMatrixError(f"required governed artifact missing: {name}")
    return _read_records(artifacts[name])


def _require_any_records(artifacts: Mapping[str, Path], names: tuple[str, ...]) -> Iterable[dict[str, Any]]:
    for name in names:
        if name in artifacts:
            return _read_records(artifacts[name])
    raise StrictEngineeringMatrixError(f"required governed artifact missing: {'|'.join(names)}")


def _optional_records(artifacts: Mapping[str, Path], *names: str) -> Iterable[dict[str, Any]]:
    for name in names:
        if name in artifacts:
            return _read_records(artifacts[name])
    return iter(())


def _align_daily_bars(
    records: Iterable[dict[str, Any]],
    stock_index: Mapping[str, int],
    date_index: Mapping[str, int],
    shape: tuple[int, int],
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], np.ndarray]:
    fields = ("open", "high", "low", "close", "pre_close", "volume", "amount")
    values = {name: np.full(shape, np.nan, dtype=np.float32) for name in fields}
    validity = {name: np.zeros(shape, dtype=np.bool_) for name in fields}
    observed = np.zeros(shape, dtype=np.bool_)
    seen: set[tuple[int, int]] = set()
    for row in records:
        position = _position(row, stock_index, date_index)
        if position is None:
            continue
        if position in seen:
            raise StrictEngineeringMatrixError(f"duplicate daily bar row: {row.get('ts_code')}/{row.get('trade_date')}")
        seen.add(position)
        observed[position] = True
        for field_name in fields:
            source_name = "vol" if field_name == "volume" and "volume" not in row else field_name
            numeric = _finite_float(row.get(source_name))
            if numeric is not None:
                values[field_name][position] = numeric
                validity[field_name][position] = True
    return values, validity, observed


def _align_scalar(
    records: Iterable[dict[str, Any]],
    stock_index: Mapping[str, int],
    date_index: Mapping[str, int],
    shape: tuple[int, int],
    field_names: tuple[str, ...],
    dataset: str,
    *,
    required: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    values = np.full(shape, np.nan, dtype=np.float32)
    validity = np.zeros(shape, dtype=np.bool_)
    seen: set[tuple[int, int]] = set()
    for row in records:
        position = _position(row, stock_index, date_index)
        if position is None:
            continue
        numeric = next((_finite_float(row.get(name)) for name in field_names if _finite_float(row.get(name)) is not None), None)
        if numeric is None:
            continue
        if position in seen:
            raise StrictEngineeringMatrixError(f"duplicate {dataset} row: {row.get('ts_code')}/{row.get('trade_date')}")
        seen.add(position)
        values[position] = numeric
        validity[position] = True
    if required and not np.any(validity):
        raise StrictEngineeringMatrixError(f"required governed source has no aligned values: {dataset}")
    return values, validity


def _align_named_fields(
    records: Iterable[dict[str, Any]],
    stock_index: Mapping[str, int],
    date_index: Mapping[str, int],
    shape: tuple[int, int],
    field_names: tuple[str, ...],
    dataset: str,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    values = {name: np.full(shape, np.nan, dtype=np.float32) for name in field_names}
    validity = {name: np.zeros(shape, dtype=np.bool_) for name in field_names}
    seen: set[tuple[int, int]] = set()
    for row in records:
        position = _position(row, stock_index, date_index)
        if position is None:
            continue
        if position in seen:
            raise StrictEngineeringMatrixError(f"duplicate {dataset} row: {row.get('ts_code')}/{row.get('trade_date')}")
        seen.add(position)
        for name in field_names:
            numeric = _finite_float(row.get(name))
            if numeric is not None:
                values[name][position] = numeric
                validity[name][position] = True
    return values, validity


def _align_financial_asof(
    records: Iterable[dict[str, Any]],
    stock_index: Mapping[str, int],
    trade_dates: list[str],
    shape: tuple[int, int],
    field_names: tuple[str, ...],
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    events: dict[int, list[dict[str, Any]]] = {}
    seen: set[tuple[int, str, str]] = set()
    for row in records:
        stock_position = stock_index.get(str(row.get("ts_code") or ""))
        announce_date = str(row.get("announce_date") or "")
        report_period = str(row.get("report_period") or "")
        if stock_position is None or not _valid_date(announce_date):
            continue
        key = (stock_position, announce_date, report_period)
        if key in seen:
            raise StrictEngineeringMatrixError(
                f"duplicate financial feature row: {row.get('ts_code')}/{announce_date}/{report_period}"
            )
        seen.add(key)
        events.setdefault(stock_position, []).append(row)
    values = {name: np.full(shape, np.nan, dtype=np.float32) for name in field_names}
    validity = {name: np.zeros(shape, dtype=np.bool_) for name in field_names}
    for stock_position, rows in events.items():
        rows.sort(key=lambda row: (str(row.get("announce_date") or ""), str(row.get("report_period") or "")))
        cursor = 0
        latest: dict[str, float] = {}
        for date_position, trade_date in enumerate(trade_dates):
            while cursor < len(rows) and str(rows[cursor].get("announce_date") or "") <= trade_date:
                row = rows[cursor]
                for name in field_names:
                    numeric = _finite_float(row.get(name))
                    if numeric is not None:
                        latest[name] = numeric
                cursor += 1
            for name, numeric in latest.items():
                values[name][stock_position, date_position] = numeric
                validity[name][stock_position, date_position] = True
    return values, validity


def _build_lifecycle(
    records: Iterable[dict[str, Any]], ts_codes: list[str], trade_dates: list[str]
) -> tuple[np.ndarray, np.ndarray]:
    by_code: dict[str, dict[str, Any]] = {}
    for row in records:
        code = str(row.get("ts_code") or "")
        if code in by_code:
            raise StrictEngineeringMatrixError(f"duplicate securities row: {code}")
        by_code[code] = row
    listed = np.zeros((len(ts_codes), len(trade_dates)), dtype=np.bool_)
    active = np.zeros_like(listed)
    for stock_position, code in enumerate(ts_codes):
        row = by_code.get(code)
        if row is None or not _valid_date(row.get("list_date")):
            continue
        list_date = str(row["list_date"])
        delist_date = str(row.get("delist_date") or "") if _valid_date(row.get("delist_date")) else None
        for date_position, trade_date in enumerate(trade_dates):
            is_active = list_date <= trade_date and (delist_date is None or trade_date < delist_date)
            listed[stock_position, date_position] = is_active
            active[stock_position, date_position] = is_active
    return listed, active


def _build_coverage_mask(
    records: Iterable[dict[str, Any]], ts_codes: list[str], trade_dates: list[str], *, dataset: str
) -> np.ndarray:
    stock_index = {code: index for index, code in enumerate(ts_codes)}
    mask = np.zeros((len(ts_codes), len(trade_dates)), dtype=np.bool_)
    for row in records:
        code = str(row.get("ts_code") or "")
        position = stock_index.get(code)
        if position is None or row.get("validated", row.get("success", True)) is not True:
            continue
        start = str(row.get("start_date") or row.get("request_start_date") or row.get("requested_start_date") or "")
        end = str(row.get("end_date") or row.get("request_end_date") or row.get("requested_end_date") or "")
        if not _valid_date(start) or not _valid_date(end) or start > end:
            raise StrictEngineeringMatrixError(f"invalid {dataset} coverage range: {code}/{start}/{end}")
        for date_position, trade_date in enumerate(trade_dates):
            if start <= trade_date <= end:
                mask[position, date_position] = True
    return mask


def _build_suspension_masks(
    records: Iterable[dict[str, Any]],
    stock_index: Mapping[str, int],
    date_index: Mapping[str, int],
    shape: tuple[int, int],
    source_covered: np.ndarray,
) -> dict[str, np.ndarray]:
    present = np.zeros(shape, dtype=np.bool_)
    timing_known = np.zeros(shape, dtype=np.bool_)
    timing_parse_status = np.zeros(shape, dtype=np.uint8)
    canonical_interval = np.zeros(shape, dtype=np.uint8)
    seen: set[tuple[int, int, str, str | None]] = set()
    for row in records:
        position = _position(row, stock_index, date_index)
        if position is None:
            continue
        suspend_type = str(row.get("suspend_type") or "")
        if suspend_type not in {"S", "R"}:
            raise StrictEngineeringMatrixError(f"invalid suspend_type: {suspend_type}")
        raw_timing = row.get("suspend_timing")
        normalized_timing = str(raw_timing).strip() if raw_timing is not None else None
        key = (position[0], position[1], suspend_type, normalized_timing)
        if key in seen:
            raise StrictEngineeringMatrixError(f"duplicate suspension event: {row.get('ts_code')}/{row.get('trade_date')}")
        seen.add(key)
        if not source_covered[position]:
            raise StrictEngineeringMatrixError(f"suspension event outside validated coverage: {row.get('ts_code')}/{row.get('trade_date')}")
        present[position] = True
        timing_known[position] |= bool(normalized_timing)
        parse_code, interval_code = _parse_suspend_timing(normalized_timing)
        timing_parse_status[position] = max(timing_parse_status[position], parse_code)
        canonical_interval[position] = max(canonical_interval[position], interval_code)
    known_absent = source_covered & ~present
    conservative_excluded = present.copy()
    recognized_interval = (canonical_interval >= 1) & (canonical_interval <= 3)
    full_day_known = recognized_interval
    full_day_value = canonical_interval == 1
    open_known = recognized_interval
    open_value = (canonical_interval == 1) | (canonical_interval == 2)
    return {
        "suspension_event_known_absent": known_absent,
        "suspension_event_present": present,
        "suspension_timing_known": timing_known,
        "suspension_timing_parse_status": timing_parse_status,
        "suspension_canonical_interval": canonical_interval,
        "full_day_suspended_value": full_day_value,
        "full_day_suspended_known": full_day_known,
        "open_suspended_value": open_value,
        "open_suspended_known": open_known,
        "conservative_open_excluded": conservative_excluded,
    }


def _parse_suspend_timing(value: str | None) -> tuple[int, int]:
    if value is None or not value.strip():
        return 1, 0
    normalized = value.strip().lower().replace(" ", "")
    if any(token in normalized for token in ("全天", "全日", "fullday", "09:30-15:00", "9:30-15:00")):
        return 2, 1
    if any(token in normalized for token in ("开盘", "open", "09:30", "9:30")):
        return 3, 2
    if any(token in normalized for token in ("盘中", "intraday", "临时")):
        return 4, 3
    return 5, 0


def _build_st_masks(
    records: Iterable[dict[str, Any]],
    stock_index: Mapping[str, int],
    date_index: Mapping[str, int],
    shape: tuple[int, int],
    known: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    effective = np.zeros(shape, dtype=np.bool_)
    information_available = known.copy()
    seen: set[tuple[int, int, str]] = set()
    for row in records:
        position = _position(row, stock_index, date_index)
        if position is None:
            continue
        status_type = str(row.get("type") or "")
        key = (position[0], position[1], status_type)
        if key in seen:
            raise StrictEngineeringMatrixError(f"duplicate stock_st row: {row.get('ts_code')}/{row.get('trade_date')}/{status_type}")
        seen.add(key)
        if not known[position]:
            raise StrictEngineeringMatrixError(f"stock_st row outside validated coverage: {row.get('ts_code')}/{row.get('trade_date')}")
        effective[position] = True
        information_available[position] = True
    return effective, information_available


def _build_target(
    adjusted_open: np.ndarray,
    adjusted_open_validity: np.ndarray,
    active: np.ndarray,
    entry_possible: np.ndarray,
    exit_possible: np.ndarray,
    buyable: np.ndarray,
    sellable: np.ndarray,
    conservative_excluded: np.ndarray,
    gaps: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    target = np.full(adjusted_open.shape, np.nan, dtype=np.float32)
    validity = np.zeros(adjusted_open.shape, dtype=np.bool_)
    if adjusted_open.shape[1] < 3:
        return target, validity
    valid = (
        adjusted_open_validity[:, 1:-1]
        & adjusted_open_validity[:, 2:]
        & active[:, 1:-1]
        & active[:, 2:]
        & entry_possible[:, 1:-1]
        & exit_possible[:, 2:]
        & buyable[:, 1:-1]
        & sellable[:, 2:]
        & ~conservative_excluded[:, 1:-1]
        & ~conservative_excluded[:, 2:]
        & ~gaps[:, 1:-1]
        & ~gaps[:, 2:]
        & (adjusted_open[:, 1:-1] != 0.0)
    )
    computed = np.full(valid.shape, np.nan, dtype=np.float32)
    np.divide(adjusted_open[:, 2:], adjusted_open[:, 1:-1], out=computed, where=valid)
    computed[valid] -= 1.0
    validity[:, :-2] = valid & np.isfinite(computed)
    target[:, :-2] = computed
    target[~validity] = np.nan
    return target, validity


def _validate_invariants(arrays: Mapping[str, np.ndarray]) -> dict[str, int]:
    checks = {
        "event_outside_coverage": arrays["suspension_event_present"] & ~arrays["suspension_source_covered"],
        "event_open_execution_allowed": arrays["suspension_event_present"] & arrays["open_execution_value"],
        "missing_bar_inferred_suspension": arrays["suspension_associated_bar_absence"] & ~arrays["suspension_event_present"],
        "unexplained_gap_used_by_signal": arrays["unexplained_data_gap"] & arrays["signal_eligible_at_close"],
        "unexplained_gap_used_by_target_endpoint": arrays["unexplained_data_gap"] & (
            arrays["realized_entry_possible"] | arrays["realized_exit_possible"]
        ),
        "target_on_event_endpoint": arrays["target_available"][:, :-2] & (
            arrays["conservative_open_excluded"][:, 1:-1] | arrays["conservative_open_excluded"][:, 2:]
        ),
        "carried_adjustment_used": arrays["adjustment_carried"] & arrays["adjusted_open_validity"],
    }
    counts = {name: int(np.count_nonzero(mask)) for name, mask in checks.items()}
    if any(counts.values()):
        raise StrictEngineeringMatrixError(f"strict matrix invariant violation: {counts}")
    return counts


def _engineering_blockers(
    ts_codes: list[str],
    covered: np.ndarray,
    membership: np.ndarray,
    active: np.ndarray,
    gaps: np.ndarray,
    target_available: np.ndarray,
    target_breadth: np.ndarray,
    min_breadth: int,
    invariant_counts: Mapping[str, int],
) -> list[str]:
    blockers: list[str] = []
    required_coverage = membership & active
    missing_coverage = [
        ts_codes[index]
        for index in range(len(ts_codes))
        if np.any(required_coverage[index] & ~covered[index])
    ]
    if missing_coverage:
        blockers.append(f"suspension_coverage_incomplete:{len(missing_coverage)}")
    if np.count_nonzero(target_breadth >= min_breadth) == 0:
        blockers.append("no_evaluable_target_dates_at_policy_breadth")
    if not np.any(target_available):
        blockers.append("target_unavailable")
    for name, count in invariant_counts.items():
        if count:
            blockers.append(f"invariant_violation:{name}:{count}")
    return blockers


def _position(
    row: Mapping[str, Any], stock_index: Mapping[str, int], date_index: Mapping[str, int]
) -> tuple[int, int] | None:
    stock_position = stock_index.get(str(row.get("ts_code") or ""))
    trade_date = str(row.get("trade_date") or "")
    date_position = date_index.get(trade_date)
    if stock_position is None or date_position is None:
        return None
    return stock_position, date_position


def _finite_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if np.isfinite(numeric) else None


def _valid_date(value: Any) -> bool:
    try:
        datetime.strptime(str(value), "%Y%m%d")
    except (TypeError, ValueError):
        return False
    return True


def _validate_universe_proof(root: Path, manifest: dict[str, Any]) -> None:
    checks = manifest.get("proof_checks", {})
    required = {
        "rejected_snapshots_zero",
        "member_count_exact",
        "weights_within_policy",
        "natural_month_coverage_complete",
        "source_lineage_verified",
        "removed_member_leakage_zero",
        "membership_lag_one_trade_day",
    }
    failed = sorted(name for name in required if checks.get(name) is not True)
    if failed:
        raise StrictEngineeringMatrixError(f"historical universe proof checks failed: {','.join(failed)}")
    for filename, expected_hash in manifest.get("partition_sha256", {}).items():
        path = root / filename
        if not path.is_file() or _sha256(path) != expected_hash:
            raise StrictEngineeringMatrixError(f"historical universe partition drift: {filename}")


def _load_exact_array(path: Path, shape: tuple[int, ...], dtype: np.dtype[Any]) -> np.ndarray:
    array = np.load(path, allow_pickle=False)
    if tuple(array.shape) != shape or array.dtype != np.dtype(dtype):
        raise StrictEngineeringMatrixError(
            f"strict array contract mismatch: {path.name} {array.shape}/{array.dtype} != {shape}/{np.dtype(dtype)}"
        )
    return array


def _validate_existing_generation(root: Path, content_hash: str, semantic_hash: str) -> None:
    manifest_path = root / "task_052a_strict_matrix_manifest.json"
    if not manifest_path.is_file():
        raise StrictEngineeringMatrixError(f"existing strict matrix generation is incomplete: {root}")
    manifest = _read_json(manifest_path)
    if manifest.get("content_hash") != content_hash or manifest.get("semantic_hash") != semantic_hash:
        raise StrictEngineeringMatrixError(f"strict matrix content-address collision or mutation: {root}")
    for filename, expected_hash in manifest.get("partition_sha256", {}).items():
        path = root / filename
        if not path.is_file() or _sha256(path) != expected_hash:
            raise StrictEngineeringMatrixError(f"strict matrix immutable partition drift: {filename}")


def _read_records(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise StrictEngineeringMatrixError(f"expected JSON object at {path}:{line_number}")
            yield payload


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise StrictEngineeringMatrixError(f"expected JSON object: {path}")
    return payload


def _read_json_list(path: Path, *, sorted_required: bool) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or any(not isinstance(item, str) for item in payload):
        raise StrictEngineeringMatrixError(f"expected string list: {path}")
    if sorted_required and payload != sorted(set(payload)):
        raise StrictEngineeringMatrixError(f"axis must be sorted and unique: {path.name}")
    return payload


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_npy(path: Path, value: np.ndarray) -> None:
    with path.open("wb") as handle:
        np.save(handle, value, allow_pickle=False)


def _hash_lines(values: list[str]) -> str:
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()


def _hash_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

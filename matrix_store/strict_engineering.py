"""Task 052-A strict point-in-time engineering matrix generation."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

from artifact_schema.writer import write_json_artifact
from data_lake.task052_freeze import validate_task052_governed_freeze


DETERMINISTIC_CREATED_AT = "1970-01-01T00:00:00Z"
STRICT_MATRIX_SEMANTIC_CONTRACT = {
    "task": "052-A",
    "axes": "byte_exact_universe_axes",
    "raw_alignment": "exact_ts_code_trade_date_only",
    "raw_missing_value": "nan_with_false_validity",
    "bar_observation": "explicit_daily_bars_source_row_only",
    "bar_inference": "prohibited",
    "adjustment_factor_default": "prohibited",
    "membership_effective_rule": "one_trade_day_lag_from_governed_universe",
    "target": "next_open_return_from_t_plus_1_open_to_t_plus_2_open",
    "publication": "content_addressed_atomic_directory_rename",
    "version": 1,
}


class StrictEngineeringMatrixError(RuntimeError):
    """Raised when strict PIT matrix invariants are not satisfied."""


@dataclass(frozen=True)
class StrictEngineeringPITMatrixConfig:
    required_raw_fields: tuple[str, ...] = (
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "adj_factor",
    )
    daily_bar_artifact_name: str = "daily_bars"
    target_name: str = "next_open_t1_t2_return"
    research_readiness_requirements: Mapping[str, bool] = field(
        default_factory=lambda: {
            "historical_st_intervals_proved": False,
            "historical_suspensions_proved": False,
            "untouched_holdout_proved": False,
            "research_firewall_enabled": False,
        }
    )
    config_version: str = "task_052a_strict_engineering_matrix_v1"

    def __post_init__(self) -> None:
        if "open" not in self.required_raw_fields:
            raise ValueError("open is required for the Task 052-A next-open target")
        if "adj_factor" not in self.required_raw_fields:
            raise ValueError("adj_factor must be explicit; fill-with-one is prohibited")
        if len(set(self.required_raw_fields)) != len(self.required_raw_fields):
            raise ValueError("required_raw_fields must be unique")

    @property
    def semantic_hash(self) -> str:
        return _hash_json(
            {
                "contract": STRICT_MATRIX_SEMANTIC_CONTRACT,
                "config": {
                    **asdict(self),
                    "research_readiness_requirements": dict(sorted(self.research_readiness_requirements.items())),
                },
            }
        )


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
    """The sole Task 052-A builder for strict engineering PIT matrices."""

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
        freeze_manifest = _read_json(freeze_root / "task_052a_governed_freeze_manifest.json")
        universe_manifest = _read_json(universe_root / "task_052a_universe_proof_manifest.json")
        _validate_universe_proof(universe_root, universe_manifest)
        ts_codes = _read_json_list(universe_root / "ts_codes.json")
        trade_dates = _read_json_list(universe_root / "trade_dates.json")
        shape = (len(ts_codes), len(trade_dates))

        artifact_paths = {
            str(item["logical_name"]): freeze_root / str(item["relative_path"])
            for item in freeze_manifest.get("artifacts", [])
        }
        daily_bars_path = artifact_paths.get(self.config.daily_bar_artifact_name)
        if daily_bars_path is None:
            raise StrictEngineeringMatrixError(
                f"explicit daily bar artifact is required: {self.config.daily_bar_artifact_name}"
            )
        records_by_artifact = {
            logical_name: list(_read_records(path))
            for logical_name, path in sorted(artifact_paths.items())
            if path.suffix == ".jsonl"
        }
        raw, validity, source_counts = _align_raw_fields_exact(
            records_by_artifact,
            ts_codes,
            trade_dates,
            self.config.required_raw_fields,
        )
        bar_observed_mask = _explicit_bar_observed_mask(
            records_by_artifact.get(self.config.daily_bar_artifact_name, []),
            ts_codes,
            trade_dates,
        )
        _assert_bar_fields_have_explicit_rows(raw, validity, bar_observed_mask)
        _assert_adjustment_factor_not_filled(raw["adj_factor"], validity["adj_factor"])

        membership = _load_exact_array(universe_root / "index_membership.npy", shape, np.bool_)
        index_weight = _load_exact_array(universe_root / "index_weight.npy", shape, np.float32)
        membership_known_1d = _load_exact_array(universe_root / "membership_known.npy", (shape[1],), np.bool_)
        membership_known = np.broadcast_to(membership_known_1d, shape).copy()
        target, target_validity = _build_next_open_t1_t2_target(raw["open"], validity["open"])

        engineering_blockers: list[str] = []
        if not np.any(bar_observed_mask):
            engineering_blockers.append("no_explicit_daily_bar_rows_on_axis")
        for field_name in self.config.required_raw_fields:
            if not np.any(validity[field_name]):
                engineering_blockers.append(f"required_raw_field_unobserved:{field_name}")
        if not np.any(target_validity):
            engineering_blockers.append("next_open_target_unavailable")
        engineering_matrix_ready = not engineering_blockers
        research_blockers = [
            key for key, value in sorted(self.config.research_readiness_requirements.items()) if not bool(value)
        ]
        alpha_discovery_ready = engineering_matrix_ready and not research_blockers
        readiness = {
            "engineering_matrix_ready": engineering_matrix_ready,
            "engineering_blockers": engineering_blockers,
            "alpha_discovery_ready": alpha_discovery_ready,
            "research_blockers": research_blockers,
            "readiness_split_enforced": True,
            "governed_freeze_ready": bool(freeze_validation["valid"]),
            "historical_universe_proof_ready": True,
        }

        generation_inputs = {
            "semantic_hash": self.config.semantic_hash,
            "governed_freeze_content_hash": freeze_manifest["content_hash"],
            "historical_universe_content_hash": universe_manifest["content_hash"],
            "stock_axis_hash": universe_manifest["stock_axis_hash"],
            "date_axis_hash": universe_manifest["date_axis_hash"],
            "required_raw_fields": list(self.config.required_raw_fields),
            "research_readiness_requirements": dict(sorted(self.config.research_readiness_requirements.items())),
        }
        content_hash = _hash_json(generation_inputs)
        generation_id = f"matrix_052a_{content_hash[:24]}"
        target_dir = Path(output_root) / generation_id
        if not target_dir.exists():
            self._write_generation(
                target_dir=target_dir,
                ts_codes=ts_codes,
                trade_dates=trade_dates,
                raw=raw,
                validity=validity,
                bar_observed_mask=bar_observed_mask,
                membership=membership,
                index_weight=index_weight,
                membership_known=membership_known,
                target=target,
                target_validity=target_validity,
                source_counts=source_counts,
                readiness=readiness,
                generation_inputs=generation_inputs,
                content_hash=content_hash,
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
            engineering_matrix_ready=engineering_matrix_ready,
            alpha_discovery_ready=alpha_discovery_ready,
        )

    def _write_generation(
        self,
        *,
        target_dir: Path,
        ts_codes: list[str],
        trade_dates: list[str],
        raw: dict[str, np.ndarray],
        validity: dict[str, np.ndarray],
        bar_observed_mask: np.ndarray,
        membership: np.ndarray,
        index_weight: np.ndarray,
        membership_known: np.ndarray,
        target: np.ndarray,
        target_validity: np.ndarray,
        source_counts: dict[str, int],
        readiness: dict[str, Any],
        generation_inputs: dict[str, Any],
        content_hash: str,
    ) -> None:
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f".{target_dir.name}.", dir=target_dir.parent))
        try:
            _write_json(temporary / "ts_codes.json", ts_codes)
            _write_json(temporary / "trade_dates.json", trade_dates)
            for field_name in self.config.required_raw_fields:
                _write_npy(temporary / f"{field_name}.npy", raw[field_name].astype(np.float32))
                _write_npy(temporary / f"{field_name}_valid_mask.npy", validity[field_name].astype(np.bool_))
            _write_npy(temporary / "bar_observed_mask.npy", bar_observed_mask.astype(np.bool_))
            _write_npy(temporary / "index_membership.npy", membership.astype(np.bool_))
            _write_npy(temporary / "index_weight.npy", index_weight.astype(np.float32))
            _write_npy(temporary / "membership_known_mask.npy", membership_known.astype(np.bool_))
            _write_npy(temporary / f"{self.config.target_name}.npy", target.astype(np.float32))
            _write_npy(temporary / "target_available_mask.npy", target_validity.astype(np.bool_))
            write_json_artifact(
                temporary / "task_052a_readiness_report.json",
                readiness,
                "task_052a_matrix_readiness_report",
                "matrix_store.strict_engineering",
                created_at=DETERMINISTIC_CREATED_AT,
            )
            partitions = {
                path.name: _sha256(path)
                for path in sorted(temporary.iterdir())
                if path.is_file()
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
                "raw_fields": list(self.config.required_raw_fields),
                "raw_field_source_observation_counts": source_counts,
                "validity_masks": [f"{field}_valid_mask.npy" for field in self.config.required_raw_fields],
                "bar_observation_source": self.config.daily_bar_artifact_name,
                "bar_inference_used": False,
                "adjustment_factor_fill_value": None,
                "membership_lag_trade_days": 1,
                "target_contract": {
                    "name": self.config.target_name,
                    "signal_date": "t",
                    "entry_price": "open[t+1]",
                    "exit_price": "open[t+2]",
                    "formula": "open[t+2] / open[t+1] - 1",
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
            os.replace(temporary, target_dir)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise


def _align_raw_fields_exact(
    records_by_artifact: Mapping[str, list[dict[str, Any]]],
    ts_codes: list[str],
    trade_dates: list[str],
    fields: tuple[str, ...],
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict[str, int]]:
    stock_index = {code: index for index, code in enumerate(ts_codes)}
    date_index = {date: index for index, date in enumerate(trade_dates)}
    values = {field: np.full((len(ts_codes), len(trade_dates)), np.nan, dtype=np.float32) for field in fields}
    validity = {field: np.zeros((len(ts_codes), len(trade_dates)), dtype=np.bool_) for field in fields}
    source_counts = {field: 0 for field in fields}
    owners: dict[tuple[str, int, int], str] = {}
    for artifact_name, records in sorted(records_by_artifact.items()):
        for row in records:
            stock_position = stock_index.get(str(row.get("ts_code") or ""))
            date_position = date_index.get(str(row.get("trade_date") or ""))
            if stock_position is None or date_position is None:
                continue
            for field_name in fields:
                raw_value = row.get(field_name)
                if raw_value is None:
                    continue
                try:
                    numeric = float(raw_value)
                except (TypeError, ValueError):
                    continue
                if not np.isfinite(numeric):
                    continue
                key = (field_name, stock_position, date_position)
                if key in owners:
                    raise StrictEngineeringMatrixError(
                        f"ambiguous duplicate raw observation for {field_name} at "
                        f"{ts_codes[stock_position]}/{trade_dates[date_position]} from {owners[key]} and {artifact_name}"
                    )
                owners[key] = artifact_name
                values[field_name][stock_position, date_position] = numeric
                validity[field_name][stock_position, date_position] = True
                source_counts[field_name] += 1
    return values, validity, source_counts


def _explicit_bar_observed_mask(
    records: Iterable[dict[str, Any]],
    ts_codes: list[str],
    trade_dates: list[str],
) -> np.ndarray:
    stock_index = {code: index for index, code in enumerate(ts_codes)}
    date_index = {date: index for index, date in enumerate(trade_dates)}
    mask = np.zeros((len(ts_codes), len(trade_dates)), dtype=np.bool_)
    seen: set[tuple[int, int]] = set()
    for row in records:
        stock_position = stock_index.get(str(row.get("ts_code") or ""))
        date_position = date_index.get(str(row.get("trade_date") or ""))
        if stock_position is None or date_position is None:
            continue
        key = (stock_position, date_position)
        if key in seen:
            raise StrictEngineeringMatrixError(
                f"duplicate daily bar row at {ts_codes[stock_position]}/{trade_dates[date_position]}"
            )
        seen.add(key)
        mask[key] = True
    return mask


def _assert_bar_fields_have_explicit_rows(
    raw: Mapping[str, np.ndarray],
    validity: Mapping[str, np.ndarray],
    bar_observed_mask: np.ndarray,
) -> None:
    for field_name in ("open", "high", "low", "close", "volume", "amount"):
        if field_name not in raw:
            continue
        if np.any(validity[field_name] & ~bar_observed_mask):
            raise StrictEngineeringMatrixError(f"bar field observed without explicit daily bar row: {field_name}")


def _assert_adjustment_factor_not_filled(values: np.ndarray, validity: np.ndarray) -> None:
    if np.any(np.isfinite(values[~validity])):
        raise StrictEngineeringMatrixError("invalid adjustment factors must remain NaN")


def _build_next_open_t1_t2_target(open_values: np.ndarray, open_validity: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    target = np.full(open_values.shape, np.nan, dtype=np.float32)
    validity = np.zeros(open_values.shape, dtype=np.bool_)
    if open_values.shape[1] < 3:
        return target, validity
    valid = open_validity[:, 1:-1] & open_validity[:, 2:]
    nonzero_entry = open_values[:, 1:-1] != 0.0
    valid &= nonzero_entry
    computed = np.full(valid.shape, np.nan, dtype=np.float32)
    np.divide(open_values[:, 2:], open_values[:, 1:-1], out=computed, where=valid)
    computed[valid] -= 1.0
    target[:, :-2] = computed
    validity[:, :-2] = valid & np.isfinite(computed)
    target[~validity] = np.nan
    return target, validity


def _validate_universe_proof(root: Path, manifest: dict[str, Any]) -> None:
    checks = manifest.get("proof_checks", {})
    required_checks = {
        "rejected_snapshots_zero",
        "member_count_exact",
        "weights_within_policy",
        "natural_month_coverage_complete",
        "source_lineage_verified",
        "removed_member_leakage_zero",
        "membership_lag_one_trade_day",
    }
    failed = sorted(name for name in required_checks if checks.get(name) is not True)
    if failed:
        raise StrictEngineeringMatrixError(f"historical universe proof checks failed: {','.join(failed)}")
    if manifest.get("membership_lag_trade_days") != 1:
        raise StrictEngineeringMatrixError("historical universe membership lag must equal one trade day")
    for filename, expected_hash in manifest.get("partition_sha256", {}).items():
        path = root / filename
        if not path.is_file() or _sha256(path) != expected_hash:
            raise StrictEngineeringMatrixError(f"historical universe partition drift: {filename}")


def _load_exact_array(path: Path, shape: tuple[int, ...], dtype: np.dtype[Any]) -> np.ndarray:
    array = np.load(path, allow_pickle=False)
    if tuple(array.shape) != shape:
        raise StrictEngineeringMatrixError(f"strict array shape mismatch: {path.name} {array.shape} != {shape}")
    if array.dtype != np.dtype(dtype):
        raise StrictEngineeringMatrixError(f"strict array dtype mismatch: {path.name} {array.dtype} != {np.dtype(dtype)}")
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


def _read_json_list(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or any(not isinstance(item, str) for item in payload):
        raise StrictEngineeringMatrixError(f"expected string list: {path}")
    if payload != sorted(set(payload)):
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
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

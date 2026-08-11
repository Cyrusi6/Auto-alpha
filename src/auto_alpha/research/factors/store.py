"""Content-addressed factor records, identity hashing, lifecycle, storage, and normalized overlays."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FactorRecord:
    factor_id: str
    formula: list[str]
    formula_tokens: list[int]
    formula_hash: str
    feature_version: str
    operator_version: str
    lookback_days: int
    created_at: str
    status: str = "candidate"
    description: str | None = None
    metrics: dict[str, float] | None = None
    transform_method: str | None = None
    gate_status: str | None = None
    gate_reasons: list[str] | None = None
    metadata: dict[str, object] | None = None
    parent_factor_ids: list[str] | None = None
    factor_type: str | None = None
    batch_id: str | None = None


@dataclass(frozen=True)
class ExperimentRecord:
    experiment_id: str
    factor_id: str
    data_dir: str
    output_dir: str
    train_dates: list[str]
    valid_dates: list[str]
    test_dates: list[str]
    metrics_by_split: dict[str, dict[str, float]]
    created_at: str
    notes: str | None = None


@dataclass(frozen=True)
class FactorValueRecord:
    factor_id: str
    trade_date: str
    ts_code: str
    value: float | None


@dataclass(frozen=True)
class StorageResult:
    path: str
    records: int

import hashlib
import json


def stable_formula_hash(
    formula_tokens: list[int],
    formula_names: list[str],
    feature_version: str,
    operator_version: str,
) -> str:
    payload = {
        "feature_version": feature_version,
        "formula_names": formula_names,
        "formula_tokens": formula_tokens,
        "operator_version": operator_version,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def make_factor_id(formula_hash: str) -> str:
    return f"factor_{formula_hash[:16]}"


def make_experiment_id(factor_id: str, created_at: str) -> str:
    safe_timestamp = "".join(char if char.isalnum() else "_" for char in created_at).strip("_")
    suffix = factor_id.removeprefix("factor_")
    return f"exp_{suffix}_{safe_timestamp}"

from enum import StrEnum
from typing import Any, Mapping


class FactorLifecycleStatus(StrEnum):
    generated = "generated"
    research_evaluated = "research_evaluated"
    research_rejected = "research_rejected"
    validation_candidate = "validation_candidate"
    validation_data_blocked = "validation_data_blocked"
    statistically_rejected = "statistically_rejected"
    historical_replay_passed = "historical_replay_passed"
    clean_holdout_passed = "clean_holdout_passed"
    factor_certified = "factor_certified"
    composite_unvalidated = "composite_unvalidated"


VALIDATION_ADMISSION_STATUS = FactorLifecycleStatus.validation_candidate.value


def has_positive_oos_evidence(payload: Any) -> bool:
    """Return true only for an explicit positive, evaluable test-split decision."""

    status = str(_value(payload, "status") or "")
    if status != VALIDATION_ADMISSION_STATUS:
        return False
    metadata = _mapping(_value(payload, "metadata"))
    decision = _mapping(metadata.get("gate_decision"))
    checks = _mapping(decision.get("checks"))
    return bool(
        decision.get("passed") is True
        and checks.get("oos_evidence_positive") is True
        and _finite_positive(checks.get("test_evaluable_date_count"))
        and _finite_positive(checks.get("test_valid_observation_count"))
        and _finite_positive(checks.get("test_rank_ic_mean"))
    )


def validation_admission_reason(payload: Any) -> str:
    if str(_value(payload, "status") or "") != VALIDATION_ADMISSION_STATUS:
        return "factor_status_not_validation_candidate"
    if not has_positive_oos_evidence(payload):
        return "positive_oos_evidence_missing"
    return "validation_candidate_admitted"


def _value(payload: Any, key: str) -> Any:
    if isinstance(payload, Mapping):
        return payload.get(key)
    return getattr(payload, key, None)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _finite_positive(value: Any) -> bool:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return False
    return numeric > 0.0 and numeric < float("inf")

import json
import math
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

import torch



class LocalFactorStore:
    def __init__(self, root_dir: str | Path):
        self.root_dir = Path(root_dir)
        self.factor_path = self.root_dir / "factors.jsonl"
        self.experiment_path = self.root_dir / "experiments.jsonl"
        self.values_dir = self.root_dir / "factor_values"

    def save_factor(self, record: FactorRecord) -> StorageResult:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self._append_jsonl(self.factor_path, record)
        return StorageResult(path=str(self.factor_path), records=1)

    def save_experiment(self, record: ExperimentRecord) -> StorageResult:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self._append_jsonl(self.experiment_path, record)
        return StorageResult(path=str(self.experiment_path), records=1)

    def save_factor_values(
        self,
        factor_id: str,
        ts_codes: list[str],
        trade_dates: list[str],
        values: Any,
    ) -> StorageResult:
        self.values_dir.mkdir(parents=True, exist_ok=True)
        matrix = self._to_matrix(values)
        path = self.values_dir / f"{factor_id}.jsonl"
        count = 0
        with path.open("w", encoding="utf-8") as handle:
            for date_idx, trade_date in enumerate(trade_dates):
                for stock_idx, ts_code in enumerate(ts_codes):
                    value = matrix[stock_idx][date_idx]
                    record = FactorValueRecord(
                        factor_id=factor_id,
                        trade_date=trade_date,
                        ts_code=ts_code,
                        value=self._clean_float(value),
                    )
                    handle.write(json.dumps(asdict(record), ensure_ascii=False, sort_keys=True))
                    handle.write("\n")
                    count += 1
        return StorageResult(path=str(path), records=count)

    def load_factors(self) -> list[FactorRecord]:
        return [FactorRecord(**self._factor_payload_with_defaults(payload)) for payload in self._read_jsonl(self.factor_path)]

    def list_factors(self, status: str | None = None, factor_type: str | None = None) -> list[FactorRecord]:
        records = self.load_factors()
        if status is not None:
            records = [record for record in records if record.status == status]
        if factor_type is not None:
            records = [record for record in records if (record.factor_type or "single") == factor_type]
        return records

    def load_latest_factor(self, status: str | None = None, factor_type: str | None = None) -> FactorRecord | None:
        records = self.list_factors(status=status, factor_type=factor_type)
        return records[-1] if records else None

    def load_experiments(self) -> list[ExperimentRecord]:
        return [ExperimentRecord(**payload) for payload in self._read_jsonl(self.experiment_path)]

    def load_factor_values(self, factor_id: str) -> list[FactorValueRecord]:
        path = self.values_dir / f"{factor_id}.jsonl"
        return [FactorValueRecord(**payload) for payload in self._read_jsonl(path)]

    def find_factor_by_hash(self, formula_hash: str) -> FactorRecord | None:
        for record in self.load_factors():
            if record.formula_hash == formula_hash:
                return record
        return None

    def update_factor_status(
        self,
        factor_id: str,
        status: str,
        reason: str | None = None,
        promotion_decision: dict[str, Any] | None = None,
    ) -> StorageResult:
        records = self.load_factors()
        updated: list[FactorRecord] = []
        count = 0
        for record in records:
            if record.factor_id != factor_id:
                updated.append(record)
                continue
            reasons = list(record.gate_reasons or [])
            if reason:
                reasons.append(reason)
            metadata = dict(record.metadata or {})
            if promotion_decision is not None:
                metadata["promotion_decision"] = promotion_decision
            updated.append(
                FactorRecord(
                    factor_id=record.factor_id,
                    formula=record.formula,
                    formula_tokens=record.formula_tokens,
                    formula_hash=record.formula_hash,
                    feature_version=record.feature_version,
                    operator_version=record.operator_version,
                    lookback_days=record.lookback_days,
                    created_at=record.created_at,
                    status=status,
                    description=record.description,
                    metrics=record.metrics,
                    transform_method=record.transform_method,
                    gate_status=record.gate_status,
                    gate_reasons=reasons or None,
                    metadata=metadata or None,
                    parent_factor_ids=record.parent_factor_ids,
                    factor_type=record.factor_type,
                    batch_id=record.batch_id,
                )
            )
            count += 1

        self.root_dir.mkdir(parents=True, exist_ok=True)
        with self.factor_path.open("w", encoding="utf-8") as handle:
            for record in updated:
                handle.write(json.dumps(asdict(record), ensure_ascii=False, sort_keys=True))
                handle.write("\n")
        return StorageResult(path=str(self.factor_path), records=count)

    def load_factor_values_matrix(
        self,
        factor_id: str,
        ts_codes: list[str],
        trade_dates: list[str],
        device: torch.device | str | None = None,
    ) -> torch.Tensor:
        target_device = torch.device(device) if device is not None else None
        matrix = torch.zeros((len(ts_codes), len(trade_dates)), dtype=torch.float32, device=target_device)
        stock_index = {ts_code: idx for idx, ts_code in enumerate(ts_codes)}
        date_index = {trade_date: idx for idx, trade_date in enumerate(trade_dates)}
        for record in self.load_factor_values(factor_id):
            stock_idx = stock_index.get(record.ts_code)
            date_idx = date_index.get(record.trade_date)
            if stock_idx is None or date_idx is None or record.value is None:
                continue
            matrix[stock_idx, date_idx] = float(record.value)
        return matrix

    def sync_status_from_model_registry(self, registry: Any, model_version_id: str) -> StorageResult:
        model = registry.get_model_version(model_version_id)
        if model is None:
            raise FileNotFoundError(f"model version not found: {model_version_id}")
        return self.update_factor_status(
            model.factor_id,
            model.lifecycle_status,
            reason=f"model_registry:{model.lifecycle_status}",
        )

    @staticmethod
    def _append_jsonl(path: Path, record: object) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(LocalFactorStore._to_payload(record), ensure_ascii=False, sort_keys=True))
            handle.write("\n")

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    @staticmethod
    def _to_payload(record: object) -> dict[str, Any]:
        if is_dataclass(record) and not isinstance(record, type):
            return asdict(record)
        if isinstance(record, dict):
            return dict(record)
        raise TypeError(f"record must be a dataclass instance or dict: {type(record)!r}")

    @staticmethod
    def _factor_payload_with_defaults(payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        normalized.setdefault("transform_method", None)
        normalized.setdefault("gate_status", None)
        normalized.setdefault("gate_reasons", None)
        normalized.setdefault("metadata", None)
        normalized.setdefault("parent_factor_ids", None)
        normalized.setdefault("factor_type", None)
        normalized.setdefault("batch_id", None)
        return normalized

    @staticmethod
    def _to_matrix(values: Any) -> list[list[float]]:
        if hasattr(values, "detach"):
            values = values.detach().cpu()
        if hasattr(values, "tolist"):
            values = values.tolist()
        return values

    @staticmethod
    def _clean_float(value: Any) -> float | None:
        if value is None:
            return None
        numeric = float(value)
        if not math.isfinite(numeric):
            return None
        return numeric

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any, Iterable


def publish_normalized_factor_overlay(
    output_root: str | Path,
    records: Iterable[dict[str, Any]],
    *,
    source_lineage: dict[str, str],
    semantics_contract_hash: str,
) -> dict[str, Any]:
    """Publish an immutable normalized overlay without touching the source store."""

    root = Path(output_root)
    canonical_records = sorted(
        (dict(record) for record in records),
        key=lambda row: (str(row.get("formula_hash", "")), str(row.get("factor_id", ""))),
    )
    records_bytes = b"".join(_canonical_json(row) + b"\n" for row in canonical_records)
    records_sha256 = hashlib.sha256(records_bytes).hexdigest()
    content_payload = {
        "overlay_version": "task054b_normalized_factor_overlay_v1",
        "records_sha256": records_sha256,
        "record_count": len(canonical_records),
        "semantics_contract_hash": semantics_contract_hash,
        "source_lineage": dict(sorted(source_lineage.items())),
    }
    content_hash = hashlib.sha256(_canonical_json(content_payload)).hexdigest()
    generation_id = f"normalized_factors_{content_hash[:24]}"
    generations = root / "generations"
    target = generations / generation_id
    manifest = content_payload | {
        "generation_id": generation_id,
        "content_hash": content_hash,
        "records_file": "normalized_factors.jsonl",
    }

    if target.exists():
        _validate_existing(target, manifest, records_sha256)
    else:
        generations.mkdir(parents=True, exist_ok=True)
        staging = generations / f".{generation_id}.{uuid.uuid4().hex}.staging"
        staging.mkdir(parents=False, exist_ok=False)
        try:
            (staging / "normalized_factors.jsonl").write_bytes(records_bytes)
            (staging / "overlay_manifest.json").write_bytes(_pretty_json(manifest))
            os.replace(staging, target)
        finally:
            if staging.exists():
                for path in staging.iterdir():
                    path.unlink()
                staging.rmdir()

    pointer = {
        "generation_id": generation_id,
        "content_hash": content_hash,
        "manifest": f"generations/{generation_id}/overlay_manifest.json",
    }
    root.mkdir(parents=True, exist_ok=True)
    pointer_tmp = root / f".current.{uuid.uuid4().hex}.tmp"
    pointer_tmp.write_bytes(_pretty_json(pointer))
    os.replace(pointer_tmp, root / "current.json")
    return manifest | {"generation_dir": str(target)}


def _validate_existing(target: Path, expected: dict[str, Any], records_sha256: str) -> None:
    manifest_path = target / "overlay_manifest.json"
    records_path = target / "normalized_factors.jsonl"
    if not manifest_path.is_file() or not records_path.is_file():
        raise RuntimeError("normalized_overlay_generation_incomplete")
    actual = json.loads(manifest_path.read_text(encoding="utf-8"))
    if actual != expected:
        raise RuntimeError("normalized_overlay_generation_collision")
    if hashlib.sha256(records_path.read_bytes()).hexdigest() != records_sha256:
        raise RuntimeError("normalized_overlay_records_sha_mismatch")


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pretty_json(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")

__all__ = [
    "ExperimentRecord",
    "FactorRecord",
    "FactorLifecycleStatus",
    "FactorValueRecord",
    "LocalFactorStore",
    "StorageResult",
    "make_experiment_id",
    "make_factor_id",
    "has_positive_oos_evidence",
    "stable_formula_hash",
    "validation_admission_reason",
    "publish_normalized_factor_overlay",
]

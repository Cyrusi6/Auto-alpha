"""Local JSONL storage for factor records."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

import torch

from .models import ExperimentRecord, FactorRecord, FactorValueRecord, StorageResult


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

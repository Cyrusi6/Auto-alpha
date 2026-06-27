"""Local JSONL storage for factor records."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

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

    def load_experiments(self) -> list[ExperimentRecord]:
        return [ExperimentRecord(**payload) for payload in self._read_jsonl(self.experiment_path)]

    def load_factor_values(self, factor_id: str) -> list[FactorValueRecord]:
        path = self.values_dir / f"{factor_id}.jsonl"
        return [FactorValueRecord(**payload) for payload in self._read_jsonl(path)]

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

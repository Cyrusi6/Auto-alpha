"""Local JSONL storage for A-share datasets."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .config import AShareDataConfig


DATASET_PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "securities": ("ts_code",),
    "trade_calendar": ("trade_date",),
    "daily_bars": ("ts_code", "trade_date"),
    "daily_basic": ("ts_code", "trade_date"),
    "financial_features": ("ts_code", "report_period", "announce_date"),
}


@dataclass(frozen=True)
class StorageWriteResult:
    dataset: str
    path: str
    records: int


class LocalAshareStorage:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)

    @property
    def manifest_path(self) -> Path:
        return self.data_dir / "manifest.json"

    def dataset_path(self, dataset_name: str) -> Path:
        return self.data_dir / dataset_name / "records.jsonl"

    def read_dataset(self, dataset_name: str) -> list[dict[str, Any]]:
        path = self.dataset_path(dataset_name)
        if not path.exists():
            return []

        records: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if stripped:
                    payload = json.loads(stripped)
                    if isinstance(payload, dict):
                        records.append(payload)
        return records

    def write_dataset(
        self,
        dataset_name: str,
        records: Sequence[object],
        mode: str = "overwrite",
    ) -> StorageWriteResult:
        if mode not in {"overwrite", "append"}:
            raise ValueError("mode must be one of: overwrite, append")

        path = self.dataset_path(dataset_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        payloads = [self._to_jsonable(record) for record in records]
        if mode == "append":
            payloads = self._deduplicate_records(dataset_name, [*self.read_dataset(dataset_name), *payloads])

        with path.open("w", encoding="utf-8") as handle:
            for payload in payloads:
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
                handle.write("\n")

        return StorageWriteResult(dataset=dataset_name, path=str(path), records=len(payloads))

    def write_manifest(
        self,
        config: AShareDataConfig,
        datasets: Sequence[StorageWriteResult],
    ) -> StorageWriteResult:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        path = self.manifest_path
        payload = {
            "provider": config.provider,
            "universe": config.universe,
            "start_date": config.start_date,
            "end_date": config.end_date,
            "adjust": config.adjust,
            "datasets": [asdict(result) for result in datasets],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return StorageWriteResult(dataset="manifest", path=str(path), records=1)

    @staticmethod
    def _deduplicate_records(dataset_name: str, records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        key_fields = DATASET_PRIMARY_KEYS.get(dataset_name)
        if key_fields is None:
            return list(records)

        order: list[tuple[Any, ...]] = []
        deduped: dict[tuple[Any, ...], dict[str, Any]] = {}
        for record in records:
            key = tuple(record.get(field) for field in key_fields)
            if any(value is None for value in key):
                key = (*key, len(order))
            if key not in deduped:
                order.append(key)
            deduped[key] = record
        return [deduped[key] for key in order]

    @staticmethod
    def _to_jsonable(record: object) -> dict[str, Any]:
        if is_dataclass(record) and not isinstance(record, type):
            return asdict(record)
        if isinstance(record, dict):
            return dict(record)
        raise TypeError(f"record must be a dataclass instance or dict: {type(record)!r}")

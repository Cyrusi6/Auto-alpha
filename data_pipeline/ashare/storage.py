"""Local JSONL storage for A-share datasets."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .config import AShareDataConfig


@dataclass(frozen=True)
class StorageWriteResult:
    dataset: str
    path: str
    records: int


class LocalAshareStorage:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)

    def write_dataset(self, dataset_name: str, records: Sequence[object]) -> StorageWriteResult:
        dataset_dir = self.data_dir / dataset_name
        dataset_dir.mkdir(parents=True, exist_ok=True)
        path = dataset_dir / "records.jsonl"

        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                payload = self._to_jsonable(record)
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
                handle.write("\n")

        return StorageWriteResult(dataset=dataset_name, path=str(path), records=len(records))

    def write_manifest(
        self,
        config: AShareDataConfig,
        datasets: Sequence[StorageWriteResult],
    ) -> StorageWriteResult:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        path = self.data_dir / "manifest.json"
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
    def _to_jsonable(record: object) -> dict[str, Any]:
        if is_dataclass(record) and not isinstance(record, type):
            return asdict(record)
        if isinstance(record, dict):
            return dict(record)
        raise TypeError(f"record must be a dataclass instance or dict: {type(record)!r}")

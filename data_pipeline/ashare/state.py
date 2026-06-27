"""Pipeline sync state persistence for local A-share datasets."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DatasetSyncState:
    dataset: str
    last_sync_at: str
    records: int
    start_date: str
    end_date: str | None


@dataclass
class PipelineSyncState:
    datasets: dict[str, DatasetSyncState] = field(default_factory=dict)
    updated_at: str | None = None

    def update_dataset(
        self,
        dataset: str,
        records: int,
        start_date: str,
        end_date: str | None,
        synced_at: str | None = None,
    ) -> None:
        timestamp = synced_at or _utc_now()
        self.datasets[dataset] = DatasetSyncState(
            dataset=dataset,
            last_sync_at=timestamp,
            records=records,
            start_date=start_date,
            end_date=end_date,
        )
        self.updated_at = timestamp

    def to_dict(self) -> dict[str, Any]:
        return {
            "updated_at": self.updated_at,
            "datasets": {
                name: asdict(dataset_state)
                for name, dataset_state in sorted(self.datasets.items())
            },
        }


def load_pipeline_state(path: str | Path) -> PipelineSyncState:
    state_path = Path(path)
    if not state_path.exists():
        return PipelineSyncState()

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    datasets = {
        name: DatasetSyncState(**dataset_payload)
        for name, dataset_payload in (payload.get("datasets") or {}).items()
    }
    return PipelineSyncState(
        datasets=datasets,
        updated_at=payload.get("updated_at"),
    )


def save_pipeline_state(state: PipelineSyncState, path: str | Path) -> Path:
    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(state.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return state_path


def default_pipeline_state_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / "pipeline_state.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

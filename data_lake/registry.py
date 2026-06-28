"""Local JSON/JSONL data lake registry."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from artifact_schema.writer import utc_now, write_json_artifact, write_jsonl_artifact

from .models import DataLakeRegistry, DatasetVersionRecord, ResearchDataFreeze


class LocalDataLakeRegistry:
    def __init__(self, root_dir: str | Path):
        self.root_dir = Path(root_dir)
        self.registry_path = self.root_dir / "data_lake_registry.json"
        self.versions_path = self.root_dir / "dataset_versions.jsonl"
        self.freezes_path = self.root_dir / "research_freezes.jsonl"
        self.events_path = self.root_dir / "data_lake_events.jsonl"

    def register_dataset_version(self, record: DatasetVersionRecord) -> DatasetVersionRecord:
        existing = self.find_version_by_content_hash(record.content_hash)
        if existing is not None:
            return existing
        versions = [*self.list_versions(), record]
        self._write_versions(versions)
        self._append_event("create_version", record.dataset_version_id, {"content_hash": record.content_hash})
        self._write_registry()
        return record

    def get_dataset_version(self, version_id: str) -> DatasetVersionRecord | None:
        return next((record for record in self.list_versions() if record.dataset_version_id == version_id), None)

    def find_version_by_content_hash(self, content_hash: str) -> DatasetVersionRecord | None:
        if not content_hash:
            return None
        return next((record for record in self.list_versions() if record.content_hash == content_hash), None)

    def latest_dataset_version(self, provider: str | None = None, status: str = "validated") -> DatasetVersionRecord | None:
        records = [
            record for record in self.list_versions()
            if (provider is None or record.provider == provider) and (status is None or record.status == status)
        ]
        return records[-1] if records else None

    def list_versions(self) -> list[DatasetVersionRecord]:
        return [DatasetVersionRecord(**payload) for payload in _read_jsonl(self.versions_path)]

    def register_freeze(self, freeze: ResearchDataFreeze) -> ResearchDataFreeze:
        existing = self.get_freeze(freeze.freeze_id)
        if existing is not None:
            return existing
        freezes = [*self.list_freezes(), freeze]
        self._write_freezes(freezes)
        self._append_event("create_freeze", freeze.freeze_id, {"dataset_version_id": freeze.dataset_version_id})
        self._write_registry()
        return freeze

    def get_freeze(self, freeze_id: str) -> ResearchDataFreeze | None:
        return next((freeze for freeze in self.list_freezes() if freeze.freeze_id == freeze_id), None)

    def list_freezes(self) -> list[ResearchDataFreeze]:
        return [ResearchDataFreeze(**payload) for payload in _read_jsonl(self.freezes_path)]

    def validate_registry(self) -> dict[str, object]:
        versions = self.list_versions()
        freezes = self.list_freezes()
        version_ids = {record.dataset_version_id for record in versions}
        missing = [freeze.freeze_id for freeze in freezes if freeze.dataset_version_id not in version_ids]
        return {"version_count": len(versions), "freeze_count": len(freezes), "missing_version_freezes": missing, "ok": not missing}

    def write_report(self, output_dir: str | Path) -> tuple[Path, Path]:
        from .report import write_data_lake_report

        return write_data_lake_report(self, output_dir)

    def _write_versions(self, records: Iterable[DatasetVersionRecord]) -> None:
        write_jsonl_artifact(self.versions_path, [record.to_dict() for record in records], "dataset_versions", "data_lake")

    def _write_freezes(self, records: Iterable[ResearchDataFreeze]) -> None:
        write_jsonl_artifact(self.freezes_path, [record.to_dict() for record in records], "research_freezes", "data_lake")

    def _append_event(self, event_type: str, target_id: str, metadata: dict[str, object]) -> None:
        events = _read_jsonl(self.events_path)
        events.append({"event_type": event_type, "target_id": target_id, "created_at": utc_now(), "metadata": metadata})
        write_jsonl_artifact(self.events_path, events, "data_lake_events", "data_lake")

    def _write_registry(self) -> None:
        versions = self.list_versions()
        freezes = self.list_freezes()
        payload = DataLakeRegistry(
            created_at=utc_now(),
            dataset_versions=len(versions),
            research_freezes=len(freezes),
            latest_dataset_version_id=versions[-1].dataset_version_id if versions else None,
            latest_freeze_id=freezes[-1].freeze_id if freezes else None,
        ).to_dict()
        write_json_artifact(self.registry_path, payload, "data_lake_registry", "data_lake")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                payload = json.loads(line)
                if isinstance(payload, dict):
                    rows.append(payload)
    return rows

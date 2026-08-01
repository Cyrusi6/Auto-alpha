"""Retention helpers for local research freezes."""

from __future__ import annotations

from pathlib import Path

from artifact_schema.writer import utc_now, write_json_artifact

from .registry import LocalDataLakeRegistry


def build_retention_report(registry: LocalDataLakeRegistry) -> dict[str, object]:
    freezes = registry.list_freezes()
    total = 0
    for freeze in freezes:
        root = Path(freeze.freeze_dir)
        if root.exists():
            total += sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
    return {
        "created_at": utc_now(),
        "freeze_count": len(freezes),
        "total_size_bytes": total,
        "latest_validated_freeze": freezes[-1].freeze_id if freezes else None,
        "retired_freezes": [],
    }


def write_retention_report(registry: LocalDataLakeRegistry, output_dir: str | Path) -> Path:
    return write_json_artifact(Path(output_dir) / "data_retention_report.json", build_retention_report(registry), "data_retention_report", "data_lake")

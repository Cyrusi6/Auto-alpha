"""Data lake report writers."""

from __future__ import annotations

from pathlib import Path

from artifact_schema.writer import utc_now, write_json_artifact

from .models import DataLakeReport, DatasetVersionRecord, ResearchDataFreeze


def write_dataset_version_manifest(version: DatasetVersionRecord, output_dir: str | Path) -> Path:
    return write_json_artifact(Path(output_dir) / "dataset_version_manifest.json", version.to_dict(), "dataset_version_manifest", "data_lake")


def write_research_freeze(freeze: ResearchDataFreeze, output_dir: str | Path) -> Path:
    return write_json_artifact(Path(output_dir) / "research_data_freeze.json", freeze.to_dict(), "research_data_freeze", "data_lake")


def write_data_lake_report(registry, output_dir: str | Path) -> tuple[Path, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    versions = [record.to_dict() for record in registry.list_versions()]
    freezes = [record.to_dict() for record in registry.list_freezes()]
    report = DataLakeReport(
        created_at=utc_now(),
        registry_dir=str(registry.root_dir),
        versions=versions,
        freezes=freezes,
        latest_dataset_version_id=versions[-1]["dataset_version_id"] if versions else None,
        latest_freeze_id=freezes[-1]["freeze_id"] if freezes else None,
        status="ok",
    )
    json_path = write_json_artifact(root / "data_lake_report.json", report.to_dict(), "data_lake_report", "data_lake")
    md_path = root / "data_lake_report.md"
    lines = [
        "# Data Lake Report",
        "",
        f"- Dataset versions: {len(versions)}",
        f"- Research freezes: {len(freezes)}",
        f"- Latest version: {report.latest_dataset_version_id or ''}",
        f"- Latest freeze: {report.latest_freeze_id or ''}",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path

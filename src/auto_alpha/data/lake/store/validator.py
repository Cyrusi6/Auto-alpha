"""Validation helpers for dataset versions and research inputs."""

from __future__ import annotations

from pathlib import Path

from .freeze import validate_freeze
from .models import DatasetVersionRecord, FreezeValidationIssue, FreezeValidationReport


def validate_dataset_version(version: DatasetVersionRecord) -> dict[str, object]:
    missing = [item["dataset"] for item in version.dataset_fingerprints if item.get("missing")]
    duplicates = [item["dataset"] for item in version.dataset_fingerprints if int(item.get("duplicate_key_count", 0) or 0) > 0]
    return {
        "dataset_version_id": version.dataset_version_id,
        "missing_dataset_count": len(missing),
        "duplicate_dataset_count": len(duplicates),
        "missing_datasets": missing,
        "duplicate_datasets": duplicates,
        "status": "error" if missing else ("warning" if duplicates else "passed"),
    }


def validate_research_input(
    data_dir: str | Path | None = None,
    data_freeze_dir: str | Path | None = None,
    require_freeze: bool = False,
) -> FreezeValidationReport:
    if data_freeze_dir is None:
        if require_freeze:
            return FreezeValidationReport(
                freeze_id=None,
                freeze_dir="",
                status="error",
                checked_files=0,
                error_count=1,
                warning_count=0,
                issues=[FreezeValidationIssue("error", "missing_freeze", "research input requires data_freeze_dir")],
                content_hash=None,
                created_at="",
            )
        return FreezeValidationReport(
            freeze_id=None,
            freeze_dir=str(data_dir or ""),
            status="legacy",
            checked_files=0,
            error_count=0,
            warning_count=1,
            issues=[FreezeValidationIssue("warning", "mutable_data", "research uses mutable data_dir without freeze")],
            content_hash=None,
            created_at="",
        )
    canonical_manifest = Path(data_freeze_dir) / "canonical_freeze_manifest.json"
    if canonical_manifest.is_file():
        try:
            from .canonical_freeze import validate_canonical_research_freeze

            payload = validate_canonical_research_freeze(canonical_manifest)
        except Exception as exc:
            return FreezeValidationReport(
                freeze_id=None,
                freeze_dir=str(data_freeze_dir),
                status="error",
                checked_files=0,
                error_count=1,
                warning_count=0,
                issues=[
                    FreezeValidationIssue(
                        "error",
                        "canonical_freeze_validation_failed",
                        f"{type(exc).__name__}: {exc}",
                        str(canonical_manifest),
                    )
                ],
                content_hash=None,
                created_at="",
            )
        blockers = list(payload.get("blockers") or [])
        warnings = list(payload.get("warnings") or [])
        return FreezeValidationReport(
            freeze_id=str(payload.get("generation_id") or ""),
            freeze_dir=str(data_freeze_dir),
            status="passed" if not blockers else "error",
            checked_files=int(payload.get("partition_count", 0) or 0),
            error_count=len(blockers),
            warning_count=len(warnings),
            issues=[
                FreezeValidationIssue("error", "canonical_freeze_blocker", blocker, str(canonical_manifest))
                for blocker in blockers
            ]
            + [
                FreezeValidationIssue("warning", "canonical_freeze_warning", warning, str(canonical_manifest))
                for warning in warnings
            ],
            content_hash=str(payload.get("content_hash") or ""),
            created_at="",
        )
    return validate_freeze(data_freeze_dir)

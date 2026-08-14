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
    data_admission_verdict_path: str | Path | None = None,
    governed_research: bool = False,
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
    source_manifest = Path(data_freeze_dir) / "source_freeze_manifest.json"
    if source_manifest.is_file():
        try:
            from .source_freeze import validate_source_freeze_generation

            payload = validate_source_freeze_generation(source_manifest)
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
                        "source_freeze_validation_failed",
                        f"{type(exc).__name__}: {exc}",
                        str(source_manifest),
                    )
                ],
                content_hash=None,
                created_at="",
            )
        producer_blockers = list(payload.get("blockers") or [])
        warnings = list(payload.get("warnings") or [])
        admission_issues: list[FreezeValidationIssue] = []
        if data_admission_verdict_path is None:
            severity = "error" if require_freeze else "warning"
            admission_issues.append(
                FreezeValidationIssue(
                    severity,
                    "data_admission_verdict_required",
                    "a Source Freeze Generation cannot authorize governed research",
                    str(source_manifest),
                )
            )
        else:
            try:
                from .admission import (
                    AdmissionVerificationError,
                    validate_data_admission_verdict,
                )

                validate_data_admission_verdict(
                    data_admission_verdict_path,
                    expected_source_generation_id=str(payload.get("generation_id") or ""),
                    require_admitted=True,
                )
            except AdmissionVerificationError as exc:
                admission_issues.append(
                    FreezeValidationIssue(
                        "error",
                        "data_admission_verdict_invalid_or_blocked",
                        f"{type(exc).__name__}: {exc}",
                        str(data_admission_verdict_path),
                    )
                )
        producer_issues = [
            FreezeValidationIssue(
                "warning",
                "source_freeze_producer_check",
                blocker,
                str(source_manifest),
            )
            for blocker in producer_blockers
        ]
        warning_issues = [
            FreezeValidationIssue("warning", "source_freeze_warning", warning, str(source_manifest))
            for warning in warnings
        ]
        issues = admission_issues + producer_issues + warning_issues
        error_count = sum(issue.severity == "error" for issue in issues)
        warning_count = sum(issue.severity == "warning" for issue in issues)
        return FreezeValidationReport(
            freeze_id=str(payload.get("generation_id") or ""),
            freeze_dir=str(data_freeze_dir),
            status="error" if error_count else "passed",
            checked_files=int(payload.get("partition_count", 0) or 0),
            error_count=error_count,
            warning_count=warning_count,
            issues=issues,
            content_hash=str(payload.get("content_hash") or ""),
            created_at="",
        )
    if governed_research or data_admission_verdict_path is not None:
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
                    "source_freeze_manifest_required",
                    "governed research requires source_freeze_manifest.json and an admitted verdict",
                    str(source_manifest),
                )
            ],
            content_hash=None,
            created_at="",
        )
    return validate_freeze(data_freeze_dir)

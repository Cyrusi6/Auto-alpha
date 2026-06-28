"""Research data freeze creation and validation."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from artifact_schema.writer import utc_now, write_json_artifact, write_jsonl_artifact
from data_pipeline.ashare.storage import LocalAshareStorage

from .fingerprint import hash_file_streaming
from .models import DatasetVersionRecord, FreezeValidationIssue, FreezeValidationReport, ResearchDataFreeze


def create_research_freeze(
    data_dir: str | Path,
    freeze_dir: str | Path,
    dataset_version: DatasetVersionRecord,
    freeze_name: str,
    mode: str = "copy",
    artifact_paths: dict[str, str | None] | None = None,
    matrix_cache_dir: str | Path | None = None,
) -> ResearchDataFreeze:
    if mode not in {"copy", "hardlink", "manifest_only"}:
        raise ValueError("freeze mode must be copy, hardlink, or manifest_only")
    source_data = Path(data_dir)
    root = Path(freeze_dir)
    root.mkdir(parents=True, exist_ok=True)
    frozen_data = root / "data"
    copied_mode = mode
    if mode in {"copy", "hardlink"}:
        datasets = {str(item["dataset"]) for item in dataset_version.dataset_fingerprints}
        datasets.update(path.parent.name for path in source_data.glob("*/records.jsonl"))
        for dataset in sorted(datasets):
            source = source_data / dataset / "records.jsonl"
            if not source.exists():
                continue
            target = frozen_data / dataset / "records.jsonl"
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                target.unlink()
            if mode == "hardlink":
                try:
                    os.link(source, target)
                except OSError:
                    shutil.copy2(source, target)
                    copied_mode = "copy"
            else:
                shutil.copy2(source, target)
        universe_dir = source_data / "universe"
        if universe_dir.exists():
            target_universe = frozen_data / "universe"
            if target_universe.exists():
                shutil.rmtree(target_universe)
            shutil.copytree(universe_dir, target_universe)
    else:
        frozen_data.mkdir(parents=True, exist_ok=True)

    if matrix_cache_dir is not None and Path(matrix_cache_dir).exists() and mode != "manifest_only":
        target_matrix = root / "matrix_cache"
        if target_matrix.exists():
            shutil.rmtree(target_matrix)
        shutil.copytree(matrix_cache_dir, target_matrix)

    version_manifest_path = write_json_artifact(
        root / "dataset_version_manifest.json",
        dataset_version.to_dict(),
        "dataset_version_manifest",
        "data_lake",
    )
    file_manifest = _build_file_manifest(root, external_data_dir=source_data if mode == "manifest_only" else None)
    content_hash = _manifest_content_hash(file_manifest)
    freeze_id = f"freeze_{content_hash[:16]}"
    freeze = ResearchDataFreeze(
        freeze_id=freeze_id,
        dataset_version_id=dataset_version.dataset_version_id,
        freeze_name=freeze_name,
        freeze_dir=str(root),
        freeze_mode=copied_mode,
        data_dir=str(frozen_data if mode != "manifest_only" else source_data),
        matrix_cache_dir=str(root / "matrix_cache") if (root / "matrix_cache").exists() else None,
        artifact_paths=dict(artifact_paths or {}),
        frozen_at=utc_now(),
        content_hash=content_hash,
        immutable_check_status="not_validated",
        metadata={"source_data_dir": str(source_data)},
    )
    write_json_artifact(root / "research_data_freeze.json", freeze.to_dict(), "research_data_freeze", "data_lake")
    manifest_payload = {
        "freeze_id": freeze_id,
        "dataset_version_id": dataset_version.dataset_version_id,
        "freeze_name": freeze_name,
        "freeze_mode": copied_mode,
        "content_hash": content_hash,
        "files": file_manifest,
        "artifact_paths": dict(artifact_paths or {}),
        "created_at": freeze.frozen_at,
    }
    write_json_artifact(root / "freeze_manifest.json", manifest_payload, "freeze_manifest", "data_lake")
    report = validate_freeze(root)
    write_freeze_validation_report(report, root)
    return ResearchDataFreeze(
        **{
            **freeze.to_dict(),
            "immutable_check_status": report.status,
        }
    )


def validate_freeze(freeze_dir: str | Path) -> FreezeValidationReport:
    root = Path(freeze_dir)
    manifest_path = root / "freeze_manifest.json"
    issues: list[FreezeValidationIssue] = []
    if not manifest_path.exists():
        return FreezeValidationReport(
            freeze_id=None,
            freeze_dir=str(root),
            status="error",
            checked_files=0,
            error_count=1,
            warning_count=0,
            issues=[FreezeValidationIssue("error", "missing_manifest", "freeze_manifest.json is missing", str(manifest_path))],
            content_hash=None,
            created_at=utc_now(),
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest.get("files") or []
    checked = 0
    for entry in files:
        path = Path(entry.get("path", ""))
        if not path.is_absolute():
            path = root / path
        expected = entry.get("sha256")
        if not path.exists():
            issues.append(FreezeValidationIssue("error", "missing_file", "freeze file is missing", str(path)))
            continue
        checked += 1
        actual = hash_file_streaming(path)
        if expected and actual != expected:
            issues.append(FreezeValidationIssue("error", "hash_drift", "freeze file hash changed", str(path), {"expected": expected, "actual": actual}))
    registered = {str(entry.get("relative_path") or entry.get("path")) for entry in files}
    for path in root.rglob("*"):
        if path.is_file() and path.name not in {"freeze_validation_report.json"}:
            rel = str(path.relative_to(root))
            if rel not in registered and path.name != "freeze_manifest.json":
                issues.append(FreezeValidationIssue("warning", "unregistered_file", "file is not listed in freeze manifest", rel))
    error_count = sum(issue.severity == "error" for issue in issues)
    warning_count = sum(issue.severity == "warning" for issue in issues)
    return FreezeValidationReport(
        freeze_id=manifest.get("freeze_id"),
        freeze_dir=str(root),
        status="error" if error_count else ("warning" if warning_count else "passed"),
        checked_files=checked,
        error_count=error_count,
        warning_count=warning_count,
        issues=issues,
        content_hash=manifest.get("content_hash"),
        created_at=utc_now(),
    )


def write_freeze_validation_report(report: FreezeValidationReport, output_dir: str | Path) -> Path:
    return write_json_artifact(Path(output_dir) / "freeze_validation_report.json", report.to_dict(), "freeze_validation_report", "data_lake")


def _build_file_manifest(root: Path, external_data_dir: Path | None = None) -> list[dict[str, object]]:
    source = external_data_dir or root
    files: list[dict[str, object]] = []
    if external_data_dir is not None:
        candidates = [path for path in source.rglob("records.jsonl") if path.is_file()]
    else:
        candidates = [path for path in root.rglob("*") if path.is_file() and path.name not in {"freeze_manifest.json", "freeze_validation_report.json"}]
    for path in sorted(candidates):
        rel = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
        files.append(
            {
                "relative_path": rel,
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "sha256": hash_file_streaming(path),
            }
        )
    return files


def _manifest_content_hash(files: list[dict[str, object]]) -> str:
    payload = [{"path": item["relative_path"], "sha256": item["sha256"]} for item in files]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

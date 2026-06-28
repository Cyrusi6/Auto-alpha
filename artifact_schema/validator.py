"""Artifact schema validation with legacy compatibility."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import (
    ArtifactCompatibilityMode,
    ArtifactSeverity,
    ArtifactValidationIssue,
    ArtifactValidationResult,
)
from .registry import get_definition, get_registry, infer_artifact_type


def validate_artifact(
    path: str | Path,
    registry: dict | None = None,
    strict: bool = False,
    sample_jsonl_records: int | None = None,
) -> ArtifactValidationResult:
    target = Path(path)
    active_registry = registry or get_registry()
    issues: list[ArtifactValidationIssue] = []
    if not target.exists():
        issues.append(_issue(target, ArtifactSeverity.error, "missing_file", "artifact file does not exist", None))
        return ArtifactValidationResult(str(target), None, None, ArtifactCompatibilityMode.compatible, False, issues)
    if target.name.endswith(".schema.json"):
        return ArtifactValidationResult(str(target), None, None, ArtifactCompatibilityMode.compatible, True, [])

    inferred_type = infer_artifact_type(target, active_registry)
    definition = get_definition(inferred_type)
    artifact_type = inferred_type
    schema_version = None
    compatibility_mode = ArtifactCompatibilityMode.compatible
    if definition is None:
        issues.append(_issue(target, ArtifactSeverity.warning, "unknown_artifact", "artifact type could not be inferred", None))
        return ArtifactValidationResult(str(target), None, None, compatibility_mode, True, issues)

    if definition.json_or_jsonl == "jsonl":
        sidecar = _read_json(Path(f"{target}.schema.json"))
        if sidecar:
            artifact_type = sidecar.get("artifact_type") or artifact_type
            schema_version = sidecar.get("schema_version")
            compatibility_mode = ArtifactCompatibilityMode.strict if artifact_type == definition.artifact_type else ArtifactCompatibilityMode.compatible
        else:
            schema_version = definition.schema_version
            compatibility_mode = ArtifactCompatibilityMode.legacy
            issues.append(_issue(target, ArtifactSeverity.warning, "legacy_artifact", "JSONL artifact has no schema sidecar; inferred by filename", artifact_type))
        issues.extend(validate_jsonl_records(target, definition, sample_size=sample_jsonl_records))
    else:
        payload = _read_json(target, issues, artifact_type)
        if isinstance(payload, dict):
            artifact_type = payload.get("artifact_type") or artifact_type
            schema_version = payload.get("schema_version") or definition.schema_version
            if payload.get("artifact_type") is None or payload.get("schema_version") is None:
                compatibility_mode = ArtifactCompatibilityMode.legacy
                issues.append(_issue(target, ArtifactSeverity.warning, "legacy_artifact", "JSON artifact lacks artifact_type/schema_version; inferred by filename", artifact_type))
            else:
                compatibility_mode = ArtifactCompatibilityMode.strict
            issues.extend(validate_payload(payload, definition, target))

    if schema_version and _version_tuple(str(schema_version)) > _version_tuple(definition.schema_version):
        severity = ArtifactSeverity.error if strict else ArtifactSeverity.warning
        issues.append(_issue(target, severity, "future_schema_version", "artifact schema version is newer than local registry", artifact_type))
    valid = not any(issue.severity == ArtifactSeverity.error for issue in issues)
    return ArtifactValidationResult(str(target), artifact_type, schema_version, compatibility_mode, valid, issues)


def validate_payload(payload: dict[str, Any], definition, path: str | Path = "") -> list[ArtifactValidationIssue]:
    issues: list[ArtifactValidationIssue] = []
    for field in definition.required_fields:
        if field not in payload:
            issues.append(_issue(Path(path), ArtifactSeverity.error, "missing_required_field", f"missing required field: {field}", definition.artifact_type))
    known = set(definition.required_fields) | set(definition.optional_fields)
    for field in payload:
        if field not in known:
            issues.append(_issue(Path(path), ArtifactSeverity.info, "unknown_field", f"unknown field: {field}", definition.artifact_type))
    return issues


def validate_jsonl_records(path: str | Path, definition, sample_size: int | None = None) -> list[ArtifactValidationIssue]:
    target = Path(path)
    issues: list[ArtifactValidationIssue] = []
    count = 0
    with target.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if sample_size is not None and count >= sample_size:
                break
            if not line.strip():
                continue
            count += 1
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                issues.append(_issue(target, ArtifactSeverity.error, "malformed_jsonl", f"line {line_number}: {exc}", definition.artifact_type))
                continue
            if not isinstance(payload, dict):
                issues.append(_issue(target, ArtifactSeverity.error, "non_object_jsonl_record", f"line {line_number} is not an object", definition.artifact_type))
                continue
            for field in definition.required_fields:
                if field not in payload:
                    issues.append(
                        _issue(target, ArtifactSeverity.error, "missing_required_field", f"line {line_number} missing field: {field}", definition.artifact_type)
                    )
    if count == 0 and not definition.allow_empty:
        issues.append(_issue(target, ArtifactSeverity.error, "empty_jsonl", "JSONL artifact is empty", definition.artifact_type))
    elif count == 0:
        issues.append(_issue(target, ArtifactSeverity.warning, "empty_jsonl", "JSONL artifact is empty", definition.artifact_type))
    return issues


def _read_json(path: Path, issues: list[ArtifactValidationIssue] | None = None, artifact_type: str | None = None) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        if issues is not None:
            issues.append(_issue(path, ArtifactSeverity.error, "malformed_json", str(exc), artifact_type))
        return None
    if not isinstance(payload, dict):
        if issues is not None:
            issues.append(_issue(path, ArtifactSeverity.error, "non_object_json", "JSON artifact root must be an object", artifact_type))
        return None
    return payload


def _issue(path: Path, severity: str, code: str, message: str, artifact_type: str | None) -> ArtifactValidationIssue:
    return ArtifactValidationIssue(severity=severity, code=code, message=message, path=str(path), artifact_type=artifact_type)


def _version_tuple(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for part in version.split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    return tuple(parts)

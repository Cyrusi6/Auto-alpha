"""Dataclasses for artifact schema versioning and validation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class ArtifactCompatibilityMode:
    strict = "strict"
    compatible = "compatible"
    legacy = "legacy"


class ArtifactSeverity:
    info = "info"
    warning = "warning"
    error = "error"


@dataclass(frozen=True)
class ArtifactSchemaVersion:
    artifact_type: str
    version: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArtifactSchemaDefinition:
    artifact_type: str
    schema_version: str
    required_fields: list[str]
    optional_fields: list[str] = field(default_factory=list)
    file_patterns: list[str] = field(default_factory=list)
    json_or_jsonl: str = "json"
    allow_empty: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArtifactMetadata:
    artifact_type: str
    schema_version: str
    producer: str
    created_at: str
    compatibility_mode: str = ArtifactCompatibilityMode.strict
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArtifactManifestEntry:
    path: str
    relative_path: str
    artifact_type: str | None
    schema_version: str | None
    compatibility_mode: str
    size_bytes: int
    sha256: str
    record_count: int | None = None
    created_at: str | None = None
    producer: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArtifactManifest:
    created_at: str
    root_dir: str | None
    entries: list[ArtifactManifestEntry]

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "root_dir": self.root_dir,
            "entries": [entry.to_dict() for entry in self.entries],
        }


@dataclass(frozen=True)
class ArtifactValidationIssue:
    severity: str
    code: str
    message: str
    path: str
    artifact_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArtifactValidationResult:
    path: str
    artifact_type: str | None
    schema_version: str | None
    compatibility_mode: str
    valid: bool
    issues: list[ArtifactValidationIssue]

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "artifact_type": self.artifact_type,
            "schema_version": self.schema_version,
            "compatibility_mode": self.compatibility_mode,
            "valid": self.valid,
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class ArtifactValidationReport:
    created_at: str
    results: list[ArtifactValidationResult]

    @property
    def error_count(self) -> int:
        return sum(1 for result in self.results for issue in result.issues if issue.severity == ArtifactSeverity.error)

    @property
    def warning_count(self) -> int:
        return sum(1 for result in self.results for issue in result.issues if issue.severity == ArtifactSeverity.warning)

    @property
    def legacy_count(self) -> int:
        return sum(1 for result in self.results if result.compatibility_mode == ArtifactCompatibilityMode.legacy)

    @property
    def unknown_count(self) -> int:
        return sum(1 for result in self.results if result.artifact_type is None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "artifact_count": len(self.results),
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "legacy_artifact_count": self.legacy_count,
            "unknown_artifact_count": self.unknown_count,
            "results": [result.to_dict() for result in self.results],
        }

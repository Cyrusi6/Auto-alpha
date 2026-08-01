"""Dataclasses for local release manifests and gates."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ReleaseConfig:
    release_name: str
    output_dir: str
    artifact_dirs: list[str] = field(default_factory=list)
    artifact_catalog_paths: list[str] = field(default_factory=list)
    run_tests: bool = False
    pytest_args: str = ""
    run_build: bool = False
    run_import_smoke: bool = False
    run_dashboard_import: bool = False
    run_schema_validation: bool = False
    allow_network: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReleaseArtifactSummary:
    path: str
    size_bytes: int
    sha256: str
    kind: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReleaseManifest:
    release_name: str
    created_at: str
    git_commit: str
    git_branch: str
    config: dict[str, Any]
    artifacts: list[ReleaseArtifactSummary] = field(default_factory=list)
    build_artifacts: list[ReleaseArtifactSummary] = field(default_factory=list)
    paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "artifacts": [item.to_dict() for item in self.artifacts],
            "build_artifacts": [item.to_dict() for item in self.build_artifacts],
        }


@dataclass(frozen=True)
class DependencyInventory:
    files: list[dict[str, Any]]
    dependencies: dict[str, list[str]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ModuleInventory:
    modules: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CliInventory:
    entries: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReleaseGateCheck:
    name: str
    status: str
    message: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    duration_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReleaseGateReport:
    release_name: str
    created_at: str
    status: str
    checks: list[ReleaseGateCheck]

    @property
    def error_count(self) -> int:
        return sum(1 for check in self.checks if check.status == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for check in self.checks if check.status == "warning")

    def to_dict(self) -> dict[str, Any]:
        return {
            "release_name": self.release_name,
            "created_at": self.created_at,
            "status": self.status,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "checks": [check.to_dict() for check in self.checks],
        }

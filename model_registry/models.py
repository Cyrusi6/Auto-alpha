"""Dataclasses for local model lifecycle governance."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class ModelKind:
    single_factor = "single_factor"
    composite_factor = "composite_factor"
    risk_model = "risk_model"
    optimizer_policy = "optimizer_policy"
    execution_profile = "execution_profile"


class ModelLifecycleStatus:
    research_candidate = "research_candidate"
    approved = "approved"
    production_candidate = "production_candidate"
    active = "active"
    paused = "paused"
    quarantined = "quarantined"
    deprecated = "deprecated"
    retired = "retired"
    rejected = "rejected"


class ModelLifecycleAction:
    register = "register"
    approve = "approve"
    activate = "activate"
    pause = "pause"
    resume = "resume"
    quarantine = "quarantine"
    deprecate = "deprecate"
    retire = "retire"
    reject = "reject"
    rollback = "rollback"
    sync_factor_store = "sync_factor_store"


TERMINAL_STATUSES = {
    ModelLifecycleStatus.retired,
    ModelLifecycleStatus.rejected,
}


@dataclass(frozen=True)
class ModelVersionRecord:
    model_version_id: str
    model_kind: str
    factor_id: str
    factor_type: str
    formula_hash: str
    parent_factor_ids: list[str] = field(default_factory=list)
    source_batch_id: str | None = None
    source_run_id: str | None = None
    source_artifacts: dict[str, str] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)
    gate_status: str | None = None
    lifecycle_status: str = ModelLifecycleStatus.research_candidate
    created_at: str = ""
    updated_at: str = ""
    activated_at: str | None = None
    deactivated_at: str | None = None
    retired_at: str | None = None
    schema_version: str = "1.0"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ModelDeploymentRecord:
    deployment_id: str
    model_version_id: str
    model_kind: str
    environment: str = "paper"
    status: str = "active"
    activation_approval_id: str | None = None
    rollback_from_deployment_id: str | None = None
    started_at: str = ""
    ended_at: str | None = None
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ModelLifecycleEvent:
    event_id: str
    model_version_id: str
    from_status: str | None
    to_status: str
    action: str
    actor: str
    reason: str | None = None
    approval_id: str | None = None
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ModelRegistryManifest:
    created_at: str
    model_versions: int
    deployments: int
    events: int
    active_deployments: int
    status_counts: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ModelLineageGraph:
    created_at: str
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ModelRegistryReport:
    created_at: str
    manifest: dict[str, Any]
    active_models: list[dict[str, Any]]
    latest_models: list[dict[str, Any]]
    deployments: list[dict[str, Any]]
    recent_events: list[dict[str, Any]]
    lineage_graph_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

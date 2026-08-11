"""Factor registry models, lineage, lifecycle state, storage, reporting, and command workflow."""

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

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from auto_alpha.research.factors.store import LocalFactorStore



def build_model_lineage_graph(
    registry: LocalModelRegistry,
    factor_store: LocalFactorStore | None = None,
    artifact_catalog_paths: list[str] | None = None,
    artifact_dirs: list[str] | None = None,
) -> ModelLineageGraph:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    warnings: list[str] = []
    factors = {record.factor_id: record for record in (factor_store.load_factors() if factor_store is not None else [])}
    for model in registry.load_model_versions():
        nodes.append({"id": model.model_version_id, "type": "model_version", "status": model.lifecycle_status, "factor_id": model.factor_id})
        nodes.append({"id": model.factor_id, "type": "factor", "factor_type": model.factor_type})
        edges.append({"source": model.factor_id, "target": model.model_version_id, "type": "registered_as"})
        for parent in model.parent_factor_ids:
            nodes.append({"id": parent, "type": "parent_factor"})
            edges.append({"source": parent, "target": model.factor_id, "type": "derived_from"})
        factor = factors.get(model.factor_id)
        if factor and factor.batch_id:
            nodes.append({"id": factor.batch_id, "type": "research_batch"})
            edges.append({"source": factor.batch_id, "target": model.factor_id, "type": "promoted_by"})
    for deployment in registry.load_deployments():
        nodes.append({"id": deployment.deployment_id, "type": "deployment", "status": deployment.status, "environment": deployment.environment})
        edges.append({"source": deployment.model_version_id, "target": deployment.deployment_id, "type": "deployed_as"})
        if deployment.rollback_from_deployment_id:
            edges.append({"source": deployment.rollback_from_deployment_id, "target": deployment.deployment_id, "type": "rolled_back_from"})
    for catalog_path in artifact_catalog_paths or []:
        try:
            catalog = json.loads(Path(catalog_path).read_text(encoding="utf-8"))
            entries = list(catalog.get("entries") or ())
        except (OSError, json.JSONDecodeError, AttributeError) as exc:
            warnings.append(f"catalog_unreadable:{catalog_path}:{exc}")
            continue
        catalog_id = f"catalog:{Path(catalog_path).name}"
        nodes.append({"id": catalog_id, "type": "artifact_catalog", "path": catalog_path})
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("path"):
                continue
            nodes.append(
                {
                    "id": entry["path"],
                    "type": entry.get("stage", "artifact"),
                    "name": entry.get("name", ""),
                    "kind": entry.get("kind", ""),
                }
            )
            edges.append({"source": entry["path"], "target": catalog_id, "type": "listed_in"})
    for artifact_dir in artifact_dirs or []:
        path = Path(artifact_dir)
        if not path.exists():
            warnings.append(f"artifact_dir_missing:{artifact_dir}")
            continue
        nodes.append({"id": str(path), "type": "artifact_dir"})
        for filename, node_type in _SETTLEMENT_ARTIFACT_TYPES.items():
            artifact_path = path / filename
            if artifact_path.exists():
                nodes.append({"id": str(artifact_path), "type": node_type, "path": str(artifact_path)})
                edges.append({"source": str(artifact_path), "target": str(path), "type": "contained_in"})
    return ModelLineageGraph(
        created_at=datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        nodes=_dedupe_nodes(nodes),
        edges=edges,
        warnings=warnings,
    )


def _dedupe_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for node in nodes:
        node_id = str(node.get("id"))
        if node_id in seen:
            continue
        seen.add(node_id)
        result.append(node)
    return result


_SETTLEMENT_ARTIFACT_TYPES = {
    "settlement_report.json": "settlement_report",
    "account_reconciliation_report.json": "account_reconciliation",
    "account_nav.jsonl": "account_nav",
    "cash_buckets.jsonl": "cash_buckets",
    "realized_pnl.jsonl": "realized_pnl",
}

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    ModelLifecycleStatus.research_candidate: {
        ModelLifecycleStatus.approved,
        ModelLifecycleStatus.production_candidate,
        ModelLifecycleStatus.rejected,
        ModelLifecycleStatus.retired,
    },
    ModelLifecycleStatus.approved: {
        ModelLifecycleStatus.production_candidate,
        ModelLifecycleStatus.active,
        ModelLifecycleStatus.paused,
        ModelLifecycleStatus.rejected,
        ModelLifecycleStatus.retired,
    },
    ModelLifecycleStatus.production_candidate: {
        ModelLifecycleStatus.active,
        ModelLifecycleStatus.paused,
        ModelLifecycleStatus.quarantined,
        ModelLifecycleStatus.deprecated,
        ModelLifecycleStatus.retired,
        ModelLifecycleStatus.rejected,
    },
    ModelLifecycleStatus.active: {
        ModelLifecycleStatus.paused,
        ModelLifecycleStatus.quarantined,
        ModelLifecycleStatus.deprecated,
        ModelLifecycleStatus.retired,
    },
    ModelLifecycleStatus.paused: {
        ModelLifecycleStatus.active,
        ModelLifecycleStatus.quarantined,
        ModelLifecycleStatus.retired,
    },
    ModelLifecycleStatus.quarantined: {
        ModelLifecycleStatus.active,
        ModelLifecycleStatus.paused,
        ModelLifecycleStatus.retired,
    },
    ModelLifecycleStatus.deprecated: {
        ModelLifecycleStatus.active,
        ModelLifecycleStatus.retired,
    },
}


def validate_transition(
    from_status: str,
    to_status: str,
    action: str,
    *,
    approval_id: str | None = None,
    explicit_override: bool = False,
) -> None:
    if from_status in TERMINAL_STATUSES:
        raise ValueError(f"terminal model status cannot transition: {from_status} -> {to_status}")
    if from_status == ModelLifecycleStatus.quarantined and to_status == ModelLifecycleStatus.active and not explicit_override:
        raise ValueError("quarantined model activation requires explicit_override=True")
    if to_status == ModelLifecycleStatus.active and from_status == ModelLifecycleStatus.production_candidate:
        if not approval_id and not explicit_override:
            raise ValueError("production_candidate activation requires approval_id or explicit_override=True")
    if to_status not in ALLOWED_TRANSITIONS.get(from_status, set()):
        raise ValueError(f"illegal model lifecycle transition: {from_status} -> {to_status}")
    if action == ModelLifecycleAction.resume and to_status != ModelLifecycleStatus.active:
        raise ValueError("resume action must transition to active")

import hashlib
import json
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact
from auto_alpha.research.factors.store import FactorRecord, LocalFactorStore



class LocalModelRegistry:
    def __init__(self, root_dir: str | Path):
        self.root_dir = Path(root_dir)
        self.versions_path = self.root_dir / "model_versions.jsonl"
        self.state_path = self.root_dir / "model_state.json"
        self.deployments_path = self.root_dir / "model_deployments.jsonl"
        self.events_path = self.root_dir / "lifecycle_events.jsonl"
        self.manifest_path = self.root_dir / "model_registry_manifest.json"

    def register_factor_record(
        self,
        factor_record: FactorRecord,
        model_kind: str | None = None,
        source_artifacts: dict[str, str] | None = None,
        metrics: dict[str, float] | None = None,
        metadata: dict[str, Any] | None = None,
        lifecycle_status: str | None = None,
    ) -> ModelVersionRecord:
        kind = model_kind or _kind_from_factor(factor_record)
        existing = self._find_existing(factor_record.factor_id, factor_record.formula_hash, kind)
        if existing is not None:
            return existing
        now = _utc_now()
        record = ModelVersionRecord(
            model_version_id=make_model_version_id(kind, factor_record.factor_id, factor_record.formula_hash),
            model_kind=kind,
            factor_id=factor_record.factor_id,
            factor_type=factor_record.factor_type or ("composite" if kind == ModelKind.composite_factor else "single"),
            formula_hash=factor_record.formula_hash,
            parent_factor_ids=list(factor_record.parent_factor_ids or []),
            source_batch_id=factor_record.batch_id,
            source_run_id=str((factor_record.metadata or {}).get("search_id") or ""),
            source_artifacts=source_artifacts or {},
            metrics=dict(metrics or factor_record.metrics or {}),
            gate_status=factor_record.gate_status,
            lifecycle_status=lifecycle_status or _status_from_factor(factor_record.status),
            created_at=now,
            updated_at=now,
            metadata=dict(metadata or factor_record.metadata or {}),
        )
        self.root_dir.mkdir(parents=True, exist_ok=True)
        _append_jsonl(self.versions_path, record.to_dict())
        self._append_event(
            record.model_version_id,
            None,
            record.lifecycle_status,
            ModelLifecycleAction.register,
            "system",
            "registered factor model",
            metadata={"factor_id": record.factor_id},
        )
        self._write_state()
        return record

    def register_portfolio_policy(
        self,
        certified_portfolio_policy: dict[str, Any],
        source_artifacts: dict[str, str] | None = None,
        metadata: dict[str, Any] | None = None,
        lifecycle_status: str | None = None,
    ) -> ModelVersionRecord:
        policy_payload = _extract_policy_payload(certified_portfolio_policy)
        policy_id = str(policy_payload.get("policy_id") or policy_payload.get("portfolio_policy_id") or "")
        if not policy_id:
            raise ValueError("portfolio policy payload must include policy_id")
        formula_hash = hashlib.sha256(
            json.dumps(policy_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        existing = self._find_existing(policy_id, formula_hash, ModelKind.optimizer_policy)
        if existing is not None:
            return existing
        now = _utc_now()
        merged_metadata = {
            "portfolio_policy": policy_payload,
            "source_factor_id": policy_payload.get("source_factor_id"),
            "certification_status": policy_payload.get("certification_status"),
            **dict(metadata or {}),
        }
        record = ModelVersionRecord(
            model_version_id=make_model_version_id(ModelKind.optimizer_policy, policy_id, formula_hash),
            model_kind=ModelKind.optimizer_policy,
            factor_id=policy_id,
            factor_type="optimizer_policy",
            formula_hash=formula_hash,
            parent_factor_ids=[str(policy_payload.get("source_factor_id"))] if policy_payload.get("source_factor_id") else [],
            source_batch_id=str(policy_payload.get("source_suite_name") or ""),
            source_run_id=str(policy_payload.get("metadata", {}).get("lab_id") or "") if isinstance(policy_payload.get("metadata"), dict) else "",
            source_artifacts=source_artifacts or {},
            metrics={},
            gate_status=str(policy_payload.get("certification_status") or ""),
            lifecycle_status=lifecycle_status or ModelLifecycleStatus.approved,
            created_at=now,
            updated_at=now,
            metadata=merged_metadata,
        )
        self.root_dir.mkdir(parents=True, exist_ok=True)
        _append_jsonl(self.versions_path, record.to_dict())
        self._append_event(
            record.model_version_id,
            None,
            record.lifecycle_status,
            ModelLifecycleAction.register,
            "system",
            "registered portfolio optimizer policy",
            metadata={"portfolio_policy_id": policy_id},
        )
        self._write_state()
        return record

    def get_model_version(self, model_version_id: str) -> ModelVersionRecord | None:
        for record in self.load_model_versions():
            if record.model_version_id == model_version_id:
                return record
        return None

    def find_by_factor_id(self, factor_id: str) -> list[ModelVersionRecord]:
        return [record for record in self.load_model_versions() if record.factor_id == factor_id]

    def latest_by_status(self, status: str, model_kind: str | None = None) -> ModelVersionRecord | None:
        records = [record for record in self.load_model_versions() if record.lifecycle_status == status]
        if model_kind is not None:
            records = [record for record in records if record.model_kind == model_kind]
        return records[-1] if records else None

    def latest_active(self, model_kind: str = ModelKind.composite_factor, environment: str = "paper") -> ModelVersionRecord | None:
        deployment = self.latest_active_deployment(model_kind=model_kind, environment=environment)
        return self.get_model_version(deployment.model_version_id) if deployment is not None else None

    def latest_active_optimizer_policy(self, environment: str = "paper") -> ModelVersionRecord | None:
        return self.latest_active(model_kind=ModelKind.optimizer_policy, environment=environment)

    def latest_active_deployment(self, model_kind: str = ModelKind.composite_factor, environment: str = "paper") -> ModelDeploymentRecord | None:
        deployments = [
            item
            for item in self.load_deployments()
            if item.model_kind == model_kind and item.environment == environment and item.status == "active"
        ]
        return deployments[-1] if deployments else None

    def transition(
        self,
        model_version_id: str,
        action: str,
        to_status: str,
        actor: str,
        reason: str | None,
        approval_id: str | None = None,
        explicit_override: bool = False,
    ) -> ModelVersionRecord:
        record = self._require_model(model_version_id)
        validate_transition(
            record.lifecycle_status,
            to_status,
            action,
            approval_id=approval_id,
            explicit_override=explicit_override,
        )
        now = _utc_now()
        updated = replace(
            record,
            lifecycle_status=to_status,
            updated_at=now,
            activated_at=now if to_status == ModelLifecycleStatus.active else record.activated_at,
            deactivated_at=now if record.lifecycle_status == ModelLifecycleStatus.active and to_status != ModelLifecycleStatus.active else record.deactivated_at,
            retired_at=now if to_status == ModelLifecycleStatus.retired else record.retired_at,
        )
        self._rewrite_versions(updated)
        self._append_event(model_version_id, record.lifecycle_status, to_status, action, actor, reason, approval_id=approval_id)
        self._write_state()
        return updated

    def activate(
        self,
        model_version_id: str,
        approval_id: str | None = None,
        actor: str = "local_operator",
        reason: str | None = None,
        environment: str = "paper",
        explicit_override: bool = False,
    ) -> tuple[ModelVersionRecord, ModelDeploymentRecord]:
        record = self.transition(
            model_version_id,
            ModelLifecycleAction.activate,
            ModelLifecycleStatus.active,
            actor,
            reason,
            approval_id=approval_id,
            explicit_override=explicit_override,
        )
        now = _utc_now()
        deployments = []
        for deployment in self.load_deployments():
            if deployment.model_kind == record.model_kind and deployment.environment == environment and deployment.status == "active":
                previous = replace(deployment, status="previous", ended_at=now)
                deployments.append(previous)
                self._append_event(
                    deployment.model_version_id,
                    ModelLifecycleStatus.active,
                    ModelLifecycleStatus.deprecated,
                    ModelLifecycleAction.activate,
                    actor,
                    "superseded by active deployment",
                    metadata={"new_model_version_id": model_version_id, "deployment_id": deployment.deployment_id},
                )
            else:
                deployments.append(deployment)
        deployment = ModelDeploymentRecord(
            deployment_id=make_deployment_id(record.model_kind, environment, now),
            model_version_id=model_version_id,
            model_kind=record.model_kind,
            environment=environment,
            status="active",
            activation_approval_id=approval_id,
            started_at=now,
            reason=reason,
            metadata={"factor_id": record.factor_id},
        )
        deployments.append(deployment)
        self._write_deployments(deployments)
        self._write_state()
        return record, deployment

    def pause(self, model_version_id: str, reason: str | None, actor: str) -> ModelVersionRecord:
        record = self.transition(model_version_id, ModelLifecycleAction.pause, ModelLifecycleStatus.paused, actor, reason)
        self._mark_deployments(model_version_id, "paused")
        return record

    def quarantine(self, model_version_id: str, reason: str | None, actor: str) -> ModelVersionRecord:
        record = self.transition(model_version_id, ModelLifecycleAction.quarantine, ModelLifecycleStatus.quarantined, actor, reason)
        self._mark_deployments(model_version_id, "paused")
        return record

    def retire(self, model_version_id: str, reason: str | None, actor: str) -> ModelVersionRecord:
        record = self.transition(model_version_id, ModelLifecycleAction.retire, ModelLifecycleStatus.retired, actor, reason)
        self._mark_deployments(model_version_id, "retired")
        return record

    def rollback(
        self,
        model_kind: str = ModelKind.composite_factor,
        environment: str = "paper",
        deployment_id: str | None = None,
        actor: str = "local_operator",
        reason: str | None = None,
        explicit_override: bool = False,
    ) -> tuple[ModelVersionRecord, ModelDeploymentRecord]:
        deployments = self.load_deployments()
        target = None
        if deployment_id:
            target = next((item for item in deployments if item.deployment_id == deployment_id), None)
        else:
            previous = [item for item in deployments if item.model_kind == model_kind and item.environment == environment and item.status == "previous"]
            if previous:
                target = previous[-1]
            else:
                paused = [item for item in deployments if item.model_kind == model_kind and item.environment == environment and item.status == "paused"]
                target = paused[-1] if paused else None
        if target is None:
            raise ValueError("no rollback deployment target is available")
        record, deployment = self.activate(
            target.model_version_id,
            approval_id=None,
            actor=actor,
            reason=reason or "rollback",
            environment=environment,
            explicit_override=True if explicit_override else True,
        )
        deployment = replace(deployment, rollback_from_deployment_id=target.deployment_id)
        self._replace_deployment(deployment)
        self._append_event(
            record.model_version_id,
            record.lifecycle_status,
            ModelLifecycleStatus.active,
            ModelLifecycleAction.rollback,
            actor,
            reason,
            metadata={"rollback_from_deployment_id": target.deployment_id},
        )
        return record, deployment

    def sync_factor_store_status(self, factor_store: LocalFactorStore, model_version_id: str) -> None:
        record = self._require_model(model_version_id)
        status = record.lifecycle_status
        factor_store.update_factor_status(record.factor_id, status, reason=f"model_registry:{status}")
        self._append_event(
            model_version_id,
            status,
            status,
            ModelLifecycleAction.sync_factor_store,
            "system",
            "synced factor store status",
        )

    def load_model_versions(self) -> list[ModelVersionRecord]:
        return [ModelVersionRecord(**_version_defaults(payload)) for payload in _registry_store_read_jsonl(self.versions_path)]

    def load_deployments(self) -> list[ModelDeploymentRecord]:
        return [ModelDeploymentRecord(**_deployment_defaults(payload)) for payload in _registry_store_read_jsonl(self.deployments_path)]

    def load_events(self) -> list[ModelLifecycleEvent]:
        return [ModelLifecycleEvent(**_event_defaults(payload)) for payload in _registry_store_read_jsonl(self.events_path)]

    def write_manifest(self) -> ModelRegistryManifest:
        versions = self.load_model_versions()
        deployments = self.load_deployments()
        events = self.load_events()
        counts: dict[str, int] = {}
        for record in versions:
            counts[record.lifecycle_status] = counts.get(record.lifecycle_status, 0) + 1
        manifest = ModelRegistryManifest(
            created_at=_utc_now(),
            model_versions=len(versions),
            deployments=len(deployments),
            events=len(events),
            active_deployments=sum(1 for item in deployments if item.status == "active"),
            status_counts=counts,
        )
        write_json_artifact(self.manifest_path, manifest.to_dict(), artifact_type="model_registry_manifest", producer="model_registry")
        return manifest

    def _find_existing(self, factor_id: str, formula_hash: str, model_kind: str) -> ModelVersionRecord | None:
        for record in self.load_model_versions():
            if record.factor_id == factor_id and record.formula_hash == formula_hash and record.model_kind == model_kind:
                return record
        return None

    def _require_model(self, model_version_id: str) -> ModelVersionRecord:
        record = self.get_model_version(model_version_id)
        if record is None:
            raise FileNotFoundError(f"model version not found: {model_version_id}")
        return record

    def _rewrite_versions(self, updated: ModelVersionRecord) -> None:
        records = [updated if item.model_version_id == updated.model_version_id else item for item in self.load_model_versions()]
        self.root_dir.mkdir(parents=True, exist_ok=True)
        with self.versions_path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True))
                handle.write("\n")

    def _append_event(
        self,
        model_version_id: str,
        from_status: str | None,
        to_status: str,
        action: str,
        actor: str,
        reason: str | None,
        approval_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = _utc_now()
        event = ModelLifecycleEvent(
            event_id=make_event_id(model_version_id, action, now, len(self.load_events())),
            model_version_id=model_version_id,
            from_status=from_status,
            to_status=to_status,
            action=action,
            actor=actor,
            reason=reason,
            approval_id=approval_id,
            created_at=now,
            metadata=metadata or {},
        )
        self.root_dir.mkdir(parents=True, exist_ok=True)
        _append_jsonl(self.events_path, event.to_dict())

    def _write_deployments(self, deployments: list[ModelDeploymentRecord]) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        with self.deployments_path.open("w", encoding="utf-8") as handle:
            for record in deployments:
                handle.write(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True))
                handle.write("\n")

    def _replace_deployment(self, deployment: ModelDeploymentRecord) -> None:
        deployments = [deployment if item.deployment_id == deployment.deployment_id else item for item in self.load_deployments()]
        self._write_deployments(deployments)

    def _mark_deployments(self, model_version_id: str, status: str) -> None:
        now = _utc_now()
        deployments = [
            replace(item, status=status, ended_at=now) if item.model_version_id == model_version_id and item.status == "active" else item
            for item in self.load_deployments()
        ]
        self._write_deployments(deployments)
        self._write_state()

    def _write_state(self) -> None:
        manifest = self.write_manifest()
        state = {
            "created_at": _utc_now(),
            "manifest": manifest.to_dict(),
            "active_deployments": [item.to_dict() for item in self.load_deployments() if item.status == "active"],
        }
        write_json_artifact(self.state_path, state, artifact_type="model_state", producer="model_registry")


def make_model_version_id(model_kind: str, factor_id: str, formula_hash: str) -> str:
    digest = hashlib.sha256(f"{model_kind}|{factor_id}|{formula_hash}".encode("utf-8")).hexdigest()
    return f"mv_{digest[:16]}"


def make_deployment_id(model_kind: str, environment: str, created_at: str) -> str:
    safe = "".join(char if char.isalnum() else "_" for char in created_at).strip("_")
    digest = hashlib.sha256(f"{model_kind}|{environment}|{created_at}".encode("utf-8")).hexdigest()[:8]
    return f"dep_{model_kind}_{environment}_{safe}_{digest}"


def make_event_id(model_version_id: str, action: str, created_at: str, index: int) -> str:
    digest = hashlib.sha256(f"{model_version_id}|{action}|{created_at}|{index}".encode("utf-8")).hexdigest()
    return f"evt_{digest[:16]}"


def _kind_from_factor(record: FactorRecord) -> str:
    return ModelKind.composite_factor if (record.factor_type or "") == "composite" else ModelKind.single_factor


def _extract_policy_payload(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("portfolio_policy", "selected_policy", "certified_portfolio_policy", "policy"):
        candidate = payload.get(key)
        if isinstance(candidate, dict):
            return dict(candidate)
    return dict(payload)


def _status_from_factor(status: str) -> str:
    allowed = {
        ModelLifecycleStatus.research_candidate,
        ModelLifecycleStatus.approved,
        ModelLifecycleStatus.production_candidate,
        ModelLifecycleStatus.active,
        ModelLifecycleStatus.paused,
        ModelLifecycleStatus.quarantined,
        ModelLifecycleStatus.deprecated,
        ModelLifecycleStatus.retired,
        ModelLifecycleStatus.rejected,
    }
    return status if status in allowed else ModelLifecycleStatus.research_candidate


def _registry_store_read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        handle.write("\n")


def _version_defaults(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    normalized.setdefault("parent_factor_ids", [])
    normalized.setdefault("source_batch_id", None)
    normalized.setdefault("source_run_id", None)
    normalized.setdefault("source_artifacts", {})
    normalized.setdefault("metrics", {})
    normalized.setdefault("gate_status", None)
    normalized.setdefault("activated_at", None)
    normalized.setdefault("deactivated_at", None)
    normalized.setdefault("retired_at", None)
    normalized.setdefault("schema_version", "1.0")
    normalized.setdefault("metadata", {})
    return normalized


def _deployment_defaults(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    normalized.setdefault("environment", "paper")
    normalized.setdefault("status", "active")
    normalized.setdefault("activation_approval_id", None)
    normalized.setdefault("rollback_from_deployment_id", None)
    normalized.setdefault("ended_at", None)
    normalized.setdefault("reason", None)
    normalized.setdefault("metadata", {})
    return normalized


def _event_defaults(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    normalized.setdefault("reason", None)
    normalized.setdefault("approval_id", None)
    normalized.setdefault("metadata", {})
    return normalized


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

import json
from datetime import datetime
from pathlib import Path

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact



def build_model_registry_report(registry: LocalModelRegistry, lineage_graph_path: str | None = None) -> ModelRegistryReport:
    manifest = registry.write_manifest()
    versions = registry.load_model_versions()
    deployments = registry.load_deployments()
    return ModelRegistryReport(
        created_at=datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        manifest=manifest.to_dict(),
        active_models=[record.to_dict() for record in versions if record.lifecycle_status == "active"],
        latest_models=[record.to_dict() for record in versions[-10:]],
        deployments=[record.to_dict() for record in deployments],
        recent_events=[event.to_dict() for event in registry.load_events()[-25:]],
        lineage_graph_path=lineage_graph_path,
    )


def write_model_registry_report(registry: LocalModelRegistry, output_dir: str | Path | None = None) -> tuple[Path, Path]:
    root = Path(output_dir) if output_dir is not None else registry.root_dir
    root.mkdir(parents=True, exist_ok=True)
    lineage = build_model_lineage_graph(registry)
    lineage_path = root / "model_lineage_graph.json"
    write_json_artifact(lineage_path, lineage.to_dict(), artifact_type="model_lineage_graph", producer="model_registry")
    report = build_model_registry_report(registry, str(lineage_path))
    json_path = root / "model_registry_report.json"
    md_path = root / "model_registry_report.md"
    write_json_artifact(json_path, report.to_dict(), artifact_type="model_registry_report", producer="model_registry")
    md_path.write_text(_render_markdown(report), encoding="utf-8")
    registry.write_manifest()
    return json_path, md_path


def write_lineage_graph(registry: LocalModelRegistry, graph, output_dir: str | Path | None = None) -> Path:
    root = Path(output_dir) if output_dir is not None else registry.root_dir
    root.mkdir(parents=True, exist_ok=True)
    path = root / "model_lineage_graph.json"
    write_json_artifact(path, graph.to_dict(), artifact_type="model_lineage_graph", producer="model_registry")
    return path


def _render_markdown(report: ModelRegistryReport) -> str:
    lines = [
        "# Model Registry Report",
        "",
        f"- model_versions: {report.manifest.get('model_versions', 0)}",
        f"- active_deployments: {report.manifest.get('active_deployments', 0)}",
        "",
        "## Active Models",
        "",
        "| model_version_id | kind | factor_id | status |",
        "| --- | --- | --- | --- |",
    ]
    for record in report.active_models:
        lines.append(
            f"| {record.get('model_version_id')} | {record.get('model_kind')} | {record.get('factor_id')} | {record.get('lifecycle_status')} |"
        )
    lines.extend(["", "## Status Counts", "", "```json", json.dumps(report.manifest.get("status_counts", {}), indent=2), "```", ""])
    return "\n".join(lines)

import argparse
import json
from pathlib import Path

from auto_alpha.research.factors.store import LocalFactorStore



def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage local model registry records.")
    parser.add_argument("--registry-dir", required=True)
    parser.add_argument("--factor-store-dir")
    parser.add_argument("--pretty", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in [
        "register-factor",
        "register-production-candidate-bundle",
        "show-production-candidates",
        "list-models",
        "show-model",
        "show-active",
        "activate",
        "pause",
        "resume",
        "quarantine",
        "retire",
        "rollback",
        "lineage",
        "report",
    ]:
        cmd = sub.add_parser(name)
        cmd.add_argument("--factor-id")
        cmd.add_argument("--model-version-id")
        cmd.add_argument("--model-kind", default=ModelKind.composite_factor)
        cmd.add_argument("--approval-id")
        cmd.add_argument("--actor", default="local_operator")
        cmd.add_argument("--reason")
        cmd.add_argument("--environment", default="paper")
        cmd.add_argument("--artifact-dir", action="append", default=[])
        cmd.add_argument("--artifact-catalog-path", action="append", default=[])
        cmd.add_argument("--production-candidate-bundle-path")
        cmd.add_argument("--dry-run", action="store_true")
        cmd.add_argument("--explicit-override", action="store_true")
        cmd.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    registry = LocalModelRegistry(args.registry_dir)
    try:
        payload = _run(args, registry)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2 if args.pretty else None))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def _run(args: argparse.Namespace, registry: LocalModelRegistry) -> dict:
    if args.command == "register-factor":
        if not args.factor_store_dir or not args.factor_id:
            raise ValueError("register-factor requires --factor-store-dir and --factor-id")
        factors = LocalFactorStore(args.factor_store_dir).load_factors()
        factor = next((record for record in factors if record.factor_id == args.factor_id), None)
        if factor is None:
            raise FileNotFoundError(f"factor not found: {args.factor_id}")
        record = registry.register_factor_record(factor, model_kind=args.model_kind)
        write_model_registry_report(registry)
        return {"model_version": record.to_dict(), "model_version_id": record.model_version_id}
    if args.command in {"register-production-candidate-bundle", "show-production-candidates"}:
        if not args.production_candidate_bundle_path:
            raise ValueError(f"{args.command} requires --production-candidate-bundle-path")
        candidates = _registry_run_registry_read_jsonl(Path(args.production_candidate_bundle_path))
        payload = {
            "status": "dry_run" if args.dry_run or args.command == "show-production-candidates" else "recorded",
            "candidate_count": len(candidates),
            "candidates": candidates,
            "note": "production candidate bundles are not activated by this command",
        }
        if args.command == "register-production-candidate-bundle" and not args.dry_run:
            path = Path(args.registry_dir) / "production_candidate_bundle_registry.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            payload["production_candidate_bundle_registry_path"] = str(path)
        return payload
    if args.command == "list-models":
        return {"models": [record.to_dict() for record in registry.load_model_versions()]}
    if args.command == "show-model":
        record = registry.get_model_version(args.model_version_id)
        if record is None:
            raise FileNotFoundError(f"model version not found: {args.model_version_id}")
        return record.to_dict()
    if args.command == "show-active":
        record = registry.latest_active(model_kind=args.model_kind, environment=args.environment)
        deployment = registry.latest_active_deployment(model_kind=args.model_kind, environment=args.environment)
        return {"model_version": record.to_dict() if record else None, "deployment": deployment.to_dict() if deployment else None}
    if args.command == "activate":
        record, deployment = registry.activate(
            args.model_version_id,
            approval_id=args.approval_id,
            actor=args.actor,
            reason=args.reason,
            environment=args.environment,
            explicit_override=args.explicit_override,
        )
        _sync_if_possible(args, registry, record.model_version_id)
        write_model_registry_report(registry)
        return {"model_version": record.to_dict(), "deployment": deployment.to_dict()}
    if args.command == "pause":
        record = registry.pause(args.model_version_id, args.reason, args.actor)
        _sync_if_possible(args, registry, record.model_version_id)
        write_model_registry_report(registry)
        return record.to_dict()
    if args.command == "resume":
        record = registry.transition(
            args.model_version_id,
            ModelLifecycleAction.resume,
            ModelLifecycleStatus.active,
            args.actor,
            args.reason,
            explicit_override=args.explicit_override,
        )
        _sync_if_possible(args, registry, record.model_version_id)
        write_model_registry_report(registry)
        return record.to_dict()
    if args.command == "quarantine":
        record = registry.quarantine(args.model_version_id, args.reason, args.actor)
        _sync_if_possible(args, registry, record.model_version_id)
        write_model_registry_report(registry)
        return record.to_dict()
    if args.command == "retire":
        record = registry.retire(args.model_version_id, args.reason, args.actor)
        _sync_if_possible(args, registry, record.model_version_id)
        write_model_registry_report(registry)
        return record.to_dict()
    if args.command == "rollback":
        record, deployment = registry.rollback(
            model_kind=args.model_kind,
            environment=args.environment,
            actor=args.actor,
            reason=args.reason,
            explicit_override=args.explicit_override,
        )
        _sync_if_possible(args, registry, record.model_version_id)
        write_model_registry_report(registry)
        return {"model_version": record.to_dict(), "deployment": deployment.to_dict()}
    if args.command == "lineage":
        factor_store = LocalFactorStore(args.factor_store_dir) if args.factor_store_dir else None
        graph = build_model_lineage_graph(registry, factor_store, args.artifact_catalog_path, args.artifact_dir)
        path = write_lineage_graph(registry, graph)
        return graph.to_dict() | {"path": str(path)}
    if args.command == "report":
        json_path, md_path = write_model_registry_report(registry)
        return {"model_registry_report_path": str(json_path), "model_registry_report_md_path": str(md_path)}
    raise ValueError(f"unsupported command: {args.command}")


def _sync_if_possible(args: argparse.Namespace, registry: LocalModelRegistry, model_version_id: str) -> None:
    if args.factor_store_dir:
        registry.sync_factor_store_status(LocalFactorStore(args.factor_store_dir), model_version_id)


def _registry_run_registry_read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


if __name__ == "__main__":
    raise SystemExit(main())

__all__ = [
    "LocalModelRegistry",
    "ModelDeploymentRecord",
    "ModelKind",
    "ModelLifecycleAction",
    "ModelLifecycleEvent",
    "ModelLifecycleStatus",
    "ModelLineageGraph",
    "ModelRegistryManifest",
    "ModelRegistryReport",
    "ModelVersionRecord",
    "build_model_lineage_graph",
    "build_model_registry_report",
    "make_model_version_id",
    "validate_transition",
    "write_lineage_graph",
    "write_model_registry_report",
]

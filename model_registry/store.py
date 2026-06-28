"""Local JSON/JSONL model registry store."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact
from factor_store import FactorRecord, LocalFactorStore

from .models import (
    ModelDeploymentRecord,
    ModelKind,
    ModelLifecycleAction,
    ModelLifecycleEvent,
    ModelLifecycleStatus,
    ModelRegistryManifest,
    ModelVersionRecord,
)
from .state_machine import validate_transition


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
        return [ModelVersionRecord(**_version_defaults(payload)) for payload in _read_jsonl(self.versions_path)]

    def load_deployments(self) -> list[ModelDeploymentRecord]:
        return [ModelDeploymentRecord(**_deployment_defaults(payload)) for payload in _read_jsonl(self.deployments_path)]

    def load_events(self) -> list[ModelLifecycleEvent]:
        return [ModelLifecycleEvent(**_event_defaults(payload)) for payload in _read_jsonl(self.events_path)]

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


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
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

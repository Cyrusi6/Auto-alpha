import json

import pytest

from artifact_schema.validator import validate_artifact
from factor_store import FactorRecord, LocalFactorStore, stable_formula_hash
from model_registry import LocalModelRegistry, ModelKind, ModelLifecycleStatus, build_model_lineage_graph
from model_registry.report import write_lineage_graph, write_model_registry_report


def _save_factor(root, factor_id="factor_model_registry", status="production_candidate"):
    store = LocalFactorStore(root)
    formula_tokens = [0]
    formula_names = ["RET_1D"]
    formula_hash = stable_formula_hash(formula_tokens, formula_names, "ashare_features_v1", "ashare_ops_v1")
    store.save_factor(
        FactorRecord(
            factor_id=factor_id,
            formula=formula_names,
            formula_tokens=formula_tokens,
            formula_hash=formula_hash,
            feature_version="ashare_features_v1",
            operator_version="ashare_ops_v1",
            lookback_days=1,
            created_at="2026-06-28T00:00:00Z",
            status=status,
            metrics={"score": 1.0},
            parent_factor_ids=["factor_a", "factor_b"],
            factor_type="composite",
            batch_id="batch_1",
        )
    )
    return store


def test_model_registry_register_activate_pause_rollback_and_sync(tmp_path):
    factor_store = _save_factor(tmp_path / "store")
    registry = LocalModelRegistry(tmp_path / "registry")
    factor = factor_store.load_factors()[0]
    model = registry.register_factor_record(factor, model_kind=ModelKind.composite_factor)

    assert model.lifecycle_status == ModelLifecycleStatus.production_candidate
    with pytest.raises(ValueError):
        registry.activate(model.model_version_id)

    active, deployment = registry.activate(model.model_version_id, approval_id="approval_1")
    assert active.lifecycle_status == ModelLifecycleStatus.active
    assert deployment.status == "active"
    factor_store.sync_status_from_model_registry(registry, model.model_version_id)
    assert factor_store.load_factors()[0].status == ModelLifecycleStatus.active

    paused = registry.pause(model.model_version_id, reason="manual pause", actor="tester")
    assert paused.lifecycle_status == ModelLifecycleStatus.paused
    rolled_back, rollback_deployment = registry.rollback(actor="tester", explicit_override=True)
    assert rolled_back.lifecycle_status == ModelLifecycleStatus.active
    assert rollback_deployment.rollback_from_deployment_id

    report_json, report_md = write_model_registry_report(registry)
    lineage = build_model_lineage_graph(registry, factor_store)
    lineage_path = write_lineage_graph(registry, lineage)

    assert report_json.exists()
    assert report_md.exists()
    assert lineage_path.exists()
    assert validate_artifact(report_json).valid
    assert validate_artifact(lineage_path).valid
    json.dumps(registry.write_manifest().to_dict())


def test_model_registry_register_is_idempotent_and_new_active_supersedes_old(tmp_path):
    factor_store = _save_factor(tmp_path / "store", factor_id="factor_registry_a")
    registry = LocalModelRegistry(tmp_path / "registry")
    factor_a = factor_store.load_factors()[0]
    model_a = registry.register_factor_record(factor_a)
    duplicate = registry.register_factor_record(factor_a)

    assert duplicate.model_version_id == model_a.model_version_id
    assert len(registry.load_model_versions()) == 1

    registry.activate(model_a.model_version_id, approval_id="approval_a")
    formula_tokens = [1]
    formula_names = ["RET_5D"]
    formula_hash = stable_formula_hash(formula_tokens, formula_names, "ashare_features_v1", "ashare_ops_v1")
    factor_store.save_factor(
        FactorRecord(
            factor_id="factor_registry_b",
            formula=formula_names,
            formula_tokens=formula_tokens,
            formula_hash=formula_hash,
            feature_version="ashare_features_v1",
            operator_version="ashare_ops_v1",
            lookback_days=5,
            created_at="2026-06-28T00:00:00Z",
            status="production_candidate",
            factor_type="composite",
        )
    )
    model_b = registry.register_factor_record(factor_store.load_factors()[-1])
    registry.activate(model_b.model_version_id, approval_id="approval_b")

    deployments = registry.load_deployments()
    assert any(item.model_version_id == model_a.model_version_id and item.status == "previous" for item in deployments)
    assert registry.latest_active().model_version_id == model_b.model_version_id


def test_model_registry_terminal_status_blocks_transition(tmp_path):
    factor_store = _save_factor(tmp_path / "store")
    registry = LocalModelRegistry(tmp_path / "registry")
    model = registry.register_factor_record(factor_store.load_factors()[0])
    retired = registry.retire(model.model_version_id, reason="end of life", actor="tester")

    assert retired.lifecycle_status == ModelLifecycleStatus.retired
    with pytest.raises(ValueError):
        registry.activate(model.model_version_id, explicit_override=True)

import json

from approval import ApprovalType, LocalApprovalStore
from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from factor_lifecycle import evaluate_factor_health, load_lifecycle_policy
from factor_lifecycle.run_lifecycle import main as lifecycle_main
from factor_store import FactorRecord, LocalFactorStore, stable_formula_hash
from model_core.data_loader import AShareDataLoader
from model_registry import LocalModelRegistry, ModelLifecycleStatus


def _prepare_factor(tmp_path, status="production_candidate"):
    data_dir = tmp_path / "data"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True)
    loader = AShareDataLoader(data_dir=data_dir, device="cpu").load_data()
    store = LocalFactorStore(tmp_path / "store")
    factor_id = "factor_lifecycle_smoke"
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
            metrics={"score": 1.0, "rank_ic": 0.1},
            parent_factor_ids=["factor_a", "factor_b"],
            factor_type="composite",
        )
    )
    values = [[float(stock_idx + date_idx + 1) for date_idx, _date in enumerate(loader.trade_dates)] for stock_idx, _code in enumerate(loader.ts_codes)]
    store.save_factor_values(factor_id, loader.ts_codes, loader.trade_dates, values)
    return data_dir, store, factor_id


def test_factor_health_and_lifecycle_review_approval_flow(tmp_path, capsys):
    data_dir, store, factor_id = _prepare_factor(tmp_path)
    loader = AShareDataLoader(data_dir=data_dir, device="cpu").load_data()
    metrics, checks = evaluate_factor_health(loader, store, factor_id, "20240104", load_lifecycle_policy(None))

    assert metrics["factor_value_count"] > 0
    assert any(check.name == "recent_coverage" for check in checks)

    exit_code = lifecycle_main(
        [
            "propose-activation",
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(tmp_path / "store"),
            "--registry-dir",
            str(tmp_path / "registry"),
            "--approval-store-dir",
            str(tmp_path / "approvals"),
            "--output-dir",
            str(tmp_path / "lifecycle"),
            "--factor-id",
            factor_id,
            "--as-of-date",
            "20240104",
            "--create-review-package",
            "--require-approval",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["approval_status"] == "pending"
    assert payload["approval_id"]
    assert (tmp_path / "lifecycle" / "factor_lifecycle_report.json").exists()
    assert (tmp_path / "lifecycle" / "model_review_package.json").exists()
    assert (tmp_path / "registry" / "model_versions.jsonl").exists()

    approval_store = LocalApprovalStore(tmp_path / "approvals")
    batch = approval_store.load_batch(payload["approval_id"])
    assert batch.approval_type == ApprovalType.model_lifecycle
    assert batch.model_version_id
    assert batch.model_lifecycle_action == "activate"

    approval_store.approve(payload["approval_id"], reviewer="reviewer")
    apply_exit = lifecycle_main(
        [
            "apply-approved",
            "--factor-store-dir",
            str(tmp_path / "store"),
            "--registry-dir",
            str(tmp_path / "registry"),
            "--approval-store-dir",
            str(tmp_path / "approvals"),
            "--output-dir",
            str(tmp_path / "lifecycle_apply"),
            "--approval-id",
            payload["approval_id"],
            "--pretty",
        ]
    )
    apply_payload = json.loads(capsys.readouterr().out)

    assert apply_exit == 0
    assert apply_payload["model_version"]["lifecycle_status"] == ModelLifecycleStatus.active
    assert LocalModelRegistry(tmp_path / "registry").latest_active() is not None
    assert store.load_factors()[0].status == ModelLifecycleStatus.active


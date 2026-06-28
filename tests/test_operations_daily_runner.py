import json

from approval import LocalApprovalStore
from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from factor_store import FactorRecord, LocalFactorStore, stable_formula_hash
from model_core.data_loader import AShareDataLoader
from model_registry import LocalModelRegistry
from operations import ProductionDailyRunner
from operations import run_daily
from paper_account import LocalPaperAccount


def _prepare_data_and_factor(tmp_path, status="production_candidate"):
    data_dir = tmp_path / "data"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True)
    loader = AShareDataLoader(data_dir=data_dir, device="cpu").load_data()
    store = LocalFactorStore(tmp_path / "store")
    factor_id = "factor_prod_ops"
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
            created_at="2026-06-27T00:00:00Z",
            status=status,
            metrics={"score": 1.0},
            parent_factor_ids=["factor_a", "factor_b"],
            factor_type="composite",
            metadata={"component_factor_ids": ["factor_a", "factor_b"]},
        )
    )
    values = [[float(stock_idx + date_idx) for date_idx, _date in enumerate(loader.trade_dates)] for stock_idx, _code in enumerate(loader.ts_codes)]
    store.save_factor_values(factor_id, loader.ts_codes, loader.trade_dates, values)
    LocalPaperAccount(tmp_path / "account").reset(1_000_000.0)
    return data_dir, tmp_path / "store", factor_id


def test_daily_run_requires_approval_then_executes_approved_batch(tmp_path):
    data_dir, store_dir, factor_id = _prepare_data_and_factor(tmp_path)
    runner = ProductionDailyRunner(
        data_dir=data_dir,
        factor_store_dir=store_dir,
        approval_store_dir=tmp_path / "approvals",
        paper_account_dir=tmp_path / "account",
        output_dir=tmp_path / "production",
        orders_dir=tmp_path / "orders",
        latest_production=True,
        rebalance_date="20240104",
        portfolio_method="risk_aware",
        index_code="000300.SH",
        top_n=2,
        max_weight=0.10,
        use_factor_risk_model=True,
        max_active_style_exposure=1.0,
        capacity_aware=True,
        execution_plan_dir=tmp_path / "orders" / "plan",
    )
    proposed = runner.run(require_approval=True)

    assert proposed.status == "pending_approval"
    assert proposed.factor_id == factor_id
    assert proposed.approval_id
    assert proposed.summary["style_exposures"]
    assert proposed.summary["active_style_exposures"]
    assert proposed.summary["risk_decomposition"]
    assert proposed.summary["child_order_count"] > 0
    assert not (tmp_path / "orders" / "paper_fills.jsonl").exists()
    pending_batch = LocalApprovalStore(tmp_path / "approvals").load_batch(proposed.approval_id)
    assert pending_batch.status == "pending"
    assert pending_batch.parent_orders
    assert pending_batch.child_orders
    assert pending_batch.capacity_summary

    failed_exit = run_daily.main(
        [
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(store_dir),
            "--approval-store-dir",
            str(tmp_path / "approvals"),
            "--paper-account-dir",
            str(tmp_path / "account"),
            "--output-dir",
            str(tmp_path / "failed"),
            "--orders-dir",
            str(tmp_path / "failed_orders"),
            "--approval-id",
            proposed.approval_id,
            "--execute-approved",
        ]
    )
    assert failed_exit == 1

    LocalApprovalStore(tmp_path / "approvals").approve(proposed.approval_id, reviewer="reviewer")
    executed = runner.run(approval_id=proposed.approval_id, execute_approved=True)

    assert executed.status == "executed"
    assert executed.executed is True
    assert executed.summary["style_exposures"]
    assert executed.summary["active_style_exposures"]
    assert executed.summary["execution_quality"]
    assert (tmp_path / "orders" / "paper_fills.jsonl").exists()
    assert (tmp_path / "orders" / "plan" / "child_fills.jsonl").exists()
    assert (tmp_path / "account" / "account_state.json").exists()
    assert (tmp_path / "account" / "account_snapshots.jsonl").exists()
    account_payload = json.loads((tmp_path / "account" / "account_state.json").read_text(encoding="utf-8"))
    assert all("child_order_id" in entry for entry in account_payload["trade_ledger"])
    json.dumps(executed.to_dict())


def test_daily_run_falls_back_to_latest_approved_composite(tmp_path):
    data_dir, store_dir, factor_id = _prepare_data_and_factor(tmp_path, status="approved")
    runner = ProductionDailyRunner(
        data_dir=data_dir,
        factor_store_dir=store_dir,
        approval_store_dir=tmp_path / "approvals",
        paper_account_dir=tmp_path / "account",
        output_dir=tmp_path / "production",
        orders_dir=tmp_path / "orders",
        latest_production=True,
        rebalance_date="20240104",
        top_n=2,
        max_weight=0.10,
    )
    proposed = runner.run(require_approval=True)

    assert proposed.status == "pending_approval"
    assert proposed.factor_id == factor_id


def test_daily_run_simulated_broker_is_idempotent(tmp_path):
    data_dir, store_dir, _factor_id = _prepare_data_and_factor(tmp_path)
    propose_runner = ProductionDailyRunner(
        data_dir=data_dir,
        factor_store_dir=store_dir,
        approval_store_dir=tmp_path / "approvals",
        paper_account_dir=tmp_path / "account",
        output_dir=tmp_path / "production",
        orders_dir=tmp_path / "orders",
        latest_production=True,
        rebalance_date="20240104",
        portfolio_method="risk_aware",
        index_code="000300.SH",
        top_n=2,
        max_weight=0.10,
        use_factor_risk_model=True,
        capacity_aware=True,
    )
    proposed = propose_runner.run(require_approval=True)
    LocalApprovalStore(tmp_path / "approvals").approve(proposed.approval_id, reviewer="reviewer")
    execute_runner = ProductionDailyRunner(
        data_dir=data_dir,
        factor_store_dir=store_dir,
        approval_store_dir=tmp_path / "approvals",
        paper_account_dir=tmp_path / "account",
        output_dir=tmp_path / "production_execute",
        orders_dir=tmp_path / "orders_execute",
        rebalance_date="20240104",
        capacity_aware=True,
        broker_adapter="simulated",
        broker_store_dir=tmp_path / "broker",
        broker_reconcile=True,
    )
    executed = execute_runner.run(approval_id=proposed.approval_id, execute_approved=True)
    first_state = json.loads((tmp_path / "account" / "account_state.json").read_text(encoding="utf-8"))
    replay = execute_runner.run(approval_id=proposed.approval_id, execute_approved=True)
    second_state = json.loads((tmp_path / "account" / "account_state.json").read_text(encoding="utf-8"))

    assert executed.status == "executed"
    assert executed.summary["broker_adapter"] == "simulated"
    assert executed.summary["broker_report_path"]
    assert replay.summary["idempotent_replay_count"] > 0
    assert first_state["cash"] == second_state["cash"]
    assert len(first_state["trade_ledger"]) == len(second_state["trade_ledger"])


def test_daily_run_file_broker_exports_without_account_update(tmp_path):
    data_dir, store_dir, _factor_id = _prepare_data_and_factor(tmp_path)
    runner = ProductionDailyRunner(
        data_dir=data_dir,
        factor_store_dir=store_dir,
        approval_store_dir=tmp_path / "approvals",
        paper_account_dir=tmp_path / "account",
        output_dir=tmp_path / "production",
        orders_dir=tmp_path / "orders",
        latest_production=True,
        rebalance_date="20240104",
        capacity_aware=True,
    )
    proposed = runner.run(require_approval=True)
    LocalApprovalStore(tmp_path / "approvals").approve(proposed.approval_id, reviewer="reviewer")
    file_runner = ProductionDailyRunner(
        data_dir=data_dir,
        factor_store_dir=store_dir,
        approval_store_dir=tmp_path / "approvals",
        paper_account_dir=tmp_path / "account",
        output_dir=tmp_path / "production_file",
        orders_dir=tmp_path / "orders_file",
        rebalance_date="20240104",
        capacity_aware=True,
        broker_adapter="file",
        broker_store_dir=tmp_path / "broker_file",
        broker_outbox_dir=tmp_path / "broker_file" / "outbox",
    )
    result = file_runner.run(approval_id=proposed.approval_id, execute_approved=True)
    state = json.loads((tmp_path / "account" / "account_state.json").read_text(encoding="utf-8"))

    assert result.status == "broker_exported"
    assert result.executed is False
    assert result.summary["broker_outbox_manifest_path"]
    assert state["cash"] == 1_000_000.0


def test_daily_run_can_require_active_model_registry_entry(tmp_path):
    data_dir, store_dir, factor_id = _prepare_data_and_factor(tmp_path)
    factor_store = LocalFactorStore(store_dir)
    registry = LocalModelRegistry(tmp_path / "registry")
    model = registry.register_factor_record(factor_store.load_factors()[0])
    active, _deployment = registry.activate(model.model_version_id, approval_id="approval_model")
    registry.sync_factor_store_status(factor_store, active.model_version_id)

    runner = ProductionDailyRunner(
        data_dir=data_dir,
        factor_store_dir=store_dir,
        approval_store_dir=tmp_path / "approvals",
        paper_account_dir=tmp_path / "account",
        output_dir=tmp_path / "production",
        orders_dir=tmp_path / "orders",
        latest_production=True,
        rebalance_date="20240104",
        top_n=2,
        max_weight=0.10,
        use_model_registry=True,
        model_registry_dir=tmp_path / "registry",
        require_active_model=True,
    )
    proposed = runner.run(require_approval=True)

    assert proposed.factor_id == factor_id
    assert proposed.summary["model_version_id"] == active.model_version_id
    approval = LocalApprovalStore(tmp_path / "approvals").load_batch(proposed.approval_id)
    assert approval.model_version_id == active.model_version_id
    assert approval.lifecycle_summary["model_lifecycle_status"] == "active"

    registry.pause(active.model_version_id, reason="test pause", actor="tester")
    blocked_runner = ProductionDailyRunner(
        data_dir=data_dir,
        factor_store_dir=store_dir,
        approval_store_dir=tmp_path / "approvals2",
        paper_account_dir=tmp_path / "account",
        output_dir=tmp_path / "blocked",
        orders_dir=tmp_path / "blocked_orders",
        latest_production=True,
        rebalance_date="20240104",
        top_n=2,
        max_weight=0.10,
        use_model_registry=True,
        model_registry_dir=tmp_path / "registry",
        require_active_model=True,
    )

    blocked = blocked_runner.run(require_approval=True)

    assert blocked.status == "failed"
    assert "active model" in blocked.error or "paused" in blocked.error

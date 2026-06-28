import json

from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from execution import ExecutionFill, export_fills_jsonl
from broker_adapter import BrokerOrderRequest, SimulatedBrokerAdapter
from factor_store import FactorRecord, LocalFactorStore, stable_formula_hash
from model_core.data_loader import AShareDataLoader
from model_registry import LocalModelRegistry
from model_registry.report import write_model_registry_report
from monitoring import run_monitor
from monitoring.checks import check_quality_report
from paper_account import LocalPaperAccount


def _prepare_monitoring_artifacts(tmp_path):
    data_dir = tmp_path / "data"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True)
    loader = AShareDataLoader(data_dir=data_dir, device="cpu").load_data()
    store = LocalFactorStore(tmp_path / "store")
    factor_id = "factor_monitor"
    formula_tokens = [0]
    formula_names = ["RET_1D"]
    store.save_factor(
        FactorRecord(
            factor_id=factor_id,
            formula=formula_names,
            formula_tokens=formula_tokens,
            formula_hash=stable_formula_hash(formula_tokens, formula_names, "ashare_features_v1", "ashare_ops_v1"),
            feature_version="ashare_features_v1",
            operator_version="ashare_ops_v1",
            lookback_days=1,
            created_at="2026-06-27T00:00:00Z",
            status="production_candidate",
            factor_type="composite",
        )
    )
    store.save_factor_values(
        factor_id,
        loader.ts_codes,
        loader.trade_dates,
        [[float(stock_idx + date_idx) for date_idx, _date in enumerate(loader.trade_dates)] for stock_idx, _code in enumerate(loader.ts_codes)],
    )
    account = LocalPaperAccount(tmp_path / "account")
    account.reset(100000.0)
    account.mark_to_market({"000001.SZ": 10.0}, "20240104")
    orders_dir = tmp_path / "orders"
    export_fills_jsonl(
        [
            ExecutionFill("20240104", "000001.SZ", "BUY", 10.0, 0, 0.0, "REJECTED", reason="limit_up"),
            ExecutionFill("20240104", "600000.SH", "BUY", 10.0, 0, 0.0, "REJECTED", reason="limit_up"),
        ],
        orders_dir / "paper_fills.jsonl",
    )
    (orders_dir / "risk_model_report.json").write_text(
        json.dumps(
            {
                "violations": [],
                "metrics": {"tracking_error": 0.1},
                "factor_risk_contribution": {
                    "factor_risk_share": 0.4,
                    "specific_risk_share": 0.6,
                    "factor_contributions": {"size": 0.2, "value": 0.1},
                },
            }
        ),
        encoding="utf-8",
    )
    (orders_dir / "risk_exposures.jsonl").write_text(
        '{"trade_date":"20240104","max_style_exposure_abs":0.5,"max_active_style_exposure_abs":0.4}\n',
        encoding="utf-8",
    )
    (orders_dir / "risk_decomposition.jsonl").write_text(
        '{"trade_date":"20240104","active":{"total_risk":0.2}}\n',
        encoding="utf-8",
    )
    (orders_dir / "return_attribution.jsonl").write_text(
        '{"trade_date":"20240104","total_active_return":0.01}\n',
        encoding="utf-8",
    )
    plan_dir = orders_dir / "plan"
    plan_dir.mkdir(parents=True)
    (plan_dir / "capacity_report.json").write_text(
        '{"portfolio":{"capacity_warning_count":1,"estimated_impact_cost":10.0,"total_order_value":1000.0}}',
        encoding="utf-8",
    )
    (plan_dir / "execution_quality.json").write_text(
        '{"execution_fill_rate":0.25,"rejected_child_orders":2,"partial_child_orders":1,"unfilled_order_value":750.0}',
        encoding="utf-8",
    )
    broker_dir = tmp_path / "broker"
    request = BrokerOrderRequest(
        client_order_id="child_monitor",
        batch_id="batch_monitor",
        trade_date="20240104",
        ts_code="000001.SZ",
        side="BUY",
        shares=100,
        order_value=1000.0,
        price=10.0,
        child_order_id="child_monitor",
    )
    broker = SimulatedBrokerAdapter(broker_dir, prices={"000001.SZ": 10.0}, volumes={"000001.SZ": 10_000.0})
    broker.submit_orders([request], batch_id="batch_monitor")
    (orders_dir / "broker").mkdir()
    (orders_dir / "broker" / "broker_reconciliation.json").write_text(
        '{"batch_id":"batch_monitor","status_mismatch_count":0,"orphan_fills":0,"unfilled_value":0.0,"issues":[]}',
        encoding="utf-8",
    )
    settlement_dir = tmp_path / "settlement"
    settlement_dir.mkdir()
    (settlement_dir / "settlement_report.json").write_text(
        json.dumps(
            {
                "settlement_aware": True,
                "settlement_profile": "cn_ashare_paper_default",
                "pending_settlement_event_count": 1,
                "failed_settlement_event_count": 0,
                "settlement_reconciliation_error_count": 0,
                "available_cash": 100000.0,
                "withdrawable_cash": 100000.0,
                "frozen_cash": 0.0,
                "unsettled_receivable": 10.0,
                "unsettled_payable": 0.0,
                "realized_pnl": 1.0,
                "unrealized_pnl": 2.0,
                "nav_difference": 0.0,
                "fee_tax_total": 5.0,
            }
        ),
        encoding="utf-8",
    )
    (settlement_dir / "account_reconciliation_report.json").write_text(
        '{"error_count":0,"warning_count":0,"nav_difference":0.0,"issues":[]}',
        encoding="utf-8",
    )
    (settlement_dir / "fee_tax_report.json").write_text(
        '{"fee_tax_total":5.0,"commission":3.0,"stamp_duty":1.0,"transfer_fee":1.0,"total_fee_tax":5.0}',
        encoding="utf-8",
    )
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    (release_dir / "artifact_validation_report.json").write_text(
        '{"error_count":0,"warning_count":1,"legacy_artifact_count":1,"unknown_artifact_count":0,"results":[]}',
        encoding="utf-8",
    )
    (release_dir / "release_gate_report.json").write_text(
        '{"status":"passed","error_count":0,"warning_count":0,"checks":[{"name":"git_clean_check","status":"passed"}]}',
        encoding="utf-8",
    )
    (release_dir / "release_manifest.json").write_text(
        '{"release_name":"unit","build_artifacts":[{"path":"dist/auto_alpha.whl"}]}',
        encoding="utf-8",
    )
    return data_dir, tmp_path / "store", tmp_path / "account", orders_dir, broker_dir, release_dir


def test_monitoring_report_cli_writes_alerts(tmp_path, capsys):
    data_dir, store_dir, account_dir, orders_dir, broker_dir, release_dir = _prepare_monitoring_artifacts(tmp_path)
    registry_dir = tmp_path / "model_registry"
    registry = LocalModelRegistry(registry_dir)
    model = registry.register_factor_record(LocalFactorStore(store_dir).load_factors()[0])
    active, _deployment = registry.activate(model.model_version_id, approval_id="model_approval")
    write_model_registry_report(registry)
    lifecycle_dir = tmp_path / "model_lifecycle"
    lifecycle_dir.mkdir()
    (lifecycle_dir / "factor_lifecycle_report.json").write_text(
        json.dumps(
            {
                "evaluation": {
                    "decision": {"recommended_action": "keep_active"},
                    "checks": [{"name": "recent_coverage", "severity": "info", "passed": True}],
                }
            }
        ),
        encoding="utf-8",
    )
    exit_code = run_monitor.main(
        [
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(store_dir),
            "--paper-account-dir",
            str(account_dir),
            "--orders-dir",
            str(orders_dir),
            "--output-dir",
            str(tmp_path / "monitoring"),
            "--as-of-date",
            "20240104",
            "--broker-store-dir",
            str(broker_dir),
            "--broker-batch-id",
            "batch_monitor",
            "--artifact-validation-report-path",
            str(release_dir / "artifact_validation_report.json"),
            "--release-gate-report-path",
            str(release_dir / "release_gate_report.json"),
            "--release-manifest-path",
            str(release_dir / "release_manifest.json"),
            "--settlement-report-path",
            str(tmp_path / "settlement" / "settlement_report.json"),
            "--account-reconciliation-report-path",
            str(tmp_path / "settlement" / "account_reconciliation_report.json"),
            "--fee-tax-report-path",
            str(tmp_path / "settlement" / "fee_tax_report.json"),
            "--model-registry-dir",
            str(registry_dir),
            "--model-version-id",
            active.model_version_id,
            "--factor-lifecycle-report-path",
            str(lifecycle_dir / "factor_lifecycle_report.json"),
            "--model-lineage-graph-path",
            str(registry_dir / "model_lineage_graph.json"),
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["checks"]["data_freshness"]["ok"] is True
    assert payload["checks"]["style_exposure_drift"]["exists"] is True
    assert payload["checks"]["active_risk_drift"]["exists"] is True
    assert payload["checks"]["factor_risk_concentration"]["exists"] is True
    assert payload["checks"]["attribution_anomaly"]["exists"] is True
    assert payload["checks"]["capacity_warnings"]["capacity_warning_count"] == 1
    assert payload["checks"]["execution_quality"]["execution_fill_rate"] == 0.25
    assert payload["checks"]["unfilled_orders"]["unfilled_order_value"] == 750.0
    assert payload["checks"]["impact_cost_spike"]["impact_cost_ratio"] == 0.01
    assert payload["checks"]["broker_reconciliation"]["exists"] is True
    assert payload["checks"]["open_broker_orders"]["orders"] == 1
    assert payload["checks"]["broker_idempotency"]["exists"] is True
    assert payload["checks"]["artifact_schema_validation"]["artifact_schema_warning_count"] == 1
    assert payload["checks"]["release_gate"]["release_gate_status"] == "passed"
    assert payload["checks"]["package_build_artifacts"]["package_build_status"] == "passed"
    assert payload["checks"]["settlement_report"]["pending_settlement_event_count"] == 1
    assert payload["checks"]["account_reconciliation"]["error_count"] == 0
    assert payload["checks"]["settlement_fee_tax"]["fee_tax_total"] == 5.0
    assert payload["checks"]["model_registry"]["model_versions"] == 1
    assert payload["checks"]["active_model_status"]["active_model_version_id"] == active.model_version_id
    assert payload["checks"]["model_lifecycle_health"]["recommended_action"] == "keep_active"
    assert payload["checks"]["model_lineage_completeness"]["model_lineage_node_count"] >= 1
    assert any(alert["check"] == "fill_quality" for alert in payload["alerts"])
    assert (tmp_path / "monitoring" / "monitoring_report.json").exists()
    assert (tmp_path / "monitoring" / "monitoring_report.md").exists()
    assert (tmp_path / "monitoring" / "alerts.jsonl").exists()


def test_monitoring_quality_error_produces_error_alert(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "quality_report.json").write_text(
        '{"total_errors":1,"total_warnings":0}',
        encoding="utf-8",
    )
    payload, alerts = check_quality_report(data_dir)

    assert payload["ok"] is False
    assert alerts[0].severity == "error"


def test_monitoring_reads_data_source_smoke_artifacts(tmp_path, capsys):
    data_dir, store_dir, account_dir, orders_dir, broker_dir, _release_dir = _prepare_monitoring_artifacts(tmp_path)
    smoke_dir = tmp_path / "smoke"
    smoke_dir.mkdir()
    smoke_report = {
        "provider": "tushare",
        "status": "WARNING",
        "diagnostic_counts": {"missing_fields": 1},
        "provider_probe": [
            {"status": "WARNING", "diagnostic_code": "missing_fields"},
            {"status": "OK", "diagnostic_code": None},
        ],
    }
    field_coverage = {
        "datasets": [
            {"dataset": "daily_bars", "records": 3, "missing_fields": ["amount"], "duplicate_key_count": 0},
            {"dataset": "daily_basic", "records": 0, "missing_fields": [], "duplicate_key_count": 0},
        ]
    }
    audit_summary = {"total_requests": 4, "failed_requests": 0, "cache_hit_rate": 0.5, "errors_by_category": {}}
    baseline = {"compared": True, "has_differences": True, "difference_count": 1, "metrics": {"max_record_count_diff": 1}}
    (smoke_dir / "data_source_smoke_report.json").write_text(json.dumps(smoke_report), encoding="utf-8")
    (smoke_dir / "field_coverage.json").write_text(json.dumps(field_coverage), encoding="utf-8")
    (smoke_dir / "audit_summary.json").write_text(json.dumps(audit_summary), encoding="utf-8")
    (smoke_dir / "baseline_compare_summary.json").write_text(json.dumps(baseline), encoding="utf-8")

    exit_code = run_monitor.main(
        [
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(store_dir),
            "--paper-account-dir",
            str(account_dir),
            "--orders-dir",
            str(orders_dir),
            "--output-dir",
            str(tmp_path / "monitoring_ds"),
            "--as-of-date",
            "20240104",
            "--broker-store-dir",
            str(broker_dir),
            "--broker-batch-id",
            "batch_monitor",
            "--data-source-smoke-report-path",
            str(smoke_dir / "data_source_smoke_report.json"),
            "--field-coverage-path",
            str(smoke_dir / "field_coverage.json"),
            "--audit-summary-path",
            str(smoke_dir / "audit_summary.json"),
            "--baseline-compare-path",
            str(smoke_dir / "baseline_compare_summary.json"),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["checks"]["data_source_smoke"]["provider_status"] == "WARNING"
    assert payload["checks"]["provider_readiness"]["api_permission_issue_count"] == 0
    assert payload["checks"]["field_coverage"]["missing_field_count"] == 1
    assert payload["checks"]["field_coverage"]["empty_dataset_count"] == 1
    assert payload["checks"]["data_source_audit"]["data_source_cache_hit_rate"] == 0.5
    assert payload["checks"]["baseline_compare"]["baseline_diff_count"] == 1

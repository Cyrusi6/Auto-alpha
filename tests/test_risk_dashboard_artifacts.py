from dashboard.config import DashboardConfig
from dashboard.data_service import AshareDashboardService


def test_dashboard_service_reads_risk_artifacts(tmp_path):
    backtest_risk = tmp_path / "backtest" / "risk"
    backtest_risk.mkdir(parents=True)
    (backtest_risk / "risk_model_report.json").write_text(
        '{"metrics":{"tracking_error":0.1},"violations":[],"style_exposures":{"size":0.1}}',
        encoding="utf-8",
    )
    (backtest_risk / "risk_model_report.md").write_text("# Risk Model Report", encoding="utf-8")
    (tmp_path / "backtest" / "risk_exposures.jsonl").write_text(
        '{"trade_date":"20240104","max_active_style_exposure_abs":0.2}\n',
        encoding="utf-8",
    )
    (tmp_path / "backtest" / "risk_decomposition.jsonl").write_text(
        '{"trade_date":"20240104","active":{"total_risk":0.1}}\n',
        encoding="utf-8",
    )
    (tmp_path / "backtest" / "return_attribution.jsonl").write_text(
        '{"trade_date":"20240104","total_active_return":0.01}\n',
        encoding="utf-8",
    )
    orders_plan = tmp_path / "orders" / "plan"
    orders_plan.mkdir(parents=True)
    (orders_plan / "capacity_report.json").write_text(
        '{"portfolio":{"capacity_warning_count":1,"estimated_impact_cost":10.0}}',
        encoding="utf-8",
    )
    (orders_plan / "execution_plan.json").write_text(
        '{"schedule":{"buckets":["open"],"child_orders":[]},"quality":{"execution_fill_rate":0.5}}',
        encoding="utf-8",
    )
    (orders_plan / "parent_orders.jsonl").write_text('{"parent_order_id":"parent_1"}\n', encoding="utf-8")
    (orders_plan / "child_orders.jsonl").write_text('{"child_order_id":"child_1"}\n', encoding="utf-8")
    (orders_plan / "child_fills.jsonl").write_text('{"child_order_id":"child_1","status":"FILLED"}\n', encoding="utf-8")
    (orders_plan / "execution_quality.json").write_text('{"execution_fill_rate":0.5}\n', encoding="utf-8")
    broker_dir = tmp_path / "orders" / "broker"
    broker_dir.mkdir()
    (broker_dir / "broker_report.json").write_text(
        '{"batch_id":"batch_1","summary":{"submitted_orders":1,"filled_orders":1}}',
        encoding="utf-8",
    )
    (broker_dir / "broker_reconciliation.json").write_text(
        '{"batch_id":"batch_1","orphan_fills":0,"issues":[]}',
        encoding="utf-8",
    )
    (broker_dir / "broker_orders.jsonl").write_text('{"broker_order_id":"bo_1","status":"FILLED"}\n', encoding="utf-8")
    (broker_dir / "broker_events.jsonl").write_text('{"event_id":"be_1","status":"FILLED"}\n', encoding="utf-8")
    (broker_dir / "broker_fills.jsonl").write_text('{"broker_fill_id":"bf_1","status":"FILLED"}\n', encoding="utf-8")
    (tmp_path / "orders" / "outbox").mkdir()
    (tmp_path / "orders" / "outbox" / "broker_instruction_manifest.json").write_text(
        '{"batch_id":"batch_1","schema_name":"generic_broker_csv","orders":1}',
        encoding="utf-8",
    )
    (tmp_path / "backtest" / "optimization_result.json").write_text(
        '{"factor_id":"factor_x","weights":{"000001.SZ":0.1}}',
        encoding="utf-8",
    )
    smoke_dir = tmp_path / "data_source_smoke"
    smoke_dir.mkdir()
    (smoke_dir / "data_source_smoke_report.json").write_text(
        '{"provider":"sample","status":"OK","diagnostic_counts":{"OK":1},"datasets":[]}',
        encoding="utf-8",
    )
    (smoke_dir / "provider_probe.json").write_text('{"probes":[{"status":"OK"}]}', encoding="utf-8")
    (smoke_dir / "field_coverage.json").write_text('{"datasets":[{"dataset":"daily_bars","records":3}]}', encoding="utf-8")
    (smoke_dir / "audit_summary.json").write_text('{"total_requests":1,"cache_hit_rate":0.0}', encoding="utf-8")
    (smoke_dir / "incremental_recovery_report.json").write_text('{"ok":true}', encoding="utf-8")
    (smoke_dir / "baseline_compare_summary.json").write_text('{"compared":true,"difference_count":0}', encoding="utf-8")
    (smoke_dir / "dataset_contracts.json").write_text('{"datasets":[{"dataset":"daily_bars"}]}', encoding="utf-8")
    service = AshareDashboardService(
        DashboardConfig(
            data_dir=tmp_path / "data",
            factor_store_dir=tmp_path / "store",
            report_dir=tmp_path / "reports",
            backtest_dir=tmp_path / "backtest",
            orders_dir=tmp_path / "orders",
            data_source_smoke_dir=smoke_dir,
        )
    )

    assert service.load_risk_report_json()["metrics"]["tracking_error"] == 0.1
    assert "Risk Model Report" in service.load_risk_report_markdown()
    assert service.load_optimization_result()["factor_id"] == "factor_x"
    assert not service.load_risk_exposures().empty
    assert not service.load_risk_decomposition().empty
    assert not service.load_return_attribution().empty
    assert service.load_capacity_report()["portfolio"]["capacity_warning_count"] == 1
    assert service.load_execution_quality()["execution_fill_rate"] == 0.5
    assert not service.load_parent_orders().empty
    assert not service.load_child_orders().empty
    assert not service.load_child_fills().empty
    assert service.load_broker_report()["summary"]["submitted_orders"] == 1
    assert service.load_broker_reconciliation()["batch_id"] == "batch_1"
    assert not service.load_broker_orders().empty
    assert not service.load_broker_events().empty
    assert not service.load_broker_fills().empty
    assert service.load_broker_instruction_manifest()["orders"] == 1
    assert service.load_data_source_smoke_report()["status"] == "OK"
    assert len(service.load_provider_probe()["probes"]) == 1
    assert service.load_field_coverage_report()["datasets"][0]["dataset"] == "daily_bars"
    assert service.load_data_source_audit_summary()["total_requests"] == 1
    assert service.load_incremental_recovery_report()["ok"] is True
    assert service.load_baseline_compare_summary()["difference_count"] == 0
    assert service.load_dataset_contracts()["datasets"][0]["dataset"] == "daily_bars"

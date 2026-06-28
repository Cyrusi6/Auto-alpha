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
    schema_dir = tmp_path / "schema_validation"
    schema_dir.mkdir()
    (schema_dir / "artifact_validation_report.json").write_text(
        '{"error_count":0,"warning_count":1,"legacy_artifact_count":1,"unknown_artifact_count":0,"results":[]}',
        encoding="utf-8",
    )
    (schema_dir / "artifact_schema_manifest.json").write_text('{"entries":[{"artifact_type":"monitoring_report"}]}', encoding="utf-8")
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    (release_dir / "release_gate_report.json").write_text('{"status":"passed","error_count":0,"warning_count":0,"checks":[]}', encoding="utf-8")
    (release_dir / "release_manifest.json").write_text(
        '{"release_name":"r1","build_artifacts":[{"path":"dist/a.whl"}]}',
        encoding="utf-8",
    )
    (release_dir / "dependency_inventory.json").write_text('{"files":[{"path":"pyproject.toml"}]}', encoding="utf-8")
    (release_dir / "module_inventory.json").write_text('{"modules":[{"module":"data_pipeline"}]}', encoding="utf-8")
    (release_dir / "cli_inventory.json").write_text('{"entries":[{"module":"data_pipeline.run_pipeline"}]}', encoding="utf-8")
    ci_dir = tmp_path / "ci"
    ci_dir.mkdir()
    (ci_dir / "ci_report.json").write_text('{"status":"passed","commands":[{"name":"import_smoke"}]}', encoding="utf-8")
    corpus_dir = tmp_path / "formula_corpus"
    corpus_dir.mkdir()
    (corpus_dir / "formula_corpus_stats.json").write_text('{"total_records":2,"valid_records":2}', encoding="utf-8")
    (corpus_dir / "formula_corpus.jsonl").write_text('{"formula_hash":"h1","formula_tokens":[0],"valid":true}\n', encoding="utf-8")
    batch_eval_dir = tmp_path / "formula_batch_eval"
    batch_eval_dir.mkdir()
    (batch_eval_dir / "formula_batch_eval_result.json").write_text(
        '{"batch_id":"b1","summary":{"total":1},"benchmark":{"device":"cpu"}}',
        encoding="utf-8",
    )
    (batch_eval_dir / "formula_eval_results.jsonl").write_text('{"status":"approved","score":1.0}\n', encoding="utf-8")
    pretrain_dir = tmp_path / "pretrain"
    pretrain_dir.mkdir()
    (pretrain_dir / "alphagpt_pretrain_result.json").write_text(
        '{"status":"success","summary":{"latest_checkpoint_path":"latest.pt"}}',
        encoding="utf-8",
    )
    (pretrain_dir / "alphagpt_pretrain_history.jsonl").write_text('{"epoch":0,"loss":1.0}\n', encoding="utf-8")
    service = AshareDashboardService(
        DashboardConfig(
            data_dir=tmp_path / "data",
            factor_store_dir=tmp_path / "store",
            report_dir=tmp_path / "reports",
            backtest_dir=tmp_path / "backtest",
            orders_dir=tmp_path / "orders",
            data_source_smoke_dir=smoke_dir,
            schema_validation_dir=schema_dir,
            release_dir=release_dir,
            ci_dir=ci_dir,
            formula_corpus_dir=corpus_dir,
            formula_batch_eval_dir=batch_eval_dir,
            pretrain_dir=pretrain_dir,
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
    assert service.load_artifact_validation_report()["warning_count"] == 1
    assert len(service.load_artifact_schema_manifest()["entries"]) == 1
    assert service.load_release_gate_report()["status"] == "passed"
    assert service.load_release_manifest()["release_name"] == "r1"
    assert service.load_dependency_inventory()["files"][0]["path"] == "pyproject.toml"
    assert service.load_module_inventory()["modules"][0]["module"] == "data_pipeline"
    assert service.load_cli_inventory()["entries"][0]["module"] == "data_pipeline.run_pipeline"
    assert service.load_ci_report()["status"] == "passed"
    assert service.load_formula_corpus_stats()["valid_records"] == 2
    assert not service.load_formula_corpus().empty
    assert service.load_formula_batch_eval_result()["batch_id"] == "b1"
    assert not service.load_formula_eval_results().empty
    assert service.load_alphagpt_pretrain_result()["status"] == "success"
    assert not service.load_alphagpt_pretrain_history().empty

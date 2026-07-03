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
    settlement_dir = tmp_path / "settlement"
    settlement_dir.mkdir()
    (settlement_dir / "settlement_report.json").write_text(
        '{"settlement_aware":true,"settlement_profile":"cn_ashare_paper_default","pending_settlement_event_count":1,"fee_tax_total":5.0}',
        encoding="utf-8",
    )
    (settlement_dir / "settlement_events.jsonl").write_text(
        '{"settlement_event_id":"se_1","status":"pending","event_type":"trade_buy_cash"}\n',
        encoding="utf-8",
    )
    (settlement_dir / "cash_buckets.jsonl").write_text('{"trade_date":"20240104","available_cash":100.0}\n', encoding="utf-8")
    (settlement_dir / "position_lots.jsonl").write_text('{"lot_id":"lot_1","ts_code":"000001.SZ","shares_remaining":100}\n', encoding="utf-8")
    (settlement_dir / "position_availability.jsonl").write_text(
        '{"ts_code":"000001.SZ","available_shares":100,"total_shares":100}\n',
        encoding="utf-8",
    )
    (settlement_dir / "realized_pnl.jsonl").write_text('{"ts_code":"000001.SZ","realized_pnl":1.0}\n', encoding="utf-8")
    (settlement_dir / "account_nav.jsonl").write_text('{"trade_date":"20240104","equity":1000.0}\n', encoding="utf-8")
    (settlement_dir / "account_reconciliation_report.json").write_text(
        '{"error_count":0,"nav_difference":0.0,"issues":[]}',
        encoding="utf-8",
    )
    (settlement_dir / "account_performance_report.json").write_text('{"total_return":0.0}', encoding="utf-8")
    (settlement_dir / "fee_tax_report.json").write_text('{"fee_tax_total":5.0,"total_fee_tax":5.0}', encoding="utf-8")
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
    model_registry_dir = tmp_path / "model_registry"
    model_registry_dir.mkdir()
    (model_registry_dir / "model_registry_report.json").write_text(
        '{"status_counts":{"active":1},"active_model_id":"model_1","active_models":[{"model_version_id":"model_1"}]}',
        encoding="utf-8",
    )
    (model_registry_dir / "model_registry_manifest.json").write_text(
        '{"model_count":1,"model_versions":1,"deployments":1,"events":1}',
        encoding="utf-8",
    )
    (model_registry_dir / "model_versions.jsonl").write_text('{"model_version_id":"model_1","factor_id":"factor_x","lifecycle_status":"active"}\n', encoding="utf-8")
    (model_registry_dir / "model_deployments.jsonl").write_text('{"deployment_id":"deploy_1","model_version_id":"model_1","status":"active"}\n', encoding="utf-8")
    (model_registry_dir / "lifecycle_events.jsonl").write_text('{"event_id":"event_1","model_version_id":"model_1","to_status":"active"}\n', encoding="utf-8")
    (model_registry_dir / "model_lineage_graph.json").write_text('{"nodes":[{"id":"model_1"}],"edges":[]}', encoding="utf-8")
    model_lifecycle_dir = tmp_path / "model_lifecycle"
    model_lifecycle_dir.mkdir()
    (model_lifecycle_dir / "factor_lifecycle_report.json").write_text(
        '{"decision":{"recommended_action":"approve_for_activation","status":"ok"},"metrics":{}}',
        encoding="utf-8",
    )
    (model_lifecycle_dir / "factor_health_checks.jsonl").write_text('{"name":"recent_coverage","passed":true}\n', encoding="utf-8")
    (model_lifecycle_dir / "lifecycle_decisions.jsonl").write_text('{"factor_id":"factor_x","recommended_action":"approve_for_activation"}\n', encoding="utf-8")
    (model_lifecycle_dir / "model_review_package.json").write_text('{"model_version_id":"model_1","factor_id":"factor_x"}', encoding="utf-8")
    validation_campaign_dir = tmp_path / "validation_campaign_store"
    validation_campaign_dir.mkdir()
    (validation_campaign_dir / "validation_campaign_registry.json").write_text(
        '{"status":"ready","validation_campaign_count":1,"candidate_count":2,"shard_count":2,"result_count":2,"leaderboard_count":1,"certification_queue_count":1}',
        encoding="utf-8",
    )
    (validation_campaign_dir / "validation_campaign_store_report.json").write_text(
        '{"status":"ready","candidate_count":2,"shard_count":2,"result_count":2,"leaderboard_count":1,"certification_queue_count":1}',
        encoding="utf-8",
    )
    (validation_campaign_dir / "validation_candidates.jsonl").write_text('{"validation_candidate_id":"vc1","factor_id":"factor_x","formula_hash":"h"}\n', encoding="utf-8")
    (validation_campaign_dir / "validation_shards.jsonl").write_text('{"shard_id":"s1","validation_campaign_id":"vcamp","shard_index":0,"status":"success"}\n', encoding="utf-8")
    (validation_campaign_dir / "validation_candidate_results.jsonl").write_text('{"validation_candidate_id":"vc1","factor_id":"factor_x","validation_status":"passed","validation_score":1.0}\n', encoding="utf-8")
    (validation_campaign_dir / "validation_leaderboard.jsonl").write_text('{"rank":1,"validation_candidate_id":"vc1","factor_id":"factor_x","validation_score":1.0}\n', encoding="utf-8")
    (validation_campaign_dir / "factor_certification_queue.jsonl").write_text('{"queue_id":"q1","validation_candidate_id":"vc1","factor_id":"factor_x","priority":1}\n', encoding="utf-8")
    (validation_campaign_dir / "validation_candidate_dedup_report.json").write_text('{"validation_campaign_id":"vcamp","candidate_count":2,"duplicate_count":0}', encoding="utf-8")
    (validation_campaign_dir / "validation_large_campaign_plan.json").write_text('{"experiment_id":"e","workflow":"real_data_validation_campaign_large_plan","status":"blocked","blocked":true,"resource_plan":{},"compute_jobs":[]}', encoding="utf-8")
    statement_dir = tmp_path / "statement_import_mismatch"
    statement_dir.mkdir()
    (statement_dir / "broker_statement_manifest.json").write_text(
        '{"statement_id":"stmt_1","account_id":"paper_ashare","schema_name":"generic_broker_statement","record_counts":{"cash":1}}',
        encoding="utf-8",
    )
    (statement_dir / "broker_statement_import_report.json").write_text(
        '{"statement_id":"stmt_1","status":"ok","manifest":{"record_counts":{"cash":1}},"validation":{"error_count":0,"warning_count":0}}',
        encoding="utf-8",
    )
    (statement_dir / "broker_statement_parse_issues.jsonl").write_text('{"severity":"warning","code":"sample","message":"sample"}\n', encoding="utf-8")
    (statement_dir / "broker_statement_validation_report.json").write_text('{"statement_id":"stmt_1","issues":[]}', encoding="utf-8")
    (statement_dir / "normalized_external_cash.jsonl").write_text('{"account_id":"paper_ashare","cash_balance":100.0}\n', encoding="utf-8")
    reconciliation_dir = tmp_path / "eod_reconciliation_mismatch"
    reconciliation_dir.mkdir()
    (reconciliation_dir / "eod_reconciliation_report.json").write_text(
        '{"statement_id":"stmt_1","status":"error","summary":{"break_count":1,"cash_difference":100.0}}',
        encoding="utf-8",
    )
    (reconciliation_dir / "reconciliation_breaks.jsonl").write_text('{"break_id":"brk_1","break_type":"cash_balance_mismatch","severity":"error"}\n', encoding="utf-8")
    (reconciliation_dir / "external_account_mirror.json").write_text('{"statement_id":"stmt_1","account_id":"paper_ashare"}', encoding="utf-8")
    (reconciliation_dir / "external_cash_mirror.jsonl").write_text('{"account_id":"paper_ashare","cash_balance":100.0}\n', encoding="utf-8")
    (reconciliation_dir / "adjustment_proposals.jsonl").write_text('{"adjustment_id":"adj_1","adjustment_type":"cash_manual_adjustment"}\n', encoding="utf-8")
    (reconciliation_dir / "adjustment_proposal_batch.json").write_text('{"adjustment_batch_id":"batch_1","proposals":[]}', encoding="utf-8")
    (reconciliation_dir / "adjustment_application_result.json").write_text('{"approval_id":"appr_1","applied_count":1}', encoding="utf-8")
    (tmp_path / "account").mkdir(exist_ok=True)
    (tmp_path / "account" / "adjustment_ledger.jsonl").write_text('{"adjustment_id":"adj_1","approval_id":"appr_1"}\n', encoding="utf-8")
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
            model_registry_dir=model_registry_dir,
            model_lifecycle_dir=model_lifecycle_dir,
            validation_campaign_store_dir=validation_campaign_dir,
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
    assert service.load_validation_campaign_registry()["candidate_count"] == 2
    assert service.load_validation_campaign_store_report()["result_count"] == 2
    assert not service.load_validation_candidates().empty
    assert not service.load_validation_shards_campaign().empty
    assert not service.load_validation_candidate_results().empty
    assert not service.load_validation_leaderboard().empty
    assert not service.load_factor_certification_queue().empty
    assert service.load_validation_candidate_dedup_report()["duplicate_count"] == 0
    assert service.load_validation_large_campaign_plan()["status"] == "blocked"
    assert service.load_settlement_report()["settlement_aware"] is True
    assert not service.load_settlement_events().empty
    assert not service.load_cash_buckets().empty
    assert not service.load_position_lots().empty
    assert not service.load_position_availability().empty
    assert not service.load_realized_pnl().empty
    assert not service.load_account_nav().empty
    assert service.load_account_reconciliation_report()["error_count"] == 0
    assert service.load_account_performance_report()["total_return"] == 0.0
    assert service.load_fee_tax_report()["fee_tax_total"] == 5.0
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
    assert service.load_broker_statement_manifest()["statement_id"] == "stmt_1"
    assert service.load_broker_statement_import_report()["status"] == "ok"
    assert not service.load_broker_statement_parse_issues().empty
    assert service.load_broker_statement_validation_report()["statement_id"] == "stmt_1"
    assert not service.load_normalized_external("cash").empty
    assert service.load_eod_reconciliation_report()["status"] == "error"
    assert not service.load_reconciliation_breaks().empty
    assert service.load_external_account_mirror()["statement_id"] == "stmt_1"
    assert not service.load_external_mirror_table("cash").empty
    assert not service.load_adjustment_proposals().empty
    assert service.load_adjustment_proposal_batch()["adjustment_batch_id"] == "batch_1"
    assert service.load_adjustment_application_result()["approval_id"] == "appr_1"
    assert not service.load_adjustment_ledger().empty
    assert service.load_formula_batch_eval_result()["batch_id"] == "b1"
    assert not service.load_formula_eval_results().empty
    assert service.load_alphagpt_pretrain_result()["status"] == "success"
    assert not service.load_alphagpt_pretrain_history().empty
    assert service.load_model_registry_report()["active_model_id"] == "model_1"
    assert service.load_model_registry_manifest()["model_versions"] == 1
    assert not service.load_model_versions().empty
    assert not service.load_model_deployments().empty
    assert not service.load_model_lifecycle_events().empty
    assert len(service.load_model_lineage_graph()["nodes"]) == 1
    assert service.load_factor_lifecycle_report()["decision"]["recommended_action"] == "approve_for_activation"
    assert not service.load_factor_health_checks().empty
    assert not service.load_lifecycle_decisions().empty
    assert service.load_model_review_package()["model_version_id"] == "model_1"

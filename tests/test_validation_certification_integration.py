import json

from artifact_schema.validator import validate_artifact
from dashboard.config import DashboardConfig
from dashboard.data_service import AshareDashboardService
from monitoring import run_monitor
from research_suite import ResearchSuiteConfig, ResearchSuiteRunner


def test_research_suite_runs_validation_and_certification_before_promotion(tmp_path):
    config = ResearchSuiteConfig(
        suite_name="validation_suite",
        provider="sample",
        data_dir=str(tmp_path / "data"),
        universe_name="csi300_sample",
        index_code="000300.SH",
        factor_store_dir=str(tmp_path / "store"),
        report_dir=str(tmp_path / "reports"),
        output_dir=str(tmp_path / "suite"),
        backtest_dir=str(tmp_path / "backtest"),
        orders_dir=str(tmp_path / "orders"),
        as_of_date="20240104",
        factor_transform="winsorize_zscore",
        search_seed=42,
        search_population_size=4,
        search_generations=1,
        search_max_candidates=2,
        top_k=2,
        composite_method="rank_average",
        promote_latest_composite=True,
        walk_forward_train_size=1,
        walk_forward_test_size=1,
        walk_forward_step_size=1,
        skip_orders=True,
        run_validation_lab=True,
        validation_lab_dir=str(tmp_path / "validation"),
        run_multiple_testing=True,
        run_overfit_risk=True,
        run_placebo=True,
        placebo_trials=3,
        run_regime_validation=True,
        run_sensitivity_validation=True,
        run_stress_backtest_validation=True,
        run_factor_certification=True,
        factor_certification_dir=str(tmp_path / "cert"),
        certification_policy_profile="sample_lenient_certification",
        require_certification=True,
    )

    result = ResearchSuiteRunner(config).run()
    stage_statuses = {stage.name: stage.status for stage in result.stages}

    assert result.status == "success"
    assert stage_statuses["validation_lab"] == "success"
    assert stage_statuses["factor_certification"] == "success"
    assert stage_statuses["promotion"] == "success"
    assert result.summary["validation_lab_enabled"] is True
    assert result.summary["certification_status"] in {"certified", "conditional"}
    assert (tmp_path / "validation" / "validation_lab_report.json").exists()
    assert (tmp_path / "cert" / "factor_certification_decision.json").exists()
    assert (tmp_path / "suite" / "artifact_catalog.json").exists()


def test_validation_and_certification_artifacts_are_schema_registered(tmp_path):
    validation_report = tmp_path / "validation_lab_report.json"
    validation_report.write_text(
        json.dumps(
            {
                "artifact_type": "validation_lab_report",
                "schema_version": "1.0",
                "producer": "test",
                "created_at": "2026-06-28T00:00:00Z",
                "target": {"factor_id": "factor_x"},
                "split_method": "simple_walk_forward",
                "validation_summary": {},
                "multiple_testing_summary": {},
                "overfit_risk_summary": {},
                "placebo_summary": {},
                "regime_summary": {},
                "sensitivity_summary": {},
                "stress_backtest_summary": {},
                "status": "passed",
            }
        ),
        encoding="utf-8",
    )
    decision = tmp_path / "factor_certification_decision.json"
    decision.write_text(
        json.dumps(
            {
                "artifact_type": "factor_certification_decision",
                "schema_version": "1.0",
                "producer": "test",
                "factor_id": "factor_x",
                "status": "certified",
                "passed": True,
                "policy_id": "policy_x",
                "created_at": "2026-06-28T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    assert validate_artifact(validation_report, strict=True).valid is True
    assert validate_artifact(decision, strict=True).valid is True


def test_monitoring_and_dashboard_read_validation_certification_artifacts(tmp_path, capsys):
    validation_dir = tmp_path / "validation"
    cert_dir = tmp_path / "cert"
    validation_dir.mkdir()
    cert_dir.mkdir()
    (validation_dir / "validation_lab_report.json").write_text(
        '{"status":"passed","validation_summary":{"blocker_count":0,"warning_count":1,"out_of_sample_score":0.1,"window_pass_ratio":1.0},"overfit_risk_summary":{"pbo_estimate":0.2,"deflated_ic_like_score":0.1},"placebo_summary":{"candidate_vs_placebo_percentile":0.8,"null_exceedance_ratio":0.2},"regime_summary":{"regime_pass_ratio":1.0},"sensitivity_summary":{"sensitivity_pass_ratio":1.0},"stress_backtest_summary":{"stress_backtest_pass_ratio":1.0}}',
        encoding="utf-8",
    )
    (validation_dir / "factor_validation_summary.json").write_text(
        '{"factor_id":"factor_x","out_of_sample_score":0.1,"window_pass_ratio":1.0,"blocker_count":0,"warning_count":1}',
        encoding="utf-8",
    )
    (validation_dir / "multiple_testing_report.json").write_text(
        '{"total_trials":10,"effective_trial_count":5,"selection_bias_warning":false}',
        encoding="utf-8",
    )
    (validation_dir / "overfit_risk_report.json").write_text(
        '{"pbo_estimate":0.2,"deflated_ic_like_score":0.1,"overfit_risk_level":"low"}',
        encoding="utf-8",
    )
    (validation_dir / "placebo_test_report.json").write_text(
        '{"candidate_vs_placebo_percentile":0.8,"null_exceedance_ratio":0.2}',
        encoding="utf-8",
    )
    (validation_dir / "regime_validation_report.json").write_text('{"regime_pass_ratio":1.0}', encoding="utf-8")
    (validation_dir / "sensitivity_report.json").write_text('{"sensitivity_pass_ratio":1.0}', encoding="utf-8")
    (validation_dir / "stress_backtest_report.json").write_text('{"stress_backtest_pass_ratio":1.0}', encoding="utf-8")
    (validation_dir / "validation_splits.jsonl").write_text('{"split_id":"s1"}\n', encoding="utf-8")
    (validation_dir / "factor_validation_results.jsonl").write_text('{"split_id":"s1"}\n', encoding="utf-8")
    (validation_dir / "placebo_trials.jsonl").write_text('{"trial":0,"mode":"random_label_test","score":0.0}\n', encoding="utf-8")
    (validation_dir / "regime_results.jsonl").write_text('{"regime_name":"up"}\n', encoding="utf-8")
    (validation_dir / "sensitivity_results.jsonl").write_text('{"scenario_id":"base"}\n', encoding="utf-8")
    (validation_dir / "stress_backtest_results.jsonl").write_text('{"scenario_id":"base"}\n', encoding="utf-8")
    (validation_dir / "validation_issues.jsonl").write_text('{"severity":"warning","code":"small_sample"}\n', encoding="utf-8")
    (cert_dir / "factor_certification_decision.json").write_text(
        '{"factor_id":"factor_x","status":"certified","passed":true,"required_remediation":[],"checks":{"blocker_count":0}}',
        encoding="utf-8",
    )
    (cert_dir / "factor_certification_scorecard.json").write_text(
        '{"factor_id":"factor_x","summary":{"blocker_count":0},"checks":[]}',
        encoding="utf-8",
    )
    (cert_dir / "factor_certification_policy.json").write_text('{"profile_name":"sample_lenient_certification"}', encoding="utf-8")
    (cert_dir / "factor_certification_package.json").write_text('{"factor_id":"factor_x"}', encoding="utf-8")
    (cert_dir / "factor_certification_checks.jsonl").write_text('{"name":"validation_lab_check","status":"passed"}\n', encoding="utf-8")

    exit_code = run_monitor.main(
        [
            "--data-dir",
            str(tmp_path / "missing_data"),
            "--factor-store-dir",
            str(tmp_path / "missing_store"),
            "--paper-account-dir",
            str(tmp_path / "missing_account"),
            "--orders-dir",
            str(tmp_path / "missing_orders"),
            "--output-dir",
            str(tmp_path / "monitoring"),
            "--as-of-date",
            "20240104",
            "--validation-lab-report-path",
            str(validation_dir / "validation_lab_report.json"),
            "--factor-validation-summary-path",
            str(validation_dir / "factor_validation_summary.json"),
            "--multiple-testing-report-path",
            str(validation_dir / "multiple_testing_report.json"),
            "--overfit-risk-report-path",
            str(validation_dir / "overfit_risk_report.json"),
            "--placebo-test-report-path",
            str(validation_dir / "placebo_test_report.json"),
            "--regime-validation-report-path",
            str(validation_dir / "regime_validation_report.json"),
            "--sensitivity-report-path",
            str(validation_dir / "sensitivity_report.json"),
            "--stress-backtest-report-path",
            str(validation_dir / "stress_backtest_report.json"),
            "--factor-certification-decision-path",
            str(cert_dir / "factor_certification_decision.json"),
            "--factor-certification-scorecard-path",
            str(cert_dir / "factor_certification_scorecard.json"),
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    service = AshareDashboardService(
        DashboardConfig(
            data_dir=tmp_path / "missing_data",
            factor_store_dir=tmp_path / "missing_store",
            report_dir=tmp_path / "missing_reports",
            backtest_dir=tmp_path / "missing_backtest",
            orders_dir=tmp_path / "missing_orders",
            validation_lab_dir=validation_dir,
            factor_certification_dir=cert_dir,
        )
    )

    assert exit_code in {0, 1}
    assert "paths" in payload
    assert (tmp_path / "monitoring" / "monitoring_report.json").exists()
    assert service.load_validation_lab_report()["status"] == "passed"
    assert not service.load_validation_splits().empty
    assert service.load_factor_certification_decision()["status"] == "certified"
    assert not service.load_factor_certification_checks().empty

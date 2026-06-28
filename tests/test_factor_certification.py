import json

from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from factor_certification.decision import make_certification_decision
from factor_certification.policy import policy_profile
from factor_certification.run_certify import main as certify_main
from factor_certification.scorecard import build_factor_certification_scorecard
from factor_store import FactorRecord, LocalFactorStore, stable_formula_hash
from model_core.data_loader import AShareDataLoader
from validation_lab.run_validation import main as validation_main


def _prepare_factor_and_validation(tmp_path):
    data_dir = tmp_path / "data"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True)
    loader = AShareDataLoader(data_dir=data_dir, device="cpu").load_data()
    store = LocalFactorStore(tmp_path / "store")
    formula_tokens = [0]
    formula_names = ["RET_1D"]
    factor_hash = stable_formula_hash(formula_tokens, formula_names, "ashare_features_v1", "ashare_ops_v1")
    factor_id = f"factor_{factor_hash[:16]}"
    store.save_factor(
        FactorRecord(
            factor_id=factor_id,
            formula=formula_names,
            formula_tokens=formula_tokens,
            formula_hash=factor_hash,
            feature_version="ashare_features_v1",
            operator_version="ashare_ops_v1",
            lookback_days=1,
            created_at="2026-06-28T00:00:00Z",
            status="approved",
            metrics={"score": 0.5},
            factor_type="single",
        )
    )
    store.save_factor_values(
        factor_id,
        loader.ts_codes,
        loader.trade_dates,
        [[float(stock_idx + date_idx + 1) for date_idx, _date in enumerate(loader.trade_dates)] for stock_idx, _code in enumerate(loader.ts_codes)],
    )
    validation_exit = validation_main(
        [
            "run-suite",
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(tmp_path / "store"),
            "--factor-id",
            factor_id,
            "--output-dir",
            str(tmp_path / "validation"),
            "--run-multiple-testing",
            "--run-overfit-risk",
            "--run-placebo",
            "--placebo-trials",
            "3",
            "--run-regime",
            "--run-sensitivity",
            "--run-stress-backtest",
        ]
    )
    assert validation_exit == 0
    return factor_id


def test_factor_certification_cli_certifies_lenient_sample_artifacts(tmp_path, capsys):
    factor_id = _prepare_factor_and_validation(tmp_path)
    capsys.readouterr()

    exit_code = certify_main(
        [
            "run",
            "--factor-store-dir",
            str(tmp_path / "store"),
            "--factor-id",
            factor_id,
            "--output-dir",
            str(tmp_path / "cert"),
            "--policy-profile",
            "sample_lenient_certification",
            "--validation-lab-report-path",
            str(tmp_path / "validation" / "validation_lab_report.json"),
            "--factor-validation-summary-path",
            str(tmp_path / "validation" / "factor_validation_summary.json"),
            "--multiple-testing-report-path",
            str(tmp_path / "validation" / "multiple_testing_report.json"),
            "--overfit-risk-report-path",
            str(tmp_path / "validation" / "overfit_risk_report.json"),
            "--placebo-test-report-path",
            str(tmp_path / "validation" / "placebo_test_report.json"),
            "--regime-validation-report-path",
            str(tmp_path / "validation" / "regime_validation_report.json"),
            "--sensitivity-report-path",
            str(tmp_path / "validation" / "sensitivity_report.json"),
            "--stress-backtest-report-path",
            str(tmp_path / "validation" / "stress_backtest_report.json"),
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["certification_status"] == "certified"
    assert payload["certification_passed"] is True
    assert (tmp_path / "cert" / "factor_certification_decision.json").exists()
    assert (tmp_path / "cert" / "factor_certification_checks.jsonl.schema.json").exists()


def test_factor_certification_missing_required_artifacts_needs_data(tmp_path):
    factor_id = _prepare_factor_and_validation(tmp_path)
    policy = policy_profile("research_standard")
    scorecard = build_factor_certification_scorecard(factor_id, policy, {})
    decision = make_certification_decision(scorecard, policy)

    assert decision.status == "insufficient_data"
    assert decision.passed is False
    assert "data_freeze_check" in decision.reasons


def test_factor_certification_blocker_rejects_candidate(tmp_path):
    factor_id = _prepare_factor_and_validation(tmp_path)
    leakage_report = tmp_path / "leakage_audit_report.json"
    leakage_report.write_text('{"blocker_count":1}', encoding="utf-8")
    policy = policy_profile("research_standard")

    scorecard = build_factor_certification_scorecard(
        factor_id,
        policy,
        {"leakage_audit_report_path": str(leakage_report)},
    )
    decision = make_certification_decision(scorecard, policy)

    assert decision.status == "rejected"
    assert decision.passed is False
    assert "leakage_check" in decision.reasons


def test_factor_certification_apply_status_updates_factor_store(tmp_path, capsys):
    factor_id = _prepare_factor_and_validation(tmp_path)
    capsys.readouterr()

    exit_code = certify_main(
        [
            "run",
            "--factor-store-dir",
            str(tmp_path / "store"),
            "--factor-id",
            factor_id,
            "--output-dir",
            str(tmp_path / "cert"),
            "--validation-lab-report-path",
            str(tmp_path / "validation" / "validation_lab_report.json"),
            "--factor-validation-summary-path",
            str(tmp_path / "validation" / "factor_validation_summary.json"),
            "--multiple-testing-report-path",
            str(tmp_path / "validation" / "multiple_testing_report.json"),
            "--overfit-risk-report-path",
            str(tmp_path / "validation" / "overfit_risk_report.json"),
            "--placebo-test-report-path",
            str(tmp_path / "validation" / "placebo_test_report.json"),
            "--regime-validation-report-path",
            str(tmp_path / "validation" / "regime_validation_report.json"),
            "--sensitivity-report-path",
            str(tmp_path / "validation" / "sensitivity_report.json"),
            "--stress-backtest-report-path",
            str(tmp_path / "validation" / "stress_backtest_report.json"),
            "--apply-status",
            "--pretty",
        ]
    )
    json.loads(capsys.readouterr().out)

    assert exit_code == 0
    factor = LocalFactorStore(tmp_path / "store").load_factors()[0]
    assert factor.status == "certified"
    assert factor.metadata
    assert "promotion_decision" in factor.metadata

import json
import math

import pytest
import torch

from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from factor_store import FactorRecord, LocalFactorStore, stable_formula_hash
from model_core.data_loader import AShareDataLoader
from validation_lab.metrics import evaluate_factor_splits
from validation_lab.models import StressBacktestResult
from validation_lab.multiple_testing import analyze_multiple_testing
from validation_lab.overfit import estimate_overfit_risk
from validation_lab.placebo import run_placebo_tests
from validation_lab.regime import run_regime_validation
from validation_lab.run_validation import main as validation_main
from validation_lab.sensitivity import run_sensitivity_tests
from validation_lab.splits import (
    build_cscv_splits,
    build_purged_embargo_splits,
    build_simple_walk_forward_splits,
)
from validation_lab.stress_backtest import UnsupportedStressBacktestError, run_stress_backtest_bundle


def _prepare_factor(tmp_path, status="approved"):
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
            status=status,
            metrics={"score": 0.5, "rank_ic": 0.1},
            factor_type="single",
        )
    )
    values = [
        [float(stock_idx + date_idx + 1) for date_idx, _date in enumerate(loader.trade_dates)]
        for stock_idx, _code in enumerate(loader.ts_codes)
    ]
    store.save_factor_values(factor_id, loader.ts_codes, loader.trade_dates, values)
    return data_dir, store, loader, factor_id


def test_validation_splits_are_deterministic_and_degrade_on_small_samples():
    dates = ["20240102", "20240103", "20240104", "20240105"]

    simple_a = build_simple_walk_forward_splits(dates, train_size=2, test_size=1, step_size=1)
    simple_b = build_simple_walk_forward_splits(list(reversed(dates)), train_size=2, test_size=1, step_size=1)
    assert [item.to_dict() for item in simple_a] == [item.to_dict() for item in simple_b]
    assert simple_a[0].train_dates == ["20240102", "20240103"]
    assert simple_a[0].test_dates == ["20240104"]

    purged = build_purged_embargo_splits(dates, n_splits=2, embargo_size=1)
    assert purged
    assert all(set(split.train_dates).isdisjoint(split.test_dates) for split in purged)

    cscv = build_cscv_splits(dates, n_groups=2, max_combinations=4)
    assert cscv
    assert cscv == build_cscv_splits(dates, n_groups=2, max_combinations=4)

    degraded = build_cscv_splits(["20240102"], n_groups=3, max_combinations=2)
    assert degraded[0].metadata["warning"].startswith("insufficient dates")


def test_validation_metrics_multiple_testing_and_null_diagnostics_are_serializable(tmp_path):
    _data_dir, store, loader, factor_id = _prepare_factor(tmp_path)
    factors = store.load_factor_values_matrix(factor_id, loader.ts_codes, loader.trade_dates, device="cpu")
    splits = build_simple_walk_forward_splits(loader.trade_dates, train_size=1, test_size=1, step_size=1)

    window_results, summary, issues = evaluate_factor_splits(
        factors,
        loader.target_ret,
        loader.trade_dates,
        splits,
        factor_id,
    )
    multiple_testing, rows = analyze_multiple_testing(factor_store=store)
    overfit = estimate_overfit_risk(window_results, multiple_testing)
    placebo, placebo_rows = run_placebo_tests(factor_id, factors, loader.target_ret, loader.trade_dates, summary.out_of_sample_score, n_trials=4)
    regimes, regime_summary = run_regime_validation(factors, loader.target_ret, loader.trade_dates, loader.raw_data_cache)
    sensitivity, surface = run_sensitivity_tests(summary.out_of_sample_score, [1, 2], [0.05], [1.0], [0.1])
    def rerun(scenario_id, parameters):
        return StressBacktestResult(
            scenario_id=scenario_id,
            parameters=parameters,
            metrics={"total_return": 0.0, "fill_rate": 1.0},
            passed=True,
        )

    stress, stress_summary = run_stress_backtest_bundle(
        {"score": summary.out_of_sample_score},
        cost_multipliers=[1.0],
        participations=[0.1],
        top_n_values=[1],
        max_weight_values=[0.1],
        simulator_rerun=rerun,
    )

    payload = {
        "summary": summary.to_dict(),
        "issues": [issue.to_dict() for issue in issues],
        "multiple_testing": multiple_testing.to_dict(),
        "overfit": overfit.to_dict(),
        "placebo": placebo.to_dict(),
        "regime_summary": regime_summary,
        "sensitivity_surface": surface,
        "stress_summary": stress_summary,
        "row_count": len(rows) + len(placebo_rows) + len(regimes) + len(sensitivity) + len(stress),
    }
    json.dumps(payload, ensure_ascii=False)

    assert summary.split_count == len(splits)
    assert multiple_testing.effective_trial_count >= 1
    assert 0.0 <= overfit.pbo_estimate <= 1.0
    assert 0.0 <= placebo.candidate_vs_placebo_percentile <= 1.0
    assert all(math.isfinite(float(value)) for value in summary.metrics.values())


def test_stress_backtest_without_real_rerun_fails_closed():
    with pytest.raises(UnsupportedStressBacktestError, match="actual_simulator_rerun"):
        run_stress_backtest_bundle({"total_return": 1.0, "fill_rate": 1.0})


def test_validation_lab_cli_writes_report_artifacts(tmp_path, capsys):
    data_dir, _store, _loader, factor_id = _prepare_factor(tmp_path)

    exit_code = validation_main(
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
            "--split-method",
            "simple_walk_forward",
            "--train-size",
            "1",
            "--test-size",
            "1",
            "--run-multiple-testing",
            "--run-overfit-risk",
            "--run-placebo",
            "--placebo-trials",
            "3",
            "--run-regime",
            "--run-sensitivity",
            "--run-stress-backtest",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["factor_id"] == factor_id
    assert payload["split_count"] >= 1
    assert (tmp_path / "validation" / "validation_lab_report.json").exists()
    assert (tmp_path / "validation" / "factor_validation_summary.json").exists()
    assert (tmp_path / "validation" / "multiple_testing_report.json").exists()
    assert (tmp_path / "validation" / "overfit_risk_report.json").exists()
    assert (tmp_path / "validation" / "placebo_trials.jsonl.schema.json").exists()


def test_validation_lab_reads_alpha_validation_candidate_pool(tmp_path, capsys):
    data_dir, _store, _loader, factor_id = _prepare_factor(tmp_path)
    pool_path = tmp_path / "candidate_pool.jsonl"
    pool_path.write_text(
        json.dumps(
            {
                "factor_id": factor_id,
                "formula_hash": "hash",
                "formula_names": ["RET_1D"],
                "feature_version": "ashare_features_v1",
                "source_campaign": "camp",
                "rank": 1,
                "score_components": {"base_score": 1.0},
                "factor_store_dir": str(tmp_path / "store"),
                "factor_values_path": str(tmp_path / "store" / "factor_values" / f"{factor_id}.jsonl"),
                "recommended_validation_split": "walk_forward_long_history",
                "family": "return",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    exit_code = validation_main(
        [
            "validate-candidates",
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(tmp_path / "store"),
            "--validation-candidate-pool-path",
            str(pool_path),
            "--max-candidates",
            "1",
            "--output-dir",
            str(tmp_path / "validation_pool"),
            "--split-method",
            "simple_walk_forward",
            "--train-size",
            "1",
            "--test-size",
            "1",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["validated_candidate_count"] == 1
    assert (tmp_path / "validation_pool" / "validation_candidate_pool_report.json").exists()
    assert (tmp_path / "validation_pool" / "validation_candidate_pool_results.jsonl").exists()


def test_validation_metrics_handle_nan_and_small_sample():
    factors = torch.tensor([[1.0], [float("nan")], [2.0]])
    target = torch.tensor([[0.1], [0.2], [float("nan")]])
    splits = build_simple_walk_forward_splits(["20240102"], train_size=2, test_size=1, step_size=1)

    results, summary, issues = evaluate_factor_splits(factors, target, ["20240102"], splits, "factor_nan")

    assert results == []
    assert summary.status == "data_blocked"
    assert any(issue.code == "data_blocked_window" for issue in issues)

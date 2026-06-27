import json

import torch

from evaluation import (
    build_factor_report,
    evaluate_by_splits,
    split_trade_dates,
    write_factor_report,
)
from model_core.backtest import AShareFactorEvaluator


def test_split_trade_dates_three_dates_is_one_each():
    split = split_trade_dates(["20240103", "20240102", "20240104"])

    assert split.train_dates == ["20240102"]
    assert split.valid_dates == ["20240103"]
    assert split.test_dates == ["20240104"]


def test_split_trade_dates_more_dates_are_non_overlapping_and_test_last():
    dates = [f"2024010{idx}" for idx in range(1, 7)]
    split = split_trade_dates(dates)

    assert not (set(split.train_dates) & set(split.valid_dates))
    assert not (set(split.train_dates) & set(split.test_dates))
    assert not (set(split.valid_dates) & set(split.test_dates))
    assert split.test_dates[-1] == dates[-1]


def test_evaluate_by_splits_returns_float_metrics():
    factors = torch.tensor([[0.3, 0.4, 0.5], [0.2, 0.1, 0.0], [-0.1, -0.2, -0.3]])
    target = torch.tensor([[0.03, 0.04, 0.05], [0.01, 0.0, -0.01], [-0.02, -0.03, -0.04]])
    trade_dates = ["20240102", "20240103", "20240104"]
    split = split_trade_dates(trade_dates)

    metrics = evaluate_by_splits(AShareFactorEvaluator(), factors, {}, target, trade_dates, split)

    assert set(metrics) == {"train", "valid", "test", "all"}
    for split_metrics in metrics.values():
        json.dumps(split_metrics)
        assert all(isinstance(value, float) for value in split_metrics.values())


def test_write_factor_report_outputs_json_and_markdown(tmp_path):
    report = build_factor_report(
        factor_id="factor_1234567890abcdef",
        experiment_id="exp_1234567890abcdef_20260627T000000Z",
        formula=["RET_1D"],
        formula_tokens=[0],
        metrics_by_split={
            split: {
                "rank_ic_mean": 0.1,
                "rank_ic_std": 0.05,
                "rank_ic_ir": 0.2,
                "rank_ic_t_stat": 1.0,
                "rank_ic_positive_ratio": 1.0,
                "top_bottom_spread": 0.3,
                "top_bottom_win_rate": 1.0,
                "monotonicity": 0.5,
                "coverage": 1.0,
                "turnover": 0.4,
                "score": 0.5,
            }
            for split in ["train", "valid", "test", "all"]
        },
        n_stocks=3,
        n_dates=3,
        n_features=11,
        train_dates=["20240102"],
        valid_dates=["20240103"],
        test_dates=["20240104"],
        created_at="2026-06-27T00:00:00Z",
        transform_method="winsorize_zscore",
        gate_decision={"status": "approved", "passed": True, "reasons": [], "checks": {"coverage": 1.0}},
        max_abs_correlation=0.12,
        similar_factors=[],
        status="approved",
    )

    json_path, md_path = write_factor_report(report, tmp_path)

    assert json_path.exists()
    assert md_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["transform_method"] == "winsorize_zscore"
    assert payload["status"] == "approved"
    markdown = md_path.read_text(encoding="utf-8")
    for expected in ["factor_1234567890abcdef", "exp_1234567890abcdef", "train", "valid", "test", "all", "monotonicity", "Gate And Correlation"]:
        assert expected in markdown


def test_factor_report_old_optional_fields_are_compatible(tmp_path):
    report = build_factor_report(
        factor_id="factor_old",
        experiment_id="exp_old",
        formula=["RET_1D"],
        formula_tokens=[0],
        metrics_by_split={"all": {"score": 0.1}},
        n_stocks=1,
        n_dates=1,
        n_features=1,
        train_dates=[],
        valid_dates=[],
        test_dates=["20240102"],
        created_at="2026-06-27T00:00:00Z",
    )

    json_path, md_path = write_factor_report(report, tmp_path)

    assert json_path.exists()
    assert "candidate" in md_path.read_text(encoding="utf-8")

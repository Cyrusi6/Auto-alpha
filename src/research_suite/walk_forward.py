"""Walk-forward robustness evaluation."""

from __future__ import annotations

import math

from evaluation.metrics import evaluate_by_date_mask
from model_core.backtest import AShareFactorEvaluator

from .models import WalkForwardResult, WalkForwardWindow


def build_walk_forward_windows(
    trade_dates: list[str],
    train_size: int,
    test_size: int,
    step_size: int,
) -> list[WalkForwardWindow]:
    dates = sorted(trade_dates)
    train_size = max(int(train_size), 1)
    test_size = max(int(test_size), 1)
    step_size = max(int(step_size), 1)
    windows: list[WalkForwardWindow] = []
    start = 0
    while start + train_size + test_size <= len(dates):
        train_dates = dates[start : start + train_size]
        test_dates = dates[start + train_size : start + train_size + test_size]
        windows.append(WalkForwardWindow(train_dates=train_dates, test_dates=test_dates))
        start += step_size
    if not windows and len(dates) >= 2:
        windows.append(WalkForwardWindow(train_dates=dates[:1], test_dates=dates[1:]))
    return windows


def evaluate_factor_walk_forward(
    loader,
    store,
    factor_id: str,
    windows: list[WalkForwardWindow],
    evaluator: AShareFactorEvaluator | None = None,
) -> WalkForwardResult:
    evaluator = evaluator or AShareFactorEvaluator()
    factors = store.load_factor_values_matrix(factor_id, loader.ts_codes, loader.trade_dates, device="cpu")
    rows = []
    for idx, window in enumerate(windows):
        train_metrics = evaluate_by_date_mask(
            evaluator,
            factors,
            loader.raw_data_cache,
            loader.target_ret,
            loader.trade_dates,
            window.train_dates,
        )
        test_metrics = evaluate_by_date_mask(
            evaluator,
            factors,
            loader.raw_data_cache,
            loader.target_ret,
            loader.trade_dates,
            window.test_dates,
        )
        rows.append(
            {
                "window": idx,
                "train_dates": window.train_dates,
                "test_dates": window.test_dates,
                "train_metrics": train_metrics,
                "test_metrics": test_metrics,
            }
        )
    return WalkForwardResult(factor_id=factor_id, windows=rows, summary=summarize_walk_forward_rows(rows))


def summarize_walk_forward(result: WalkForwardResult) -> dict[str, float]:
    return summarize_walk_forward_rows(result.windows)


def summarize_walk_forward_rows(rows: list[dict]) -> dict[str, float]:
    scores = [_finite(row.get("test_metrics", {}).get("score", 0.0)) for row in rows]
    rank_ics = [_finite(row.get("test_metrics", {}).get("rank_ic_mean", 0.0)) for row in rows]
    if not scores:
        return {
            "mean_test_score": 0.0,
            "mean_test_rank_ic": 0.0,
            "positive_test_score_ratio": 0.0,
            "score_stability": 0.0,
            "worst_test_score": 0.0,
            "n_windows": 0.0,
        }
    mean_score = sum(scores) / len(scores)
    variance = sum((score - mean_score) ** 2 for score in scores) / len(scores)
    stability = 1.0 / (1.0 + math.sqrt(max(variance, 0.0)))
    return {
        "mean_test_score": float(mean_score),
        "mean_test_rank_ic": float(sum(rank_ics) / len(rank_ics)),
        "positive_test_score_ratio": float(sum(1 for score in scores if score > 0.0) / len(scores)),
        "score_stability": float(stability),
        "worst_test_score": float(min(scores)),
        "n_windows": float(len(scores)),
    }


def _finite(value) -> float:
    try:
        numeric = float(value)
        return numeric if math.isfinite(numeric) else 0.0
    except (TypeError, ValueError):
        return 0.0

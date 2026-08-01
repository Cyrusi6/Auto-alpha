"""Portfolio trial metric extraction."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .models import PortfolioTrialMetrics


def metrics_from_backtest(trial_id: str, policy_id: str, scenario_id: str, backtest_result_path: str | Path, status: str = "success") -> PortfolioTrialMetrics:
    payload = _read_json(backtest_result_path)
    metrics = payload.get("metrics", {}) if isinstance(payload.get("metrics"), dict) else {}
    total_return = _finite(metrics.get("total_return"))
    sharpe = _finite(metrics.get("sharpe"))
    max_drawdown = _finite(metrics.get("max_drawdown"))
    avg_turnover = _finite(metrics.get("avg_turnover"))
    tracking_error = _finite(metrics.get("tracking_error"))
    fill_rate = _finite(metrics.get("fill_rate"))
    reject_rate = _finite(metrics.get("constraint_reject_rate"))
    capacity_warning_count = _finite(metrics.get("capacity_warning_count"))
    violations = _finite(metrics.get("risk_constraint_violations"))
    score = sharpe + total_return - max_drawdown - 0.25 * avg_turnover - 0.25 * reject_rate - 0.1 * violations
    return PortfolioTrialMetrics(
        trial_id=trial_id,
        policy_id=policy_id,
        scenario_id=scenario_id,
        status=status,
        score=float(score),
        total_return=total_return,
        sharpe=sharpe,
        max_drawdown=max_drawdown,
        avg_turnover=avg_turnover,
        tracking_error=tracking_error,
        fill_rate=fill_rate,
        constraint_reject_rate=reject_rate,
        capacity_warning_count=capacity_warning_count,
        risk_constraint_violations=violations,
        diagnostics=metrics,
    )


def failed_metrics(trial_id: str, policy_id: str, scenario_id: str, error: str) -> PortfolioTrialMetrics:
    return PortfolioTrialMetrics(trial_id, policy_id, scenario_id, "failed", -999.0, diagnostics={"error": error})


def _read_json(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {}
    return json.loads(target.read_text(encoding="utf-8"))


def _finite(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return numeric if math.isfinite(numeric) else 0.0

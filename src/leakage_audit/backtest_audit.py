"""Leakage checks for backtest artifacts."""

from __future__ import annotations

import json
from pathlib import Path

from .models import BacktestLeakageResult, LeakageIssue


def audit_backtest_artifacts(backtest_result_path: str | Path | None, strict: bool = False) -> BacktestLeakageResult:
    if not backtest_result_path or not Path(backtest_result_path).exists():
        return BacktestLeakageResult(False, 0, 0, 0, False, "not_provided", [])
    payload = json.loads(Path(backtest_result_path).read_text(encoding="utf-8"))
    metrics = payload.get("metrics", {}) if isinstance(payload, dict) else {}
    issues: list[LeakageIssue] = []
    inactive = int(float(metrics.get("inactive_security_order_count", 0.0) or 0.0))
    if inactive:
        issues.append(LeakageIssue("blocker" if strict else "warning", "inactive_security_order", "backtest traded inactive securities", "backtest_result"))
    same_day_warning = int(float(metrics.get("signal_lag_days", 0.0) or 0.0)) == 0 and payload
    if same_day_warning:
        issues.append(LeakageIssue("warning", "same_day_signal_execution", "signal and execution may use same-day close data", "backtest_result"))
    blockers = sum(1 for issue in issues if issue.severity == "blocker")
    warnings = sum(1 for issue in issues if issue.severity == "warning")
    return BacktestLeakageResult(True, warnings, blockers, inactive, same_day_warning, "blocked" if blockers else ("warning" if warnings else "passed"), issues)

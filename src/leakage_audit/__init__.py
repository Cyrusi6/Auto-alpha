"""Future-data leakage audit utilities for A-share research artifacts."""

from .backtest_audit import audit_backtest_artifacts
from .factor_audit import audit_factor_values
from .models import (
    BacktestLeakageResult,
    FactorValueLeakageResult,
    FormulaLeakageScanResult,
    LeakageAuditConfig,
    LeakageAuditReport,
    LeakageIssue,
    LeakageSeverity,
    SurvivorshipAuditResult,
    TruncationConsistencyResult,
)
from .static_analysis import scan_formula_leakage
from .truncation import run_truncation_consistency_test

__all__ = [
    "BacktestLeakageResult",
    "FactorValueLeakageResult",
    "FormulaLeakageScanResult",
    "LeakageAuditConfig",
    "LeakageAuditReport",
    "LeakageIssue",
    "LeakageSeverity",
    "SurvivorshipAuditResult",
    "TruncationConsistencyResult",
    "audit_backtest_artifacts",
    "audit_factor_values",
    "run_truncation_consistency_test",
    "scan_formula_leakage",
]

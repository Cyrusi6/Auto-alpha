"""Future-data leakage audit utilities for A-share research artifacts."""

from auto_alpha.validation.firewall.leakage_backtest_audit import audit_backtest_artifacts
from auto_alpha.validation.firewall.leakage_factor_audit import audit_factor_values
from auto_alpha.validation.firewall.leakage_models import BacktestLeakageResult
from auto_alpha.validation.firewall.leakage_models import FactorValueLeakageResult
from auto_alpha.validation.firewall.leakage_models import FormulaLeakageScanResult
from auto_alpha.validation.firewall.leakage_models import LeakageAuditConfig
from auto_alpha.validation.firewall.leakage_models import LeakageAuditReport
from auto_alpha.validation.firewall.leakage_models import LeakageIssue
from auto_alpha.validation.firewall.leakage_models import LeakageSeverity
from auto_alpha.validation.firewall.leakage_models import SurvivorshipAuditResult
from auto_alpha.validation.firewall.leakage_models import TruncationConsistencyResult
from auto_alpha.validation.firewall.leakage_static_analysis import scan_formula_leakage
from auto_alpha.validation.firewall.leakage_truncation import run_truncation_consistency_test

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

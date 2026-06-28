"""Monitoring report dataclasses."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class MonitoringAlert:
    severity: str
    check: str
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MonitoringReport:
    created_at: str
    as_of_date: str
    checks: dict[str, Any]
    alerts: list[MonitoringAlert]

    def to_dict(self) -> dict[str, Any]:
        pit = self.checks.get("point_in_time_validation", {}) if isinstance(self.checks, dict) else {}
        survivorship = self.checks.get("survivorship_bias", {}) if isinstance(self.checks, dict) else {}
        leakage = self.checks.get("leakage_audit", {}) if isinstance(self.checks, dict) else {}
        truncation = self.checks.get("truncation_consistency", {}) if isinstance(self.checks, dict) else {}
        cutoff = self.checks.get("feature_cutoff_policy", {}) if isinstance(self.checks, dict) else {}
        backtest = self.checks.get("backtest", {}) if isinstance(self.checks, dict) else {}
        corporate = self.checks.get("corporate_action_report", {}) if isinstance(self.checks, dict) else {}
        ca_ledger = self.checks.get("corporate_action_ledger", {}) if isinstance(self.checks, dict) else {}
        settlement = self.checks.get("settlement_report", {}) if isinstance(self.checks, dict) else {}
        account_reconciliation = self.checks.get("account_reconciliation", {}) if isinstance(self.checks, dict) else {}
        fee_tax = self.checks.get("settlement_fee_tax", {}) if isinstance(self.checks, dict) else {}
        return {
            "created_at": self.created_at,
            "as_of_date": self.as_of_date,
            "checks": self.checks,
            "alerts": [alert.to_dict() for alert in self.alerts],
            "pit_blocker_count": int(pit.get("pit_blocker_count", 0) or 0) if isinstance(pit, dict) else 0,
            "pit_warning_count": int(pit.get("pit_warning_count", 0) or 0) if isinstance(pit, dict) else 0,
            "leakage_blocker_count": int(leakage.get("leakage_blocker_count", 0) or 0) if isinstance(leakage, dict) else 0,
            "leakage_warning_count": int(leakage.get("leakage_warning_count", 0) or 0) if isinstance(leakage, dict) else 0,
            "truncation_consistency_passed": truncation.get("truncation_consistency_passed") if isinstance(truncation, dict) else None,
            "truncation_max_abs_diff": float(truncation.get("truncation_max_abs_diff", 0.0) or 0.0) if isinstance(truncation, dict) else 0.0,
            "survivorship_warning_count": int(survivorship.get("survivorship_warning_count", 0) or 0) if isinstance(survivorship, dict) else 0,
            "current_only_security_master": bool(survivorship.get("current_only_security_master", False)) if isinstance(survivorship, dict) else False,
            "active_universe_coverage": float(pit.get("active_universe_coverage", 0.0) or 0.0) if isinstance(pit, dict) else 0.0,
            "inactive_security_order_count": int(backtest.get("inactive_security_order_count", 0) or 0) if isinstance(backtest, dict) else 0,
            "feature_cutoff_mode": str(cutoff.get("feature_cutoff_mode", "")) if isinstance(cutoff, dict) else "",
            "corporate_action_event_count": int(corporate.get("corporate_action_event_count", 0) or 0) if isinstance(corporate, dict) else 0,
            "unprocessed_corporate_action_count": int(corporate.get("unprocessed_corporate_action_count", 0) or 0) if isinstance(corporate, dict) else 0,
            "corporate_action_ledger_entries": int(ca_ledger.get("corporate_action_ledger_entries", 0) or 0) if isinstance(ca_ledger, dict) else 0,
            "corporate_action_cash_amount": float(ca_ledger.get("corporate_action_cash_amount", 0.0) or 0.0) if isinstance(ca_ledger, dict) else 0.0,
            "pending_settlement_event_count": int(settlement.get("pending_settlement_event_count", 0) or 0) if isinstance(settlement, dict) else 0,
            "failed_settlement_event_count": int(settlement.get("failed_settlement_event_count", 0) or 0) if isinstance(settlement, dict) else 0,
            "settlement_reconciliation_error_count": int(settlement.get("settlement_reconciliation_error_count", 0) or 0) if isinstance(settlement, dict) else 0,
            "settlement_nav_difference": float(settlement.get("nav_difference", 0.0) or 0.0) if isinstance(settlement, dict) else 0.0,
            "account_reconciliation_error_count": int(account_reconciliation.get("error_count", 0) or 0) if isinstance(account_reconciliation, dict) else 0,
            "total_fee_tax": float(fee_tax.get("total_fee_tax", 0.0) or 0.0) if isinstance(fee_tax, dict) else 0.0,
        }

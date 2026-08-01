"""Order gate helpers for pre-trade risk controls."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .exposure import normalize_order
from .limit_engine import RiskControlLimitEngine
from .models import RiskControlReport, RiskControlStatus
from .policy import load_policy
from .report import write_risk_control_report
from .state import LocalRiskControlState


def evaluate_order_records(
    orders: list[Any],
    *,
    policy_path: str | Path | None = None,
    policy_profile: str = "cn_ashare_paper_default",
    state_dir: str | Path,
    output_dir: str | Path,
    batch_id: str = "",
    trade_date: str | None = None,
    scope: str = "order",
    allow_clipping: bool = False,
    available_cash: float | None = None,
    available_shares: dict[str, float] | None = None,
) -> tuple[RiskControlReport, dict[str, list[dict[str, Any]]], dict[str, Path]]:
    policy = load_policy(policy_path, profile=policy_profile)
    state = LocalRiskControlState(state_dir)
    normalized = [normalize_order(order, idx) for idx, order in enumerate(orders)]
    engine = RiskControlLimitEngine(
        policy,
        allow_clipping=allow_clipping,
        available_cash=available_cash,
        available_shares=available_shares,
        kill_switch=state.load_kill_switch(),
        batch_id=batch_id,
        scope=scope,
    )
    report = engine.evaluate(normalized, trade_date=trade_date)
    accepted, rejected, clipped = split_orders_by_decision(normalized, report)
    paths = write_risk_control_report(report, output_dir, accepted, rejected, clipped)
    state.append_usage(report.usage)
    state.append_audit("evaluate_orders", report.status, "pre-trade risk controls evaluated", {"batch_id": batch_id, "scope": scope})
    state.write_state_summary({"last_report_path": str(paths["risk_control_report_path"]), "last_status": report.status})
    return report, {"accepted": accepted, "rejected": rejected, "clipped": clipped}, paths


def evaluate_orders_file(
    orders_path: str | Path,
    *,
    policy_path: str | Path | None = None,
    policy_profile: str = "cn_ashare_paper_default",
    state_dir: str | Path,
    output_dir: str | Path,
    batch_id: str = "",
    trade_date: str | None = None,
    scope: str = "order",
    allow_clipping: bool = False,
) -> tuple[RiskControlReport, dict[str, list[dict[str, Any]]], dict[str, Path]]:
    records = _read_jsonl(Path(orders_path))
    return evaluate_order_records(
        records,
        policy_path=policy_path,
        policy_profile=policy_profile,
        state_dir=state_dir,
        output_dir=output_dir,
        batch_id=batch_id,
        trade_date=trade_date,
        scope=scope,
        allow_clipping=allow_clipping,
    )


def split_orders_by_decision(orders: list[dict[str, Any]], report: RiskControlReport) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    by_id = {str(order.get("order_id")): dict(order) for order in orders}
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    clipped: list[dict[str, Any]] = []
    for decision in report.decisions:
        order = dict(by_id.get(decision.order_id, {}))
        order["risk_control_decision_id"] = decision.decision_id
        order["risk_control_status"] = decision.status
        order["risk_control_reasons"] = decision.reasons
        if decision.status == RiskControlStatus.clipped:
            final_order = dict(decision.metadata.get("final_order", {}))
            final_order["risk_control_decision_id"] = decision.decision_id
            final_order["risk_control_status"] = decision.status
            final_order["risk_control_reasons"] = decision.reasons
            clipped.append(final_order)
            accepted.append(final_order)
        elif decision.status in {RiskControlStatus.passed, RiskControlStatus.warning}:
            accepted.append(order)
        else:
            rejected.append(order)
    return accepted, rejected, clipped


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            records.append(payload)
    return records

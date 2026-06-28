"""Fee and tax normalization for paper settlement."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact
from backtest import AShareCostModel

from .models import FeeTaxBreakdown


def estimate_fee_tax(side: str, value: float, cost_model: AShareCostModel | None = None) -> FeeTaxBreakdown:
    breakdown = (cost_model or AShareCostModel()).estimate(str(side).upper(), float(value or 0.0))
    return FeeTaxBreakdown(
        commission=float(breakdown.commission),
        stamp_duty=float(breakdown.stamp_duty),
        transfer_fee=float(breakdown.transfer_fee),
        slippage=float(breakdown.slippage),
        market_impact=float(breakdown.market_impact),
        other_fee=0.0,
        total=float(breakdown.total),
    )


def normalize_fee_tax_from_fill(fill: object, cost_model: AShareCostModel | None = None) -> tuple[FeeTaxBreakdown, list[str]]:
    payload = _payload(fill)
    warnings: list[str] = []
    fields = ["commission", "stamp_duty", "transfer_fee", "slippage", "market_impact", "other_fee"]
    if any(payload.get(field) not in {None, ""} for field in fields):
        values = {field: float(payload.get(field) or 0.0) for field in fields}
        total = float(payload.get("cost") or payload.get("total") or sum(values.values()))
        if abs(total - sum(values.values())) > 1e-8 and not values["other_fee"]:
            values["other_fee"] += total - sum(values.values())
        return FeeTaxBreakdown(**values, total=float(total)), warnings
    value = float(payload.get("value") or 0.0)
    side = str(payload.get("side") or "")
    if value > 0 and side:
        return estimate_fee_tax(side, value, cost_model=cost_model), warnings
    total = float(payload.get("cost") or 0.0)
    if total:
        warnings.append("legacy_cost_only")
    return FeeTaxBreakdown(other_fee=total, total=total), warnings


def write_fee_tax_report(fills: list[object], path: str | Path, cost_model: AShareCostModel | None = None) -> Path:
    summary = {
        "commission": 0.0,
        "stamp_duty": 0.0,
        "transfer_fee": 0.0,
        "slippage": 0.0,
        "market_impact": 0.0,
        "other_fee": 0.0,
        "total": 0.0,
        "legacy_cost_only_count": 0,
        "fill_count": len(fills),
    }
    details = []
    for fill in fills:
        payload = _payload(fill)
        breakdown, warnings = normalize_fee_tax_from_fill(payload, cost_model=cost_model)
        values = breakdown.to_dict()
        for key in ("commission", "stamp_duty", "transfer_fee", "slippage", "market_impact", "other_fee", "total"):
            summary[key] += float(values.get(key, 0.0) or 0.0)
        if "legacy_cost_only" in warnings:
            summary["legacy_cost_only_count"] += 1
        details.append(
            {
                "trade_date": payload.get("trade_date"),
                "ts_code": payload.get("ts_code"),
                "side": payload.get("side"),
                "status": payload.get("status"),
                "broker_fill_id": payload.get("broker_fill_id"),
                "child_order_id": payload.get("child_order_id"),
                "fee_tax": values,
                "warnings": warnings,
            }
        )
    summary["fee_tax_total"] = summary["total"]
    summary["total_fee_tax"] = summary["total"]
    payload = {"summary": summary, "details": details}
    target = Path(path)
    if target.suffix.lower() != ".json":
        target = target / "fee_tax_report.json"
    write_json_artifact(target, payload, "fee_tax_report", "settlement_engine")
    return target


def _payload(fill: object) -> dict[str, Any]:
    if hasattr(fill, "to_dict"):
        return dict(fill.to_dict())
    if hasattr(fill, "__dataclass_fields__"):
        return {field: getattr(fill, field) for field in fill.__dataclass_fields__}
    return dict(fill)

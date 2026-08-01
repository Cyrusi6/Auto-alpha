"""Capacity report writers."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from artifact_schema.writer import write_json_artifact

from .models import CapacityConfig, CapacityReport, PortfolioCapacity


def build_capacity_report(
    portfolio: PortfolioCapacity,
    config: CapacityConfig,
    metadata: dict[str, object] | None = None,
) -> CapacityReport:
    return CapacityReport(
        trade_date=portfolio.trade_date,
        config=config,
        portfolio=portfolio,
        created_at=datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        metadata=metadata or {},
    )


def write_capacity_report(report: CapacityReport, output_dir: str | Path) -> tuple[Path, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "capacity_report.json"
    md_path = root / "capacity_report.md"
    payload = report.to_dict()
    write_json_artifact(json_path, payload, artifact_type="capacity_report", producer="capacity_model")
    md_path.write_text(_markdown(payload), encoding="utf-8")
    return json_path, md_path


def _markdown(payload: dict[str, object]) -> str:
    portfolio = payload.get("portfolio", {}) if isinstance(payload.get("portfolio"), dict) else {}
    records = portfolio.get("records", []) if isinstance(portfolio.get("records"), list) else []
    lines = [
        "# Capacity Report",
        "",
        f"- trade_date: `{payload.get('trade_date')}`",
        f"- total_order_value: `{portfolio.get('total_order_value', 0.0)}`",
        f"- estimated_impact_cost: `{portfolio.get('estimated_impact_cost', 0.0)}`",
        f"- capacity_warning_count: `{portfolio.get('capacity_warning_count', 0)}`",
        "",
        "| ts_code | side | order_value | amount_participation | volume_participation | impact_cost | warning |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for record in records:
        if not isinstance(record, dict):
            continue
        lines.append(
            "| {ts_code} | {side} | {order_value:.2f} | {amount_participation:.4f} | {volume_participation:.4f} | {estimated_impact_cost:.2f} | {capacity_warning} |".format(
                ts_code=record.get("ts_code", ""),
                side=record.get("side", ""),
                order_value=float(record.get("order_value", 0.0) or 0.0),
                amount_participation=float(record.get("amount_participation", 0.0) or 0.0),
                volume_participation=float(record.get("volume_participation", 0.0) or 0.0),
                estimated_impact_cost=float(record.get("estimated_impact_cost", 0.0) or 0.0),
                capacity_warning=record.get("capacity_warning", ""),
            )
        )
    return "\n".join(lines) + "\n"

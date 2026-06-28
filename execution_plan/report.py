"""Execution plan artifact writers."""

from __future__ import annotations

import json
from pathlib import Path

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import ExecutionPlanResult


def write_execution_plan_report(result: ExecutionPlanResult, output_dir: str | Path) -> dict[str, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    paths = {
        "execution_plan_path": root / "execution_plan.json",
        "execution_plan_md_path": root / "execution_plan.md",
        "parent_orders_path": root / "parent_orders.jsonl",
        "child_orders_path": root / "child_orders.jsonl",
        "child_fills_path": root / "child_fills.jsonl",
        "execution_quality_path": root / "execution_quality.json",
    }
    payload = result.to_dict()
    write_json_artifact(paths["execution_plan_path"], payload, artifact_type="execution_plan", producer="execution_plan")
    write_json_artifact(paths["execution_quality_path"], result.quality.to_dict(), artifact_type="execution_quality", producer="execution_plan")
    write_jsonl_artifact(paths["parent_orders_path"], [order.to_dict() for order in result.schedule.parent_orders], artifact_type="parent_orders", producer="execution_plan")
    write_jsonl_artifact(paths["child_orders_path"], [order.to_dict() for order in result.schedule.child_orders], artifact_type="child_orders", producer="execution_plan")
    write_jsonl_artifact(paths["child_fills_path"], [_payload(fill) for fill in result.fills], artifact_type="child_fills", producer="execution_plan")
    paths["execution_plan_md_path"].write_text(_markdown(payload), encoding="utf-8")
    return paths


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def _payload(fill: object) -> dict[str, object]:
    if hasattr(fill, "__dataclass_fields__"):
        return {field: getattr(fill, field) for field in fill.__dataclass_fields__}
    return dict(fill)


def _markdown(payload: dict[str, object]) -> str:
    schedule = payload.get("schedule", {}) if isinstance(payload.get("schedule"), dict) else {}
    quality = payload.get("quality", {}) if isinstance(payload.get("quality"), dict) else {}
    child_orders = schedule.get("child_orders", []) if isinstance(schedule.get("child_orders"), list) else []
    lines = [
        "# Execution Plan",
        "",
        f"- trade_date: `{schedule.get('trade_date')}`",
        f"- parent_order_count: `{quality.get('parent_order_count', 0)}`",
        f"- child_order_count: `{quality.get('child_order_count', 0)}`",
        f"- execution_fill_rate: `{quality.get('execution_fill_rate', 0.0)}`",
        f"- unfilled_order_value: `{quality.get('unfilled_order_value', 0.0)}`",
        "",
        "| child_order_id | parent_order_id | bucket | ts_code | side | order_value |",
        "| --- | --- | --- | --- | --- | ---: |",
    ]
    for order in child_orders:
        if not isinstance(order, dict):
            continue
        lines.append(
            "| {child_order_id} | {parent_order_id} | {bucket} | {ts_code} | {side} | {order_value:.2f} |".format(
                child_order_id=order.get("child_order_id", ""),
                parent_order_id=order.get("parent_order_id", ""),
                bucket=order.get("bucket", ""),
                ts_code=order.get("ts_code", ""),
                side=order.get("side", ""),
                order_value=float(order.get("order_value", 0.0) or 0.0),
            )
        )
    return "\n".join(lines) + "\n"

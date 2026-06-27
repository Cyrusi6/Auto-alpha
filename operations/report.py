"""Production run report writers."""

from __future__ import annotations

import json
from pathlib import Path

from .models import ProductionRunResult


def write_production_run_report(result: ProductionRunResult, output_dir: str | Path) -> tuple[Path, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "production_run.json"
    md_path = root / "production_run.md"
    payload = result.to_dict()
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(_render_markdown(payload), encoding="utf-8")
    return json_path, md_path


def _render_markdown(payload: dict) -> str:
    lines = [
        "# Production Run",
        "",
        f"- run_id: `{payload.get('run_id')}`",
        f"- status: `{payload.get('status')}`",
        f"- factor_id: `{payload.get('factor_id')}`",
        f"- rebalance_date: `{payload.get('rebalance_date')}`",
        f"- approval_id: `{payload.get('approval_id')}`",
        f"- approval_status: `{payload.get('approval_status')}`",
        f"- executed: `{payload.get('executed')}`",
        "",
        "## Summary",
        "",
        "```json",
        json.dumps(payload.get("summary", {}), ensure_ascii=False, indent=2),
        "```",
    ]
    if payload.get("error"):
        lines.extend(["", "## Error", "", str(payload["error"])])
    return "\n".join(lines) + "\n"

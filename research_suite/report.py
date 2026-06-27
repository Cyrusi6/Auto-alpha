"""Research suite report writers."""

from __future__ import annotations

import json
from pathlib import Path

from .models import PromotionDecision, ResearchSuiteResult


def write_suite_report(result: ResearchSuiteResult, output_dir: str | Path) -> tuple[Path, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    json_path = output_path / "suite_result.json"
    md_path = output_path / "suite_report.md"
    json_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(result), encoding="utf-8")
    return json_path, md_path


def write_promotion_decision(decision: PromotionDecision | None, output_dir: str | Path) -> Path:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    path = output_path / "promotion_decision.json"
    payload = decision.to_dict() if decision is not None else {}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _render_markdown(result: ResearchSuiteResult) -> str:
    lines = [
        "# Research Suite Report",
        "",
        f"- suite_name: `{result.suite_name}`",
        f"- status: `{result.status}`",
        f"- selected_factor_id: `{result.selected_factor_id or ''}`",
        "",
        "## Stages",
        "",
        "| stage | status | started_at | finished_at | error |",
        "| --- | --- | --- | --- | --- |",
    ]
    for stage in result.stages:
        lines.append(
            f"| {stage.name} | {stage.status} | {stage.started_at} | {stage.finished_at} | {stage.error or ''} |"
        )
    lines.extend(
        [
            "",
            "## Summary",
            "",
            "```json",
            json.dumps(result.summary, ensure_ascii=False, indent=2),
            "```",
            "",
            "## Promotion",
            "",
            "```json",
            json.dumps(
                result.promotion_decision.to_dict() if result.promotion_decision is not None else {},
                ensure_ascii=False,
                indent=2,
            ),
            "```",
            "",
        ]
    )
    return "\n".join(lines)

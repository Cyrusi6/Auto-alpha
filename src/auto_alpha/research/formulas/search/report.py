"""Formula search report writers."""

from __future__ import annotations

import json
from pathlib import Path

from .models import FormulaSearchResult


def write_search_report(result: FormulaSearchResult, output_dir: str | Path) -> tuple[Path, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    json_path = output_path / "search_report.json"
    md_path = output_path / "search_report.md"
    json_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(result), encoding="utf-8")
    return json_path, md_path


def _render_markdown(result: FormulaSearchResult) -> str:
    lines = [
        "# Formula Search Report",
        "",
        f"- search_id: `{result.search_id}`",
        f"- composite_factor_id: `{result.composite_factor_id or ''}`",
        f"- candidates_generated: `{result.candidates_generated}`",
        f"- candidates_valid: `{result.candidates_valid}`",
        f"- candidates_evaluated: `{result.candidates_evaluated}`",
        "",
        "## Config",
        "",
        "```json",
        json.dumps(result.config, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Generations",
        "",
        "| generation | candidates | approved | rejected | skipped | errors |",
        "| ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in result.generations:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(item.get("generation", "")),
                    str(item.get("candidates", 0)),
                    str(item.get("approved", 0)),
                    str(item.get("rejected", 0)),
                    str(item.get("skipped", 0)),
                    str(item.get("errors", 0)),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Best Candidates",
            "",
            "| rank | formula | factor_id | status | score | source | generation | complexity | lookback |",
            "| ---: | --- | --- | --- | ---: | --- | ---: | ---: | ---: |",
        ]
    )
    for rank, item in enumerate(result.best_candidates, start=1):
        candidate = item.get("candidate", {})
        formula = " ".join(candidate.get("formula_names", [])) if isinstance(candidate, dict) else ""
        lines.append(
            "| "
            + " | ".join(
                [
                    str(rank),
                    formula,
                    str(item.get("factor_id") or ""),
                    str(item.get("status") or ""),
                    f"{float(item.get('score', 0.0) or 0.0):.6f}",
                    str(candidate.get("source", "")) if isinstance(candidate, dict) else "",
                    str(candidate.get("generation", "")) if isinstance(candidate, dict) else "",
                    str(candidate.get("complexity", "")) if isinstance(candidate, dict) else "",
                    str(candidate.get("lookback", "")) if isinstance(candidate, dict) else "",
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Approved Factors",
            "",
            "```json",
            json.dumps(result.approved_factor_ids, ensure_ascii=False, indent=2),
            "```",
            "",
        ]
    )
    return "\n".join(lines)

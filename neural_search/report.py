"""Neural search report writers."""

from __future__ import annotations

import json
from pathlib import Path

from .models import NeuralSearchResult


def write_neural_search_report(result: NeuralSearchResult, output_dir: str | Path) -> tuple[Path, Path, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    result_path = root / "neural_search_result.json"
    history_path = root / "neural_training_history.jsonl"
    report_path = root / "neural_search_report.md"
    payload = result.to_dict()
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    with history_path.open("w", encoding="utf-8") as handle:
        for row in result.training_history:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    report_path.write_text(_render_markdown(payload), encoding="utf-8")
    return result_path, history_path, report_path


def _render_markdown(payload: dict) -> str:
    lines = [
        "# Neural Formula Search Report",
        "",
        f"- search_id: `{payload.get('search_id')}`",
        f"- candidates_evaluated: {payload.get('candidates_evaluated', 0)}",
        f"- composite_factor_id: `{payload.get('composite_factor_id')}`",
        "",
        "## Training Summary",
        "",
        "| Step | Phase | Loss | Avg Reward | Best Reward | Valid Rate | Unique Rate | Stable Rank |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in payload.get("training_history", []):
        lines.append(
            f"| {row.get('step')} | {row.get('phase')} | {float(row.get('loss', 0.0)):.6f} | "
            f"{float(row.get('avg_reward', 0.0)):.6f} | {float(row.get('best_reward', 0.0)):.6f} | "
            f"{float(row.get('valid_rate', 0.0)):.6f} | {float(row.get('unique_rate', 0.0)):.6f} | "
            f"{float(row.get('stable_rank', 0.0)):.6f} |"
        )
    lines.extend(["", "## Best Formulas", "", "| Formula | Reward | Status | Factor |", "| --- | ---: | --- | --- |"])
    for item in payload.get("best_formulas", [])[:20]:
        formula = " ".join(item.get("formula", []))
        lines.append(f"| `{formula}` | {float(item.get('reward', 0.0)):.6f} | {item.get('status')} | `{item.get('factor_id')}` |")
    lines.extend(["", "## Checkpoints", ""])
    for path in payload.get("checkpoint_paths", []):
        lines.append(f"- `{path}`")
    return "\n".join(lines) + "\n"

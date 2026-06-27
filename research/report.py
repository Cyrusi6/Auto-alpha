"""Batch research report writers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from factor_engine.correlation import pairwise_correlation_table
from factor_store import LocalFactorStore

from .models import BatchResearchResult


def write_batch_report(result: BatchResearchResult, output_dir: str | Path) -> tuple[Path, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    json_path = output_path / "batch_report.json"
    md_path = output_path / "batch_report.md"
    payload = result.to_dict()
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(result), encoding="utf-8")
    return json_path, md_path


def build_correlation_table(
    store: LocalFactorStore,
    factor_ids: list[str],
    ts_codes: list[str],
    trade_dates: list[str],
) -> list[dict[str, Any]]:
    matrices = {
        factor_id: store.load_factor_values_matrix(
            factor_id,
            ts_codes=ts_codes,
            trade_dates=trade_dates,
            device="cpu",
        )
        for factor_id in factor_ids
    }
    return pairwise_correlation_table(matrices)


def _render_markdown(result: BatchResearchResult) -> str:
    ranked = sorted(result.results, key=lambda item: item.score, reverse=True)
    lines = [
        "# Batch Research Report",
        "",
        f"- batch_id: `{result.batch_id}`",
        f"- created_at: `{result.created_at}`",
        f"- composite_factor_id: `{result.composite_factor_id or ''}`",
        "",
        "## Summary",
        "",
        "```json",
        json.dumps(result.summary, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Candidate Ranking",
        "",
        "| rank | candidate | factor_id | status | score | complexity | lookback | source | generation | max_abs_correlation | gate_reasons |",
        "| ---: | --- | --- | --- | ---: | ---: | ---: | --- | ---: | ---: | --- |",
    ]
    for rank, item in enumerate(ranked, start=1):
        reasons = ", ".join(item.gate_reasons)
        lines.append(
            "| "
            + " | ".join(
                [
                    str(rank),
                    item.candidate.name,
                    item.factor_id or "",
                    item.status,
                    f"{float(item.score):.6f}",
                    str(item.candidate.complexity or 0),
                    str(item.candidate.lookback or 0),
                    item.candidate.source or "",
                    "" if item.candidate.generation is None else str(item.candidate.generation),
                    f"{float(item.max_abs_correlation):.6f}",
                    reasons,
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Counts",
            "",
            f"- approved: `{len(result.approved_factor_ids)}`",
            f"- rejected: `{len(result.rejected_factor_ids)}`",
            f"- skipped: `{sum(1 for item in result.results if item.status == 'skipped_existing')}`",
            f"- errors: `{sum(1 for item in result.results if item.status == 'error')}`",
            "",
            "## Composite",
            "",
            "```json",
            json.dumps(result.summary.get("composite"), ensure_ascii=False, indent=2),
            "```",
        ]
    )

    top = ranked[0] if ranked else None
    if top is not None:
        lines.extend(
            [
                "",
                "## Top Factor Metrics",
                "",
                "```json",
                json.dumps(top.metrics_by_split, ensure_ascii=False, indent=2),
                "```",
            ]
        )
    lines.append("")
    return "\n".join(lines)

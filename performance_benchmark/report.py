"""Benchmark report writers."""

from __future__ import annotations

import json
from pathlib import Path

from .models import BenchmarkResult


def write_benchmark_report(result: BenchmarkResult, output_dir: str | Path) -> tuple[Path, Path]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / "benchmark_result.json"
    md_path = target / "benchmark_report.md"
    payload = result.to_dict()
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    lines = [
        "# Performance Benchmark Report",
        "",
        f"- data_dir: `{result.data_dir}`",
        f"- matrix_cache_dir: `{result.matrix_cache_dir or ''}`",
        f"- successful_items: `{result.summary.get('successful_items', 0)}`",
        f"- failed_items: `{result.summary.get('failed_items', 0)}`",
        "",
        "| item | seconds | success | n_stocks | n_dates | formulas | throughput | error |",
        "| --- | ---: | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in result.items:
        lines.append(
            "| "
            + " | ".join(
                [
                    item.name,
                    f"{item.wall_time_seconds:.6f}",
                    str(item.success),
                    str(item.n_stocks),
                    str(item.n_dates),
                    str(item.formulas_evaluated),
                    f"{item.throughput_estimate:.6f}",
                    item.error or "",
                ]
            )
            + " |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path

"""Report writers for cross-source comparisons."""

from __future__ import annotations

import json
from pathlib import Path

from .models import CrossSourceReport


def write_cross_source_report(report: CrossSourceReport, output_dir: str | Path) -> tuple[Path, Path]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / "cross_source_report.json"
    md_path = target / "cross_source_report.md"
    json_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    lines = [
        "# Cross Source Check Report",
        "",
        f"- left_data_dir: `{report.left_data_dir}`",
        f"- right_data_dir: `{report.right_data_dir}`",
        f"- has_differences: `{report.has_differences}`",
        "",
        "| dataset | count diff | missing left | missing right | max numeric diff | mean numeric diff | date range diff |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in report.datasets:
        lines.append(
            "| "
            + " | ".join(
                [
                    item.dataset,
                    str(item.record_count_diff),
                    str(item.missing_keys_left),
                    str(item.missing_keys_right),
                    f"{item.numeric_field_max_abs_diff:.10f}",
                    f"{item.numeric_field_mean_abs_diff:.10f}",
                    str(item.date_range_diff),
                ]
            )
            + " |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path

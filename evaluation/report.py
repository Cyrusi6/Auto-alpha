"""Factor report generation."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class FactorReport:
    factor_id: str
    experiment_id: str
    formula: list[str]
    formula_tokens: list[int]
    metrics_by_split: dict[str, dict[str, float]]
    n_stocks: int
    n_dates: int
    n_features: int
    train_dates: list[str]
    valid_dates: list[str]
    test_dates: list[str]
    created_at: str


def build_factor_report(
    factor_id: str,
    experiment_id: str,
    formula: list[str],
    formula_tokens: list[int],
    metrics_by_split: dict[str, dict[str, float]],
    n_stocks: int,
    n_dates: int,
    n_features: int,
    train_dates: list[str],
    valid_dates: list[str],
    test_dates: list[str],
    created_at: str,
) -> FactorReport:
    return FactorReport(
        factor_id=factor_id,
        experiment_id=experiment_id,
        formula=formula,
        formula_tokens=formula_tokens,
        metrics_by_split=metrics_by_split,
        n_stocks=n_stocks,
        n_dates=n_dates,
        n_features=n_features,
        train_dates=train_dates,
        valid_dates=valid_dates,
        test_dates=test_dates,
        created_at=created_at,
    )


def write_factor_report(report: FactorReport, output_dir: str | Path) -> tuple[Path, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    json_path = output_path / "factor_report.json"
    md_path = output_path / "factor_report.md"

    json_path.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(report), encoding="utf-8")
    return json_path, md_path


def _date_range(dates: list[str]) -> str:
    if not dates:
        return "N/A"
    return f"{dates[0]} - {dates[-1]}"


def _render_markdown(report: FactorReport) -> str:
    lines = [
        "# Factor Report",
        "",
        f"- factor_id: `{report.factor_id}`",
        f"- experiment_id: `{report.experiment_id}`",
        f"- formula: `{' '.join(report.formula)}`",
        f"- created_at: `{report.created_at}`",
        "",
        "## Sample Ranges",
        "",
        f"- train: `{_date_range(report.train_dates)}`",
        f"- valid: `{_date_range(report.valid_dates)}`",
        f"- test: `{_date_range(report.test_dates)}`",
        "",
        "## Metrics",
        "",
        "| split | rank_ic_mean | rank_ic_ir | top_bottom_spread | coverage | turnover | score |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for split_name in ["train", "valid", "test", "all"]:
        metrics = report.metrics_by_split.get(split_name, {})
        lines.append(
            "| {split} | {rank_ic_mean:.6f} | {rank_ic_ir:.6f} | {top_bottom_spread:.6f} | "
            "{coverage:.6f} | {turnover:.6f} | {score:.6f} |".format(
                split=split_name,
                rank_ic_mean=float(metrics.get("rank_ic_mean", 0.0)),
                rank_ic_ir=float(metrics.get("rank_ic_ir", 0.0)),
                top_bottom_spread=float(metrics.get("top_bottom_spread", 0.0)),
                coverage=float(metrics.get("coverage", 0.0)),
                turnover=float(metrics.get("turnover", 0.0)),
                score=float(metrics.get("score", 0.0)),
            )
        )
    lines.append("")
    return "\n".join(lines)

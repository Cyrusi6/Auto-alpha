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
    transform_method: str | None = None
    gate_decision: dict[str, object] | None = None
    max_abs_correlation: float | None = None
    similar_factors: list[dict[str, object]] | None = None
    status: str | None = None


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
    transform_method: str | None = None,
    gate_decision: dict[str, object] | None = None,
    max_abs_correlation: float | None = None,
    similar_factors: list[dict[str, object]] | None = None,
    status: str | None = None,
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
        transform_method=transform_method,
        gate_decision=gate_decision,
        max_abs_correlation=max_abs_correlation,
        similar_factors=similar_factors,
        status=status,
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
        f"- status: `{report.status or 'candidate'}`",
        f"- transform_method: `{report.transform_method or 'raw'}`",
        f"- max_abs_correlation: `{float(report.max_abs_correlation or 0.0):.6f}`",
        "",
        "## Sample Ranges",
        "",
        f"- train: `{_date_range(report.train_dates)}`",
        f"- valid: `{_date_range(report.valid_dates)}`",
        f"- test: `{_date_range(report.test_dates)}`",
        "",
        "## Metrics",
        "",
    ]
    metric_names = _metric_names(report.metrics_by_split)
    lines.append("| split | " + " | ".join(metric_names) + " |")
    lines.append("| --- | " + " | ".join("---:" for _ in metric_names) + " |")
    for split_name in ["train", "valid", "test", "all"]:
        metrics = report.metrics_by_split.get(split_name, {})
        values = [f"{float(metrics.get(name, 0.0)):.6f}" for name in metric_names]
        lines.append("| " + split_name + " | " + " | ".join(values) + " |")
    lines.extend(_render_gate_section(report))
    lines.append("")
    return "\n".join(lines)


def _metric_names(metrics_by_split: dict[str, dict[str, float]]) -> list[str]:
    preferred = [
        "rank_ic_mean",
        "rank_ic_std",
        "rank_ic_ir",
        "rank_ic_t_stat",
        "rank_ic_positive_ratio",
        "top_bottom_spread",
        "top_bottom_win_rate",
        "monotonicity",
        "coverage",
        "turnover",
        "score",
    ]
    present = {
        key
        for split_metrics in metrics_by_split.values()
        for key in split_metrics.keys()
    }
    ordered = [name for name in preferred if name in present]
    ordered.extend(sorted(present - set(ordered)))
    return ordered or ["score"]


def _render_gate_section(report: FactorReport) -> list[str]:
    if report.gate_decision is None and not report.similar_factors:
        return []
    lines = ["", "## Gate And Correlation", ""]
    if report.gate_decision is not None:
        lines.append("```json")
        lines.append(json.dumps(report.gate_decision, ensure_ascii=False, indent=2))
        lines.append("```")
    similar_count = len(report.similar_factors or [])
    lines.append(f"- similar_factors: `{similar_count}`")
    return lines

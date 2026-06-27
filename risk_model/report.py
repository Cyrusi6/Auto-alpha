"""Risk report generation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import torch

from .constraints import check_risk_constraints
from .covariance import portfolio_volatility, tracking_error
from .exposures import active_exposure, benchmark_exposure, portfolio_exposure
from .models import RiskConstraintConfig, RiskMetrics, RiskReport


def build_risk_report(
    weights,
    benchmark_weights,
    loader,
    index_code: str,
    as_of_date: str,
    factor_id: str | None = None,
    config: RiskConstraintConfig | None = None,
    covariance=None,
    turnover: float = 0.0,
) -> RiskReport:
    config = config or RiskConstraintConfig()
    cov = covariance if covariance is not None else None
    if cov is None:
        from .covariance import estimate_return_covariance

        cov = estimate_return_covariance(loader)
    weight_tensor = _to_tensor(weights)
    benchmark = _to_tensor(benchmark_weights)
    portfolio = portfolio_exposure(weight_tensor, loader)
    benchmark_data = benchmark_exposure(index_code, as_of_date, benchmark, loader)
    active = active_exposure(weight_tensor, benchmark, loader)
    passed, violations, checks = check_risk_constraints(weight_tensor, benchmark, loader, config)
    active_weight = float(torch.abs(weight_tensor - benchmark).sum().item())
    metrics = RiskMetrics(
        portfolio_volatility=portfolio_volatility(weight_tensor, cov),
        tracking_error=tracking_error(weight_tensor, benchmark, cov),
        active_share=0.5 * active_weight,
        hhi=portfolio.concentration_hhi,
        top_weight=portfolio.top_weight,
        n_positions=float(portfolio.n_positions),
        industry_active_max=float(max((abs(value) for value in active.industry_weights.values()), default=0.0)),
        total_active_weight=active_weight,
        turnover=float(turnover),
        violations=float(len(violations)),
    )
    return RiskReport(
        factor_id=factor_id,
        index_code=index_code,
        as_of_date=as_of_date,
        portfolio=portfolio,
        benchmark=benchmark_data,
        active=active,
        metrics=metrics,
        violations=violations,
        checks={**checks, "passed": passed},
    )


def write_risk_report(report: RiskReport, output_dir: str | Path) -> tuple[Path, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "risk_report.json"
    md_path = root / "risk_report.md"
    payload = report.to_dict()
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(_render_markdown(payload), encoding="utf-8")
    return json_path, md_path


def _render_markdown(payload: dict[str, Any]) -> str:
    metrics = payload.get("metrics", {})
    violations = payload.get("violations", [])
    lines = [
        "# Risk Report",
        "",
        f"- factor_id: `{payload.get('factor_id')}`",
        f"- index_code: `{payload.get('index_code')}`",
        f"- as_of_date: `{payload.get('as_of_date')}`",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
    ]
    for key, value in sorted(metrics.items()):
        lines.append(f"| {key} | {float(value):.6f} |")
    lines.extend(["", "## Violations", ""])
    if violations:
        lines.extend(f"- {item}" for item in violations)
    else:
        lines.append("- none")
    lines.extend(["", "## Industry Weights", "", "| Industry | Portfolio | Benchmark | Active |", "| --- | ---: | ---: | ---: |"])
    portfolio = payload.get("portfolio", {}).get("industry_weights", {})
    benchmark = payload.get("benchmark", {}).get("exposure", {}).get("industry_weights", {})
    active = payload.get("active", {}).get("industry_weights", {})
    for industry in sorted(set(portfolio) | set(benchmark) | set(active)):
        lines.append(
            f"| {industry} | {float(portfolio.get(industry, 0.0)):.6f} | "
            f"{float(benchmark.get(industry, 0.0)):.6f} | {float(active.get(industry, 0.0)):.6f} |"
        )
    return "\n".join(lines) + "\n"


def _to_tensor(values) -> torch.Tensor:
    return values.detach().cpu().to(dtype=torch.float32) if hasattr(values, "detach") else torch.tensor(values, dtype=torch.float32)

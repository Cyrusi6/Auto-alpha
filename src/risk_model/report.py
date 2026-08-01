"""Risk report generation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import torch

from .constraints import check_risk_constraints
from .covariance import portfolio_volatility, tracking_error
from .decomposition import active_risk_decomposition, portfolio_factor_exposure, portfolio_risk_decomposition
from .exposures import active_exposure, benchmark_exposure, portfolio_exposure
from .factor_model import build_barra_like_risk_model
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
    factor_risk_model=None,
    attribution_summary: dict[str, Any] | None = None,
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
    style_exposures = None
    active_style_exposures = None
    factor_risk_contribution = None
    active_risk_contribution = None
    factor_covariance_summary = None
    specific_risk_summary = None
    if factor_risk_model is not None:
        date_idx = loader.trade_dates.index(as_of_date) if as_of_date in loader.trade_dates else len(loader.trade_dates) - 1
        exposure = portfolio_factor_exposure(weight_tensor, factor_risk_model, date_idx)
        active_factor_exposure = portfolio_factor_exposure(weight_tensor - benchmark, factor_risk_model, date_idx)
        style_names = set(factor_risk_model.exposure_matrix.style_factor_names)
        industry_names = set(factor_risk_model.exposure_matrix.industry_factor_names)
        style_exposures = {name: float(exposure.get(name, 0.0)) for name in sorted(style_names)}
        active_style_exposures = {name: float(active_factor_exposure.get(name, 0.0)) for name in sorted(style_names)}
        factor_risk_contribution = portfolio_risk_decomposition(weight_tensor, factor_risk_model, date_idx)
        active_risk_contribution = active_risk_decomposition(weight_tensor, benchmark, factor_risk_model, date_idx)
        factor_cov = _to_tensor(factor_risk_model.factor_covariance)
        specific = _to_tensor(factor_risk_model.specific_risk)
        factor_covariance_summary = {
            "factor_count": float(factor_cov.shape[0]),
            "trace": float(torch.trace(factor_cov).item()),
            "max_diag": float(torch.diag(factor_cov).max().item()) if factor_cov.numel() else 0.0,
        }
        specific_risk_summary = {
            "mean": float(specific.mean().item()) if specific.numel() else 0.0,
            "max": float(specific.max().item()) if specific.numel() else 0.0,
        }
        checks = {
            **checks,
            "max_style_exposure_abs": max((abs(value) for value in style_exposures.values()), default=0.0),
            "max_active_style_exposure_abs": max((abs(value) for value in active_style_exposures.values()), default=0.0),
            "factor_risk_share": float(factor_risk_contribution.get("factor_risk_share", 0.0)),
            "specific_risk_share": float(factor_risk_contribution.get("specific_risk_share", 0.0)),
        }
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
        style_exposures=style_exposures,
        active_style_exposures=active_style_exposures,
        industry_exposures={name: float(active.industry_weights.get(name, 0.0)) for name in active.industry_weights},
        factor_covariance_summary=factor_covariance_summary,
        specific_risk_summary=specific_risk_summary,
        factor_risk_contribution=factor_risk_contribution,
        active_risk_contribution=active_risk_contribution,
        attribution_summary=attribution_summary,
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


def build_risk_model_report(
    weights,
    benchmark_weights,
    loader,
    index_code: str,
    as_of_date: str,
    factor_id: str | None = None,
    lookback: int | None = None,
    shrinkage: float = 0.1,
    attribution_summary: dict[str, Any] | None = None,
) -> RiskReport:
    risk_model = build_barra_like_risk_model(loader, lookback=lookback, shrinkage=shrinkage)
    return build_risk_report(
        weights,
        benchmark_weights,
        loader,
        index_code,
        as_of_date,
        factor_id=factor_id,
        factor_risk_model=risk_model,
        attribution_summary=attribution_summary,
    )


def write_risk_model_report(report: RiskReport, output_dir: str | Path) -> tuple[Path, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "risk_model_report.json"
    md_path = root / "risk_model_report.md"
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
    style = payload.get("style_exposures", {})
    active_style = payload.get("active_style_exposures", {})
    if style or active_style:
        lines.extend(["", "## Style Exposures", "", "| Style | Portfolio | Active |", "| --- | ---: | ---: |"])
        for name in sorted(set(style) | set(active_style)):
            lines.append(f"| {name} | {float(style.get(name, 0.0)):.6f} | {float(active_style.get(name, 0.0)):.6f} |")
    risk = payload.get("factor_risk_contribution", {})
    if risk:
        lines.extend(
            [
                "",
                "## Risk Decomposition",
                "",
                f"- total_risk: `{float(risk.get('total_risk', 0.0)):.6f}`",
                f"- factor_risk: `{float(risk.get('factor_risk', 0.0)):.6f}`",
                f"- specific_risk: `{float(risk.get('specific_risk', 0.0)):.6f}`",
            ]
        )
    attribution = payload.get("attribution_summary", {})
    if attribution:
        lines.extend(["", "## Attribution", "", "```json", json.dumps(attribution, ensure_ascii=False, indent=2), "```"])
    return "\n".join(lines) + "\n"


def _to_tensor(values) -> torch.Tensor:
    return values.detach().cpu().to(dtype=torch.float32) if hasattr(values, "detach") else torch.tensor(values, dtype=torch.float32)

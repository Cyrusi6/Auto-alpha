"""Portfolio optimizer models, policy, engine, and command workflow."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class OptimizationConfig:
    objective: str = "alpha_risk"
    risk_aversion: float = 1.0
    turnover_penalty: float = 0.1
    benchmark_weight: float = 0.25
    max_weight: float = 0.10
    max_names: int = 20
    min_names: int = 1
    max_turnover: float = 1.00
    max_industry_active_weight: float = 0.20
    max_tracking_error: float = 1.00
    long_only: bool = True
    cash_weight: float = 0.0
    use_factor_risk_model: bool = False
    max_style_exposure: float | None = None
    max_active_style_exposure: float | None = None
    max_factor_risk_contribution: float | None = None
    style_exposure_targets: dict[str, float] | None = None
    risk_model_lookback: int | None = None
    risk_model_shrinkage: float = 0.1


@dataclass(frozen=True)
class OptimizationResult:
    weights: dict[str, float]
    objective_value: float
    predicted_alpha: float
    predicted_risk: float
    tracking_error: float
    turnover: float
    violations: list[str]
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "weights": {key: float(value) for key, value in self.weights.items()},
            "objective_value": float(self.objective_value),
            "predicted_alpha": float(self.predicted_alpha),
            "predicted_risk": float(self.predicted_risk),
            "tracking_error": float(self.tracking_error),
            "turnover": float(self.turnover),
            "violations": list(self.violations),
            "diagnostics": self.diagnostics,
        }


def to_jsonable_dataclass(record) -> dict[str, Any]:
    return asdict(record)

import torch

from auto_alpha.portfolio.risk.model import (
    RiskConstraintConfig,
    active_risk_decomposition,
    build_barra_like_risk_model,
    check_risk_constraints,
    portfolio_factor_exposure,
    portfolio_risk_decomposition,
    tracking_error,
)



class PortfolioOptimizer:
    def __init__(self, config: OptimizationConfig | None = None):
        self.config = config or OptimizationConfig()

    def optimize(
        self,
        alpha_scores,
        current_weights,
        benchmark_weights,
        covariance,
        loader,
        *,
        factor_risk_model=None,
        date_index: int | None = None,
    ) -> OptimizationResult:
        alpha = _finite_tensor(alpha_scores, len(loader.ts_codes))
        current = _finite_tensor(current_weights, len(loader.ts_codes))
        benchmark = _finite_tensor(benchmark_weights, len(loader.ts_codes))
        cov = covariance.detach().cpu().to(dtype=torch.float32) if hasattr(covariance, "detach") else torch.tensor(covariance, dtype=torch.float32)
        weights = self._initial_weights(alpha, benchmark)
        weights = self._apply_turnover(weights, current)
        weights = self._apply_tracking_error(weights, benchmark, cov)
        risk_decomposition = None
        active_decomposition = None
        style_exposure = {}
        active_style_exposure = {}
        if self.config.use_factor_risk_model:
            if factor_risk_model is None or date_index is None:
                raise ValueError("point-in-time factor risk model and date_index are required")
            weights = self._apply_style_limits(weights, benchmark, factor_risk_model, date_index)
        weights = self._finalize(weights)

        predicted_alpha = float((weights * alpha).sum().item())
        predicted_risk = float(max((weights @ cov @ weights).item(), 0.0) ** 0.5)
        te = tracking_error(weights, benchmark, cov)
        turnover = float(torch.abs(weights - current).sum().item())
        constraint_config = RiskConstraintConfig(
            max_weight=self.config.max_weight,
            max_industry_active_weight=self.config.max_industry_active_weight,
            max_tracking_error=self.config.max_tracking_error,
            max_turnover=self.config.max_turnover,
            min_names=self.config.min_names,
            max_names=self.config.max_names,
        )
        _, violations, checks = check_risk_constraints(weights, benchmark, loader, constraint_config)
        if turnover > self.config.max_turnover + 1e-9 and "max_turnover" not in violations:
            violations.append("max_turnover")
        if factor_risk_model is not None:
            risk_decomposition = portfolio_risk_decomposition(weights, factor_risk_model, date_index)
            active_decomposition = active_risk_decomposition(weights, benchmark, factor_risk_model, date_index)
            factor_exposure = portfolio_factor_exposure(weights, factor_risk_model, date_index)
            active_factor_exposure = portfolio_factor_exposure(weights - benchmark, factor_risk_model, date_index)
            style_names = set(factor_risk_model.exposure_matrix.style_factor_names)
            style_exposure = {name: float(factor_exposure.get(name, 0.0)) for name in sorted(style_names)}
            active_style_exposure = {name: float(active_factor_exposure.get(name, 0.0)) for name in sorted(style_names)}
            max_style = max((abs(value) for value in style_exposure.values()), default=0.0)
            max_active_style = max((abs(value) for value in active_style_exposure.values()), default=0.0)
            max_factor_share = float(risk_decomposition.get("factor_risk_share", 0.0))
            checks = {
                **checks,
                "style_exposure": style_exposure,
                "active_style_exposure": active_style_exposure,
                "max_style_exposure_abs": max_style,
                "max_active_style_exposure_abs": max_active_style,
                "factor_risk": float(risk_decomposition.get("factor_risk", 0.0)),
                "specific_risk": float(risk_decomposition.get("specific_risk", 0.0)),
                "factor_risk_share": max_factor_share,
            }
            if self.config.max_style_exposure is not None and max_style > self.config.max_style_exposure + 1e-9:
                violations.append("max_style_exposure")
            if self.config.max_active_style_exposure is not None and max_active_style > self.config.max_active_style_exposure + 1e-9:
                violations.append("max_active_style_exposure")
            if self.config.max_factor_risk_contribution is not None and max_factor_share > self.config.max_factor_risk_contribution + 1e-9:
                violations.append("max_factor_risk_contribution")
        objective = predicted_alpha - self.config.risk_aversion * predicted_risk - self.config.turnover_penalty * turnover
        return OptimizationResult(
            weights={
                ts_code: float(weights[idx].item())
                for idx, ts_code in enumerate(loader.ts_codes)
                if float(weights[idx].item()) > 1e-10
            },
            objective_value=float(objective),
            predicted_alpha=predicted_alpha,
            predicted_risk=predicted_risk,
            tracking_error=float(te),
            turnover=turnover,
            violations=violations,
            diagnostics={
                "checks": checks,
                "weight_sum": float(weights.sum().item()),
                "cash_weight": float(max(0.0, 1.0 - weights.sum().item())),
                "selected_names": int((weights > 1e-9).sum().item()),
                "use_factor_risk_model": bool(self.config.use_factor_risk_model),
                "style_exposure": style_exposure,
                "active_style_exposure": active_style_exposure,
                "risk_decomposition": risk_decomposition or {},
                "active_risk_decomposition": active_decomposition or {},
            },
        )

    def _initial_weights(self, alpha: torch.Tensor, benchmark: torch.Tensor) -> torch.Tensor:
        n = alpha.numel()
        max_names = max(1, min(self.config.max_names, n))
        valid = torch.isfinite(alpha)
        if not bool(valid.any()):
            return torch.zeros(n, dtype=torch.float32)
        order = torch.argsort(torch.where(valid, alpha, torch.full_like(alpha, -1e9)), descending=True)
        selected = order[:max_names]
        budget = min(max(0.0, 1.0 - self.config.cash_weight), max_names * self.config.max_weight)
        rank_scores = torch.linspace(float(max_names), 1.0, steps=max_names, dtype=torch.float32)
        alpha_weights = torch.zeros(n, dtype=torch.float32)
        alpha_weights[selected] = rank_scores / torch.clamp(rank_scores.sum(), min=1e-6) * budget

        benchmark_slice = torch.zeros(n, dtype=torch.float32)
        benchmark_slice[selected] = torch.clamp(benchmark[selected], min=0.0)
        if float(benchmark_slice.sum().item()) > 1e-12:
            benchmark_slice = benchmark_slice / benchmark_slice.sum() * budget
        else:
            benchmark_slice = alpha_weights.clone()

        blend = max(0.0, min(1.0, self.config.benchmark_weight))
        weights = (1.0 - blend) * alpha_weights + blend * benchmark_slice
        return self._finalize(weights)

    def _apply_turnover(self, weights: torch.Tensor, current: torch.Tensor) -> torch.Tensor:
        turnover = float(torch.abs(weights - current).sum().item())
        if turnover <= self.config.max_turnover or turnover <= 1e-12:
            return weights
        ratio = max(0.0, min(1.0, self.config.max_turnover / turnover))
        return self._finalize(current + (weights - current) * ratio)

    def _apply_tracking_error(self, weights: torch.Tensor, benchmark: torch.Tensor, cov: torch.Tensor) -> torch.Tensor:
        result = weights
        for _ in range(10):
            te = tracking_error(result, benchmark, cov)
            if te <= self.config.max_tracking_error + 1e-9:
                break
            result = self._finalize(0.75 * result + 0.25 * benchmark)
        return result

    def _apply_style_limits(self, weights: torch.Tensor, benchmark: torch.Tensor, factor_risk_model, date_index: int) -> torch.Tensor:
        result = weights
        for _ in range(12):
            factor_exposure = portfolio_factor_exposure(result, factor_risk_model, date_index)
            active_factor_exposure = portfolio_factor_exposure(result - benchmark, factor_risk_model, date_index)
            style_names = set(factor_risk_model.exposure_matrix.style_factor_names)
            max_style = max((abs(float(factor_exposure.get(name, 0.0))) for name in style_names), default=0.0)
            max_active = max((abs(float(active_factor_exposure.get(name, 0.0))) for name in style_names), default=0.0)
            style_ok = self.config.max_style_exposure is None or max_style <= self.config.max_style_exposure + 1e-9
            active_ok = self.config.max_active_style_exposure is None or max_active <= self.config.max_active_style_exposure + 1e-9
            if style_ok and active_ok:
                break
            result = self._finalize(0.75 * result + 0.25 * benchmark)
        return result

    def _finalize(self, weights: torch.Tensor) -> torch.Tensor:
        result = torch.nan_to_num(weights.clone().to(dtype=torch.float32), nan=0.0, posinf=0.0, neginf=0.0)
        if self.config.long_only:
            result = torch.clamp(result, min=0.0)
        result = torch.clamp(result, max=self.config.max_weight)
        if self.config.max_names > 0 and int((result > 1e-12).sum().item()) > self.config.max_names:
            keep = torch.argsort(result, descending=True)[: self.config.max_names]
            mask = torch.zeros_like(result)
            mask[keep] = 1.0
            result = result * mask
        max_budget = max(0.0, 1.0 - self.config.cash_weight)
        total = float(result.sum().item())
        if total > max_budget and total > 1e-12:
            result = result / total * max_budget
            result = torch.clamp(result, max=self.config.max_weight)
        return torch.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0)


def _finite_tensor(values, n: int) -> torch.Tensor:
    tensor = values.detach().cpu().to(dtype=torch.float32) if hasattr(values, "detach") else torch.tensor(values, dtype=torch.float32)
    tensor = tensor.reshape(n)
    return torch.nan_to_num(tensor, nan=0.0, posinf=0.0, neginf=0.0)

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact



@dataclass(frozen=True)
class PortfolioPolicy:
    policy_id: str
    policy_name: str
    portfolio_method: str = "risk_aware"
    index_code: str = "000300.SH"
    top_n: int = 20
    max_weight: float = 0.10
    max_names: int = 20
    min_names: int = 1
    risk_aversion: float = 1.0
    turnover_penalty: float = 0.1
    benchmark_weight: float = 1.0
    max_turnover: float = 1.0
    max_industry_active_weight: float = 0.20
    max_tracking_error: float = 1.0
    use_factor_risk_model: bool = False
    risk_model_lookback: int | None = None
    risk_model_shrinkage: float = 0.1
    max_style_exposure: float | None = None
    max_active_style_exposure: float | None = None
    max_factor_risk_contribution: float | None = None
    cash_weight: float = 0.0
    long_only: bool = True
    certification_status: str | None = None
    certification_decision_path: str | None = None
    source_factor_id: str | None = None
    source_suite_name: str | None = None
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PortfolioPolicyLoadResult:
    policy: PortfolioPolicy | None
    source_path: str | None
    certified: bool
    status: str
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["policy"] = self.policy.to_dict() if self.policy is not None else None
        return payload


def build_portfolio_policy(
    policy_name: str = "risk_aware_default",
    portfolio_method: str = "risk_aware",
    index_code: str = "000300.SH",
    top_n: int = 20,
    max_weight: float = 0.10,
    max_names: int | None = None,
    risk_aversion: float = 1.0,
    turnover_penalty: float = 0.1,
    benchmark_weight: float = 1.0,
    max_turnover: float = 1.0,
    max_industry_active_weight: float = 0.20,
    max_tracking_error: float = 1.0,
    use_factor_risk_model: bool = False,
    risk_model_lookback: int | None = None,
    risk_model_shrinkage: float = 0.1,
    max_style_exposure: float | None = None,
    max_active_style_exposure: float | None = None,
    max_factor_risk_contribution: float | None = None,
    source_factor_id: str | None = None,
    source_suite_name: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> PortfolioPolicy:
    policy = PortfolioPolicy(
        policy_id="",
        policy_name=policy_name,
        portfolio_method=portfolio_method,
        index_code=index_code,
        top_n=int(top_n),
        max_weight=float(max_weight),
        max_names=int(max_names if max_names is not None else top_n),
        risk_aversion=float(risk_aversion),
        turnover_penalty=float(turnover_penalty),
        benchmark_weight=float(benchmark_weight),
        max_turnover=float(max_turnover),
        max_industry_active_weight=float(max_industry_active_weight),
        max_tracking_error=float(max_tracking_error),
        use_factor_risk_model=bool(use_factor_risk_model),
        risk_model_lookback=risk_model_lookback,
        risk_model_shrinkage=float(risk_model_shrinkage),
        max_style_exposure=max_style_exposure,
        max_active_style_exposure=max_active_style_exposure,
        max_factor_risk_contribution=max_factor_risk_contribution,
        source_factor_id=source_factor_id,
        source_suite_name=source_suite_name,
        created_at=_utc_now(),
        metadata=dict(metadata or {}),
    )
    return replace(policy, policy_id=make_portfolio_policy_id(policy))


def make_portfolio_policy_id(policy: PortfolioPolicy | dict[str, Any]) -> str:
    payload = policy.to_dict() if hasattr(policy, "to_dict") else dict(policy)
    normalized = _policy_hash_payload(payload)
    digest = hashlib.sha256(json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return f"portfolio_policy_{digest[:16]}"


def portfolio_policy_hash(policy: PortfolioPolicy | dict[str, Any]) -> str:
    payload = policy.to_dict() if hasattr(policy, "to_dict") else dict(policy)
    return hashlib.sha256(json.dumps(_policy_hash_payload(payload), ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def from_portfolio_policy(policy: PortfolioPolicy) -> OptimizationConfig:
    return OptimizationConfig(
        risk_aversion=policy.risk_aversion,
        turnover_penalty=policy.turnover_penalty,
        benchmark_weight=policy.benchmark_weight,
        max_weight=policy.max_weight,
        max_names=policy.max_names,
        min_names=policy.min_names,
        max_turnover=policy.max_turnover,
        max_industry_active_weight=policy.max_industry_active_weight,
        max_tracking_error=policy.max_tracking_error,
        long_only=policy.long_only,
        cash_weight=policy.cash_weight,
        use_factor_risk_model=policy.use_factor_risk_model,
        risk_model_lookback=policy.risk_model_lookback,
        risk_model_shrinkage=policy.risk_model_shrinkage,
        max_style_exposure=policy.max_style_exposure,
        max_active_style_exposure=policy.max_active_style_exposure,
        max_factor_risk_contribution=policy.max_factor_risk_contribution,
    )


def load_portfolio_policy(path: str | Path | None) -> PortfolioPolicy | None:
    if not path:
        return None
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    policy_payload = _extract_policy_payload(payload)
    policy_payload = _filter_policy_payload(policy_payload)
    policy_payload.setdefault("policy_id", "")
    policy_payload.setdefault("policy_name", policy_payload.get("name") or "portfolio_policy")
    policy_payload.setdefault("created_at", payload.get("created_at") or _utc_now())
    policy = PortfolioPolicy(**policy_payload)
    expected_id = make_portfolio_policy_id(policy)
    if not policy.policy_id:
        policy = replace(policy, policy_id=expected_id)
    return policy


def portfolio_policy_from_payload(payload: dict[str, Any]) -> PortfolioPolicy:
    policy_payload = _filter_policy_payload(_extract_policy_payload(payload))
    policy_payload.setdefault("policy_id", "")
    policy_payload.setdefault("policy_name", policy_payload.get("name") or "portfolio_policy")
    policy_payload.setdefault("created_at", payload.get("created_at") or _utc_now())
    policy = PortfolioPolicy(**policy_payload)
    if not policy.policy_id:
        policy = replace(policy, policy_id=make_portfolio_policy_id(policy))
    return policy


def write_portfolio_policy(policy: PortfolioPolicy, output_dir: str | Path, filename: str = "portfolio_policy.json") -> tuple[Path, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / filename
    md_path = json_path.with_suffix(".md")
    write_json_artifact(json_path, policy.to_dict(), artifact_type="portfolio_policy", producer="portfolio_optimizer")
    md_path.write_text(_policy_markdown(policy), encoding="utf-8")
    return json_path, md_path


def validate_certified_portfolio_policy(
    portfolio_policy_path: str | Path | None = None,
    certification_decision_path: str | Path | None = None,
    require: bool = False,
) -> PortfolioPolicyLoadResult:
    policy = load_portfolio_policy(portfolio_policy_path)
    reasons: list[str] = []
    status = policy.certification_status if policy else None
    if certification_decision_path and Path(certification_decision_path).exists():
        decision = json.loads(Path(certification_decision_path).read_text(encoding="utf-8"))
        status = str(decision.get("status") or decision.get("certification_status") or status or "")
        if not bool(decision.get("passed", status in {"certified", "conditional"})):
            reasons.append("certification_decision_not_passed")
    elif policy and policy.certification_status:
        status = str(policy.certification_status)
    elif require:
        reasons.append("certification_decision_missing")
    final_status = status or "not_certified"
    certified = final_status in {"certified", "conditional"}
    if require and not certified:
        reasons.append(f"portfolio_policy_not_certified:{final_status}")
    return PortfolioPolicyLoadResult(policy=policy, source_path=str(portfolio_policy_path) if portfolio_policy_path else None, certified=certified, status=final_status, reasons=reasons)


def _extract_policy_payload(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("portfolio_policy", "selected_policy", "certified_portfolio_policy", "policy"):
        candidate = payload.get(key)
        if isinstance(candidate, dict):
            return dict(candidate)
    return dict(payload)


def _filter_policy_payload(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = set(PortfolioPolicy.__dataclass_fields__.keys())
    normalized = {key: value for key, value in payload.items() if key in allowed}
    metadata = dict(normalized.get("metadata") or {})
    for key in ("artifact_type", "schema_version", "producer", "artifact_metadata"):
        if key in payload:
            metadata[key] = payload[key]
    normalized["metadata"] = metadata
    return normalized


def _policy_hash_payload(payload: dict[str, Any]) -> dict[str, Any]:
    ignored = {"policy_id", "created_at", "certification_status", "certification_decision_path"}
    return {key: value for key, value in payload.items() if key not in ignored}


def _policy_markdown(policy: PortfolioPolicy) -> str:
    lines = [
        "# Portfolio Policy",
        "",
        f"- policy_id: `{policy.policy_id}`",
        f"- method: `{policy.portfolio_method}`",
        f"- index_code: `{policy.index_code}`",
        f"- top_n: {policy.top_n}",
        f"- max_weight: {policy.max_weight}",
        f"- risk_aversion: {policy.risk_aversion}",
        f"- turnover_penalty: {policy.turnover_penalty}",
        f"- certification_status: `{policy.certification_status or 'not_certified'}`",
        "",
        "```json",
        json.dumps(policy.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        "",
    ]
    return "\n".join(lines)


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

import argparse
import json
from pathlib import Path

from auto_alpha.portfolio.simulator.backtest import describe_factor
from auto_alpha.portfolio.simulator.backtest import select_factor_id
from auto_alpha.execution.trading.engine import export_orders_jsonl
from auto_alpha.research.factors.store import LocalFactorStore
from auto_alpha.research.formulas.data_loader import AShareDataLoader
from auto_alpha.portfolio.risk.model import (
    benchmark_weights_from_index_members,
    build_barra_like_risk_model,
    build_risk_report,
    estimate_return_covariance,
    write_risk_model_report,
    write_risk_report,
)



def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run benchmark-aware A-share portfolio optimization.")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--factor-store-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--factor-id")
    parser.add_argument("--latest-approved", action="store_true")
    parser.add_argument("--factor-type", choices=["single", "composite", "any"], default="any")
    parser.add_argument("--index-code", default="000300.SH")
    parser.add_argument("--as-of-date")
    parser.add_argument("--max-weight", type=float, default=0.10)
    parser.add_argument("--max-names", type=int, default=20)
    parser.add_argument("--risk-aversion", type=float, default=1.0)
    parser.add_argument("--turnover-penalty", type=float, default=0.1)
    parser.add_argument("--max-turnover", type=float, default=1.0)
    parser.add_argument("--max-industry-active-weight", type=float, default=0.20)
    parser.add_argument("--max-tracking-error", type=float, default=1.0)
    parser.add_argument("--use-factor-risk-model", action="store_true")
    parser.add_argument("--risk-model-lookback", type=int)
    parser.add_argument("--risk-model-shrinkage", type=float, default=0.1)
    parser.add_argument("--max-style-exposure", type=float)
    parser.add_argument("--max-active-style-exposure", type=float)
    parser.add_argument("--max-factor-risk-contribution", type=float)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    output_dir = Path(args.output_dir)
    loader = AShareDataLoader(data_dir=args.data_dir, device="cpu").load_data()
    store = LocalFactorStore(args.factor_store_dir)
    factor_id = select_factor_id(store, args.factor_id, latest_approved=args.latest_approved, factor_type=args.factor_type)
    factor_meta = describe_factor(store, factor_id)
    factor_matrix = store.load_factor_values_matrix(factor_id, loader.ts_codes, loader.trade_dates)
    as_of_date = args.as_of_date or loader.trade_dates[-1]
    date_idx = loader.trade_dates.index(as_of_date) if as_of_date in loader.trade_dates else len(loader.trade_dates) - 1
    benchmark = benchmark_weights_from_index_members(loader, args.index_code, as_of_date)
    covariance = estimate_return_covariance(loader, as_of_index=date_idx)
    factor_risk_model = (
        build_barra_like_risk_model(loader, lookback=args.risk_model_lookback, shrinkage=args.risk_model_shrinkage, as_of_index=date_idx)
        if args.use_factor_risk_model else None
    )
    config = OptimizationConfig(
        risk_aversion=args.risk_aversion,
        turnover_penalty=args.turnover_penalty,
        max_weight=args.max_weight,
        max_names=args.max_names,
        max_turnover=args.max_turnover,
        max_industry_active_weight=args.max_industry_active_weight,
        max_tracking_error=args.max_tracking_error,
        use_factor_risk_model=args.use_factor_risk_model,
        risk_model_lookback=args.risk_model_lookback,
        risk_model_shrinkage=args.risk_model_shrinkage,
        max_style_exposure=args.max_style_exposure,
        max_active_style_exposure=args.max_active_style_exposure,
        max_factor_risk_contribution=args.max_factor_risk_contribution,
    )
    result = PortfolioOptimizer(config).optimize(
        factor_matrix[:, date_idx],
        current_weights=benchmark * 0.0,
        benchmark_weights=benchmark,
        covariance=covariance,
        loader=loader,
        factor_risk_model=factor_risk_model,
        date_index=date_idx if factor_risk_model is not None else None,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    weights_records = [
        {
            "trade_date": as_of_date,
            "ts_code": ts_code,
            "optimized_weight": weight,
            "benchmark_weight": float(benchmark[loader.ts_codes.index(ts_code)].item()),
            "active_weight": weight - float(benchmark[loader.ts_codes.index(ts_code)].item()),
        }
        for ts_code, weight in result.weights.items()
    ]
    export_orders_jsonl(weights_records, output_dir / "optimized_weights.jsonl")
    (output_dir / "optimization_result.json").write_text(
        json.dumps(
            {
                **result.to_dict(),
                "factor_id": factor_id,
                "factor_type": factor_meta["factor_type"],
                "component_factor_ids": factor_meta["component_factor_ids"],
                "index_code": args.index_code,
                "as_of_date": as_of_date,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    weight_vector = _weights_to_vector(result.weights, loader.ts_codes)
    factor_risk_model = (
        build_barra_like_risk_model(
            loader,
            lookback=args.risk_model_lookback,
            shrinkage=args.risk_model_shrinkage,
            as_of_index=date_idx,
        )
        if args.use_factor_risk_model
        else None
    )
    risk_report = build_risk_report(
        weight_vector,
        benchmark,
        loader,
        args.index_code,
        as_of_date,
        factor_id=factor_id,
        covariance=covariance,
        turnover=result.turnover,
        factor_risk_model=factor_risk_model,
    )
    risk_json, risk_md = write_risk_report(risk_report, output_dir)
    risk_model_json = None
    risk_model_md = None
    if args.use_factor_risk_model:
        risk_model_json, risk_model_md = write_risk_model_report(risk_report, output_dir)
    summary = {
        "factor_id": factor_id,
        "factor_type": factor_meta["factor_type"],
        "component_factor_ids": factor_meta["component_factor_ids"],
        "output_dir": str(output_dir),
        "weights_path": str(output_dir / "optimized_weights.jsonl"),
        "optimization_result_path": str(output_dir / "optimization_result.json"),
        "risk_report_path": str(risk_json),
        "risk_report_md_path": str(risk_md),
        "risk_model_report_path": str(risk_model_json) if risk_model_json else None,
        "risk_model_report_md_path": str(risk_model_md) if risk_model_md else None,
        "metrics": risk_report.metrics.to_dict(),
        "violations": result.violations,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def _weights_to_vector(weights: dict[str, float], ts_codes: list[str]):
    import torch

    return torch.tensor([float(weights.get(ts_code, 0.0)) for ts_code in ts_codes], dtype=torch.float32)


if __name__ == "__main__":
    raise SystemExit(main())

__all__ = [
    "OptimizationConfig",
    "OptimizationResult",
    "PortfolioOptimizer",
    "PortfolioPolicy",
    "PortfolioPolicyLoadResult",
    "build_portfolio_policy",
    "from_portfolio_policy",
    "load_portfolio_policy",
    "make_portfolio_policy_id",
    "portfolio_policy_from_payload",
    "portfolio_policy_hash",
    "validate_certified_portfolio_policy",
    "write_portfolio_policy",
]

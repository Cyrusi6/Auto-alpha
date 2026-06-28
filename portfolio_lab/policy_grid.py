"""Portfolio policy grid generation."""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Iterable

from artifact_schema.writer import write_json_artifact
from portfolio_optimizer import PortfolioPolicy, build_portfolio_policy


def generate_portfolio_policy_grid(
    factor_id: str | None = None,
    methods: Iterable[str] = ("equal_weight", "risk_aware"),
    risk_aversions: Iterable[float] = (0.5, 1.0),
    turnover_penalties: Iterable[float] = (0.0, 0.1),
    benchmark_weights: Iterable[float] = (1.0,),
    max_weight_values: Iterable[float] = (0.10,),
    max_names_values: Iterable[int] = (2, 20),
    max_turnover_values: Iterable[float] = (1.0,),
    max_tracking_error_values: Iterable[float] = (1.0,),
    top_n_values: Iterable[int] = (2, 20),
    index_code: str = "000300.SH",
    use_factor_risk_model: bool = False,
    max_trials: int | None = None,
) -> list[PortfolioPolicy]:
    policies: list[PortfolioPolicy] = []
    for values in itertools.product(
        list(methods),
        list(risk_aversions),
        list(turnover_penalties),
        list(benchmark_weights),
        list(max_weight_values),
        list(max_names_values),
        list(max_turnover_values),
        list(max_tracking_error_values),
        list(top_n_values),
    ):
        method, risk_aversion, turnover_penalty, benchmark_weight, max_weight, max_names, max_turnover, max_tracking_error, top_n = values
        if method == "equal_weight" and len([item for item in policies if item.portfolio_method == "equal_weight"]) >= 1:
            continue
        policy = build_portfolio_policy(
            policy_name=f"{method}_top{int(top_n)}_w{float(max_weight):.3f}_ra{float(risk_aversion):.2f}",
            portfolio_method=str(method),
            index_code=index_code,
            top_n=int(top_n),
            max_weight=float(max_weight),
            max_names=int(max_names),
            risk_aversion=float(risk_aversion),
            turnover_penalty=float(turnover_penalty),
            benchmark_weight=float(benchmark_weight),
            max_turnover=float(max_turnover),
            max_tracking_error=float(max_tracking_error),
            use_factor_risk_model=bool(use_factor_risk_model),
            source_factor_id=factor_id,
        )
        policies.append(policy)
        if max_trials is not None and len(policies) >= max_trials:
            break
    return policies


def load_policy_grid(path: str | Path) -> list[PortfolioPolicy]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    records = payload.get("policies") if isinstance(payload, dict) else payload
    policies = []
    for record in records or []:
        if isinstance(record, dict):
            allowed = {key: value for key, value in record.items() if key in PortfolioPolicy.__dataclass_fields__}
            policies.append(PortfolioPolicy(**allowed))
    return policies


def write_policy_grid(policies: list[PortfolioPolicy], output_dir: str | Path) -> Path:
    path = Path(output_dir) / "portfolio_policy_grid.json"
    write_json_artifact(
        path,
        {"policies": [policy.to_dict() for policy in policies], "policy_count": len(policies)},
        artifact_type="portfolio_policy_grid",
        producer="portfolio_lab",
    )
    return path

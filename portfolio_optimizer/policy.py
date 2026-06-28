"""Serializable portfolio optimizer policies."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact

from .models import OptimizationConfig


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

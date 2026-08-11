"""Factor transforms, correlation, admission gates, and research pipeline."""

from __future__ import annotations

import torch


SUPPORTED_TRANSFORMS = {
    "raw",
    "winsorize",
    "zscore",
    "winsorize_zscore",
    "neutralize_market_cap",
    "neutralize_industry",
    "neutralize_industry_size",
}


def cs_winsorize_mad(factors: torch.Tensor, n_mad: float = 5.0) -> torch.Tensor:
    clean = _engine_transforms_finite(factors)
    median = clean.median(dim=0, keepdim=True).values
    centered = clean - median
    mad = torch.abs(centered).median(dim=0, keepdim=True).values
    scale = torch.where(mad < 1e-6, torch.ones_like(mad), mad)
    lower = median - n_mad * scale
    upper = median + n_mad * scale
    return _engine_transforms_finite(torch.minimum(torch.maximum(clean, lower), upper))


def cs_zscore(factors: torch.Tensor) -> torch.Tensor:
    clean = _engine_transforms_finite(factors)
    mean = clean.mean(dim=0, keepdim=True)
    std = clean.std(dim=0, keepdim=True, unbiased=False)
    std = torch.where(std < 1e-6, torch.ones_like(std), std)
    return _engine_transforms_finite((clean - mean) / std)


def neutralize_market_cap(factors: torch.Tensor, log_mkt_cap: torch.Tensor) -> torch.Tensor:
    y = _engine_transforms_finite(factors)
    x = _engine_transforms_finite(_align_matrix(log_mkt_cap, y))
    x_centered = x - x.mean(dim=0, keepdim=True)
    y_centered = y - y.mean(dim=0, keepdim=True)
    denom = (x_centered * x_centered).sum(dim=0, keepdim=True)
    safe_denom = torch.where(denom > 1e-12, denom, torch.ones_like(denom))
    beta_raw = (x_centered * y_centered).sum(dim=0, keepdim=True) / safe_denom
    beta = torch.where(denom > 1e-12, beta_raw, torch.zeros_like(denom))
    residual = y_centered - beta * x_centered
    return _engine_transforms_finite(residual)


def neutralize_industry(factors: torch.Tensor, industry_codes: torch.Tensor) -> torch.Tensor:
    clean = _engine_transforms_finite(factors)
    codes = _industry_matrix(industry_codes, clean)
    residual = clean.clone()
    valid_codes = torch.unique(codes)
    for code in valid_codes.tolist():
        mask = codes == int(code)
        count = mask.sum(dim=0, keepdim=True).clamp(min=1)
        group_mean = torch.where(mask, clean, torch.zeros_like(clean)).sum(dim=0, keepdim=True) / count
        residual = torch.where(mask, clean - group_mean, residual)
    return _engine_transforms_finite(residual)


def neutralize_industry_size(
    factors: torch.Tensor,
    industry_codes: torch.Tensor,
    log_mkt_cap: torch.Tensor,
) -> torch.Tensor:
    industry_residual = neutralize_industry(factors, industry_codes)
    return neutralize_market_cap(industry_residual, log_mkt_cap)


def preprocess_factor(
    factors: torch.Tensor,
    raw_data: dict[str, torch.Tensor],
    method: str,
) -> torch.Tensor:
    method = method.lower()
    if method not in SUPPORTED_TRANSFORMS:
        raise ValueError(f"unsupported factor transform: {method}")

    clean = _engine_transforms_finite(factors)
    if method == "raw":
        return clean
    if method == "winsorize":
        return cs_winsorize_mad(clean)
    if method == "zscore":
        return cs_zscore(clean)
    if method == "winsorize_zscore":
        return cs_zscore(cs_winsorize_mad(clean))
    if method == "neutralize_market_cap":
        return neutralize_market_cap(clean, raw_data["log_mkt_cap"])
    if method == "neutralize_industry":
        return neutralize_industry(clean, raw_data["industry_codes"])
    if method == "neutralize_industry_size":
        return neutralize_industry_size(clean, raw_data["industry_codes"], raw_data["log_mkt_cap"])
    return clean


def preprocess_factor_with_validity(
    factors: torch.Tensor,
    validity: torch.Tensor,
    raw_data: dict[str, torch.Tensor],
    method: str,
    eligible_mask: torch.Tensor | None = None,
) -> tuple[torch.Tensor, torch.Tensor]:
    valid = validity.bool() & torch.isfinite(factors)
    if eligible_mask is not None:
        valid &= eligible_mask.bool()
    masked = torch.where(valid, factors, torch.full_like(factors, float("nan")))
    result = torch.zeros_like(factors, dtype=torch.float32)
    for date_index in range(factors.shape[1]):
        date_valid = valid[:, date_index]
        if int(date_valid.sum()) < 2:
            valid[:, date_index] = False
            continue
        date_raw: dict[str, torch.Tensor] = {}
        for key, value in raw_data.items():
            if not isinstance(value, torch.Tensor):
                continue
            aligned = _align_matrix(value, factors)
            date_raw[key] = aligned[date_valid, date_index : date_index + 1]
        transformed = preprocess_factor(masked[date_valid, date_index : date_index + 1], date_raw, method)
        result[date_valid, date_index] = transformed[:, 0]
    valid &= torch.isfinite(result)
    return torch.where(valid, result, torch.zeros_like(result)), valid


def _engine_transforms_finite(x: torch.Tensor) -> torch.Tensor:
    return torch.nan_to_num(x.to(dtype=torch.float32), nan=0.0, posinf=0.0, neginf=0.0)


def _align_matrix(value: torch.Tensor, reference: torch.Tensor) -> torch.Tensor:
    if value.ndim == 1:
        return value.unsqueeze(1).expand(-1, reference.shape[1])
    return value


def _industry_matrix(industry_codes: torch.Tensor, reference: torch.Tensor) -> torch.Tensor:
    if industry_codes.ndim == 1:
        return industry_codes.to(device=reference.device).long().unsqueeze(1).expand(-1, reference.shape[1])
    return industry_codes.to(device=reference.device).long()

from typing import Any

import torch

from auto_alpha.research.factors.store import LocalFactorStore
from auto_alpha.research.factors.store import FactorValueRecord


def factor_correlation(x: torch.Tensor, y: torch.Tensor) -> float:
    x_clean = _engine_correlation_finite(x).reshape(-1)
    y_clean = _engine_correlation_finite(y).reshape(-1)
    mask = torch.isfinite(x.reshape(-1)) & torch.isfinite(y.reshape(-1))
    if int(mask.sum().item()) < 2:
        return 0.0
    x_valid = x_clean[mask]
    y_valid = y_clean[mask]
    x_centered = x_valid - x_valid.mean()
    y_centered = y_valid - y_valid.mean()
    denom = x_centered.norm() * y_centered.norm()
    if float(denom.item()) <= 1e-12:
        return 0.0
    return float((x_centered * y_centered).sum().item() / denom.item())


def max_abs_correlation(candidate: torch.Tensor, existing_matrices: list[torch.Tensor]) -> float:
    if not existing_matrices:
        return 0.0
    return float(max(abs(factor_correlation(candidate, matrix)) for matrix in existing_matrices))


def factor_correlation_matrix(
    factor_matrices: dict[str, torch.Tensor] | list[torch.Tensor],
    factor_ids: list[str] | None = None,
) -> dict[str, dict[str, float]]:
    if isinstance(factor_matrices, dict):
        ids = list(factor_matrices.keys())
        matrices = [factor_matrices[factor_id] for factor_id in ids]
    else:
        matrices = list(factor_matrices)
        ids = factor_ids or [f"factor_{idx}" for idx in range(len(matrices))]
    result: dict[str, dict[str, float]] = {}
    for row_id, row_matrix in zip(ids, matrices):
        result[row_id] = {}
        for col_id, col_matrix in zip(ids, matrices):
            result[row_id][col_id] = float(factor_correlation(row_matrix, col_matrix))
    return result


def pairwise_correlation_table(
    factor_matrices: dict[str, torch.Tensor] | list[torch.Tensor],
    factor_ids: list[str] | None = None,
) -> list[dict[str, float | str]]:
    matrix = factor_correlation_matrix(factor_matrices, factor_ids)
    ids = list(matrix.keys())
    rows: list[dict[str, float | str]] = []
    for left_idx, left_id in enumerate(ids):
        for right_id in ids[left_idx + 1 :]:
            corr = float(matrix[left_id][right_id])
            rows.append(
                {
                    "factor_id_1": left_id,
                    "factor_id_2": right_id,
                    "correlation": corr,
                    "abs_correlation": abs(corr),
                }
            )
    rows.sort(key=lambda row: float(row["abs_correlation"]), reverse=True)
    return rows


def load_existing_factor_matrices(
    store: LocalFactorStore,
    factor_ids: list[str],
    ts_codes: list[str],
    trade_dates: list[str],
    device: torch.device | str | None = None,
) -> dict[str, torch.Tensor]:
    return {
        factor_id: factor_values_to_matrix(
            store.load_factor_values(factor_id),
            ts_codes=ts_codes,
            trade_dates=trade_dates,
            device=device,
        )
        for factor_id in factor_ids
    }


def find_similar_factors(
    candidate: torch.Tensor,
    store: LocalFactorStore,
    ts_codes: list[str],
    trade_dates: list[str],
    threshold: float = 0.8,
    device: torch.device | str | None = None,
) -> list[dict[str, Any]]:
    factors = store.load_factors()
    factor_ids = [record.factor_id for record in factors]
    matrices = load_existing_factor_matrices(store, factor_ids, ts_codes, trade_dates, device=device)
    similar: list[dict[str, Any]] = []
    for factor_id, matrix in matrices.items():
        corr = factor_correlation(candidate, matrix)
        if abs(corr) >= threshold:
            similar.append(
                {
                    "factor_id": factor_id,
                    "correlation": float(corr),
                    "abs_correlation": float(abs(corr)),
                }
            )
    similar.sort(key=lambda item: item["abs_correlation"], reverse=True)
    return similar


def factor_values_to_matrix(
    records: list[FactorValueRecord],
    ts_codes: list[str],
    trade_dates: list[str],
    device: torch.device | str | None = None,
) -> torch.Tensor:
    target_device = torch.device(device) if device is not None else None
    matrix = torch.zeros((len(ts_codes), len(trade_dates)), dtype=torch.float32, device=target_device)
    stock_index = {ts_code: idx for idx, ts_code in enumerate(ts_codes)}
    date_index = {trade_date: idx for idx, trade_date in enumerate(trade_dates)}
    for record in records:
        stock_idx = stock_index.get(record.ts_code)
        date_idx = date_index.get(record.trade_date)
        if stock_idx is None or date_idx is None or record.value is None:
            continue
        matrix[stock_idx, date_idx] = float(record.value)
    return matrix


def _engine_correlation_finite(x: torch.Tensor) -> torch.Tensor:
    return torch.nan_to_num(x.to(dtype=torch.float32), nan=0.0, posinf=0.0, neginf=0.0)

from dataclasses import asdict, dataclass
import math
from typing import Any

from auto_alpha.research.factors.store import FactorLifecycleStatus


@dataclass(frozen=True)
class FactorGateConfig:
    min_coverage: float = 0.8
    min_test_rank_ic_ir: float = 0.0
    min_test_score: float = 0.0
    min_test_evaluable_dates: int = 1
    min_test_valid_observations: int = 2
    max_turnover: float = 1.0
    max_abs_correlation: float = 0.95
    require_positive_test_rank_ic: bool = True


@dataclass(frozen=True)
class FactorGateDecision:
    passed: bool
    status: str
    reasons: list[str]
    checks: dict[str, float | bool | str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_factor_gate(
    metrics_by_split: dict[str, dict[str, float]],
    max_abs_corr: float,
    config: FactorGateConfig,
) -> FactorGateDecision:
    test_metrics = metrics_by_split.get("test", {})
    checks: dict[str, float | bool | str] = {
        "coverage": _engine_gate_finite(test_metrics.get("coverage")),
        "test_rank_ic_mean": float(test_metrics.get("rank_ic_mean", 0.0)),
        "test_rank_ic_ir": float(test_metrics.get("rank_ic_ir", 0.0)),
        "test_score": float(test_metrics.get("score", 0.0)),
        "test_evaluable_date_count": _engine_gate_finite(test_metrics.get("evaluable_date_count")),
        "test_valid_observation_count": _engine_gate_finite(test_metrics.get("valid_observation_count")),
        "turnover": _engine_gate_finite(test_metrics.get("turnover")),
        "max_abs_correlation": float(max_abs_corr),
        "require_positive_test_rank_ic": bool(config.require_positive_test_rank_ic),
    }

    reasons: list[str] = []
    if float(checks["coverage"]) < config.min_coverage:
        reasons.append("coverage_below_threshold")
    if float(checks["test_rank_ic_ir"]) < config.min_test_rank_ic_ir:
        reasons.append("test_rank_ic_ir_below_threshold")
    if float(checks["test_score"]) < config.min_test_score:
        reasons.append("test_score_below_threshold")
    if float(checks["test_evaluable_date_count"]) < config.min_test_evaluable_dates:
        reasons.append("test_evaluable_dates_below_threshold")
    if float(checks["test_valid_observation_count"]) < config.min_test_valid_observations:
        reasons.append("test_valid_observations_below_threshold")
    if float(checks["turnover"]) > config.max_turnover:
        reasons.append("turnover_above_threshold")
    if float(checks["max_abs_correlation"]) > config.max_abs_correlation:
        reasons.append("correlation_above_threshold")
    if config.require_positive_test_rank_ic and float(checks["test_rank_ic_mean"]) <= 0:
        reasons.append("test_rank_ic_not_positive")

    checks["oos_evidence_positive"] = bool(
        float(checks["test_evaluable_date_count"]) >= config.min_test_evaluable_dates
        and float(checks["test_valid_observation_count"]) >= config.min_test_valid_observations
        and float(checks["test_rank_ic_mean"]) > 0.0
        and float(checks["test_rank_ic_ir"]) >= config.min_test_rank_ic_ir
        and float(checks["test_score"]) >= config.min_test_score
    )
    if not checks["oos_evidence_positive"] and "test_rank_ic_not_positive" not in reasons:
        reasons.append("positive_oos_evidence_missing")

    passed = not reasons
    return FactorGateDecision(
        passed=passed,
        status=(
            FactorLifecycleStatus.validation_candidate.value
            if passed
            else FactorLifecycleStatus.research_rejected.value
        ),
        reasons=reasons,
        checks=checks,
    )


def _engine_gate_finite(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return numeric if math.isfinite(numeric) else 0.0

from dataclasses import dataclass
from typing import Any

import torch

from auto_alpha.research.search.evaluation import evaluate_by_splits, split_trade_dates
from auto_alpha.research.factors.store import LocalFactorStore
from auto_alpha.research.factors.store import FactorLifecycleStatus
from auto_alpha.research.formulas.backtest import AShareFactorEvaluator



@dataclass(frozen=True)
class FactorResearchResult:
    transformed_factors: torch.Tensor
    transform_method: str
    metrics_by_split: dict[str, dict[str, float]]
    max_abs_correlation: float
    similar_factors: list[dict[str, Any]]
    gate_decision: FactorGateDecision | None
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "transform_method": self.transform_method,
            "metrics_by_split": self.metrics_by_split,
            "max_abs_correlation": float(self.max_abs_correlation),
            "similar_factors": self.similar_factors,
            "gate_decision": self.gate_decision.to_dict() if self.gate_decision is not None else None,
            "status": self.status,
        }


class FactorResearchPipeline:
    def __init__(
        self,
        evaluator: AShareFactorEvaluator | None = None,
        gate_config: FactorGateConfig | None = None,
        enable_gate: bool = True,
        correlation_threshold: float = 0.95,
    ):
        self.evaluator = evaluator or AShareFactorEvaluator()
        self.gate_config = gate_config or FactorGateConfig(max_abs_correlation=correlation_threshold)
        self.enable_gate = enable_gate
        self.correlation_threshold = correlation_threshold

    def run(
        self,
        factors: torch.Tensor,
        raw_data: dict[str, torch.Tensor],
        target_ret: torch.Tensor,
        target_available: torch.Tensor | None,
        trade_dates: list[str],
        ts_codes: list[str],
        store: LocalFactorStore,
        transform_method: str = "raw",
        train_ratio: float = 0.6,
        valid_ratio: float = 0.2,
        label_horizon: int = 1,
        embargo_size: int | None = None,
    ) -> FactorResearchResult:
        governed_target_available = target_available
        if governed_target_available is None:
            candidate = raw_data.get("target_available_mask")
            governed_target_available = candidate if isinstance(candidate, torch.Tensor) else None
        if governed_target_available is None or governed_target_available.shape != target_ret.shape:
            raise ValueError("strict target availability mask is required for factor research")
        governed_target_available = governed_target_available.to(device=target_ret.device, dtype=torch.bool)
        evaluation_target = torch.where(
            governed_target_available & torch.isfinite(target_ret),
            target_ret,
            torch.full_like(target_ret, float("nan")),
        )
        evaluation_raw = dict(raw_data)
        evaluation_raw["target_available_mask"] = governed_target_available
        transformed = preprocess_factor(factors, raw_data, transform_method)
        effective_embargo = max(int(label_horizon), int(embargo_size or 0))
        split_result = split_trade_dates(
            trade_dates,
            train_ratio=train_ratio,
            valid_ratio=valid_ratio,
            embargo_size=effective_embargo,
        )
        metrics_by_split = evaluate_by_splits(
            self.evaluator,
            transformed,
            evaluation_raw,
            evaluation_target,
            trade_dates,
            split_result,
        )

        existing_factor_ids = [record.factor_id for record in store.load_factors()]
        existing_matrices = list(
            load_existing_factor_matrices(
                store,
                existing_factor_ids,
                ts_codes=ts_codes,
                trade_dates=trade_dates,
                device=transformed.device,
            ).values()
        )
        max_corr = max_abs_correlation(transformed, existing_matrices)
        similar = find_similar_factors(
            transformed,
            store,
            ts_codes=ts_codes,
            trade_dates=trade_dates,
            threshold=self.correlation_threshold,
            device=transformed.device,
        )

        gate_decision: FactorGateDecision | None = None
        status = FactorLifecycleStatus.research_evaluated.value
        if self.enable_gate:
            gate_decision = evaluate_factor_gate(metrics_by_split, max_corr, self.gate_config)
            status = gate_decision.status

        return FactorResearchResult(
            transformed_factors=transformed,
            transform_method=transform_method,
            metrics_by_split=metrics_by_split,
            max_abs_correlation=max_corr,
            similar_factors=similar,
            gate_decision=gate_decision,
            status=status,
        )

__all__ = [
    "SUPPORTED_TRANSFORMS",
    "FactorGateConfig",
    "FactorGateDecision",
    "FactorResearchPipeline",
    "FactorResearchResult",
    "cs_winsorize_mad",
    "cs_zscore",
    "evaluate_factor_gate",
    "factor_correlation",
    "factor_correlation_matrix",
    "find_similar_factors",
    "load_existing_factor_matrices",
    "max_abs_correlation",
    "pairwise_correlation_table",
    "neutralize_industry",
    "neutralize_industry_size",
    "neutralize_market_cap",
    "preprocess_factor",
]

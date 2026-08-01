"""Factor research preprocessing, evaluation, correlation, and gate pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import torch

from auto_alpha.research.discovery.evaluation import evaluate_by_splits, split_trade_dates
from auto_alpha.research.factors.store import LocalFactorStore
from auto_alpha.research.factors.store.lifecycle import FactorLifecycleStatus
from auto_alpha.research.formulas.runtime.backtest import AShareFactorEvaluator

from .correlation import find_similar_factors, load_existing_factor_matrices, max_abs_correlation
from .gate import FactorGateConfig, FactorGateDecision, evaluate_factor_gate
from .transforms import preprocess_factor


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

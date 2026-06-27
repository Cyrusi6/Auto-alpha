"""Factor research preprocessing, evaluation, correlation, and gate pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import torch

from evaluation import evaluate_by_splits, split_trade_dates
from factor_store import LocalFactorStore
from model_core.backtest import AShareFactorEvaluator

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
        trade_dates: list[str],
        ts_codes: list[str],
        store: LocalFactorStore,
        transform_method: str = "raw",
        train_ratio: float = 0.6,
        valid_ratio: float = 0.2,
    ) -> FactorResearchResult:
        transformed = preprocess_factor(factors, raw_data, transform_method)
        split_result = split_trade_dates(trade_dates, train_ratio=train_ratio, valid_ratio=valid_ratio)
        metrics_by_split = evaluate_by_splits(
            self.evaluator,
            transformed,
            raw_data,
            target_ret,
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
        status = "candidate"
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

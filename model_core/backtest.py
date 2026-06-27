"""A-share factor evaluation metrics."""

from __future__ import annotations

from dataclasses import dataclass

import torch

from .config import ModelConfig
from .ops import cs_rank


@dataclass(frozen=True)
class FactorEvaluationResult:
    rank_ic_mean: float
    rank_ic_ir: float
    top_bottom_spread: float
    coverage: float
    turnover: float
    score: float


class AShareFactorEvaluator:
    def __init__(self, top_bottom_quantile: float | None = None):
        self.top_bottom_quantile = top_bottom_quantile or ModelConfig.TOP_BOTTOM_QUANTILE

    def evaluate(
        self,
        factors: torch.Tensor,
        raw_data: dict[str, torch.Tensor],
        target_ret: torch.Tensor,
    ) -> FactorEvaluationResult:
        clean_factors = torch.nan_to_num(factors, nan=0.0, posinf=0.0, neginf=0.0)
        clean_target = torch.nan_to_num(target_ret, nan=0.0, posinf=0.0, neginf=0.0)
        valid = torch.isfinite(factors) & torch.isfinite(target_ret)
        coverage = valid.float().mean().item() if valid.numel() else 0.0

        rank_ics = []
        spreads = []
        top_sets: list[set[int]] = []
        for date_idx in range(clean_factors.shape[1]):
            mask = valid[:, date_idx]
            if int(mask.sum().item()) < 2:
                continue
            f_col = clean_factors[mask, date_idx].unsqueeze(1)
            t_col = clean_target[mask, date_idx].unsqueeze(1)
            f_rank = cs_rank(f_col).squeeze(1)
            t_rank = cs_rank(t_col).squeeze(1)
            rank_ics.append(self._corr(f_rank, t_rank))

            n = f_col.shape[0]
            group_size = max(1, int(n * self.top_bottom_quantile))
            order = torch.argsort(f_col.squeeze(1))
            bottom = order[:group_size]
            top = order[-group_size:]
            spreads.append((t_col[top].mean() - t_col[bottom].mean()).item())
            original_indices = torch.where(mask)[0]
            top_sets.append(set(int(original_indices[idx].item()) for idx in top))

        if rank_ics:
            rank_ic_tensor = torch.tensor(rank_ics, dtype=torch.float32)
            rank_ic_mean = rank_ic_tensor.mean().item()
            rank_ic_std = rank_ic_tensor.std(unbiased=False).item()
        else:
            rank_ic_mean = 0.0
            rank_ic_std = 0.0
        rank_ic_ir = rank_ic_mean / (rank_ic_std + 1e-6) if rank_ic_std > 0 else rank_ic_mean
        top_bottom_spread = float(sum(spreads) / len(spreads)) if spreads else 0.0
        turnover = self._turnover(top_sets)
        score = rank_ic_ir + top_bottom_spread - 0.1 * turnover

        return FactorEvaluationResult(
            rank_ic_mean=float(rank_ic_mean),
            rank_ic_ir=float(rank_ic_ir),
            top_bottom_spread=float(top_bottom_spread),
            coverage=float(coverage),
            turnover=float(turnover),
            score=float(score),
        )

    @staticmethod
    def _corr(x: torch.Tensor, y: torch.Tensor) -> float:
        x_centered = x - x.mean()
        y_centered = y - y.mean()
        denom = x_centered.norm() * y_centered.norm()
        if denom.item() <= 1e-12:
            return 0.0
        return float((x_centered * y_centered).sum().item() / denom.item())

    @staticmethod
    def _turnover(top_sets: list[set[int]]) -> float:
        if len(top_sets) <= 1:
            return 0.0
        changes = []
        for prev, curr in zip(top_sets[:-1], top_sets[1:]):
            denom = max(len(prev | curr), 1)
            changes.append(1.0 - len(prev & curr) / denom)
        return float(sum(changes) / len(changes)) if changes else 0.0

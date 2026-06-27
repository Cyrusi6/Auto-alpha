import math

import torch

from model_core.backtest import AShareFactorEvaluator, FactorEvaluationResult


def test_factor_evaluator_returns_finite_metrics():
    factors = torch.tensor([[0.3, 0.4, 0.5], [0.2, 0.1, 0.0], [-0.1, -0.2, -0.3]])
    target = torch.tensor([[0.03, 0.04, 0.05], [0.01, 0.0, -0.01], [-0.02, -0.03, -0.04]])

    result = AShareFactorEvaluator().evaluate(factors, {}, target)

    assert isinstance(result, FactorEvaluationResult)
    for value in result.__dict__.values():
        assert math.isfinite(value)
    assert 0.0 <= result.coverage <= 1.0


def test_aligned_factor_scores_better_than_reversed_factor():
    target = torch.tensor([[0.03, 0.04, 0.05], [0.01, 0.0, -0.01], [-0.02, -0.03, -0.04]])
    aligned = target.clone()
    reversed_factor = -target

    evaluator = AShareFactorEvaluator()
    aligned_result = evaluator.evaluate(aligned, {}, target)
    reversed_result = evaluator.evaluate(reversed_factor, {}, target)

    assert aligned_result.score > reversed_result.score

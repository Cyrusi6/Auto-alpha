import json

from factor_engine.gate import FactorGateConfig, evaluate_factor_gate


def _metrics(**overrides):
    base = {
        "coverage": 1.0,
        "rank_ic_mean": 0.1,
        "rank_ic_ir": 0.2,
        "score": 0.5,
        "turnover": 0.2,
    }
    base.update(overrides)
    return {"train": base, "valid": base, "test": base, "all": base}


def test_factor_gate_passes_normal_metrics():
    decision = evaluate_factor_gate(_metrics(), max_abs_corr=0.1, config=FactorGateConfig())

    assert decision.passed is True
    assert decision.status == "approved"
    json.dumps(decision.to_dict())


def test_factor_gate_rejects_low_coverage():
    decision = evaluate_factor_gate(_metrics(coverage=0.2), max_abs_corr=0.1, config=FactorGateConfig())

    assert decision.passed is False
    assert "coverage_below_threshold" in decision.reasons


def test_factor_gate_rejects_high_turnover():
    decision = evaluate_factor_gate(_metrics(turnover=2.0), max_abs_corr=0.1, config=FactorGateConfig())

    assert decision.passed is False
    assert "turnover_above_threshold" in decision.reasons


def test_factor_gate_rejects_high_correlation():
    decision = evaluate_factor_gate(_metrics(), max_abs_corr=0.99, config=FactorGateConfig(max_abs_correlation=0.95))

    assert decision.passed is False
    assert "correlation_above_threshold" in decision.reasons

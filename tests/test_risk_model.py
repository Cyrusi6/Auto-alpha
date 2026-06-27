import json
import math

import torch

from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from model_core.data_loader import AShareDataLoader
from risk_model import (
    RiskConstraintConfig,
    active_exposure,
    benchmark_weights_from_index_members,
    build_risk_report,
    build_security_exposures,
    check_risk_constraints,
    estimate_return_covariance,
    portfolio_exposure,
    portfolio_volatility,
    tracking_error,
    write_risk_report,
)


def _loader(tmp_path):
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=tmp_path)).sync()
    return AShareDataLoader(data_dir=tmp_path, device="cpu").load_data()


def test_risk_model_exposures_covariance_and_report(tmp_path):
    loader = _loader(tmp_path)
    exposures = build_security_exposures(loader)
    benchmark = benchmark_weights_from_index_members(loader, "000300.SH", "20240104")
    weights = torch.zeros(len(loader.ts_codes))
    weights[:2] = 0.10
    cov = estimate_return_covariance(loader)
    report = build_risk_report(weights, benchmark, loader, "000300.SH", "20240104", covariance=cov)
    json_path, md_path = write_risk_report(report, tmp_path / "risk")

    assert len(exposures) == 3
    assert abs(float(benchmark.sum().item()) - 1.0) < 1e-6
    assert portfolio_exposure(weights, loader).n_positions == 2
    assert active_exposure(weights, benchmark, loader).industry_weights
    assert cov.shape == (3, 3)
    assert math.isfinite(portfolio_volatility(weights, cov))
    assert math.isfinite(tracking_error(weights, benchmark, cov))
    assert json.loads(json_path.read_text(encoding="utf-8"))["metrics"]["tracking_error"] >= 0
    assert "Risk Report" in md_path.read_text(encoding="utf-8")


def test_risk_constraints_detect_violations(tmp_path):
    loader = _loader(tmp_path)
    benchmark = benchmark_weights_from_index_members(loader, "000300.SH", "20240104")
    weights = torch.tensor([0.5, 0.0, 0.0])

    passed, violations, checks = check_risk_constraints(
        weights,
        benchmark,
        loader,
        RiskConstraintConfig(max_weight=0.10, max_industry_active_weight=0.01, max_hhi=0.10),
    )

    assert passed is False
    assert "max_weight" in violations
    assert "max_hhi" in violations
    assert checks["top_weight"] == 0.5

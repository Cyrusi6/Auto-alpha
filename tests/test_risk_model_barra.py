import json
import math

import torch

from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from model_core.data_loader import AShareDataLoader
from risk_model import (
    active_risk_decomposition,
    attribute_active_return,
    build_barra_like_risk_model,
    build_industry_exposures,
    build_risk_model_report,
    build_style_exposures,
    portfolio_factor_exposure,
    portfolio_risk_decomposition,
    write_risk_model_report,
)


def _loader(tmp_path):
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=tmp_path)).sync()
    return AShareDataLoader(data_dir=tmp_path, device="cpu").load_data()


def test_style_and_industry_exposures_are_aligned_and_finite(tmp_path):
    loader = _loader(tmp_path)
    styles = build_style_exposures(loader)
    industry, names, codes = build_industry_exposures(loader)

    assert {"size", "value", "momentum", "volatility", "liquidity", "quality", "growth"} <= set(styles)
    for values in styles.values():
        assert values.shape == (len(loader.ts_codes), len(loader.trade_dates))
        assert torch.isfinite(values).all()
        assert torch.allclose(values.mean(dim=0), torch.zeros(len(loader.trade_dates)), atol=1e-5)
    assert industry.shape[0] == len(loader.ts_codes)
    assert industry.shape[1] == len(names)
    assert torch.allclose(industry.sum(dim=1), torch.ones(len(loader.ts_codes)))
    assert codes.shape[0] == len(loader.ts_codes)


def test_barra_like_factor_model_decomposition_and_attribution(tmp_path):
    loader = _loader(tmp_path)
    risk_model = build_barra_like_risk_model(loader, lookback=3, shrinkage=0.1)
    weights = torch.zeros(len(loader.ts_codes))
    benchmark = torch.ones(len(loader.ts_codes)) / len(loader.ts_codes)
    weights[:2] = 0.10

    exposure = portfolio_factor_exposure(weights, risk_model, date_index=1)
    decomposition = portfolio_risk_decomposition(weights, risk_model, date_index=1)
    active = active_risk_decomposition(weights, benchmark, risk_model, date_index=1)
    attribution = attribute_active_return(
        weights,
        benchmark,
        loader.target_ret[:, 1],
        risk_model.exposure_matrix,
        risk_model.factor_returns,
        date_index=1,
    )
    json_path, md_path = write_risk_model_report(
        build_risk_model_report(
            weights,
            benchmark,
            loader,
            "000300.SH",
            "20240104",
            factor_id="factor_test",
            lookback=3,
            shrinkage=0.1,
            attribution_summary=attribution,
        ),
        tmp_path / "risk",
    )

    assert risk_model.factor_covariance.shape[0] == len(risk_model.exposure_matrix.factor_names)
    assert torch.isfinite(risk_model.factor_covariance).all()
    assert torch.isfinite(risk_model.specific_risk).all()
    assert torch.all(risk_model.specific_risk >= 0)
    assert "size" in exposure
    assert math.isfinite(decomposition["total_risk"])
    assert math.isfinite(active["total_risk"])
    assert "total_active_return" in attribution
    assert json.loads(json_path.read_text(encoding="utf-8"))["factor_risk_contribution"]["total_risk"] >= 0
    assert "Style Exposures" in md_path.read_text(encoding="utf-8")

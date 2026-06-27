import torch

from factor_engine.transforms import (
    SUPPORTED_TRANSFORMS,
    cs_winsorize_mad,
    cs_zscore,
    neutralize_industry,
    neutralize_market_cap,
    preprocess_factor,
)


def test_winsorize_and_zscore_keep_shape_and_finite_values():
    factors = torch.tensor([[1.0, 2.0], [2.0, float("nan")], [100.0, 4.0]])

    winsorized = cs_winsorize_mad(factors)
    zscored = cs_zscore(factors)

    assert winsorized.shape == factors.shape
    assert zscored.shape == factors.shape
    assert torch.isfinite(winsorized).all()
    assert torch.isfinite(zscored).all()


def test_neutralize_market_cap_reduces_size_correlation():
    log_mkt_cap = torch.tensor([[1.0, 1.0], [2.0, 2.0], [3.0, 3.0], [4.0, 4.0]])
    factors = log_mkt_cap * 2.0 + torch.tensor([[0.1, 0.2], [-0.1, -0.2], [0.1, 0.2], [-0.1, -0.2]])

    residual = neutralize_market_cap(factors, log_mkt_cap)
    before = abs(_corr(factors[:, 0], log_mkt_cap[:, 0]))
    after = abs(_corr(residual[:, 0], log_mkt_cap[:, 0]))

    assert after < before


def test_neutralize_industry_group_mean_is_near_zero():
    factors = torch.tensor([[1.0, 2.0], [3.0, 4.0], [10.0, 20.0], [12.0, 24.0]])
    industry_codes = torch.tensor([0, 0, 1, 1])

    residual = neutralize_industry(factors, industry_codes)

    for code in [0, 1]:
        mask = industry_codes == code
        assert torch.allclose(residual[mask].mean(dim=0), torch.zeros(2), atol=1e-6)


def test_preprocess_factor_supports_all_methods():
    factors = torch.tensor([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]])
    raw_data = {
        "log_mkt_cap": torch.tensor([[1.0, 1.0], [2.0, 2.0], [3.0, 3.0]]),
        "industry_codes": torch.tensor([0, 1, 1]),
    }

    for method in SUPPORTED_TRANSFORMS:
        transformed = preprocess_factor(factors, raw_data, method)
        assert transformed.shape == factors.shape
        assert torch.isfinite(transformed).all()


def _corr(x: torch.Tensor, y: torch.Tensor) -> float:
    x_centered = x - x.mean()
    y_centered = y - y.mean()
    denom = x_centered.norm() * y_centered.norm()
    return float((x_centered * y_centered).sum().item() / denom.item())

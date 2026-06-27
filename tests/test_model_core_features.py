import torch

from model_core.factors import AShareFeatureEngineer
from model_core.vocab import FEATURE_NAMES


def test_ashare_feature_engineer_outputs_expected_shape_and_order():
    raw = {
        "open": torch.tensor([[10.0, 11.0, 12.0], [20.0, 19.0, 18.0]]),
        "high": torch.tensor([[11.0, 12.0, 13.0], [21.0, 20.0, 19.0]]),
        "low": torch.tensor([[9.5, 10.5, 11.5], [19.5, 18.5, 17.5]]),
        "close": torch.tensor([[10.5, 11.5, 12.5], [20.5, 19.5, 18.5]]),
        "pre_close": torch.tensor([[10.0, 10.5, 11.5], [20.0, 20.5, 19.5]]),
        "volume": torch.ones((2, 3)),
        "amount": torch.tensor([[100.0, 120.0, 140.0], [200.0, 190.0, 180.0]]),
        "turnover_rate": torch.tensor([[0.5, 0.6, 0.7], [0.3, 0.2, 0.4]]),
        "volume_ratio": torch.tensor([[1.0, 1.1, 1.2], [0.9, 0.8, 0.7]]),
        "pe_ttm": torch.tensor([[5.0, 5.1, 5.2], [7.0, 7.1, 7.2]]),
        "pb": torch.tensor([[0.5, 0.6, 0.7], [1.1, 1.2, 1.3]]),
        "total_mv": torch.tensor([[1000.0, 1010.0, 1020.0], [2000.0, 1990.0, 1980.0]]),
        "roe": torch.tensor([[0.10, 0.11, 0.12], [0.08, 0.07, 0.06]]),
        "revenue_yoy": torch.tensor([[0.20, 0.21, 0.22], [0.01, 0.02, 0.03]]),
    }

    features = AShareFeatureEngineer.compute_features(raw)

    assert features.shape == (2, len(FEATURE_NAMES), 3)
    assert AShareFeatureEngineer.INPUT_DIM == len(FEATURE_NAMES)
    assert torch.isfinite(features).all()
    assert FEATURE_NAMES[0] == "RET_1D"

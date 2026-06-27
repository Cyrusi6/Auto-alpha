import torch

from model_core.ops import OPS_CONFIG, cs_rank, cs_zscore, ts_delta, ts_delay
from model_core.vocab import FEATURE_NAMES, FORMULA_VOCAB


def test_feature_names_are_ashare_fields():
    assert "RET_1D" in FEATURE_NAMES
    assert "TURNOVER_RATE" in FEATURE_NAMES
    assert "ROE" in FEATURE_NAMES
    assert "REVENUE_YOY" in FEATURE_NAMES
    for old_name in ["LIQ_SCORE", "FOMO", "DEV", "PRESSURE"]:
        assert old_name not in FEATURE_NAMES


def test_formula_vocab_encode_decode_roundtrip():
    token_id = FORMULA_VOCAB.encode_name("RET_1D")

    assert FORMULA_VOCAB.token_name(token_id) == "RET_1D"
    assert FORMULA_VOCAB.decode_tokens([token_id]) == ["RET_1D"]


def test_ts_delay_uses_past_values_only():
    x = torch.tensor([[1.0, 2.0, 3.0, 4.0]])

    delayed = ts_delay(x, 1)

    assert delayed.tolist() == [[0.0, 1.0, 2.0, 3.0]]


def test_ts_delta_uses_past_values_only():
    x = torch.tensor([[1.0, 3.0, 6.0, 10.0]])

    delta = ts_delta(x, 1)

    assert delta.tolist() == [[1.0, 2.0, 3.0, 4.0]]


def test_cross_section_ops_preserve_shape():
    x = torch.tensor([[1.0, 2.0], [3.0, 2.0], [2.0, 5.0]])

    ranked = cs_rank(x)
    zscored = cs_zscore(x)

    assert ranked.shape == x.shape
    assert zscored.shape == x.shape
    assert torch.allclose(zscored.mean(dim=0), torch.zeros(2), atol=1e-5)


def test_ops_config_contains_ashare_operators():
    names = {name for name, _, _ in OPS_CONFIG}

    assert {"TS_MEAN3", "CS_RANK", "CS_ZSCORE"} <= names

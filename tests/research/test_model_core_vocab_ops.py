import torch

from auto_alpha.research.formulas.operators import OPS_CONFIG
from auto_alpha.research.formulas.operators import cs_rank
from auto_alpha.research.formulas.operators import cs_zscore
from auto_alpha.research.formulas.operators import get_operator_spec
from auto_alpha.research.formulas.operators import operator_arity
from auto_alpha.research.formulas.operators import operator_complexity
from auto_alpha.research.formulas.operators import operator_lookback
from auto_alpha.research.formulas.operators import ts_corr
from auto_alpha.research.formulas.operators import ts_delta
from auto_alpha.research.formulas.operators import ts_delay
from auto_alpha.research.formulas.operators import ts_max
from auto_alpha.research.formulas.operators import ts_mean
from auto_alpha.research.formulas.operators import ts_min
from auto_alpha.research.formulas.operators import ts_rank
from auto_alpha.research.formulas.operators import ts_std
from auto_alpha.research.formulas.semantics import FEATURE_NAMES
from auto_alpha.research.formulas.semantics import FORMULA_VOCAB


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

    assert {
        "TS_MEAN3",
        "TS_MEAN5",
        "TS_MEAN10",
        "TS_STD5",
        "TS_STD10",
        "TS_RANK5",
        "TS_RANK10",
        "TS_MIN5",
        "TS_MAX5",
        "TS_CORR5",
        "TS_CORR10",
        "CS_RANK",
        "CS_ZSCORE",
    } <= names


def test_new_time_series_ops_preserve_shape_and_use_past_values():
    x = torch.tensor([[1.0, 2.0, 3.0, 4.0, 5.0]])
    y = torch.tensor([[5.0, 4.0, 3.0, 2.0, 1.0]])

    assert ts_mean(x, 5).shape == x.shape
    assert ts_std(x, 5).shape == x.shape
    assert ts_rank(x, 5).shape == x.shape
    assert ts_min(x, 5).shape == x.shape
    assert ts_max(x, 5).shape == x.shape
    assert ts_corr(x, y, 5).shape == x.shape
    assert torch.isfinite(ts_corr(x, y, 5)).all()
    assert abs(ts_mean(x, 5)[0, 0].item() - 0.2) < 1e-6
    assert ts_min(x, 5)[0, 4].item() == 1.0
    assert ts_max(x, 5)[0, 4].item() == 5.0


def test_operator_metadata_helpers():
    token = FORMULA_VOCAB.encode_name("TS_CORR10")
    spec = get_operator_spec("TS_CORR10")

    assert spec.arity == 2
    assert operator_arity(token, FORMULA_VOCAB.operator_offset) == 2
    assert operator_lookback(token, FORMULA_VOCAB.operator_offset) == 10
    assert operator_complexity(token, FORMULA_VOCAB.operator_offset) >= 5

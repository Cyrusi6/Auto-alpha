import torch

from model_core.vm import StackVM
from model_core.vocab import FORMULA_VOCAB


def test_single_feature_token_returns_feature_slice():
    vm = StackVM()
    feat_tensor = torch.arange(2 * FORMULA_VOCAB.feature_count * 3, dtype=torch.float32).reshape(
        2, FORMULA_VOCAB.feature_count, 3
    )
    token = FORMULA_VOCAB.encode_name("RET_1D")

    result = vm.execute([token], feat_tensor)

    assert torch.equal(result, feat_tensor[:, token, :])


def test_delay_formula_executes():
    vm = StackVM()
    feat_tensor = torch.zeros((2, FORMULA_VOCAB.feature_count, 4))
    ret_id = FORMULA_VOCAB.encode_name("RET_1D")
    feat_tensor[:, ret_id, :] = torch.tensor([[1.0, 2.0, 3.0, 4.0], [2.0, 4.0, 6.0, 8.0]])

    result = vm.execute([ret_id, FORMULA_VOCAB.encode_name("DELAY1")], feat_tensor)

    assert result.tolist() == [[0.0, 1.0, 2.0, 3.0], [0.0, 2.0, 4.0, 6.0]]


def test_binary_ops_execute():
    vm = StackVM()
    feat_tensor = torch.ones((2, FORMULA_VOCAB.feature_count, 3))
    left = FORMULA_VOCAB.encode_name("RET_1D")
    right = FORMULA_VOCAB.encode_name("ROE")

    for op_name in ["ADD", "SUB", "MUL", "DIV"]:
        result = vm.execute([left, right, FORMULA_VOCAB.encode_name(op_name)], feat_tensor)
        assert result is not None
        assert result.shape == (2, 3)


def test_validate_and_describe():
    vm = StackVM()
    formula = [FORMULA_VOCAB.encode_name("RET_1D"), FORMULA_VOCAB.encode_name("DELAY1")]

    assert vm.validate(formula) is True
    assert vm.validate([FORMULA_VOCAB.encode_name("RET_1D"), FORMULA_VOCAB.encode_name("ADD")]) is False
    assert vm.describe(formula) == ["RET_1D", "DELAY1"]


def test_validate_with_reason_and_formula_metadata():
    vm = StackVM()
    formula = [
        FORMULA_VOCAB.encode_name("RET_1D"),
        FORMULA_VOCAB.encode_name("RET_5D"),
        FORMULA_VOCAB.encode_name("TS_CORR10"),
    ]

    assert vm.validate_with_reason(formula) == (True, "ok")
    assert vm.validate_with_reason([])[1] == "empty formula"
    assert "stack underflow" in vm.validate_with_reason([FORMULA_VOCAB.encode_name("ADD")])[1]
    assert "multi output stack" in vm.validate_with_reason(
        [FORMULA_VOCAB.encode_name("RET_1D"), FORMULA_VOCAB.encode_name("RET_5D")]
    )[1]
    assert vm.formula_lookback(formula) == 9
    assert vm.formula_complexity(formula) > len(formula)
    assert vm.canonical_formula(formula) == ["RET_1D", "RET_5D", "TS_CORR10"]
    assert "lookback=9" in vm.explain_formula(formula)

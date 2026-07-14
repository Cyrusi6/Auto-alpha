from __future__ import annotations

import pytest

from validation_campaign_store.scheduler import run_validation_shards


def _kwargs(tmp_path):
    return {
        "store_dir": tmp_path / "store",
        "data_dir": str(tmp_path),
        "factor_store_dir": str(tmp_path / "factors"),
        "output_dir": tmp_path / "output",
        "validation_campaign_id": "task054",
        "shard_count": 4,
        "max_candidates_per_shard": 5,
        "validation_policy": "task054_production_engineering_v1",
        "train_size": 756,
        "validation_size": 126,
        "test_size": 126,
        "step_size": 126,
        "label_horizon": 2,
        "research_end_date": "20240530",
        "device": "cuda",
        "task_054a_replay": True,
    }


def test_task054_scheduler_rejects_policy_override(tmp_path):
    kwargs = _kwargs(tmp_path)
    kwargs["train_size"] = 755
    with pytest.raises(ValueError, match="production_policy_parameter_override"):
        run_validation_shards(**kwargs)


def test_task054_scheduler_rejects_contract_override(tmp_path):
    kwargs = _kwargs(tmp_path)
    kwargs["research_end_date"] = "20240531"
    with pytest.raises(RuntimeError, match="task054_research_contract_mismatch"):
        run_validation_shards(**kwargs)


def test_recursive_formula_lookback_composes_nested_windows():
    from model_core.vm import StackVM
    from model_core.vocab import FORMULA_VOCAB

    vm = StackVM()
    tokens = [
        FORMULA_VOCAB.encode_name("RET_1D"),
        FORMULA_VOCAB.encode_name("TS_MEAN10"),
        FORMULA_VOCAB.encode_name("TS_RANK10"),
    ]
    assert vm.formula_lookback(tokens, {"RET_1D": 1}) == 19

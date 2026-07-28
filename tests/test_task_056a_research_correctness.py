import json
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from alpha_factory import AlphaCampaignConfig, AlphaFactoryRunner
from alpha_experiment_store.leaderboard import build_leaderboard
from backtest import AShareBacktestSimulator, AShareTradingRules
from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from evaluation import split_trade_dates
from factor_store import FactorRecord, has_positive_oos_evidence
from matrix_store.strict_engineering import _build_target
from model_core.backtest import AShareFactorEvaluator
from model_core.data_loader import AShareDataLoader
from neural_search.reward import formula_reward_from_research_result
from research.composite import register_composite_factor


def _sample_data(tmp_path):
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=tmp_path)).sync(validate=True)
    return tmp_path


def test_raw_target_tail_is_nan_and_unavailable(tmp_path):
    loader = AShareDataLoader(_sample_data(tmp_path), device="cpu", label_horizon=2).load_data()

    assert not loader.target_available[:, -2:].any()
    assert torch.isnan(loader.target_ret[:, -2:]).all()
    assert torch.equal(loader.raw_data_cache["target_available_mask"], loader.target_available)


def test_strict_next_open_target_tail_is_nan_and_unavailable():
    adjusted_open = np.array([[10.0, 11.0, 12.0, 13.0, 14.0]], dtype=np.float32)
    valid = np.ones_like(adjusted_open, dtype=np.bool_)
    target, available = _build_target(
        adjusted_open,
        valid,
        valid,
        valid,
        valid,
        valid,
        valid,
        np.zeros_like(valid),
        np.zeros_like(valid),
    )

    assert available[:, :-2].all()
    assert not available[:, -2:].any()
    assert np.isnan(target[:, -2:]).all()


def test_factor_quality_uses_validation_eligible_cells_only():
    factors = torch.tensor([[1.0, 9.0], [2.0, 9.0]])
    target = torch.tensor([[0.1, float("nan")], [0.2, float("nan")]])
    eligible = torch.tensor([[True, False], [True, False]])

    result = AShareFactorEvaluator().evaluate(
        factors,
        {"validation_common_cells": eligible},
        target,
    )

    assert result.coverage == 1.0
    assert result.valid_observation_count == 2
    assert result.evaluable_date_count == 1


def test_embargo_reserves_at_least_label_horizon():
    dates = [f"202401{day:02d}" for day in range(1, 21)]
    split = split_trade_dates(dates, embargo_size=2)

    assert len(split.embargo_dates) == 4
    assert max(split.train_dates) < min(split.valid_dates)
    assert max(split.valid_dates) < min(split.test_dates)
    assert dates.index(min(split.valid_dates)) - dates.index(max(split.train_dates)) > 2
    assert dates.index(min(split.test_dates)) - dates.index(max(split.valid_dates)) > 2


def test_neural_reward_has_no_status_bonus():
    assert formula_reward_from_research_result(SimpleNamespace(status="approved", score=0.2)) == 0.2
    assert formula_reward_from_research_result(SimpleNamespace(status="validation_candidate", score=0.2)) == 0.2
    assert formula_reward_from_research_result(SimpleNamespace(status="skipped_existing", score=5.0)) == 0.0


def test_composite_never_auto_enters_validation_candidate(tmp_path):
    from factor_store import LocalFactorStore

    store = LocalFactorStore(tmp_path / "store")
    info = register_composite_factor(
        store,
        ["factor_a", "factor_b"],
        ["000001.SZ"],
        ["20240102"],
        torch.ones((1, 1)),
        "equal_weight",
    )
    record = next(item for item in store.load_factors() if item.factor_id == info["factor_id"])

    assert record.status == "composite_unvalidated"
    assert has_positive_oos_evidence(record) is False


def test_legacy_approved_without_positive_oos_is_not_validation_ready():
    record = FactorRecord(
        factor_id="factor_legacy",
        formula=["RET_1D"],
        formula_tokens=[0],
        formula_hash="hash_legacy",
        feature_version="ashare_features_v1",
        operator_version="ashare_ops_v1",
        lookback_days=1,
        created_at="2026-07-28T00:00:00Z",
        status="approved",
        metrics={"score": 99.0},
    )

    leaderboard = build_leaderboard([record])

    assert leaderboard[0].validation_ready is False
    assert leaderboard[0].reason == "factor_status_not_validation_candidate"


def test_production_research_rejects_sample_cpu_and_non_pit_before_data_open(tmp_path, monkeypatch):
    loader = AShareDataLoader(data_dir=tmp_path / "missing", device="cpu", production_research=True)
    monkeypatch.setattr(loader, "_read_jsonl", lambda *_args, **_kwargs: pytest.fail("JSONL fallback was opened"))

    with pytest.raises(RuntimeError, match="cuda_device_required"):
        loader.load_data()

    with pytest.raises(RuntimeError, match="sample_or_lenient_provider_forbidden"):
        AlphaFactoryRunner(
            AlphaCampaignConfig(
                campaign_name="forbidden",
                data_dir=str(tmp_path / "data"),
                output_dir=str(tmp_path / "out"),
                factor_store_dir=str(tmp_path / "store"),
                production_research=True,
                provider="sample",
            )
        )


def test_financial_adjustment_and_corporate_action_are_not_available_early(tmp_path):
    data_dir = _sample_data(tmp_path / "data")
    with (data_dir / "financial_features" / "records.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "ts_code": "000001.SZ",
            "report_period": "20231231",
            "announce_date": "20240104",
            "roe": 9.99,
            "revenue_yoy": 9.99,
        }) + "\n")
    with (data_dir / "adjustment_factors" / "records.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"ts_code": "000001.SZ", "trade_date": "20240104", "adj_factor": 99.0}) + "\n")
    with (data_dir / "corporate_actions" / "records.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "ts_code": "000001.SZ",
            "ann_date": "20240104",
            "imp_ann_date": "20240104",
            "ex_date": "20240103",
            "pay_date": "20240104",
            "div_proc": "实施",
            "raw_status": "实施",
            "stk_bo_rate": 1.0,
            "source": "test",
        }) + "\n")

    loader = AShareDataLoader(
        data_dir,
        device="cpu",
        point_in_time=True,
        as_of_date="20240103",
        corporate_action_aware=True,
    ).load_data()
    stock = loader.ts_codes.index("000001.SZ")
    jan2 = loader.trade_dates.index("20240102")
    jan3 = loader.trade_dates.index("20240103")
    jan4 = loader.trade_dates.index("20240104")

    assert loader.raw_data_cache["roe"][stock, jan2].item() != pytest.approx(9.99)
    assert loader.raw_data_cache["roe"][stock, jan3].item() != pytest.approx(9.99)
    assert loader.raw_data_cache["roe"][stock, jan4].item() == pytest.approx(9.99)
    assert loader.raw_data_cache["adj_factor"][stock, jan3].item() != pytest.approx(99.0)
    assert loader.raw_data_cache["adj_factor"][stock, jan4].item() == pytest.approx(99.0)
    assert loader.raw_data_cache["stock_distribution_ratio"][stock, jan3].item() == pytest.approx(0.0)


def test_weight_drift_and_turnover_are_independently_recomputable():
    loader = SimpleNamespace(
        ts_codes=["A", "B"],
        trade_dates=["20240102", "20240103", "20240104"],
        raw_data_cache={
            "open": torch.tensor([[10.0, 20.0, 20.0], [10.0, 10.0, 10.0]]),
            "volume": torch.full((2, 3), 10_000_000.0),
            "is_suspended": torch.zeros((2, 3)),
            "active_mask": torch.ones((2, 3)),
            "up_limit": torch.full((2, 3), 100.0),
            "down_limit": torch.full((2, 3), 1.0),
        },
        target_ret=torch.zeros((2, 3)),
    )
    factors = torch.ones((2, 3))

    result = AShareBacktestSimulator(
        top_n=2,
        max_weight=0.5,
        trading_rules=AShareTradingRules(max_position_weight=0.5, volume_limit_ratio=1.0),
    ).simulate(factors, loader)
    prior_post = result.rebalance_audit[0]["post_trade_weights"]
    drifted_pre = result.rebalance_audit[1]["pre_trade_weights"]
    post = result.rebalance_audit[1]["post_trade_weights"]

    assert drifted_pre["A"] > prior_post["A"]
    assert drifted_pre["B"] < prior_post["B"]
    independent_turnover = sum(abs(post.get(code, 0.0) - drifted_pre.get(code, 0.0)) for code in loader.ts_codes)
    assert result.rebalance_audit[1]["turnover"] == pytest.approx(independent_turnover)
    assert result.snapshots[1].turnover == pytest.approx(independent_turnover)

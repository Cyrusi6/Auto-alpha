from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from task_055_a.policy import BASELINE, PREREGISTERED_SCENARIOS, stable_top_n_equal_weight
from task_055_a.simulator import EventLedgerSimulator


def _market(open_price, close_price=None, adv=None, volume=None):
    open_array = np.asarray(open_price, dtype=float)
    days, assets = open_array.shape
    payload = {
        "dates": [f"d{index}" for index in range(days)],
        "assets": [f"s{index}" for index in range(assets)],
        "open": open_array,
        "close": open_array if close_price is None else np.asarray(close_price, dtype=float),
        "adv": np.full_like(open_array, 100_000.0) if adv is None else np.asarray(adv, dtype=float),
    }
    if volume is not None:
        payload["volume"] = np.asarray(volume, dtype=float)
    return payload


def _all_true(shape):
    return {"tradable": np.ones(shape, dtype=bool)}


def test_hand_calculated_buy_cost_cash_and_open_nav():
    policy = replace(
        BASELINE,
        top_n=1,
        commission_rate=0.001,
        minimum_commission=0.0,
        transfer_fee_rate=0.0,
        stamp_duty_rate=0.0,
        fee_schedule_id="explicit_fixture_rates_v1",
        slippage_bps=10.0,
        impact_bps=20.0,
    )
    market = _market([[10.0], [11.0], [12.0]], close_price=[[10.0], [11.0], [12.0]])
    scores = np.ones((3, 1))
    result = EventLedgerSimulator(policy, initial_cash=10_000.0).run(
        market, scores, masks=_all_true((3, 1))
    )

    fill = result.fills[0]
    assert fill.filled_shares == 900
    assert fill.notional == 9_900.0
    assert fill.commission == pytest.approx(9.9)
    assert fill.slippage == pytest.approx(9.9)
    assert fill.impact == pytest.approx(19.8)
    assert fill.total_cost == pytest.approx(39.6)
    assert result.nav[1].open_pre == pytest.approx(10_000.0)
    assert result.nav[1].open_post == pytest.approx(9_960.4)
    assert result.final_positions["s0"]["total"] == 900
    assert result.nav[2].open_to_open_return == pytest.approx(10_860.4 / 9_960.4 - 1.0)


def test_close_decides_shares_and_cheaper_next_open_never_scales_up():
    policy = replace(BASELINE, top_n=1, minimum_commission=0.0, commission_rate=0.0,
                     transfer_fee_rate=0.0, stamp_duty_rate=0.0,
                     fee_schedule_id="explicit_fixture_rates_v1",
                     slippage_bps=0.0, impact_bps=0.0)
    market = _market([[10.0], [1.0]], close_price=[[10.0], [1.0]])
    result = EventLedgerSimulator(policy, initial_cash=10_000.0).run(
        market, np.ones((2, 1)), masks=_all_true((2, 1))
    )
    assert result.orders[0].requested_shares == 1_000
    assert result.fills[0].filled_shares == 1_000
    assert result.final_cash.available == pytest.approx(9_000.0)


def test_t_plus_one_lots_block_same_day_sell_and_settle_next_day():
    policy = replace(BASELINE, top_n=1, minimum_commission=0.0, commission_rate=0.0,
                     transfer_fee_rate=0.0, stamp_duty_rate=0.0,
                     fee_schedule_id="explicit_fixture_rates_v1",
                     slippage_bps=0.0, impact_bps=0.0)
    market = _market([[10.0], [10.0], [10.0]])
    scores = np.array([[1.0], [np.nan], [np.nan]])
    masks = {
        "select": np.array([[True], [False], [False]]),
        "buy": np.ones((3, 1), dtype=bool),
        "sell": np.ones((3, 1), dtype=bool),
    }
    result = EventLedgerSimulator(policy, initial_cash=10_000.0).run(market, scores, masks=masks)
    assert [fill.side for fill in result.fills] == ["BUY", "SELL"]
    assert result.fills[1].execution_index == 2
    assert result.final_cash.unsettled_receivable == pytest.approx(10_000.0)
    assert result.final_cash.available == pytest.approx(0.0)


def test_lagged_adv_capacity_does_not_read_execution_day_volume_or_adv():
    policy = replace(BASELINE, top_n=1, adv_participation=0.10, minimum_commission=0.0,
                     commission_rate=0.0, transfer_fee_rate=0.0,
                     slippage_bps=0.0, impact_bps=0.0)
    base = _market(
        [[10.0], [10.0]],
        adv=[[2_000.0], [1.0]],
        volume=[[2_000.0], [1.0]],
    )
    mutated = dict(base)
    mutated["adv"] = np.array([[2_000.0], [10_000_000.0]])
    mutated["volume"] = np.array([[2_000.0], [10_000_000.0]])
    kwargs = {"scores": np.ones((2, 1)), "masks": _all_true((2, 1))}
    first = EventLedgerSimulator(policy, initial_cash=100_000.0).run(base, **kwargs)
    second = EventLedgerSimulator(policy, initial_cash=100_000.0).run(mutated, **kwargs)
    assert first.fills[0].filled_shares == 200
    assert second.fills[0].filled_shares == 200
    assert first.fills[0].status == "PARTIAL"


def test_future_scores_cannot_change_an_already_decided_next_open_order():
    policy = replace(BASELINE, top_n=1, minimum_commission=0.0, commission_rate=0.0,
                     transfer_fee_rate=0.0, slippage_bps=0.0, impact_bps=0.0)
    market = _market([[10.0, 10.0], [10.0, 10.0], [10.0, 10.0]])
    baseline_scores = np.array([[2.0, 1.0], [1.0, 2.0], [1.0, 2.0]])
    leaked_scores = baseline_scores.copy()
    leaked_scores[1:] = [[1e9, -1e9], [-1e9, 1e9]]
    first = EventLedgerSimulator(policy, initial_cash=10_000.0).run(
        market, baseline_scores, masks=_all_true((3, 2))
    )
    second = EventLedgerSimulator(policy, initial_cash=10_000.0).run(
        market, leaked_scores, masks=_all_true((3, 2))
    )
    assert first.orders[0].to_dict() == second.orders[0].to_dict()
    assert first.fills[0].to_dict() == second.fills[0].to_dict()


def test_unknown_masks_fail_closed_and_emit_rejection():
    market = _market([[10.0], [10.0]])
    result = EventLedgerSimulator(replace(BASELINE, top_n=1), initial_cash=10_000.0).run(
        market,
        np.ones((2, 1)),
        masks={"tradable": np.array([[True], [None]], dtype=object)},
    )
    assert not result.fills
    assert result.rejections[0].reason == "mask_unknown_or_false"


def test_stable_top20_equal_weight_ties_and_five_scenarios():
    assets = [f"s{index:02d}" for index in range(25)][::-1]
    weights = stable_top_n_equal_weight(np.ones(25), assets, np.ones(25), top_n=20)
    selected = {assets[index] for index in np.flatnonzero(weights)}
    assert selected == {f"s{index:02d}" for index in range(20)}
    assert np.allclose(weights[weights > 0], 0.05)
    assert tuple(PREREGISTERED_SCENARIOS) == (
        "baseline",
        "zero_cost_accounting",
        "double_modeled_cost",
        "participation_5_percent",
        "aum_10_million",
    )


def test_corporate_action_updates_integer_shares_and_cash_before_open_nav():
    policy = replace(BASELINE, top_n=0)
    market = _market([[10.0], [5.0]])
    result = EventLedgerSimulator(policy, initial_cash=0.0).run(
        market,
        np.full((2, 1), np.nan),
        masks={
            "select": np.ones((2, 1), dtype=bool),
            "buy": np.ones((2, 1), dtype=bool),
            "sell": np.array([[True], [False]]),
        },
        initial_positions={"s0": 100},
        corporate_actions=[{
            "action_id": "ca1",
            "asset": "s0",
            "effective_index": 1,
            "share_ratio": 2.0,
            "cash_dividend_per_share": 1.0,
            "pay_index": 1,
        }],
    )
    assert result.final_positions["s0"]["total"] == 200
    assert result.final_cash.available == pytest.approx(100.0)
    assert result.nav[1].open_pre == pytest.approx(1_100.0)

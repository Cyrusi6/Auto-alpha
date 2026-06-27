from backtest import AShareCostModel, AShareTradingRules


def test_cost_model_buy_sell_and_zero_value():
    model = AShareCostModel()

    buy = model.estimate("BUY", 10000.0)
    sell = model.estimate("SELL", 10000.0)
    zero = model.estimate("BUY", 0.0)

    assert buy.stamp_duty == 0.0
    assert sell.stamp_duty > 0.0
    assert zero.total == 0.0


def test_trading_rules_lot_t_plus_one_and_limits():
    rules = AShareTradingRules(lot_size=100, max_position_weight=0.10)

    assert rules.round_shares(345.0) == 300
    assert rules.is_t_plus_one_sell_allowed(1, 1) is False
    assert rules.is_t_plus_one_sell_allowed(1, 2) is True
    assert rules.can_buy(10.0, is_suspended=True)[0] is False
    assert rules.can_sell(10.0, is_suspended=True)[0] is False
    assert rules.can_buy(10.0, is_limit_up=True)[0] is False
    assert rules.can_sell(10.0, is_limit_down=True)[0] is False
    assert rules.clamp_weight(0.25) == 0.10
    assert rules.clamp_weight(-0.1) == 0.0

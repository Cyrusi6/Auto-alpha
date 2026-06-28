import json
from dataclasses import asdict

from data_pipeline.ashare import AShareDataConfig, SampleAShareDataProvider
from data_pipeline.ashare.validators import validate_daily_bar


FORBIDDEN_TERMS = ["address", "liquidity", "fdv", "solana", "birdeye", "dexscreener", "meme"]


def test_sample_provider_returns_all_dataset_types():
    provider = SampleAShareDataProvider()
    config = AShareDataConfig(provider="sample")

    assert provider.fetch_securities(config)
    assert provider.fetch_trade_calendar(config)
    assert provider.fetch_daily_bars(config)
    assert provider.fetch_daily_basic(config)
    assert provider.fetch_financial_features(config)
    assert provider.fetch_daily_limits(config)
    assert provider.fetch_adjustment_factors(config)
    assert provider.fetch_index_members(config)
    assert provider.fetch_corporate_actions(config)


def test_sample_provider_contains_required_securities():
    securities = SampleAShareDataProvider().fetch_securities(AShareDataConfig(provider="sample"))

    assert {security.ts_code for security in securities} >= {"000001.SZ", "600000.SH", "830000.BJ"}


def test_sample_provider_daily_bars_are_valid():
    bars = SampleAShareDataProvider().fetch_daily_bars(AShareDataConfig(provider="sample"))

    for bar in bars:
        validate_daily_bar(bar)


def test_sample_provider_financial_features_have_no_lookahead():
    provider = SampleAShareDataProvider()
    config = AShareDataConfig(provider="sample")
    max_trade_date = max(bar.trade_date for bar in provider.fetch_daily_bars(config))

    for feature in provider.fetch_financial_features(config):
        assert feature.is_available_on(max_trade_date)


def test_sample_provider_payload_excludes_old_business_terms():
    provider = SampleAShareDataProvider()
    config = AShareDataConfig(provider="sample")
    records = [
        *provider.fetch_securities(config),
        *provider.fetch_trade_calendar(config),
        *provider.fetch_daily_bars(config),
        *provider.fetch_daily_basic(config),
        *provider.fetch_financial_features(config),
        *provider.fetch_daily_limits(config),
        *provider.fetch_adjustment_factors(config),
        *provider.fetch_index_members(config),
        *provider.fetch_corporate_actions(config),
    ]
    payload = json.dumps([asdict(record) for record in records], ensure_ascii=False).lower()

    for forbidden in FORBIDDEN_TERMS:
        assert forbidden not in payload


def test_sample_provider_market_constraint_data_has_expected_scenarios():
    provider = SampleAShareDataProvider()
    config = AShareDataConfig(provider="sample", index_codes=("000300.SH",))

    limits = provider.fetch_daily_limits(config)
    factors = provider.fetch_adjustment_factors(config)
    members = provider.fetch_index_members(config)

    assert any(limit.ts_code == "000001.SZ" and limit.trade_date == "20240103" and limit.up_limit == 9.55 for limit in limits)
    assert any(limit.ts_code == "600000.SH" and limit.trade_date == "20240103" and limit.down_limit == 6.70 for limit in limits)
    assert any(record.adj_factor != 1.0 for record in factors)
    assert {record.index_code for record in members} == {"000300.SH"}


def test_sample_provider_corporate_actions_cover_cash_stock_and_proposals():
    records = SampleAShareDataProvider().fetch_corporate_actions(AShareDataConfig(provider="sample"))

    assert any(record.cash_div and record.div_proc == "实施" for record in records)
    assert any((record.stk_div or 0.0) > 0 or (record.stk_bo_rate or 0.0) > 0 for record in records)
    assert any(record.cash_div and ((record.stk_div or 0.0) > 0 or (record.stk_co_rate or 0.0) > 0) for record in records)
    assert any(record.div_proc != "实施" for record in records)

import pytest

from data_pipeline.ashare.schema import DailyBar, FinancialFeature
from data_pipeline.ashare.validators import (
    ensure_no_financial_lookahead,
    is_valid_ts_code,
    is_valid_yyyymmdd,
    validate_daily_bar,
)


def make_bar(**overrides):
    values = {
        "trade_date": "20240102",
        "ts_code": "000001.SZ",
        "open": 10.0,
        "high": 11.0,
        "low": 9.5,
        "close": 10.5,
        "pre_close": 10.0,
        "volume": 100000.0,
        "amount": 1000000.0,
    }
    values.update(overrides)
    return DailyBar(**values)


@pytest.mark.parametrize(
    ("ts_code", "expected"),
    [
        ("000001.SZ", True),
        ("600000.SH", True),
        ("830000.BJ", True),
        ("000001.US", False),
        ("000001", False),
        ("abc", False),
    ],
)
def test_is_valid_ts_code(ts_code, expected):
    assert is_valid_ts_code(ts_code) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("20240229", True),
        ("20240230", False),
        ("2024-02-29", False),
        ("2024012", False),
    ],
)
def test_is_valid_yyyymmdd(value, expected):
    assert is_valid_yyyymmdd(value) is expected


def test_financial_feature_availability_uses_announce_date():
    feature = FinancialFeature(
        ts_code="000001.SZ",
        report_period="20231231",
        announce_date="20240320",
    )

    assert feature.is_available_on("20240320") is True
    assert feature.is_available_on("20240321") is True
    assert feature.is_available_on("20240319") is False


def test_ensure_no_financial_lookahead_rejects_future_announcement():
    feature = FinancialFeature(
        ts_code="000001.SZ",
        report_period="20231231",
        announce_date="20240320",
    )

    with pytest.raises(ValueError, match="announce_date=20240320"):
        ensure_no_financial_lookahead(feature, "20240319")


def test_validate_daily_bar_accepts_valid_bar():
    validate_daily_bar(make_bar())


def test_validate_daily_bar_rejects_high_below_low():
    with pytest.raises(ValueError, match="high"):
        validate_daily_bar(make_bar(high=9.0, low=10.0))


def test_validate_daily_bar_rejects_negative_price():
    with pytest.raises(ValueError, match="open"):
        validate_daily_bar(make_bar(open=-1.0))


def test_validate_daily_bar_rejects_invalid_ts_code():
    with pytest.raises(ValueError, match="ts_code"):
        validate_daily_bar(make_bar(ts_code="000001.US"))

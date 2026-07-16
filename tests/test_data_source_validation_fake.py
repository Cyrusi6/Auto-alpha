import json

import pytest

from data_pipeline.ashare import AShareDataConfig
from data_source_validation.fake_tushare import FakeTushareHttpClient
from data_source_validation.probe import probe_provider
from data_pipeline.ashare.providers.tushare_client import (
    TushareNetworkError,
    TusharePermissionError,
    TushareRateLimitError,
    TushareSchemaError,
)


def test_fake_tushare_success_returns_rows_and_metadata():
    client = FakeTushareHttpClient("success")

    envelope = client.post_with_metadata("daily", params={"start_date": "20240102"}, fields="ts_code,trade_date,vol")

    assert envelope.api_name == "daily"
    assert envelope.item_count == 3
    assert envelope.records[0]["vol"] == 123456.7
    assert client.calls[0]["params"]["start_date"] == "20240102"


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("permission_denied", TusharePermissionError),
        ("rate_limited", TushareRateLimitError),
        ("malformed_payload", TushareSchemaError),
        ("network_error", TushareNetworkError),
    ],
)
def test_fake_tushare_error_scenarios_are_structured(scenario, expected):
    client = FakeTushareHttpClient(scenario)

    with pytest.raises(expected):
        client.post("stock_basic")


def test_probe_fake_scenarios_do_not_leak_token():
    config = AShareDataConfig(
        provider="tushare",
        tushare_token="very-secret-token",
        start_date="20240102",
        end_date="20240104",
    )

    success = probe_provider(config, fake_scenario="success", datasets=["daily_bars"])
    missing = probe_provider(config, fake_scenario="missing_fields", datasets=["daily_bars"])
    denied = probe_provider(config, fake_scenario="permission_denied", datasets=["daily_bars"])

    payload = json.dumps([item.to_dict() for item in [*success, *missing, *denied]], ensure_ascii=False)
    assert success[0].status == "OK"
    assert missing[0].diagnostic_code == "missing_fields"
    assert denied[0].diagnostic_code == "permission_denied"
    assert "very-secret-token" not in payload
    assert success[0].credential_present is True
    assert success[0].credential_source_type == "synthetic_fixture"

import gzip
import json

import pytest

from data_pipeline.ashare import AShareDataConfig
from data_pipeline.ashare.providers.tushare_client import (
    TushareApiError,
    TusharePermissionError,
    TushareRateLimitError,
    TushareSchemaError,
    TushareHttpClient,
)


class FakeResponse:
    def __init__(self, payload, headers=None, raw=None):
        self.payload = payload
        self.headers = headers or {}
        self.raw = raw

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        if self.raw is not None:
            return self.raw
        return json.dumps(self.payload).encode("utf-8")


def test_tushare_http_client_posts_payload_and_maps_rows():
    captured = {}

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse(
            {
                "code": 0,
                "data": {
                    "fields": ["ts_code", "close"],
                    "items": [["000001.SZ", 10.5]],
                },
            }
        )

    config = AShareDataConfig(
        tushare_token="test-token",
        tushare_timeout_seconds=7,
        tushare_retry_count=1,
    )
    client = TushareHttpClient(config, urlopen=fake_urlopen)

    rows = client.post("daily", params={"start_date": "20240101"}, fields=["ts_code", "close"])

    assert rows == [{"ts_code": "000001.SZ", "close": 10.5}]
    assert captured["timeout"] == 7
    assert captured["payload"] == {
        "api_name": "daily",
        "token": "test-token",
        "params": {"start_date": "20240101"},
        "fields": "ts_code,close",
    }


def test_tushare_http_client_post_with_metadata_redacts_params_token():
    def fake_urlopen(request, timeout):
        return FakeResponse(
            {
                "code": 0,
                "msg": "",
                "data": {
                    "fields": ["ts_code", "close"],
                    "items": [["000001.SZ", 10.5]],
                },
            }
        )

    client = TushareHttpClient(AShareDataConfig(tushare_token="secret-token", tushare_retry_count=1), urlopen=fake_urlopen)

    envelope = client.post_with_metadata("daily", params={"start_date": "20240101"}, fields=["ts_code", "close"])

    assert envelope.api_name == "daily"
    assert envelope.params_without_token == {"start_date": "20240101"}
    assert envelope.requested_fields == "ts_code,close"
    assert envelope.response_fields == ["ts_code", "close"]
    assert envelope.records == [{"ts_code": "000001.SZ", "close": 10.5}]
    assert envelope.item_count == 1
    assert "secret-token" not in json.dumps(envelope.to_dict())


def test_tushare_http_client_decodes_gzip_response():
    payload = {
        "code": 0,
        "msg": "",
        "data": {
            "fields": ["ts_code", "close"],
            "items": [["000001.SZ", 10.5]],
        },
    }

    def fake_urlopen(request, timeout):
        assert request.headers["Accept-encoding"] == "gzip"
        return FakeResponse(payload, headers={"Content-Encoding": "gzip"}, raw=gzip.compress(json.dumps(payload).encode("utf-8")))

    client = TushareHttpClient(AShareDataConfig(tushare_token="secret-token", tushare_retry_count=1), urlopen=fake_urlopen)

    assert client.post("daily", fields=["ts_code", "close"]) == [{"ts_code": "000001.SZ", "close": 10.5}]


def test_tushare_http_client_raises_api_error_on_nonzero_code():
    def fake_urlopen(request, timeout):
        return FakeResponse({"code": 2002, "msg": "bad token"})

    config = AShareDataConfig(tushare_token="test-token", tushare_retry_count=1)
    client = TushareHttpClient(config, urlopen=fake_urlopen)

    with pytest.raises(TushareApiError, match="bad token"):
        client.post("stock_basic")


def test_tushare_http_client_maps_permission_and_rate_limit_errors():
    config = AShareDataConfig(tushare_token="test-token", tushare_retry_count=1)

    def permission_urlopen(request, timeout):
        return FakeResponse({"code": 2002, "msg": "权限不足"})

    def rate_urlopen(request, timeout):
        return FakeResponse({"code": 2003, "msg": "访问次数超过限制"})

    with pytest.raises(TusharePermissionError):
        TushareHttpClient(config, urlopen=permission_urlopen).post("stock_basic")
    with pytest.raises(TushareRateLimitError):
        TushareHttpClient(config, urlopen=rate_urlopen).post("stock_basic")


def test_tushare_http_client_raises_schema_error_on_malformed_data():
    def fake_urlopen(request, timeout):
        return FakeResponse({"code": 0, "data": {"fields": "ts_code", "items": []}})

    client = TushareHttpClient(AShareDataConfig(tushare_token="test-token", tushare_retry_count=1), urlopen=fake_urlopen)

    with pytest.raises(TushareSchemaError):
        client.post("daily")


def test_tushare_http_client_requires_token():
    client = TushareHttpClient(AShareDataConfig(tushare_token=None))

    with pytest.raises(ValueError, match="TUSHARE_TOKEN"):
        client.post("stock_basic")

import json

import pytest

from data_pipeline.ashare import AShareDataConfig
from data_pipeline.ashare.providers.tushare_client import TushareApiError, TushareHttpClient


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
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


def test_tushare_http_client_raises_api_error_on_nonzero_code():
    def fake_urlopen(request, timeout):
        return FakeResponse({"code": 2002, "msg": "bad token"})

    config = AShareDataConfig(tushare_token="test-token", tushare_retry_count=1)
    client = TushareHttpClient(config, urlopen=fake_urlopen)

    with pytest.raises(TushareApiError, match="bad token"):
        client.post("stock_basic")


def test_tushare_http_client_requires_token():
    client = TushareHttpClient(AShareDataConfig(tushare_token=None))

    with pytest.raises(ValueError, match="TUSHARE_TOKEN"):
        client.post("stock_basic")

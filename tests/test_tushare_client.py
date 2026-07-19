import json

import pytest

from data_pipeline.ashare import AShareDataConfig
from data_pipeline.ashare.network_capability import _validated_task055k_execution_capability
from data_pipeline.ashare.providers.tushare_client import (
    TushareApiError,
    TushareHttpClient,
    TushareNetworkError,
    TusharePermissionError,
    TushareRateLimitError,
    TushareSchemaError,
    parse_tushare_response_payload,
    serialize_tushare_request,
)
from data_pipeline.ashare.request_identity import TushareRequestIdentity
from data_pipeline.ashare.request_normalization import tushare_request_fingerprint
from task_055_f.transport import CANONICAL_ORIGIN, transport_identity


def _identity(api_name="daily", params=None, fields=None):
    params = dict(params or {})
    fields = list(fields or ["ts_code", "close"])
    return TushareRequestIdentity(
        request_fingerprint=tushare_request_fingerprint(api_name, params=params, fields=fields),
        transport_identity=transport_identity(api_name, params, fields),
        evidence_use_identity="e" * 64,
    )


def _parse(payload, *, api_name="daily", params=None, fields=None):
    params = dict(params or {})
    fields = list(fields or ["ts_code", "close"])
    return parse_tushare_response_payload(
        payload,
        api_name=api_name,
        params=params,
        requested_fields=fields,
        identity=_identity(api_name, params, fields),
        duration_seconds=0.25,
        endpoint=CANONICAL_ORIGIN,
    )


def test_tushare_request_serializer_and_response_parser_are_transport_free():
    request = serialize_tushare_request(
        endpoint=CANONICAL_ORIGIN,
        api_name="daily",
        token="test-token",
        params={"start_date": "20240101"},
        fields=["ts_code", "close"],
    )
    assert json.loads(request.data.decode("utf-8")) == {
        "api_name": "daily",
        "token": "test-token",
        "params": {"start_date": "20240101"},
        "fields": "ts_code,close",
    }
    envelope = _parse(
        {"code": 0, "msg": "", "data": {"fields": ["ts_code", "close"], "items": [["000001.SZ", 10.5]]}},
        params={"start_date": "20240101"},
    )
    assert envelope.records == [{"ts_code": "000001.SZ", "close": 10.5}]
    assert envelope.request_fingerprint == _identity("daily", {"start_date": "20240101"}).request_fingerprint
    assert envelope.transport_identity == _identity("daily", {"start_date": "20240101"}).transport_identity
    assert "test-token" not in json.dumps(envelope.to_dict())


def test_production_client_rejects_missing_capability_and_transport_injection():
    config = AShareDataConfig(tushare_token="synthetic", tushare_retry_count=1)
    with pytest.raises(TushareNetworkError, match="task055k_execution_capability"):
        TushareHttpClient(config)
    with pytest.raises(TypeError):
        TushareHttpClient(config, urlopen=lambda *_args, **_kwargs: None)


def test_validated_capability_is_single_use_and_preserves_three_identities():
    params = {"trade_date": "20240102"}
    fields = ["ts_code", "trade_date"]
    identity = _identity("daily", params, fields)
    capability = _validated_task055k_execution_capability(
        authority_content_hash="a" * 64,
        final_execution_seal_hash="b" * 64,
        api_name="daily",
        params=params,
        fields=fields,
        identity=identity,
        attempt_id="c" * 64,
        broker_contract_hash="d" * 64,
    )
    capability.authorize("daily", params, fields)
    with pytest.raises(Exception, match="already_consumed"):
        capability.authorize("daily", params, fields)


def test_parser_preserves_observed_empty_response_fields():
    fields = ["ts_code", "trade_date", "suspend_timing", "suspend_type"]
    envelope = _parse(
        {"code": 0, "msg": "", "data": {"fields": fields, "items": []}},
        api_name="suspend_d",
        fields=fields,
    )
    assert envelope.response_fields == fields
    assert envelope.records == []
    assert envelope.item_count == 0
    assert envelope.response_payload_hash


def test_parser_maps_permission_rate_and_api_errors():
    with pytest.raises(TusharePermissionError):
        _parse({"code": 2002, "msg": "权限不足", "data": {}})
    with pytest.raises(TushareRateLimitError):
        _parse({"code": 2003, "msg": "访问次数超过限制", "data": {}})
    with pytest.raises(TushareApiError, match="other error"):
        _parse({"code": 2004, "msg": "other error", "data": {}})


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"code": 0, "data": {"fields": "ts_code", "items": []}}, "must be lists"),
        ({"code": 0, "data": {"fields": ["ts_code", "trade_date"], "items": [["000001.SZ"]]}}, "row width"),
        ({"code": 0, "data": {"fields": ["ts_code"], "items": [["000001.SZ"]]}}, "omitted requested fields"),
    ],
)
def test_parser_fails_closed_on_malformed_schema(payload, message):
    with pytest.raises(TushareSchemaError, match=message):
        _parse(payload, fields=["ts_code", "trade_date"])

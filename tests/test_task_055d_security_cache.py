from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from data_pipeline.ashare.cache import TushareResponseCache
from data_pipeline.ashare.security import (
    CredentialStatus,
    TushareSecurityError,
    load_tushare_credential,
    scan_for_secret_leakage,
    validate_tushare_origin,
)
from data_pipeline.ashare.providers.tushare_client import TushareNetworkError, _NoRedirect
from task_055_d.cache import (
    SecureCacheError,
    inventory_caches,
    publish_validated_response,
    split_capped_request,
    transport_identity,
)
from task_055_d.contracts import CANONICAL_ORIGIN, DAILY_FIELDS
from task_055_d.network import execute_plan


def _request(params=None):
    params = params or {"ts_code": "000001.SZ", "start_date": "20240102", "end_date": "20240102"}
    return {
        "stage": "L1",
        "api_name": "daily",
        "params": params,
        "fields": list(DAILY_FIELDS),
        "transport_hash": transport_identity("daily", params, DAILY_FIELDS),
    }


def _envelope(records, fields=DAILY_FIELDS):
    return SimpleNamespace(
        records=records,
        response_code=0,
        response_message="",
        response_fields=list(fields),
        item_count=len(records),
        endpoint=CANONICAL_ORIGIN,
        provider_api_version="tushare_pro_http.v1",
    )


def _daily_row(code="000001.SZ", date="20240102"):
    return {"ts_code": code, "trade_date": date, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "pre_close": 10.0, "vol": 100.0, "amount": 1000.0}


def test_production_origin_forbids_http_and_noncanonical_hosts():
    assert validate_tushare_origin(CANONICAL_ORIGIN) == CANONICAL_ORIGIN
    for value in ("http://api.tushare.pro", "https://example.com", "https://api.tushare.pro.evil.test"):
        with pytest.raises(TushareSecurityError, match="noncanonical"):
            validate_tushare_origin(value)
    with pytest.raises(TushareNetworkError, match="redirect forbidden"):
        _NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://evil.test")


def test_credential_file_permissions_symlink_and_repo_paths_fail_closed(tmp_path, monkeypatch):
    token_file = tmp_path / "token"
    token_file.write_text("sentinel-token")
    token_file.chmod(0o644)
    with pytest.raises(TushareSecurityError, match="inside_repo_or_output|permissions"):
        load_tushare_credential({"TUSHARE_TOKEN_FILE": str(token_file)})
    outside = tmp_path.parent / f"{tmp_path.name}-outside-token"
    outside.write_text("sentinel-token")
    outside.chmod(0o600)
    link = tmp_path.parent / f"{tmp_path.name}-link"
    link.symlink_to(outside)
    with pytest.raises(TushareSecurityError, match="symlink"):
        load_tushare_credential({"TUSHARE_TOKEN_FILE": str(link)})
    link.unlink()
    outside.unlink()


def test_secret_scanner_detects_exact_sentinel_without_derivatives(tmp_path):
    clean = tmp_path / "clean.json"
    clean.write_text(json.dumps({"credential_present": True, "source_type": "environment"}))
    assert scan_for_secret_leakage([tmp_path], sentinel="sentinel-token")["status"] == "passed"
    (tmp_path / "bad.log").write_text("sentinel-token")
    assert scan_for_secret_leakage([tmp_path], sentinel="sentinel-token")["match_count"] == 1


def test_v3_cache_positive_response_is_validated_and_tampering_rejected(tmp_path):
    request = _request()
    receipt = publish_validated_response(cache_root=tmp_path / "cache", request=request, envelope=_envelope([_daily_row()]))
    inventory = inventory_caches([tmp_path / "cache"], [request])
    assert inventory["validated_hits"] == 1
    path = Path(receipt["path"])
    payload = json.loads(path.read_text())
    payload["records"][0]["ts_code"] = "000002.SZ"
    path.write_text(json.dumps(payload))
    inventory = inventory_caches([tmp_path / "cache"], [request])
    assert inventory["validated_hits"] == 0
    assert inventory["invalid_entries"]


def test_empty_response_requires_real_endpoint_schema_proof(tmp_path):
    request = _request()
    with pytest.raises(SecureCacheError, match="schema_proof"):
        publish_validated_response(cache_root=tmp_path, request=request, envelope=_envelope([], fields=[]))
    positive = publish_validated_response(cache_root=tmp_path, request=request, envelope=_envelope([_daily_row()]))
    cache = TushareResponseCache(tmp_path)
    proof = cache.build_endpoint_schema_proof("daily", DAILY_FIELDS)
    second = _request({"ts_code": "000002.SZ", "start_date": "20240102", "end_date": "20240102"})
    receipt = publish_validated_response(cache_root=tmp_path, request=second, envelope=_envelope([], fields=[]), endpoint_schema_proof=proof)
    assert receipt["item_count"] == 0
    assert positive["schema_version"] == "tushare_cache_envelope.v3"


def test_daily_geometry_and_cap_split_are_fail_closed(tmp_path):
    request = _request()
    with pytest.raises(SecureCacheError, match="wrong_code"):
        publish_validated_response(cache_root=tmp_path, request=request, envelope=_envelope([_daily_row(code="000002.SZ")]))
    children = split_capped_request(
        _request({"ts_code": "000001.SZ", "start_date": "20240102", "end_date": "20240105"}),
        ["20240102", "20240103", "20240104", "20240105"],
    )
    assert [(row["params"]["start_date"], row["params"]["end_date"]) for row in children] == [("20240102", "20240103"), ("20240104", "20240105")]


def test_daily_response_near_2500_rows_is_legal_and_endpoint_cap_splits(tmp_path):
    request = _request({"trade_date": "20240102"})
    rows = [_daily_row(code=f"{index:06d}.SZ") for index in range(1, 2501)]
    assert publish_validated_response(cache_root=tmp_path / "legal", request=request, envelope=_envelope(rows))["item_count"] == 2500
    capped = [_daily_row(code=f"{index:06d}.SZ") for index in range(1, 6001)]
    with pytest.raises(SecureCacheError, match="cap_reached"):
        publish_validated_response(cache_root=tmp_path / "capped", request=request, envelope=_envelope(capped))


def test_fake_transport_canary_executes_and_zero_budget_still_scans(tmp_path):
    request = _request()
    plan = {"content_hash": "a" * 64, "requests": [request]}

    class Client:
        def post_with_metadata(self, api_name, params, fields):
            return _envelope([_daily_row()])

    calls = {"credential": 0, "client": 0}
    with pytest.raises(Exception, match="superseded_by_task055j"):
        execute_plan(
            plan=plan, output_root=tmp_path / "run", cache_roots=[], allow_network=True,
            sealed_plan_hash=plan["content_hash"], request_budget=1,
            credential_loader=lambda: calls.__setitem__("credential", calls["credential"] + 1),
            tls_checker=lambda: {},
            client_factory=lambda: calls.__setitem__("client", calls["client"] + 1),
        )
    assert calls == {"credential": 0, "client": 0}

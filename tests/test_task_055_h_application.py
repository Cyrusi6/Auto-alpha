from __future__ import annotations

from pathlib import Path

import pytest

from data_pipeline.ashare.cache import TushareResponseCache
from task_055_f.network import _append_spend_event
from task_055_g import network_state
from task_055_h.application import apply_native_canary_response, synthetic_test_only_apply, validate_native_response_apply
from task_055_h.authorization import EXPECTED_ORDERED_REQUESTS
from task_055_h.io import sha256_file
from task_055_h.network import verify_and_accept_canary

from test_task_055_h_authorization import DAILY_FIELDS, ready_seal


def _request(api_name: str = "daily"):
    code, date, transport, evidence = EXPECTED_ORDERED_REQUESTS[0]
    fields = DAILY_FIELDS if api_name == "daily" else ["ts_code", "trade_date", "suspend_timing", "suspend_type"]
    return {"api_name": api_name, "params": {"ts_code": code, "trade_date": date}, "fields": fields, "ts_code": code, "trade_date": date, "transport_hash": transport, "evidence_use_hash": evidence}


def test_synthetic_positive_runs_full_rebuild_chain_but_is_not_sealable():
    request = _request()
    row = {"ts_code": request["ts_code"], "trade_date": request["trade_date"], "open": 10, "high": 11, "low": 9, "close": 10, "pre_close": 10, "vol": 1, "amount": 1}
    result = synthetic_test_only_apply(api_name="daily", records=[row], request=request)
    assert result["production_seal_eligible"] is False
    assert "research_firewall_sentinel" in result["stages"]
    assert "exact20_x5_causal_replay" in result["stages"]


def test_synthetic_empty_daily_routes_to_dynamic_l2():
    result = synthetic_test_only_apply(api_name="daily", records=[], request=_request())
    assert result["action"]["kind"] == "vendor_daily_absence"
    assert result["action"]["proves_suspension"] is False
    assert "dynamic_exact_suspend_l2" in result["stages"]


def test_synthetic_l2_s_r_and_empty_semantics():
    request = _request("suspend_d")
    base = {"ts_code": request["ts_code"], "trade_date": request["trade_date"], "suspend_timing": None}
    s = synthetic_test_only_apply(api_name="suspend_d", records=[base | {"suspend_type": "S"}], request=request)
    r = synthetic_test_only_apply(api_name="suspend_d", records=[base | {"suspend_type": "R"}], request=request)
    empty = synthetic_test_only_apply(api_name="suspend_d", records=[], request=request)
    assert s["action"]["outcome"] == "modeled_suspend_candidate_timing_uncertified"
    assert r["action"]["outcome"] == "resume_event_not_suspension_proof"
    assert empty["action"]["outcome"] == "vendor_suspend_absence_not_no_trade_proof"


def test_native_positive_response_publishes_verified_partition_and_rebuild_dag(tmp_path: Path):
    seal = ready_seal(tmp_path)
    request = dict(seal["canary_execution_plan"]["requests"][0])
    cache_root = tmp_path / "network_cache_data"
    cache = TushareResponseCache(cache_root, enabled=True)
    row = {
        "ts_code": request["ts_code"],
        "trade_date": request["trade_date"],
        "open": 10.0,
        "high": 11.0,
        "low": 9.0,
        "close": 10.5,
        "pre_close": 10.0,
        "vol": 1000.0,
        "amount": 10000.0,
    }
    cache_path = cache.write(
        "daily",
        params=request["params"],
        fields=request["fields"],
        records=[row],
        response_code=0,
        response_fields=request["fields"],
        item_count=1,
        response_fields_observed=True,
        endpoint="https://api.tushare.pro",
        provider_api_version="tushare_pro_http.v1",
    )
    execution = network_state.execute_l1_canary(
        state_root=tmp_path / "network_state",
        plan_manifest=seal["canary_execution_plan"],
        allow_network=True,
        sealed_plan_hash=seal["canary_execution_plan_hash"],
        request_executor=lambda _: {
            "request": request,
            "outcome": "positive_response",
            "item_count": 1,
            "cache_relative_path": str(cache_path.relative_to(cache_root)),
            "cache_sha256": sha256_file(cache_path),
        },
    )
    _append_spend_event(tmp_path / "transport_spend", {"event": "physical_post_started", "transport_hash": request["transport_hash"]})
    _append_spend_event(tmp_path / "transport_spend", {"event": "physical_post_completed", "transport_hash": request["transport_hash"]})
    acceptance = verify_and_accept_canary(
        authorization_seal=seal["manifest_path"],
        canary_execution_manifest=execution["manifest_path"],
        output_root=tmp_path / "canary_acceptance",
    )
    applied = apply_native_canary_response(
        authorization_seal=seal["manifest_path"],
        canary_acceptance=acceptance["manifest_path"],
        output_root=tmp_path / "response_apply",
    )
    validated = validate_native_response_apply(applied["manifest_path"])
    assert validated["action"]["kind"] == "immutable_daily_raw_repair"
    assert [row["stage"] for row in validated["rebuild_dag"]["stages"]][-1] == "fee_aware_causal_frontier"
    partition = Path(validated["manifest_path"]).parent / validated["response_partition"]["path"]
    partition.write_text("{}\n", encoding="utf-8")
    with pytest.raises(Exception, match="partition_invalid"):
        validate_native_response_apply(applied["manifest_path"])

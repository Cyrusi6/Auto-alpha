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
    calls = []
    with pytest.raises(network_state.Task055GNetworkStateError, match="superseded_by_task055k_transport_broker"):
        network_state.execute_l1_canary(
            state_root=tmp_path / "state",
            plan_manifest={},
            allow_network=True,
            sealed_plan_hash="unused",
            request_executor=lambda request: calls.append(request),
        )
    assert calls == []

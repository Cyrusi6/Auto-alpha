from __future__ import annotations

import json
from pathlib import Path

import pytest

from data_pipeline.ashare.cache import TushareResponseCache
from data_pipeline.ashare.request_normalization import stable_json_hash
from task_055_f.network import _append_spend_event
from task_055_g import network_state
from task_055_h.authorization import EXPECTED_ORDERED_REQUESTS
from task_055_h.io import canonical_hash, sha256_file
from task_055_h.network import (
    _native_execution,
    ordered_future_canary_gate,
    recover_cache_before_retry,
    verify_and_accept_canary,
)

from test_task_055_h_authorization import DAILY_FIELDS, ready_seal


def _request() -> dict:
    code, date, transport_hash, evidence = EXPECTED_ORDERED_REQUESTS[0]
    return {
        "stage": "L1",
        "round_id": 1,
        "api_name": "daily",
        "params": {"ts_code": code, "trade_date": date},
        "fields": DAILY_FIELDS,
        "ts_code": code,
        "trade_date": date,
        "transport_hash": transport_hash,
        "evidence_use_hash": evidence,
    }


def _record() -> dict:
    code, date, *_ = EXPECTED_ORDERED_REQUESTS[0]
    return {"ts_code": code, "trade_date": date, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "pre_close": 10.0, "vol": 1000.0, "amount": 10000.0}


def _record_for(code: str, date: str) -> dict:
    return _record() | {"ts_code": code, "trade_date": date}


def _plan(request: dict) -> dict:
    from task_055_h.authorization import EXPECTED_G_FRONTIER_ROOT, EXPECTED_G_PLAN_HASH, _canary_execution_plan

    return _canary_execution_plan(
        {
            "plan_hash": EXPECTED_G_PLAN_HASH,
            "frontier_root": EXPECTED_G_FRONTIER_ROOT,
            "lineage": {
                "truth_content_hash": "1" * 64,
                "matrix_content_hash": "2" * 64,
                "simulation_bundle_content_hash": "3" * 64,
                "fee_schedule_content_hash": "4" * 64,
                "frontier_root": EXPECTED_G_FRONTIER_ROOT,
                "key_root": "5" * 64,
            },
        },
        {
            "api_name": request["api_name"],
            "ts_code": request["ts_code"],
            "trade_date": request["trade_date"],
            "fields": request["fields"],
            "transport_hash": request["transport_hash"],
            "evidence_use_hash": request["evidence_use_hash"],
        },
    )


def test_invalid_authorization_never_calls_tls_or_credential(tmp_path: Path) -> None:
    seal = ready_seal(tmp_path)
    calls = {"tls": 0, "credential": 0}

    def tls():
        calls["tls"] += 1
        return {}

    def credential():
        calls["credential"] += 1
        return "secret"

    with pytest.raises(Exception, match="explicit_authorization_invalid"):
        ordered_future_canary_gate(
            authorization_seal=seal["manifest_path"],
            allow_network=True,
            sealed_plan_hash="wrong",
            tls_checker=tls,
            credential_loader=credential,
        )
    assert calls == {"tls": 0, "credential": 0}


def test_tls_validation_failure_never_calls_credential_loader(tmp_path: Path) -> None:
    seal = ready_seal(tmp_path)
    calls = {"credential": 0}

    def credential() -> str:
        calls["credential"] += 1
        return "secret"

    with pytest.raises(Exception, match="tls_preflight_failed"):
        ordered_future_canary_gate(
            authorization_seal=seal["manifest_path"],
            allow_network=True,
            sealed_plan_hash=seal["canary_execution_plan_hash"],
            tls_checker=lambda: {
                "status": "failed",
                "origin": "https://api.tushare.pro",
                "hostname_verified": False,
                "certificate_verified": False,
            },
            credential_loader=credential,
        )
    assert calls["credential"] == 0


def test_canary_acceptance_reopens_native_cache_and_requires_one_post(tmp_path: Path) -> None:
    seal = ready_seal(tmp_path)
    request = _request()
    task_root = Path(seal["manifest_path"]).parents[3]
    cache_data = task_root / "network_cache_data"
    cache = TushareResponseCache(cache_data, enabled=True)
    cache_path = cache.write(
        "daily",
        params=request["params"],
        fields=request["fields"],
        records=[_record()],
        response_code=0,
        response_fields=request["fields"],
        item_count=1,
        response_fields_observed=True,
        endpoint="https://api.tushare.pro",
        provider_api_version="tushare_pro_http.v1",
    )

    plan = _plan(request)

    def executor(_request):
        from task_055_h.io import sha256_file

        return {"request": request, "outcome": "positive_response", "item_count": 1, "cache_relative_path": str(cache_path.relative_to(cache_data)), "cache_sha256": sha256_file(cache_path)}

    execution = network_state.execute_l1_canary(
        state_root=task_root / "network_state",
        plan_manifest=plan,
        allow_network=True,
        sealed_plan_hash=plan["plan_hash"],
        request_executor=executor,
    )
    _append_spend_event(task_root / "transport_spend", {"event": "physical_post_started", "transport_hash": request["transport_hash"]})
    _append_spend_event(task_root / "transport_spend", {"event": "physical_post_completed", "transport_hash": request["transport_hash"]})
    accepted = verify_and_accept_canary(
        authorization_seal=seal["manifest_path"],
        canary_execution_manifest=execution["manifest_path"],
        output_root=task_root / "canary_acceptance",
    )
    assert accepted["physical_post_count"] == 1
    assert accepted["resume_authorized"] is False


def test_crash_after_cache_before_terminal_recovers_without_post(tmp_path: Path) -> None:
    request = _request()
    cache = TushareResponseCache(tmp_path / "cache", enabled=True)
    cache.write(
        "daily", params=request["params"], fields=request["fields"], records=[_record()],
        response_code=0, response_fields=request["fields"], item_count=1,
        response_fields_observed=True, endpoint="https://api.tushare.pro",
        provider_api_version="tushare_pro_http.v1",
    )
    recovered = recover_cache_before_retry(request=request, cache_data_root=tmp_path / "cache")
    assert recovered is not None
    assert recovered["outcome"] == "validated_cache_hit"
    assert recovered["physical_attempt_count"] == 0


def test_crash_after_cache_before_terminal_closes_from_cache_without_second_attempt(tmp_path: Path) -> None:
    request = _request()
    plan = _plan(request)
    state_root = tmp_path / "state"
    network_state.consolidate(state_root=state_root, plan_manifest=plan)
    attempt_id = canonical_hash([plan["plan_hash"], request["transport_hash"], 1])
    network_state._append_events(
        state_root,
        [
            {
                "event_id": canonical_hash(["physical_attempt_started", attempt_id]),
                "event": "physical_attempt_started",
                "attempt_id": attempt_id,
                "plan_hash": plan["plan_hash"],
                "stage": "L1",
                "round_id": 1,
                "transport_hash": request["transport_hash"],
                "ts_code": request["ts_code"],
                "trade_date": request["trade_date"],
            }
        ],
    )
    cache_root = tmp_path / "cache"
    cache = TushareResponseCache(cache_root, enabled=True)
    cache.write(
        "daily",
        params=request["params"],
        fields=request["fields"],
        records=[_record()],
        response_code=0,
        response_fields=request["fields"],
        item_count=1,
        response_fields_observed=True,
        endpoint="https://api.tushare.pro",
        provider_api_version="tushare_pro_http.v1",
    )
    recovered = recover_cache_before_retry(request=request, cache_data_root=cache_root)
    assert recovered is not None
    execution = {
        "schema_version": network_state.EXECUTION_SCHEMA,
        "status": "single_request_completed",
        "plan_hash": plan["plan_hash"],
        "results": [recovered],
    }
    consolidated = network_state.consolidate(
        state_root=state_root,
        plan_manifest=plan,
        execution_manifests=[execution],
    )
    assert consolidated["successful_request_count"] == 1
    assert consolidated["request_states"][0]["status"] == "cache_hit"
    assert consolidated["ledger"]["physical_attempt_count"] == 1


def test_empty_response_schema_proof_tamper_is_rejected(tmp_path: Path) -> None:
    seal = ready_seal(tmp_path)
    request = _request()
    task_root = Path(seal["manifest_path"]).parents[3]
    cache_data = task_root / "network_cache_data"
    cache = TushareResponseCache(cache_data, enabled=True)
    second_code, second_date, *_ = EXPECTED_ORDERED_REQUESTS[1]
    cache.write(
        "daily",
        params={"ts_code": second_code, "trade_date": second_date},
        fields=DAILY_FIELDS,
        records=[_record_for(second_code, second_date)],
        response_code=0,
        response_fields=DAILY_FIELDS,
        item_count=1,
        response_fields_observed=True,
        endpoint="https://api.tushare.pro",
        provider_api_version="tushare_pro_http.v1",
    )
    proof = cache.build_endpoint_schema_proof("daily", DAILY_FIELDS)
    assert proof is not None
    proof["source_cache_sha256"] = "0" * 64
    proof["proof_hash"] = stable_json_hash({key: value for key, value in proof.items() if key != "proof_hash"})
    cache_path = cache.write(
        "daily",
        params=request["params"],
        fields=request["fields"],
        records=[],
        response_code=0,
        response_fields=[],
        item_count=0,
        response_fields_observed=False,
        endpoint_schema_proof=proof,
        endpoint="https://api.tushare.pro",
        provider_api_version="tushare_pro_http.v1",
    )
    plan = _plan(request)
    execution = network_state.execute_l1_canary(
        state_root=task_root / "network_state",
        plan_manifest=plan,
        allow_network=True,
        sealed_plan_hash=plan["plan_hash"],
        request_executor=lambda _request: {
            "request": request,
            "outcome": "negative_vendor_response",
            "item_count": 0,
            "cache_relative_path": str(cache_path.relative_to(cache_data)),
            "cache_sha256": sha256_file(cache_path),
        },
    )
    _append_spend_event(
        task_root / "transport_spend",
        {"event": "physical_post_started", "transport_hash": request["transport_hash"]},
    )
    _append_spend_event(
        task_root / "transport_spend",
        {"event": "physical_post_completed", "transport_hash": request["transport_hash"]},
    )
    with pytest.raises(Exception, match="schema_proof_source_invalid"):
        verify_and_accept_canary(
            authorization_seal=seal["manifest_path"],
            canary_execution_manifest=execution["manifest_path"],
            output_root=task_root / "canary_acceptance",
        )


def test_forged_execution_inside_state_root_is_rejected(tmp_path: Path) -> None:
    state_root = tmp_path / "network_state"
    state_root.mkdir()
    semantic = {
        "schema_version": network_state.EXECUTION_SCHEMA,
        "status": "canary_completed",
        "stage": "L1",
        "round_id": 1,
        "plan_hash": "p" * 64,
        "must_stop_after_canary": True,
        "batch_started": False,
        "results": [],
    }
    payload = semantic | {
        "content_hash": canonical_hash(semantic),
        "generation_id": f"forged_{canonical_hash(semantic)[:24]}",
    }
    forged = state_root / "forged_execution.json"
    forged.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(Exception):
        _native_execution(forged, state_root)


def test_canary_acceptance_rejects_validated_cache_hit_as_physical_canary(tmp_path: Path) -> None:
    seal = ready_seal(tmp_path)
    request = _request()
    task_root = Path(seal["manifest_path"]).parents[3]
    cache_data = task_root / "network_cache_data"
    cache = TushareResponseCache(cache_data, enabled=True)
    cache_path = cache.write(
        "daily",
        params=request["params"],
        fields=request["fields"],
        records=[_record()],
        response_code=0,
        response_fields=request["fields"],
        item_count=1,
        response_fields_observed=True,
        endpoint="https://api.tushare.pro",
        provider_api_version="tushare_pro_http.v1",
    )
    plan = _plan(request)
    execution = network_state.execute_l1_canary(
        state_root=task_root / "network_state",
        plan_manifest=plan,
        allow_network=True,
        sealed_plan_hash=plan["plan_hash"],
        request_executor=lambda _request: {
            "request": request,
            "outcome": "validated_cache_hit",
            "item_count": 1,
            "cache_relative_path": str(cache_path.relative_to(cache_data)),
            "cache_sha256": sha256_file(cache_path),
        },
    )
    _append_spend_event(
        task_root / "transport_spend",
        {"event": "physical_post_started", "transport_hash": request["transport_hash"]},
    )
    _append_spend_event(
        task_root / "transport_spend",
        {"event": "physical_post_completed", "transport_hash": request["transport_hash"]},
    )
    with pytest.raises(Exception):
        verify_and_accept_canary(
            authorization_seal=seal["manifest_path"],
            canary_execution_manifest=execution["manifest_path"],
            output_root=task_root / "canary_acceptance",
        )


def test_corrupted_cache_is_not_recovered(tmp_path: Path) -> None:
    request = _request()
    cache = TushareResponseCache(tmp_path / "cache", enabled=True)
    path = cache.write(
        "daily", params=request["params"], fields=request["fields"], records=[_record()],
        response_code=0, response_fields=request["fields"], item_count=1,
        response_fields_observed=True, endpoint="https://api.tushare.pro",
        provider_api_version="tushare_pro_http.v1",
    )
    path.write_text("{}\n")
    with pytest.raises(Exception):
        recover_cache_before_retry(request=request, cache_data_root=tmp_path / "cache")

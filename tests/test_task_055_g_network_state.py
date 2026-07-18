from __future__ import annotations

from pathlib import Path

import pytest

from artifact_schema.validator import validate_artifact
from task_055_f.read_ledger import canonical_hash
from task_055_g import network_cli
from task_055_g import network_state


def _content(payload: dict) -> dict:
    result = dict(payload)
    result["content_hash"] = canonical_hash(result)
    return result


def _plan(keys, *, stage="L1", round_id=1, parent_apply_hash=None, frontier_root=None):
    frontier_root = frontier_root or canonical_hash(sorted(keys))
    api_name = "daily" if stage == "L1" else "suspend_d"
    fields = network_state.DAILY_FIELDS if stage == "L1" else network_state.SUSPEND_FIELDS
    requests = [
        network_state._request(
            stage=stage,
            round_id=round_id,
            api_name=api_name,
            ts_code=code,
            trade_date=date,
            fields=fields,
            parent_plan_hash=parent_apply_hash or frontier_root,
            frontier_root=frontier_root,
        )
        for code, date in keys
    ]
    return network_state._make_plan(
        stage=stage,
        round_id=round_id,
        requests=requests,
        lineage={
            "truth_content_hash": "1" * 64,
            "matrix_content_hash": "2" * 64,
            "simulation_bundle_content_hash": "3" * 64,
            "fee_schedule_content_hash": "4" * 64,
            "frontier_root": frontier_root,
            "key_root": frontier_root,
            "response_lineage_root": None,
        },
        frontier_root=frontier_root,
        parent_apply_hash=parent_apply_hash,
        status="sealed_round_one_exact_daily_l1" if stage == "L1" else "sealed_dynamic_exact_suspend_l2",
    )


def _result(request, *, outcome="positive_response", attempt_count=1, suffix="a"):
    payload = {
        "request": dict(request),
        "outcome": outcome,
        "physical_attempt_count": attempt_count,
        "item_count": 1 if outcome == "positive_response" else 0,
    }
    if outcome in network_state.SUCCESS_OUTCOMES:
        payload.update(
            {
                "cache_relative_path": f"responses/{request['transport_hash']}.{suffix}.json",
                "cache_sha256": canonical_hash([request["transport_hash"], suffix]),
            }
        )
    return payload


def _execution(plan, results, tag):
    return _content(
        {
            "schema_version": network_state.EXECUTION_SCHEMA,
            "status": tag,
            "plan_hash": plan["plan_hash"],
            "results": results,
        }
    )


def _rebuilt_truth(apply, rows):
    keys = sorted((row["ts_code"], row["trade_date"]) for row in rows)
    return _content(
        {
            "schema_version": "task055g_rebuilt_truth_fixture_v1",
            "status": "published",
            "records": rows,
            "key_root": canonical_hash(keys),
            "lineage": {
                "parent_network_apply_hash": apply["content_hash"],
                "response_lineage_root": apply["response_lineage_root"],
                "cache_input_root": apply["cache_input_root"],
            },
        }
    )


def _rebuilt_frontier(apply, truth, keys, *, matrix="a", bundle="b", fee="c"):
    normalized = sorted(keys)
    return _content(
        {
            "schema_version": "task055g_rebuilt_frontier_fixture_v1",
            "status": "published",
            "frontier_keys": normalized,
            "frontier_root": canonical_hash(normalized),
            "lineage": {
                "parent_network_apply_hash": apply["content_hash"],
                "truth_content_hash": truth["content_hash"],
                "matrix_content_hash": matrix * 64,
                "simulation_bundle_content_hash": bundle * 64,
                "fee_schedule_content_hash": fee * 64,
            },
        }
    )


def _complete_l1(tmp_path: Path, keys):
    state_root = tmp_path / "task_055_g_state"
    plan = _plan(keys)
    execution = _execution(plan, [_result(row) for row in plan["requests"]], "completed")
    consolidated = network_state.consolidate(
        state_root=state_root,
        plan_manifest=plan,
        execution_manifests=[execution],
    )
    applied = network_state.apply_l1(
        state_root=state_root,
        consolidation_manifest=consolidated["manifest_path"],
    )
    return state_root, plan, applied


def _assert_schema(path: str | Path, artifact_type: str) -> None:
    result = validate_artifact(path, strict=True)
    assert result.valid is True
    assert result.artifact_type == artifact_type


def test_l1_canary_resume_are_native_schema_artifacts_and_can_be_applied(tmp_path):
    keys = [("000001.SZ", "20240102"), ("000002.SZ", "20240103")]
    state_root = tmp_path / "task_055_g_state"
    plan = _plan(keys)
    calls = []

    def executor(request):
        calls.append(request["transport_hash"])
        return _result(request, suffix=f"l1-{len(calls)}")

    with pytest.raises(network_state.Task055GNetworkStateError, match="superseded_by_task055j"):
        network_state.execute_l1_canary(
            state_root=state_root,
            plan_manifest=plan,
            allow_network=True,
            sealed_plan_hash=plan["plan_hash"],
            request_executor=executor,
        )
    with pytest.raises(network_state.Task055GNetworkStateError, match="superseded_by_task055j"):
        network_state.execute_l1_resume(
            state_root=state_root,
            plan_manifest=plan,
            canary_manifest={},
            allow_network=True,
            sealed_plan_hash=plan["plan_hash"],
            request_executor=executor,
        )
    assert calls == []


def test_l1_failure_recovery_is_idempotent_and_apply_uses_real_responses(tmp_path):
    keys = [("000001.SZ", "20240102"), ("000002.SZ", "20240103")]
    plan = _plan(keys)
    first = _execution(
        plan,
        [
            _result(plan["requests"][0], suffix="first"),
            _result(plan["requests"][1], outcome="request_error", suffix="failed"),
        ],
        "partial",
    )
    state_root = tmp_path / "state"
    partial = network_state.consolidate(
        state_root=state_root,
        plan_manifest=plan,
        execution_manifests=[first],
    )
    assert partial["status"] == "responses_partial"
    assert partial["failed_request_count"] == 1
    assert network_state.ledger_summary(state_root)["physical_attempt_count"] == 2
    with pytest.raises(network_state.Task055GNetworkStateError, match="requests_not_complete"):
        network_state.apply_l1(state_root=state_root, consolidation_manifest=partial["manifest_path"])

    retry = _execution(
        plan,
        [_result(plan["requests"][1], suffix="retry")],
        "retry_completed",
    )
    complete = network_state.consolidate(
        state_root=state_root,
        plan_manifest=plan,
        execution_manifests=[first, retry],
    )
    assert complete["status"] == "responses_complete_ready_for_apply"
    assert network_state.ledger_summary(state_root)["physical_attempt_count"] == 3
    repeated = network_state.consolidate(
        state_root=state_root,
        plan_manifest=plan,
        execution_manifests=[first, retry],
    )
    assert repeated["content_hash"] == complete["content_hash"]
    assert network_state.ledger_summary(state_root)["physical_attempt_count"] == 3

    applied = network_state.apply_l1(
        state_root=state_root,
        consolidation_manifest=complete["manifest_path"],
    )
    assert applied["result_count"] == 2
    assert len(applied["cache_inputs"]) == 2
    assert applied["cache_input_root"] == canonical_hash(applied["cache_inputs"])
    verified = network_state.final_verify(state_root=state_root)
    assert verified["network_accessed"] is True
    assert verified["request_count"] == 2
    assert verified["physical_attempt_count"] == 3
    assert verified["max_request_date"] == "20240103"
    assert network_state.final_verify(state_root=state_root)["content_hash"] == verified["content_hash"]
    read_only = network_state.verify_state_read_only(state_root=state_root)
    assert read_only["content_hash"] == verified["content_hash"]


def test_l2_cannot_be_created_before_l1_apply_and_rebuild(tmp_path):
    key = ("000001.SZ", "20240102")
    plan = _plan([key])
    consolidated = network_state.consolidate(state_root=tmp_path / "state", plan_manifest=plan)
    truth = _content(
        {
            "schema_version": "task055g_rebuilt_truth_fixture_v1",
            "records": [],
            "lineage": {},
        }
    )
    frontier = _content(
        {
            "schema_version": "task055g_rebuilt_frontier_fixture_v1",
            "frontier_keys": [key],
            "frontier_root": canonical_hash([key]),
            "lineage": {},
        }
    )
    with pytest.raises(network_state.Task055GNetworkStateError, match="state_artifact_schema_invalid"):
        network_state.build_l2_plan(
            state_root=tmp_path / "state",
            l1_apply_manifest=consolidated["manifest_path"],
            truth_manifest=truth,
            frontier_manifest=frontier,
        )


def test_dynamic_l2_uses_rebuilt_truth_and_excludes_existing_s(tmp_path):
    keys = [("000001.SZ", "20240102"), ("000002.SZ", "20240103")]
    state_root, _plan_value, applied = _complete_l1(tmp_path, keys)
    truth = _rebuilt_truth(
        applied,
        [
            {"ts_code": keys[0][0], "trade_date": keys[0][1], "state": "DATA_SOURCE_GAP", "suspend_type": "none"},
            {"ts_code": keys[1][0], "trade_date": keys[1][1], "state": "VENDOR_DAILY_NON_TRADING_MODELED_CANDIDATE", "suspend_type": "S"},
        ],
    )
    frontier = _rebuilt_frontier(applied, truth, keys)
    l2 = network_state.build_l2_plan(
        state_root=state_root,
        l1_apply_manifest=applied["manifest_path"],
        truth_manifest=truth,
        frontier_manifest=frontier,
    )
    assert len(l2["requests"]) == 1
    assert l2["requests"][0]["api_name"] == "suspend_d"
    assert l2["requests"][0]["params"] == {"ts_code": keys[0][0], "trade_date": keys[0][1]}
    assert l2["parent_apply_hash"] == applied["content_hash"]
    assert l2["lineage"]["response_lineage_root"] == applied["response_lineage_root"]


def test_l2_canary_resume_and_apply_are_separate_and_offline_by_default(tmp_path):
    keys = [("000001.SZ", "20240102"), ("000002.SZ", "20240103")]
    state_root, _plan_value, applied = _complete_l1(tmp_path, keys)
    truth = _rebuilt_truth(
        applied,
        [
            {"ts_code": code, "trade_date": date, "state": "DATA_SOURCE_GAP", "suspend_type": "none"}
            for code, date in keys
        ],
    )
    frontier = _rebuilt_frontier(applied, truth, keys)
    l2 = network_state.build_l2_plan(
        state_root=state_root,
        l1_apply_manifest=applied["manifest_path"],
        truth_manifest=truth,
        frontier_manifest=frontier,
    )
    _assert_schema(l2["manifest_path"], "task055g_network_plan")
    calls = []

    def executor(request):
        calls.append(request["transport_hash"])
        return _result(request, suffix=f"network-{len(calls)}")

    before = network_state.ledger_summary(state_root)["physical_attempt_count"]
    with pytest.raises(network_state.Task055GNetworkStateError, match="superseded_by_task055j"):
        network_state.execute_l2_canary(
            state_root=state_root,
            plan_manifest=l2["manifest_path"],
            request_executor=executor,
        )
    assert calls == []
    assert network_state.ledger_summary(state_root)["physical_attempt_count"] == before

    with pytest.raises(network_state.Task055GNetworkStateError, match="superseded_by_task055j"):
        network_state.execute_l2_resume(
            state_root=state_root,
            plan_manifest=l2["manifest_path"],
            canary_manifest={},
            allow_network=True,
            sealed_plan_hash=l2["plan_hash"],
            request_executor=executor,
        )
    assert calls == []
    assert network_state.ledger_summary(state_root)["physical_attempt_count"] == before


def test_cross_round_unique_key_and_logical_budget_is_global(tmp_path):
    keys = [(f"{index:06d}.SZ", "20240102") for index in range(64)]
    state_root, _plan_value, l1_apply = _complete_l1(tmp_path, keys)
    truth = _rebuilt_truth(
        l1_apply,
        [
            {"ts_code": code, "trade_date": date, "state": "DATA_SOURCE_GAP", "suspend_type": "none"}
            for code, date in keys
        ],
    )
    frontier = _rebuilt_frontier(l1_apply, truth, keys)
    l2 = network_state.build_l2_plan(
        state_root=state_root,
        l1_apply_manifest=l1_apply["manifest_path"],
        truth_manifest=truth,
        frontier_manifest=frontier,
    )
    l2_execution = _execution(l2, [_result(row, attempt_count=0, suffix="cache") | {"outcome": "validated_cache_hit"} for row in l2["requests"]], "cache_complete")
    l2_apply = network_state.apply_l2(
        state_root=state_root,
        plan_manifest=l2["manifest_path"],
        execution_manifests=[l2_execution],
    )
    summary = network_state.ledger_summary(state_root)
    assert summary["unique_security_date_count"] == 64
    assert summary["logical_request_count"] == 128

    extra = ("999999.SZ", "20240104")
    next_truth = _rebuilt_truth(
        l2_apply,
        [{"ts_code": extra[0], "trade_date": extra[1], "state": "DATA_SOURCE_GAP", "suspend_type": "none"}],
    )
    next_frontier = _rebuilt_frontier(l2_apply, next_truth, [extra], matrix="d", bundle="e", fee="f")
    with pytest.raises(network_state.Task055GNetworkStateError, match="global_unique_security_date_budget_exceeded"):
        network_state.next_round(
            state_root=state_root,
            parent_apply_manifest=l2_apply["manifest_path"],
            truth_manifest=next_truth,
            frontier_manifest=next_frontier,
        )
    after = network_state.ledger_summary(state_root)
    assert after["unique_security_date_count"] == 64
    assert after["logical_request_count"] == 128


def test_cli_l2_canary_default_does_not_construct_credential_executor(tmp_path, monkeypatch):
    plan = _plan([("000001.SZ", "20240102")], stage="L2")
    config = tmp_path / "config.json"
    config.write_text(
        __import__("json").dumps({"state_root": str(tmp_path / "state"), "plan_manifest": plan}),
        encoding="utf-8",
    )
    calls = []
    monkeypatch.setattr(network_cli, "_build_secure_executor", lambda *_: calls.append(True))
    assert network_cli.main(["l2-canary", "--config", str(config)]) == 2
    assert calls == []
    assert network_state.ledger_summary(tmp_path / "state")["network_accessed"] is False

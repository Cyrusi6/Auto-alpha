import json
from pathlib import Path

import pytest

from task_055_b.request_plan import (
    RequestPlanConfig,
    RequestPlanError,
    ResponseEnvelope,
    build_request_plan,
    execute_request_plan,
    validate_evidence_run,
    validate_request_plan,
)


CALENDAR = ["20240102", "20240103", "20240104", "20240105", "20240108"]
GAPS = [
    {"ts_code": "000001.SZ", "trade_date": "20240103"},
    {"ts_code": "000001.SZ", "trade_date": "20240104"},
    {"ts_code": "600000.SH", "trade_date": "20240108"},
]


def _build(tmp_path: Path, *, budget: int = 10):
    return build_request_plan(
        GAPS,
        CALENDAR,
        RequestPlanConfig(output_root=tmp_path / "plans", max_network_requests=budget),
    )


def _requester(calls):
    def request(api_name, params, fields):
        calls.append((api_name, dict(params)))
        if api_name == "daily":
            trade_date = params.get("trade_date") or params["start_date"]
            ts_code = params.get("ts_code") or "000001.SZ"
            rows = ({
                "ts_code": ts_code,
                "trade_date": trade_date,
                "open": 10.0,
                "high": 10.2,
                "low": 9.8,
                "close": 10.1,
                "pre_close": 10.0,
                "vol": 100.0,
                "amount": 1000.0,
            },)
        else:
            rows = ()
        return ResponseEnvelope(records=rows, response_fields=tuple(fields))

    return request


def test_plan_is_content_addressed_bounded_and_merges_episodes(tmp_path):
    plan = _build(tmp_path)
    validated = validate_request_plan(plan["manifest_path"])

    assert validated["gap_cell_count"] == 3
    assert len(validated["episodes"]) == 2
    assert validated["request_count"] == 10
    assert validated["episodes"][0]["window_start_date"] == "20240102"
    assert validated["episodes"][0]["window_end_date"] == "20240105"
    assert validated["prospective_holdout_access_allowed"] is False
    assert Path(plan["manifest_path"]).parent.name.endswith(plan["content_hash"])

    replay = _build(tmp_path)
    assert replay["content_hash"] == plan["content_hash"]
    assert replay["manifest_path"] == plan["manifest_path"]


def test_plan_rejects_budget_overflow_and_post_boundary_cells(tmp_path):
    with pytest.raises(RequestPlanError, match="exceeds immutable budget"):
        _build(tmp_path, budget=9)
    with pytest.raises(RequestPlanError, match="trade calendar is empty|verified trade session"):
        build_request_plan(
            [{"ts_code": "000001.SZ", "trade_date": "20260701"}],
            ["20260701"],
            RequestPlanConfig(output_root=tmp_path, max_network_requests=4),
        )


def test_dual_geometry_execution_resumes_from_validated_cache(tmp_path):
    plan = _build(tmp_path)
    calls = []
    first = execute_request_plan(
        plan["manifest_path"], tmp_path / "evidence", _requester(calls), request_budget=10
    )
    assert first["status"] == "complete"
    assert first["network_request_count"] == 10
    assert first["reconciliation"]["geometry_execution_counts"] == {
        "exact_trade_date": 6,
        "security_window": 4,
    }
    assert all(
        not item["negative_response_proves_trading_state"]
        for item in first["executions"]
        if item["negative_vendor_response"]
    )

    resumed = execute_request_plan(
        plan["manifest_path"],
        tmp_path / "evidence",
        lambda *args: pytest.fail("valid cache must avoid requests"),
        request_budget=0,
    )
    assert resumed["status"] == "complete"
    assert resumed["network_request_count"] == 0
    assert resumed["cache_hit_count"] == 10
    assert validate_evidence_run(resumed["manifest_path"], request_plan=plan["manifest_path"])["status"] == "complete"


def test_budget_interrupt_is_recoverable_and_corrupt_cache_fails_closed(tmp_path):
    plan = _build(tmp_path)
    calls = []
    partial = execute_request_plan(
        plan["manifest_path"], tmp_path / "evidence", _requester(calls), request_budget=3
    )
    assert partial["status"] == "budget_exhausted"
    assert partial["completed_request_count"] == 3
    assert partial["cache_miss_count"] == 7
    assert len(partial["missing_requests"]) == 7

    completed = execute_request_plan(
        plan["manifest_path"], tmp_path / "evidence", _requester(calls), request_budget=7
    )
    assert completed["status"] == "complete"
    assert completed["cache_hit_count"] == 3
    assert completed["network_request_count"] == 7

    response_path = Path(completed["executions"][0]["response_path"])
    payload = json.loads(response_path.read_text())
    payload["records"] = []
    response_path.write_text(json.dumps(payload))
    with pytest.raises(RequestPlanError, match="cached response hash mismatch"):
        execute_request_plan(
            plan["manifest_path"], tmp_path / "evidence", _requester([]), request_budget=0
        )


def test_response_outside_query_geometry_is_rejected(tmp_path):
    plan = _build(tmp_path)

    def bad_requester(api_name, params, fields):
        return ResponseEnvelope(
            records=({"ts_code": "000001.SZ", "trade_date": "20231229"},),
            response_fields=tuple(fields),
        )

    with pytest.raises(RequestPlanError, match="outside"):
        execute_request_plan(plan["manifest_path"], tmp_path / "evidence", bad_requester, request_budget=10)

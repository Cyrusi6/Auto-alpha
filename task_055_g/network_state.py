"""Immutable Task 055-G dynamic network remediation state machine.

The state machine is deliberately transport-agnostic.  Network execution is
only possible when a caller supplies an executor *and* explicit authorization;
offline orchestration never inspects credentials or the process environment.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from task_055_f.contracts import (
    DAILY_FIELDS,
    MAX_DATE,
    MAX_LOGICAL_REQUESTS,
    MAX_PHYSICAL_ATTEMPTS,
    MAX_UNIQUE_SECURITY_DATES,
    NETWORK_PLAN_SCHEMA,
    SUSPEND_FIELDS,
)
from task_055_f.read_ledger import canonical_hash
from task_055_f.transport import evidence_use_identity, transport_identity


LEDGER_SCHEMA = "task055g_append_only_network_state_ledger_v1"
PLAN_SCHEMA = "task055g_dynamic_network_plan_v1"
CONSOLIDATION_SCHEMA = "task055g_execution_consolidation_v1"
APPLY_SCHEMA = "task055g_response_apply_v1"
EXECUTION_SCHEMA = "task055g_request_execution_v1"
NEXT_ROUND_SCHEMA = "task055g_next_round_transition_v1"
FINAL_VERIFY_SCHEMA = "task055g_network_state_verification_v1"

SUCCESS_OUTCOMES = {
    "positive_response",
    "negative_vendor_response",
    "validated_cache_hit",
}
FAILURE_OUTCOMES = {
    "failed",
    "quarantined",
    "invalid_response",
    "request_error",
}


class Task055GNetworkStateError(RuntimeError):
    """Fail-closed state-machine error."""


def seal_round_one_l1_plan(
    *,
    frontier_keys: Sequence[Sequence[str]],
    lineage: Mapping[str, Any],
    round_id: int = 1,
) -> dict[str, Any]:
    """Seal the exact-daily Fee-aware first frontier without network access."""

    frontier = sorted({(str(item[0]), str(item[1])) for item in frontier_keys})
    if len(frontier) > MAX_UNIQUE_SECURITY_DATES:
        raise Task055GNetworkStateError("round_one_frontier_unique_key_budget_exceeded")
    if any(date > MAX_DATE for _, date in frontier):
        raise Task055GNetworkStateError("round_one_frontier_future_date")
    frontier_root = canonical_hash(frontier)
    normalized_lineage = dict(lineage)
    normalized_lineage["frontier_root"] = frontier_root
    normalized_lineage.setdefault("key_root", frontier_root)
    parent_hash = canonical_hash(normalized_lineage)
    requests = [
        _request(
            stage="L1",
            round_id=round_id,
            api_name="daily",
            ts_code=code,
            trade_date=date,
            fields=DAILY_FIELDS,
            parent_plan_hash=parent_hash,
            frontier_root=frontier_root,
        )
        for code, date in frontier
    ]
    if len(requests) > MAX_LOGICAL_REQUESTS:
        raise Task055GNetworkStateError("round_one_frontier_logical_budget_exceeded")
    return _make_plan(
        stage="L1",
        round_id=round_id,
        requests=requests,
        lineage=normalized_lineage,
        frontier_root=frontier_root,
        parent_apply_hash=None,
        status="sealed_round_one_exact_daily_l1",
        extra={
            "network_executed": False,
            "token_read": False,
            "frontier_semantics": "round_1_first_terminal_held_mark_blocker_not_total_gap_count",
            "l2_requests": [],
            "l2_generation_gate": "only_after_l1_apply_and_truth_fee_aware_frontier_rebuild",
        },
    )


def consolidate(
    *,
    state_root: str | Path,
    plan_manifest: str | Path | Mapping[str, Any],
    execution_manifests: Sequence[str | Path | Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Register one immutable plan and merge its execution evidence.

    Replaying the same execution artifact is idempotent.  Failed requests stay
    retryable while successful/cache-hit requests are never charged twice.
    """

    root = Path(state_root)
    plan = _normalize_plan(plan_manifest)
    _register_plan(root, plan)
    for artifact in execution_manifests:
        _ingest_execution_artifact(root, plan, artifact)
    states = _request_states_for_plan(root, plan)
    successful = sum(value["status"] in {"succeeded", "cache_hit", "applied"} for value in states.values())
    failed = sum(value["status"] == "failed" for value in states.values())
    pending = len(plan["requests"]) - successful
    if not plan["requests"]:
        status = "responses_complete_ready_for_apply"
    elif pending == 0:
        status = "responses_complete_ready_for_apply"
    elif successful or failed:
        status = "responses_partial"
    else:
        status = "waiting_for_network"
    payload = {
        "schema_version": CONSOLIDATION_SCHEMA,
        "status": status,
        "stage": plan["stage"],
        "round_id": plan["round_id"],
        "plan_hash": plan["plan_hash"],
        "plan": plan,
        "request_count": len(plan["requests"]),
        "successful_request_count": successful,
        "failed_request_count": failed,
        "pending_request_count": pending,
        "request_states": [states[key] for key in sorted(states)],
        "lineage": dict(plan["lineage"]),
        "ledger": ledger_summary(root),
    }
    return _publish_state_artifact(root, "consolidation", payload, "consolidation_manifest.json")


def apply_l1(
    *,
    state_root: str | Path,
    consolidation_manifest: str | Path | Mapping[str, Any],
) -> dict[str, Any]:
    return _apply_plan(
        state_root=Path(state_root),
        consolidation_manifest=consolidation_manifest,
        expected_stage="L1",
        artifact_stage="l1_apply",
    )


def execute_l1_canary(
    *,
    state_root: str | Path,
    plan_manifest: str | Path | Mapping[str, Any],
    allow_network: bool = False,
    sealed_plan_hash: str | None = None,
    request_executor: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    return _execute_canary(
        state_root=Path(state_root),
        plan_manifest=plan_manifest,
        expected_stage="L1",
        allow_network=allow_network,
        sealed_plan_hash=sealed_plan_hash,
        request_executor=request_executor,
        artifact_stage="l1_canary",
    )


def execute_l1_resume(
    *,
    state_root: str | Path,
    plan_manifest: str | Path | Mapping[str, Any],
    canary_manifest: str | Path | Mapping[str, Any],
    allow_network: bool = False,
    sealed_plan_hash: str | None = None,
    request_executor: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    return _execute_resume(
        state_root=Path(state_root),
        plan_manifest=plan_manifest,
        canary_manifest=canary_manifest,
        expected_stage="L1",
        allow_network=allow_network,
        sealed_plan_hash=sealed_plan_hash,
        request_executor=request_executor,
        artifact_stage="l1_resume",
    )


def build_l2_plan(
    *,
    state_root: str | Path,
    l1_apply_manifest: str | Path | Mapping[str, Any],
    truth_manifest: str | Path | Mapping[str, Any],
    frontier_manifest: str | Path | Mapping[str, Any],
) -> dict[str, Any]:
    """Build exact-date suspend_d requests only after L1 apply + rebuild."""

    root = Path(state_root)
    apply = _validate_apply(l1_apply_manifest, expected_stage="L1")
    truth = _load_rebuilt_truth(truth_manifest)
    frontier = _load_rebuilt_frontier(frontier_manifest)
    lineage = _validate_rebuild_lineage(apply, truth, frontier)
    truth_by_key = {
        (str(row.get("ts_code")), str(row.get("trade_date"))): row
        for row in truth["records"]
    }
    requests: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    for code, date in frontier["frontier_keys"]:
        row = truth_by_key.get((code, date))
        if row is None:
            raise Task055GNetworkStateError(f"l2_truth_key_missing:{code}:{date}")
        suspend_type = str(row.get("suspend_type") or "none")
        state = str(row.get("state") or "")
        if suspend_type != "none":
            excluded.append({"ts_code": code, "trade_date": date, "reason": f"existing_suspend_state:{suspend_type}"})
            continue
        if state not in {"DATA_SOURCE_GAP", "RAW_BAR_REQUIRED_FIELD_INVALID", "SOURCE_NORMALIZATION_ZERO_FILL"}:
            excluded.append({"ts_code": code, "trade_date": date, "reason": f"not_suspend_l2_eligible:{state}"})
            continue
        requests.append(
            _request(
                stage="L2",
                round_id=int(apply["round_id"]),
                api_name="suspend_d",
                ts_code=code,
                trade_date=date,
                fields=SUSPEND_FIELDS,
                parent_plan_hash=str(apply["content_hash"]),
                frontier_root=frontier["frontier_root"],
            )
        )
    plan = _make_plan(
        stage="L2",
        round_id=int(apply["round_id"]),
        requests=requests,
        lineage=lineage,
        frontier_root=frontier["frontier_root"],
        parent_apply_hash=str(apply["content_hash"]),
        status="sealed_dynamic_exact_suspend_l2",
        extra={
            "excluded_frontier_keys": excluded,
            "empty_response_semantics": "vendor_absence_only_not_full_day_suspension_proof",
            "generation_gate": "l1_all_applied_then_truth_and_frontier_rebuilt",
        },
    )
    _register_plan(root, plan)
    return _publish_state_artifact(root, "l2_plan", plan, "l2_plan_manifest.json")


def execute_l2_canary(
    *,
    state_root: str | Path,
    plan_manifest: str | Path | Mapping[str, Any],
    allow_network: bool = False,
    sealed_plan_hash: str | None = None,
    request_executor: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Execute exactly one L2 request and stop.

    The default path is offline and exits before invoking the executor.  The
    executor is intentionally injected so this module never reads credentials.
    """

    return _execute_canary(
        state_root=Path(state_root),
        plan_manifest=plan_manifest,
        expected_stage="L2",
        allow_network=allow_network,
        sealed_plan_hash=sealed_plan_hash,
        request_executor=request_executor,
        artifact_stage="l2_canary",
    )


def execute_l2_resume(
    *,
    state_root: str | Path,
    plan_manifest: str | Path | Mapping[str, Any],
    canary_manifest: str | Path | Mapping[str, Any],
    allow_network: bool = False,
    sealed_plan_hash: str | None = None,
    request_executor: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    return _execute_resume(
        state_root=Path(state_root),
        plan_manifest=plan_manifest,
        canary_manifest=canary_manifest,
        expected_stage="L2",
        allow_network=allow_network,
        sealed_plan_hash=sealed_plan_hash,
        request_executor=request_executor,
        artifact_stage="l2_resume",
    )


def _execute_canary(
    *,
    state_root: Path,
    plan_manifest: str | Path | Mapping[str, Any],
    expected_stage: str,
    allow_network: bool,
    sealed_plan_hash: str | None,
    request_executor: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None,
    artifact_stage: str,
) -> dict[str, Any]:
    plan = _normalize_plan(plan_manifest)
    if plan["stage"] != expected_stage:
        raise Task055GNetworkStateError(f"{expected_stage.lower()}_canary_plan_stage_invalid")
    _authorize_execution(plan, allow_network, sealed_plan_hash, request_executor)
    _register_plan(state_root, plan)
    pending = _pending_requests(state_root, plan)
    if not pending:
        raise Task055GNetworkStateError(f"{expected_stage.lower()}_canary_has_no_pending_request")
    result = _execute_request(state_root, plan, pending[0], request_executor)
    payload = {
        "schema_version": EXECUTION_SCHEMA,
        "status": "canary_completed",
        "stage": expected_stage,
        "round_id": plan["round_id"],
        "plan_hash": plan["plan_hash"],
        "must_stop_after_canary": True,
        "batch_started": False,
        "attempts_recorded_in_ledger": True,
        "results": [result],
        "ledger": ledger_summary(state_root),
    }
    return _publish_state_artifact(
        state_root,
        artifact_stage,
        payload,
        f"{artifact_stage}_manifest.json",
    )


def _execute_resume(
    *,
    state_root: Path,
    plan_manifest: str | Path | Mapping[str, Any],
    canary_manifest: str | Path | Mapping[str, Any],
    expected_stage: str,
    allow_network: bool,
    sealed_plan_hash: str | None,
    request_executor: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None,
    artifact_stage: str,
) -> dict[str, Any]:
    plan = _normalize_plan(plan_manifest)
    if plan["stage"] != expected_stage:
        raise Task055GNetworkStateError(f"{expected_stage.lower()}_resume_plan_stage_invalid")
    canary = _validate_state_artifact(canary_manifest, EXECUTION_SCHEMA)
    if (
        canary.get("status") != "canary_completed"
        or canary.get("stage") != expected_stage
        or canary.get("must_stop_after_canary") is not True
        or canary.get("batch_started") is not False
        or canary.get("plan_hash") != plan["plan_hash"]
        or len(canary.get("results") or ()) != 1
    ):
        raise Task055GNetworkStateError(
            f"{expected_stage.lower()}_resume_canary_evidence_invalid"
        )
    _authorize_execution(plan, allow_network, sealed_plan_hash, request_executor)
    _register_plan(state_root, plan)
    results = [
        _execute_request(state_root, plan, request, request_executor)
        for request in _pending_requests(state_root, plan)
    ]
    payload = {
        "schema_version": EXECUTION_SCHEMA,
        "status": "resume_completed",
        "stage": expected_stage,
        "round_id": plan["round_id"],
        "plan_hash": plan["plan_hash"],
        "canary_content_hash": canary["content_hash"],
        "attempts_recorded_in_ledger": True,
        "results": results,
        "remaining_request_count": len(_pending_requests(state_root, plan)),
        "ledger": ledger_summary(state_root),
    }
    return _publish_state_artifact(
        state_root,
        artifact_stage,
        payload,
        f"{artifact_stage}_manifest.json",
    )


def apply_l2(
    *,
    state_root: str | Path,
    plan_manifest: str | Path | Mapping[str, Any],
    execution_manifests: Sequence[str | Path | Mapping[str, Any]] = (),
) -> dict[str, Any]:
    root = Path(state_root)
    consolidation = consolidate(
        state_root=root,
        plan_manifest=plan_manifest,
        execution_manifests=execution_manifests,
    )
    return _apply_plan(
        state_root=root,
        consolidation_manifest=consolidation,
        expected_stage="L2",
        artifact_stage="l2_apply",
    )


def next_round(
    *,
    state_root: str | Path,
    parent_apply_manifest: str | Path | Mapping[str, Any],
    truth_manifest: str | Path | Mapping[str, Any],
    frontier_manifest: str | Path | Mapping[str, Any],
) -> dict[str, Any]:
    """Create the next exact-daily L1 frontier after a full apply/rebuild."""

    root = Path(state_root)
    apply = _validate_apply(parent_apply_manifest)
    truth = _load_rebuilt_truth(truth_manifest)
    frontier = _load_rebuilt_frontier(frontier_manifest)
    lineage = _validate_rebuild_lineage(apply, truth, frontier)
    queried_daily = {
        str(row.get("transport_hash"))
        for row in read_ledger(root)["events"]
        if row.get("event") == "request_registered" and row.get("api_name") == "daily"
    }
    requests: list[dict[str, Any]] = []
    exhausted: list[dict[str, str]] = []
    round_id = int(apply["round_id"]) + 1
    for code, date in frontier["frontier_keys"]:
        request = _request(
            stage="L1",
            round_id=round_id,
            api_name="daily",
            ts_code=code,
            trade_date=date,
            fields=DAILY_FIELDS,
            parent_plan_hash=str(apply["content_hash"]),
            frontier_root=frontier["frontier_root"],
        )
        if request["transport_hash"] in queried_daily:
            exhausted.append({"ts_code": code, "trade_date": date, "reason": "exact_daily_already_applied"})
        else:
            requests.append(request)
    if not frontier["frontier_keys"]:
        status = "closure_complete"
    elif not requests:
        status = "blocked_authority_anchor_or_policy_required"
    else:
        status = "sealed_next_round_exact_daily_l1"
    plan = _make_plan(
        stage="L1",
        round_id=round_id,
        requests=requests,
        lineage=lineage,
        frontier_root=frontier["frontier_root"],
        parent_apply_hash=str(apply["content_hash"]),
        status=status,
        extra={"exhausted_frontier_keys": exhausted},
    )
    if requests:
        _register_plan(root, plan)
    return _publish_state_artifact(
        root,
        "next_round",
        {**plan, "schema_version": NEXT_ROUND_SCHEMA},
        "next_round_manifest.json",
    )


def run_until_blocked(
    *,
    state_root: str | Path,
    plan_manifest: str | Path | Mapping[str, Any] | None = None,
    execution_manifests: Sequence[str | Path | Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Advance only offline-safe transitions and report the exact blocking gate."""

    root = Path(state_root)
    consolidation = None
    if plan_manifest is not None:
        consolidation = consolidate(
            state_root=root,
            plan_manifest=plan_manifest,
            execution_manifests=execution_manifests,
        )
    ledger = ledger_summary(root)
    plans = _registered_plans(root)
    blocking = "no_registered_plan"
    for plan in reversed(plans):
        states = _request_states_for_plan(root, plan)
        statuses = {row["status"] for row in states.values()}
        if any(status not in {"succeeded", "cache_hit", "applied"} for status in statuses):
            blocking = "waiting_for_network_authorization"
            break
        if any(status != "applied" for status in statuses):
            blocking = "waiting_for_response_apply"
            break
        blocking = "waiting_for_truth_frontier_rebuild_or_next_round"
        break
    payload = {
        "schema_version": "task055g_run_until_blocked_v1",
        "status": "blocked",
        "blocking_gate": blocking,
        "offline_only": True,
        "consolidation_content_hash": consolidation.get("content_hash") if consolidation else None,
        "ledger": ledger,
    }
    return _publish_state_artifact(root, "run_until_blocked", payload, "run_until_blocked_manifest.json")


def final_verify(*, state_root: str | Path) -> dict[str, Any]:
    """Independently verify the append-only ledger and all native artifacts."""

    root = Path(state_root)
    payload = _recompute_final_verification(root, read_ledger(root))
    return _publish_state_artifact(
        root,
        "final_verify",
        payload,
        "network_state_verification.json",
        record=False,
    )


def verify_state_read_only(*, state_root: str | Path) -> dict[str, Any]:
    """Recompute network-state proof without mutating locks, pointers, or artifacts."""

    root = Path(state_root)
    payload = _recompute_final_verification(root, read_ledger_read_only(root))
    content_hash = canonical_hash(payload)
    return payload | {
        "content_hash": content_hash,
        "generation_id": f"final_verify_{content_hash[:24]}",
    }


def _recompute_final_verification(
    root: Path,
    ledger: Mapping[str, Any],
) -> dict[str, Any]:
    artifacts = _all_state_artifacts(root)
    artifact_hashes: dict[str, str] = {}
    applied_plan_hashes: set[str] = set()
    l1_apply_hashes: set[str] = set()
    for path in artifacts:
        payload = _load_content_manifest(path)
        artifact_hashes[str(path.relative_to(root))] = payload["content_hash"]
        if payload.get("schema_version") == APPLY_SCHEMA:
            plan = _normalize_plan(payload["plan"])
            states = _request_states_from_events(ledger["events"], plan)
            if any(value["status"] != "applied" for value in states.values()):
                raise Task055GNetworkStateError("final_verify_apply_has_unapplied_request")
            applied_plan_hashes.add(plan["plan_hash"])
            if payload.get("stage") == "L1":
                l1_apply_hashes.add(payload["content_hash"])
        if payload.get("schema_version") == PLAN_SCHEMA and payload.get("stage") == "L2":
            if payload.get("parent_apply_hash") not in l1_apply_hashes:
                raise Task055GNetworkStateError("final_verify_l2_precedes_l1_apply")
        if payload.get("schema_version") == NEXT_ROUND_SCHEMA:
            plan = _normalize_plan(payload)
            if plan.get("parent_apply_hash") not in {
                item.get("content_hash")
                for item in (_load_content_manifest(candidate) for candidate in artifacts)
                if item.get("schema_version") == APPLY_SCHEMA
            }:
                raise Task055GNetworkStateError("final_verify_next_round_parent_apply_missing")
    summary = _summarize_events(ledger["events"])
    if summary["unique_security_date_count"] > MAX_UNIQUE_SECURITY_DATES:
        raise Task055GNetworkStateError("final_verify_unique_key_budget_exceeded")
    if summary["logical_request_count"] > MAX_LOGICAL_REQUESTS:
        raise Task055GNetworkStateError("final_verify_logical_budget_exceeded")
    if summary["physical_attempt_count"] > MAX_PHYSICAL_ATTEMPTS:
        raise Task055GNetworkStateError("final_verify_physical_budget_exceeded")
    payload = {
        "schema_version": FINAL_VERIFY_SCHEMA,
        "status": "verified",
        "network_accessed": summary["network_accessed"],
        "request_count": summary["logical_request_count"],
        "max_request_date": summary["max_request_date"],
        "logical_request_count": summary["logical_request_count"],
        "physical_attempt_count": summary["physical_attempt_count"],
        "unique_security_date_count": summary["unique_security_date_count"],
        "terminal_counts": summary["terminal_counts"],
        "ledger_root": summary["ledger_root"],
        "artifact_count": len(artifacts),
        "artifact_root": canonical_hash(sorted(artifact_hashes.items())),
        "applied_plan_count": len(applied_plan_hashes),
        "offline_default_proven": summary["network_accessed"] is False,
    }
    return payload


def read_ledger(state_root: str | Path) -> dict[str, Any]:
    root = Path(state_root) / "network_ledger"
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / "ledger.lock"
    lock_path.touch(exist_ok=True)
    with lock_path.open("r+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
        try:
            events = _read_events_unlocked(root / "events.jsonl")
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return {"schema_version": LEDGER_SCHEMA, "events": events, **_summarize_events(events)}


def read_ledger_read_only(state_root: str | Path) -> dict[str, Any]:
    root = Path(state_root) / "network_ledger"
    lock_path = root / "ledger.lock"
    event_path = root / "events.jsonl"
    if not root.is_dir() or not lock_path.is_file() or not event_path.is_file():
        raise Task055GNetworkStateError("network_ledger_read_only_inputs_missing")
    with lock_path.open("r") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
        try:
            events = _read_events_unlocked(event_path)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return {"schema_version": LEDGER_SCHEMA, "events": events, **_summarize_events(events)}


def ledger_summary(state_root: str | Path) -> dict[str, Any]:
    ledger = read_ledger(state_root)
    return {key: value for key, value in ledger.items() if key != "events"}


def _apply_plan(
    *,
    state_root: Path,
    consolidation_manifest: str | Path | Mapping[str, Any],
    expected_stage: str,
    artifact_stage: str,
) -> dict[str, Any]:
    consolidation = _validate_state_artifact(consolidation_manifest, CONSOLIDATION_SCHEMA)
    plan = _normalize_plan(consolidation["plan"])
    if plan["stage"] != expected_stage:
        raise Task055GNetworkStateError(f"{artifact_stage}_plan_stage_invalid")
    states = _request_states_for_plan(state_root, plan)
    incomplete = [key for key, value in states.items() if value["status"] not in {"succeeded", "cache_hit", "applied"}]
    if incomplete:
        raise Task055GNetworkStateError(f"{artifact_stage}_requests_not_complete:{len(incomplete)}")
    results = []
    apply_events = []
    for transport_hash in sorted(states):
        result = states[transport_hash].get("result")
        if result is None:
            raise Task055GNetworkStateError(f"{artifact_stage}_successful_result_missing")
        results.append(result)
        apply_events.append(
            {
                "event_id": canonical_hash(["response_applied", plan["plan_hash"], transport_hash, result["result_hash"]]),
                "event": "response_applied",
                "plan_hash": plan["plan_hash"],
                "stage": expected_stage,
                "round_id": plan["round_id"],
                "transport_hash": transport_hash,
                "ts_code": result["request"]["ts_code"],
                "trade_date": result["request"]["trade_date"],
                "result_hash": result["result_hash"],
            }
        )
    _append_events(state_root, apply_events)
    cache_inputs = [
        {
            "api_name": result["request"]["api_name"],
            "params": result["request"]["params"],
            "fields": result["request"]["fields"],
            "transport_hash": result["request"]["transport_hash"],
            "cache_relative_path": result.get("cache_relative_path"),
            "cache_sha256": result.get("cache_sha256"),
            "result_hash": result["result_hash"],
        }
        for result in results
    ]
    response_lineage_root = canonical_hash([result["result_hash"] for result in results])
    cache_input_root = canonical_hash(cache_inputs)
    payload = {
        "schema_version": APPLY_SCHEMA,
        "status": "applied",
        "stage": expected_stage,
        "round_id": plan["round_id"],
        "plan_hash": plan["plan_hash"],
        "plan": plan,
        "parent_apply_hash": plan.get("parent_apply_hash"),
        "lineage": dict(plan["lineage"]),
        "result_count": len(results),
        "results": results,
        "response_lineage_root": response_lineage_root,
        "cache_inputs": cache_inputs,
        "cache_input_root": cache_input_root,
        "application_actions": [_application_action(expected_stage, result) for result in results],
        "next_truth_required_inputs": {
            "parent_network_apply_hash_source": "apply_manifest.content_hash",
            "response_lineage_root": response_lineage_root,
            "cache_input_root": cache_input_root,
        },
        "ledger": ledger_summary(state_root),
    }
    return _publish_state_artifact(state_root, artifact_stage, payload, f"{artifact_stage}_manifest.json")


def _validate_apply(
    value: str | Path | Mapping[str, Any], expected_stage: str | None = None
) -> dict[str, Any]:
    payload = _validate_state_artifact(value, APPLY_SCHEMA)
    if payload.get("status") != "applied":
        raise Task055GNetworkStateError("network_apply_status_invalid")
    if expected_stage and payload.get("stage") != expected_stage:
        raise Task055GNetworkStateError("network_apply_stage_invalid")
    plan = _normalize_plan(payload.get("plan") or {})
    results = list(payload.get("results") or ())
    if len(results) != len(plan["requests"]) or payload.get("result_count") != len(results):
        raise Task055GNetworkStateError("network_apply_result_count_invalid")
    if canonical_hash([result["result_hash"] for result in results]) != payload.get("response_lineage_root"):
        raise Task055GNetworkStateError("network_apply_response_lineage_invalid")
    if canonical_hash(payload.get("cache_inputs") or ()) != payload.get("cache_input_root"):
        raise Task055GNetworkStateError("network_apply_cache_input_root_invalid")
    return payload


def _validate_rebuild_lineage(
    apply: Mapping[str, Any], truth: Mapping[str, Any], frontier: Mapping[str, Any]
) -> dict[str, Any]:
    truth_lineage = dict(truth.get("lineage") or {})
    frontier_lineage = dict(frontier.get("lineage") or {})
    parent_apply = str(apply["content_hash"])
    if _first(truth_lineage, "parent_network_apply_hash", "network_apply_content_hash", "parent_apply_hash") != parent_apply:
        raise Task055GNetworkStateError("rebuilt_truth_parent_apply_mismatch")
    if _first(truth_lineage, "response_lineage_root") != apply.get("response_lineage_root"):
        raise Task055GNetworkStateError("rebuilt_truth_response_lineage_mismatch")
    if _first(truth_lineage, "cache_input_root") != apply.get("cache_input_root"):
        raise Task055GNetworkStateError("rebuilt_truth_cache_input_root_mismatch")
    truth_hash = str(truth["content_hash"])
    if _first(frontier_lineage, "truth_v2_content_hash", "truth_content_hash") != truth_hash:
        raise Task055GNetworkStateError("rebuilt_frontier_truth_hash_mismatch")
    if _first(frontier_lineage, "parent_network_apply_hash", "parent_apply_hash") != parent_apply:
        raise Task055GNetworkStateError("rebuilt_frontier_parent_apply_mismatch")
    matrix_hash = _required_lineage(frontier_lineage, "matrix_content_hash")
    bundle_hash = _first(frontier_lineage, "simulation_bundle_content_hash", "bundle_content_hash")
    fee_hash = _first(frontier_lineage, "fee_schedule_content_hash", "fee_content_hash")
    if not bundle_hash or not fee_hash:
        raise Task055GNetworkStateError("rebuilt_frontier_bundle_or_fee_lineage_missing")
    return {
        "parent_apply_hash": parent_apply,
        "truth_content_hash": truth_hash,
        "matrix_content_hash": matrix_hash,
        "simulation_bundle_content_hash": bundle_hash,
        "fee_schedule_content_hash": fee_hash,
        "frontier_content_hash": frontier["content_hash"],
        "frontier_root": frontier["frontier_root"],
        "key_root": truth.get("key_root") or canonical_hash(sorted((row["ts_code"], row["trade_date"]) for row in truth["records"])),
        "response_lineage_root": apply["response_lineage_root"],
        "cache_input_root": apply["cache_input_root"],
    }


def _load_rebuilt_truth(value: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    payload = _load_content_manifest(value)
    records = list(payload.get("records") or ())
    if not records and isinstance(value, (str, Path)) and payload.get("schema_version") == "task055f_security_date_truth_v2":
        from task_055_f.truth_v2 import validate_truth_v2

        return validate_truth_v2(value)
    keys = [(str(row.get("ts_code")), str(row.get("trade_date"))) for row in records]
    if len(keys) != len(set(keys)):
        raise Task055GNetworkStateError("rebuilt_truth_duplicate_keys")
    if any(date > MAX_DATE for _, date in keys):
        raise Task055GNetworkStateError("rebuilt_truth_future_date")
    expected_root = payload.get("key_root")
    if expected_root and expected_root != canonical_hash(sorted(keys)):
        raise Task055GNetworkStateError("rebuilt_truth_key_root_invalid")
    if not payload.get("lineage"):
        raise Task055GNetworkStateError("rebuilt_truth_lineage_missing")
    return payload | {"records": records}


def _load_rebuilt_frontier(value: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    payload = _load_content_manifest(value)
    keys = payload.get("frontier_keys") or payload.get("round_one_frontier")
    if keys is None and isinstance(value, (str, Path)) and payload.get("schema_version") == "task055f_causal_frontier_v1":
        from task_055_f.causal import validate_causal_frontier

        validated = validate_causal_frontier(value)
        keys = sorted(
            {
                (str(row["blocker"]["ts_code"]), str(row["blocker"]["trade_date"]))
                for row in validated["run_rows"]
                if (row.get("blocker") or {}).get("code") == "held_position_mark_unavailable"
            }
        )
        payload = validated
    normalized = sorted({(str(item[0]), str(item[1])) for item in (keys or ())})
    if any(date > MAX_DATE for _, date in normalized):
        raise Task055GNetworkStateError("rebuilt_frontier_future_date")
    root = payload.get("missing_key_root") or payload.get("frontier_root") or canonical_hash(normalized)
    if root != canonical_hash(normalized):
        raise Task055GNetworkStateError("rebuilt_frontier_root_invalid")
    if not payload.get("lineage"):
        raise Task055GNetworkStateError("rebuilt_frontier_lineage_missing")
    return payload | {"frontier_keys": normalized, "frontier_root": root}


def _normalize_plan(value: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    payload = _load_json(value)
    if payload.get("schema_version") == NETWORK_PLAN_SCHEMA:
        stage = "L1" if payload.get("status") == "sealed_round_one_daily_only" else "L2"
        round_id = int(payload.get("round_id") or 1)
        lineage = {
            "truth_content_hash": payload.get("truth_v2_content_hash"),
            "matrix_content_hash": payload.get("matrix_content_hash"),
            "simulation_bundle_content_hash": payload.get("simulation_bundle_content_hash"),
            "fee_schedule_content_hash": payload.get("fee_schedule_content_hash"),
            "frontier_root": payload.get("frontier_root"),
            "key_root": payload.get("frontier_root"),
            "response_lineage_root": payload.get("response_lineage_root"),
        }
        plan_hash = str(payload.get("plan_hash") or "")
        unsigned = {key: item for key, item in payload.items() if key != "plan_hash"}
        if plan_hash != canonical_hash(unsigned):
            raise Task055GNetworkStateError("legacy_plan_hash_invalid")
        normalized = {
            "schema_version": PLAN_SCHEMA,
            "status": "sealed_round_one_exact_daily_l1",
            "stage": stage,
            "round_id": round_id,
            "frontier_root": payload.get("frontier_root"),
            "parent_apply_hash": payload.get("parent_l1_apply_hash"),
            "lineage": lineage,
            "requests": [dict(row) for row in payload.get("requests") or ()],
            "source_plan_schema": NETWORK_PLAN_SCHEMA,
            "source_plan_hash": plan_hash,
        }
        normalized["plan_hash"] = canonical_hash(normalized)
    elif payload.get("schema_version") in {PLAN_SCHEMA, NEXT_ROUND_SCHEMA}:
        normalized = {
            key: item
            for key, item in payload.items()
            if key not in {"content_hash", "generation_id", "manifest_path"}
        }
        if payload.get("schema_version") == NEXT_ROUND_SCHEMA:
            normalized["schema_version"] = PLAN_SCHEMA
        plan_hash = str(normalized.get("plan_hash") or "")
        unsigned = {key: item for key, item in normalized.items() if key not in {"plan_hash", "content_hash", "generation_id", "manifest_path"}}
        if plan_hash != canonical_hash(unsigned):
            raise Task055GNetworkStateError("dynamic_plan_hash_invalid")
    else:
        raise Task055GNetworkStateError("network_plan_schema_invalid")
    requests = [dict(row) for row in normalized.get("requests") or ()]
    if normalized.get("stage") not in {"L1", "L2"} or int(normalized.get("round_id") or 0) < 1:
        raise Task055GNetworkStateError("network_plan_stage_or_round_invalid")
    expected_api = "daily" if normalized["stage"] == "L1" else "suspend_d"
    expected_fields = DAILY_FIELDS if expected_api == "daily" else SUSPEND_FIELDS
    seen = set()
    for request in requests:
        if request.get("api_name") != expected_api or tuple(request.get("fields") or ()) != tuple(expected_fields):
            raise Task055GNetworkStateError("network_plan_api_or_fields_invalid")
        params = dict(request.get("params") or {})
        if set(params) != {"ts_code", "trade_date"}:
            raise Task055GNetworkStateError("network_plan_not_exact_security_date")
        code, date = str(params["ts_code"]), str(params["trade_date"])
        if date > MAX_DATE:
            raise Task055GNetworkStateError("network_plan_date_exceeds_boundary")
        expected_transport = transport_identity(expected_api, params, expected_fields)
        if request.get("transport_hash") != expected_transport:
            raise Task055GNetworkStateError("network_plan_transport_hash_invalid")
        if expected_transport in seen:
            raise Task055GNetworkStateError("network_plan_duplicate_transport")
        seen.add(expected_transport)
        request.update({"ts_code": code, "trade_date": date, "stage": normalized["stage"], "round_id": normalized["round_id"]})
    normalized["requests"] = requests
    lineage = dict(normalized.get("lineage") or {})
    for key in (
        "matrix_content_hash",
        "simulation_bundle_content_hash",
        "fee_schedule_content_hash",
        "frontier_root",
        "key_root",
    ):
        if not lineage.get(key):
            raise Task055GNetworkStateError(f"network_plan_lineage_missing:{key}")
    return normalized


def _make_plan(
    *,
    stage: str,
    round_id: int,
    requests: Sequence[Mapping[str, Any]],
    lineage: Mapping[str, Any],
    frontier_root: str,
    parent_apply_hash: str | None,
    status: str,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": PLAN_SCHEMA,
        "status": status,
        "stage": stage,
        "round_id": round_id,
        "frontier_root": frontier_root,
        "parent_apply_hash": parent_apply_hash,
        "lineage": dict(lineage),
        "requests": [dict(row) for row in requests],
        "limits": {
            "unique_security_dates": MAX_UNIQUE_SECURITY_DATES,
            "logical_requests": MAX_LOGICAL_REQUESTS,
            "physical_attempts": MAX_PHYSICAL_ATTEMPTS,
        },
        **dict(extra or {}),
    }
    payload["plan_hash"] = canonical_hash(payload)
    return payload


def _request(
    *,
    stage: str,
    round_id: int,
    api_name: str,
    ts_code: str,
    trade_date: str,
    fields: Iterable[str],
    parent_plan_hash: str,
    frontier_root: str,
) -> dict[str, Any]:
    params = {"ts_code": ts_code, "trade_date": trade_date}
    field_list = list(fields)
    transport_hash = transport_identity(api_name, params, field_list)
    return {
        "stage": stage,
        "round_id": round_id,
        "api_name": api_name,
        "params": params,
        "fields": field_list,
        "ts_code": ts_code,
        "trade_date": trade_date,
        "transport_hash": transport_hash,
        "evidence_use_hash": evidence_use_identity(
            stage=f"task055g_{stage.lower()}_exact",
            parent_plan_hash=parent_plan_hash,
            frontier_root=frontier_root,
            transport_hash=transport_hash,
        ),
    }


def _register_plan(state_root: Path, plan: Mapping[str, Any]) -> None:
    events = [
        {
            "event_id": canonical_hash(["plan_registered", plan["plan_hash"]]),
            "event": "plan_registered",
            "plan_hash": plan["plan_hash"],
            "stage": plan["stage"],
            "round_id": plan["round_id"],
            "request_count": len(plan["requests"]),
            "frontier_root": plan.get("frontier_root"),
            "parent_apply_hash": plan.get("parent_apply_hash"),
            "plan": dict(plan),
        }
    ]
    for request in plan["requests"]:
        events.append(
            {
                "event_id": canonical_hash(["request_registered", plan["plan_hash"], request["transport_hash"]]),
                "event": "request_registered",
                "plan_hash": plan["plan_hash"],
                "stage": plan["stage"],
                "round_id": plan["round_id"],
                "api_name": request["api_name"],
                "transport_hash": request["transport_hash"],
                "evidence_use_hash": request.get("evidence_use_hash"),
                "ts_code": request["ts_code"],
                "trade_date": request["trade_date"],
                "request": dict(request),
            }
        )
    _append_events(state_root, events)


def _ingest_execution_artifact(
    state_root: Path,
    plan: Mapping[str, Any],
    value: str | Path | Mapping[str, Any],
    *,
    attempts_already_recorded: bool = False,
) -> None:
    artifact = _load_json(value)
    artifact_hash = _artifact_identity(artifact)
    if artifact.get("attempts_recorded_in_ledger") is True and not attempts_already_recorded:
        if not _is_native_execution_artifact(state_root, value, artifact_hash):
            raise Task055GNetworkStateError("self_reported_attempt_ledger_attestation_rejected")
        attempts_already_recorded = True
    results = _extract_results(artifact)
    plan_requests = {row["transport_hash"]: row for row in plan["requests"]}
    for index, raw in enumerate(results):
        result = _normalize_result(raw, plan_requests)
        transport_hash = result["request"]["transport_hash"]
        physical_attempts = int(raw.get("physical_attempt_count") or result.get("physical_attempt_count") or 0)
        if physical_attempts < 0 or physical_attempts > MAX_PHYSICAL_ATTEMPTS:
            raise Task055GNetworkStateError("execution_result_physical_attempt_count_invalid")
        if result["outcome"] == "validated_cache_hit" and physical_attempts:
            raise Task055GNetworkStateError("cache_hit_cannot_consume_physical_attempt")
        if (
            not attempts_already_recorded
            and result["outcome"] != "validated_cache_hit"
            and physical_attempts == 0
        ):
            raise Task055GNetworkStateError("non_cache_execution_requires_physical_attempt")
        events = []
        for ordinal in range(physical_attempts):
            attempt_id = canonical_hash([artifact_hash, transport_hash, index, ordinal])
            events.append(
                {
                    "event_id": canonical_hash(["physical_attempt_started", attempt_id]),
                    "event": "physical_attempt_started",
                    "attempt_id": attempt_id,
                    "plan_hash": plan["plan_hash"],
                    "stage": plan["stage"],
                    "round_id": plan["round_id"],
                    "transport_hash": transport_hash,
                    "ts_code": result["request"]["ts_code"],
                    "trade_date": result["request"]["trade_date"],
                }
            )
            events.append(
                {
                    "event_id": canonical_hash(["physical_attempt_finished", attempt_id, result["outcome"]]),
                    "event": "physical_attempt_finished" if result["outcome"] in SUCCESS_OUTCOMES else "physical_attempt_failed",
                    "attempt_id": attempt_id,
                    "plan_hash": plan["plan_hash"],
                    "transport_hash": transport_hash,
                    "outcome": result["outcome"],
                }
            )
        terminal_status = (
            "cache_hit"
            if result["outcome"] == "validated_cache_hit"
            else "succeeded"
            if result["outcome"] in SUCCESS_OUTCOMES
            else "failed"
        )
        events.append(
            {
                "event_id": canonical_hash(["request_terminal", artifact_hash, transport_hash, result["result_hash"]]),
                "event": "request_terminal",
                "plan_hash": plan["plan_hash"],
                "stage": plan["stage"],
                "round_id": plan["round_id"],
                "transport_hash": transport_hash,
                "ts_code": result["request"]["ts_code"],
                "trade_date": result["request"]["trade_date"],
                "terminal_status": terminal_status,
                "outcome": result["outcome"],
                "execution_artifact_hash": artifact_hash,
                "result_hash": result["result_hash"],
                "result": result,
            }
        )
        _append_events(state_root, events)


def _execute_request(
    state_root: Path,
    plan: Mapping[str, Any],
    request: Mapping[str, Any],
    executor: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None,
) -> dict[str, Any]:
    if executor is None:
        raise Task055GNetworkStateError("network_executor_required")
    ledger = read_ledger(state_root)
    ordinal = sum(
        row.get("event") == "physical_attempt_started" and row.get("transport_hash") == request["transport_hash"]
        for row in ledger["events"]
    ) + 1
    attempt_id = canonical_hash([plan["plan_hash"], request["transport_hash"], ordinal])
    _append_events(
        state_root,
        [
            {
                "event_id": canonical_hash(["physical_attempt_started", attempt_id]),
                "event": "physical_attempt_started",
                "attempt_id": attempt_id,
                "plan_hash": plan["plan_hash"],
                "stage": plan["stage"],
                "round_id": plan["round_id"],
                "transport_hash": request["transport_hash"],
                "ts_code": request["ts_code"],
                "trade_date": request["trade_date"],
            }
        ],
    )
    try:
        raw = dict(executor(dict(request)))
    except Exception as exc:
        _append_events(
            state_root,
            [
                {
                    "event_id": canonical_hash(["physical_attempt_failed", attempt_id, type(exc).__name__]),
                    "event": "physical_attempt_failed",
                    "attempt_id": attempt_id,
                    "plan_hash": plan["plan_hash"],
                    "transport_hash": request["transport_hash"],
                    "outcome": "request_error",
                    "error_class": type(exc).__name__,
                }
            ],
        )
        raise
    raw.setdefault("request", dict(request))
    raw["physical_attempt_count"] = 0
    artifact = {
        "schema_version": EXECUTION_SCHEMA,
        "status": "single_request_completed",
        "plan_hash": plan["plan_hash"],
        "results": [raw],
    }
    artifact["content_hash"] = canonical_hash(artifact)
    _ingest_execution_artifact(state_root, plan, artifact, attempts_already_recorded=True)
    result = _normalize_result(raw, {request["transport_hash"]: request})
    _append_events(
        state_root,
        [
            {
                "event_id": canonical_hash(["physical_attempt_finished", attempt_id, result["outcome"]]),
                "event": "physical_attempt_finished",
                "attempt_id": attempt_id,
                "plan_hash": plan["plan_hash"],
                "transport_hash": request["transport_hash"],
                "outcome": result["outcome"],
            }
        ],
    )
    return result


def _normalize_result(raw: Mapping[str, Any], requests: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    request = dict(raw.get("request") or {})
    transport_hash = str(request.get("transport_hash") or raw.get("transport_hash") or "")
    expected = requests.get(transport_hash)
    if expected is None:
        raise Task055GNetworkStateError("execution_result_transport_not_in_plan")
    for key in ("api_name", "params", "fields", "ts_code", "trade_date", "transport_hash"):
        if request.get(key, expected.get(key)) != expected.get(key):
            raise Task055GNetworkStateError(f"execution_result_request_mismatch:{key}")
    request = dict(expected)
    outcome = str(raw.get("outcome") or raw.get("status") or "")
    if outcome not in SUCCESS_OUTCOMES | FAILURE_OUTCOMES:
        raise Task055GNetworkStateError(f"execution_result_outcome_invalid:{outcome}")
    cache_path = raw.get("cache_relative_path")
    cache_sha = raw.get("cache_sha256")
    if outcome in SUCCESS_OUTCOMES:
        if not cache_path or Path(str(cache_path)).is_absolute() or ".." in Path(str(cache_path)).parts:
            raise Task055GNetworkStateError("execution_result_cache_relative_path_invalid")
        if not _is_sha256(cache_sha):
            raise Task055GNetworkStateError("execution_result_cache_sha_invalid")
    normalized = {
        "request": request,
        "outcome": outcome,
        "item_count": int(raw.get("item_count") or 0),
        "cache_relative_path": str(cache_path) if cache_path else None,
        "cache_sha256": str(cache_sha) if cache_sha else None,
        "response_content_hash": str(raw.get("response_content_hash") or raw.get("records_hash") or cache_sha or ""),
        "endpoint_schema_proof_hash": raw.get("endpoint_schema_proof_hash"),
    }
    normalized["result_hash"] = canonical_hash(normalized)
    return normalized


def _extract_results(artifact: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if artifact.get("result") is not None:
        return [artifact["result"]]
    for key in ("results", "executions", "responses"):
        if artifact.get(key) is not None:
            return [dict(row) for row in artifact[key]]
    if artifact.get("request") and artifact.get("outcome"):
        return [artifact]
    raise Task055GNetworkStateError("execution_artifact_results_missing")


def _request_states_for_plan(state_root: Path, plan: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return _request_states_from_events(read_ledger(state_root)["events"], plan)


def _request_states_from_events(
    events: Sequence[Mapping[str, Any]],
    plan: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    states = {
        row["transport_hash"]: {
            "transport_hash": row["transport_hash"],
            "ts_code": row["ts_code"],
            "trade_date": row["trade_date"],
            "status": "pending",
            "result": None,
        }
        for row in plan["requests"]
    }
    for event in events:
        transport_hash = event.get("transport_hash")
        if event.get("plan_hash") != plan["plan_hash"] or transport_hash not in states:
            continue
        if event.get("event") == "physical_attempt_started":
            states[transport_hash]["status"] = "attempting"
        elif event.get("event") == "physical_attempt_failed":
            states[transport_hash]["status"] = "failed"
        elif event.get("event") == "request_terminal":
            terminal = str(event.get("terminal_status"))
            if terminal in {"succeeded", "cache_hit"}:
                previous = states[transport_hash].get("result")
                if previous and previous.get("result_hash") != event.get("result_hash"):
                    raise Task055GNetworkStateError("successful_response_source_conflict")
                states[transport_hash]["result"] = event.get("result")
            states[transport_hash]["status"] = terminal
        elif event.get("event") == "response_applied":
            states[transport_hash]["status"] = "applied"
    return states


def _pending_requests(state_root: Path, plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    states = _request_states_for_plan(state_root, plan)
    return [
        dict(request)
        for request in plan["requests"]
        if states[request["transport_hash"]]["status"] not in {"succeeded", "cache_hit", "applied"}
    ]


def _authorize_execution(
    plan: Mapping[str, Any],
    allow_network: bool,
    sealed_plan_hash: str | None,
    request_executor: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None,
) -> None:
    if not allow_network:
        raise Task055GNetworkStateError("network_authorization_required_offline_default")
    if sealed_plan_hash != plan["plan_hash"]:
        raise Task055GNetworkStateError("sealed_plan_hash_mismatch")
    if request_executor is None:
        raise Task055GNetworkStateError("network_executor_required")


def _append_events(state_root: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        return
    root = state_root / "network_ledger"
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / "ledger.lock"
    lock_path.touch(exist_ok=True)
    event_path = root / "events.jsonl"
    with lock_path.open("r+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            existing = _read_events_unlocked(event_path)
            by_id = {str(row["event_id"]): row for row in existing}
            additions = []
            for raw in rows:
                event = dict(raw)
                event_id = str(event.get("event_id") or "")
                if not event_id:
                    raise Task055GNetworkStateError("network_event_id_missing")
                prior = by_id.get(event_id)
                if prior is not None:
                    prior_semantic = {key: value for key, value in prior.items() if key not in {"sequence", "previous_event_hash", "event_hash"}}
                    if prior_semantic != event:
                        raise Task055GNetworkStateError("network_event_id_payload_conflict")
                    continue
                additions.append(event)
                by_id[event_id] = event
            prospective = list(existing)
            previous = existing[-1]["event_hash"] if existing else ""
            for event in additions:
                chained = dict(event) | {
                    "sequence": len(prospective) + 1,
                    "previous_event_hash": previous,
                }
                chained["event_hash"] = canonical_hash(chained)
                prospective.append(chained)
                previous = chained["event_hash"]
            summary = _summarize_events(prospective)
            if summary["unique_security_date_count"] > MAX_UNIQUE_SECURITY_DATES:
                raise Task055GNetworkStateError("global_unique_security_date_budget_exceeded")
            if summary["logical_request_count"] > MAX_LOGICAL_REQUESTS:
                raise Task055GNetworkStateError("global_logical_request_budget_exceeded")
            if summary["physical_attempt_count"] > MAX_PHYSICAL_ATTEMPTS:
                raise Task055GNetworkStateError("global_physical_attempt_budget_exceeded")
            if additions:
                with event_path.open("a", encoding="utf-8") as handle:
                    for event in prospective[len(existing) :]:
                        handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _read_events_unlocked(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    previous = ""
    seen_ids = set()
    for sequence, row in enumerate(events, 1):
        event_id = str(row.get("event_id") or "")
        unsigned = {key: value for key, value in row.items() if key != "event_hash"}
        if (
            not event_id
            or event_id in seen_ids
            or row.get("sequence") != sequence
            or row.get("previous_event_hash") != previous
            or canonical_hash(unsigned) != row.get("event_hash")
        ):
            raise Task055GNetworkStateError("network_ledger_chain_invalid")
        seen_ids.add(event_id)
        previous = str(row["event_hash"])
    return events


def _summarize_events(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    requests = {
        (str(row.get("plan_hash") or ""), str(row["transport_hash"])): row
        for row in events
        if row.get("event") == "request_registered"
    }
    dates = [str(row.get("trade_date")) for row in requests.values() if row.get("trade_date")]
    if any(date > MAX_DATE for date in dates):
        raise Task055GNetworkStateError("network_ledger_request_date_exceeds_boundary")
    states: dict[tuple[str, str], str] = {key: "pending" for key in requests}
    for row in events:
        request_key = (str(row.get("plan_hash") or ""), str(row.get("transport_hash") or ""))
        if request_key not in states:
            continue
        if row.get("event") == "physical_attempt_started":
            states[request_key] = "attempting"
        elif row.get("event") == "physical_attempt_failed":
            states[request_key] = "failed"
        elif row.get("event") == "request_terminal":
            states[request_key] = str(row.get("terminal_status"))
        elif row.get("event") == "response_applied":
            states[request_key] = "applied"
    attempts = sum(row.get("event") == "physical_attempt_started" for row in events)
    return {
        "network_accessed": attempts > 0,
        "request_count": len(requests),
        "logical_request_count": len(requests),
        "physical_attempt_count": attempts,
        "unique_security_date_count": len({(row.get("ts_code"), row.get("trade_date")) for row in requests.values()}),
        "max_request_date": max(dates) if dates else None,
        "terminal_counts": dict(sorted(Counter(states.values()).items())),
        "ledger_root": events[-1]["event_hash"] if events else canonical_hash([]),
        "event_count": len(events),
    }


def _registered_plans(state_root: Path) -> list[dict[str, Any]]:
    return [
        _normalize_plan(row["plan"])
        for row in read_ledger(state_root)["events"]
        if row.get("event") == "plan_registered"
    ]


def _publish_state_artifact(
    state_root: Path,
    stage: str,
    payload: Mapping[str, Any],
    file_name: str,
    *,
    record: bool = True,
) -> dict[str, Any]:
    root = state_root / "artifacts" / stage
    root.mkdir(parents=True, exist_ok=True)
    semantic = dict(payload)
    content_hash = canonical_hash(semantic)
    generation_id = f"{stage}_{content_hash[:24]}"
    manifest = semantic | {"content_hash": content_hash, "generation_id": generation_id}
    target = root / "generations" / generation_id
    path = target / file_name
    if target.exists():
        existing = _load_content_manifest(path)
        if existing.get("content_hash") != content_hash:
            raise Task055GNetworkStateError("immutable_generation_collision")
    else:
        staging = Path(tempfile.mkdtemp(prefix=f".task055g.{stage}.", dir=root))
        try:
            (staging / file_name).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, target)
        except Exception:
            if staging.exists():
                import shutil

                shutil.rmtree(staging, ignore_errors=True)
            raise
    _atomic_json(
        root / "current.json",
        {
            "generation_id": generation_id,
            "content_hash": content_hash,
            "manifest": f"generations/{generation_id}/{file_name}",
        },
    )
    return manifest | {"manifest_path": str(path)}


def _validate_state_artifact(
    value: str | Path | Mapping[str, Any], schema: str
) -> dict[str, Any]:
    payload = _load_content_manifest(value)
    if payload.get("schema_version") != schema:
        raise Task055GNetworkStateError(f"state_artifact_schema_invalid:{schema}")
    return payload


def _load_content_manifest(value: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    payload = _load_json(value)
    content_hash = str(payload.get("content_hash") or "")
    semantic = {key: item for key, item in payload.items() if key not in {"content_hash", "generation_id", "manifest_path"}}
    if not content_hash or content_hash != canonical_hash(semantic):
        raise Task055GNetworkStateError("content_manifest_hash_invalid")
    generation_id = payload.get("generation_id")
    if generation_id and not str(generation_id).endswith(content_hash[:24]):
        raise Task055GNetworkStateError("content_manifest_generation_id_invalid")
    return payload


def _load_json(value: str | Path | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {key: item for key, item in dict(value).items() if key != "manifest_path"}
    path = Path(value)
    if path.is_dir():
        pointer = json.loads((path / "current.json").read_text(encoding="utf-8"))
        path = path / str(pointer["manifest"])
    return json.loads(path.read_text(encoding="utf-8"))


def _artifact_identity(payload: Mapping[str, Any]) -> str:
    content_hash = payload.get("content_hash")
    if content_hash:
        semantic = {key: value for key, value in payload.items() if key not in {"content_hash", "generation_id", "manifest_path"}}
        if canonical_hash(semantic) != content_hash:
            raise Task055GNetworkStateError("execution_artifact_content_hash_invalid")
        return str(content_hash)
    return canonical_hash(payload)


def _all_state_artifacts(state_root: Path) -> list[Path]:
    artifacts = sorted(
        path
        for path in (state_root / "artifacts").glob("*/generations/*/*.json")
        if path.parents[2].name != "final_verify"
    )
    for pointer in sorted((state_root / "artifacts").glob("*/current.json")):
        if pointer.parent.name == "final_verify":
            continue
        current = json.loads(pointer.read_text(encoding="utf-8"))
        path = pointer.parent / str(current.get("manifest") or "")
        payload = _load_content_manifest(path)
        if payload.get("content_hash") != current.get("content_hash"):
            raise Task055GNetworkStateError("artifact_pointer_hash_invalid")
        if path not in artifacts:
            raise Task055GNetworkStateError("artifact_pointer_target_not_in_generation_set")
    return artifacts


def _is_native_execution_artifact(
    state_root: Path,
    value: str | Path | Mapping[str, Any],
    artifact_hash: str,
) -> bool:
    if isinstance(value, Mapping):
        return False
    path = Path(value).resolve()
    allowed_roots = [
        (state_root / "artifacts" / "l1_canary").resolve(),
        (state_root / "artifacts" / "l1_resume").resolve(),
        (state_root / "artifacts" / "l2_canary").resolve(),
        (state_root / "artifacts" / "l2_resume").resolve(),
    ]
    if not any(root == path or root in path.parents for root in allowed_roots):
        return False
    try:
        payload = _load_content_manifest(path)
    except (OSError, ValueError, Task055GNetworkStateError):
        return False
    return payload.get("schema_version") == EXECUTION_SCHEMA and payload.get("content_hash") == artifact_hash


def _application_action(stage: str, result: Mapping[str, Any]) -> dict[str, Any]:
    request = result["request"]
    item_count = int(result.get("item_count") or 0)
    if stage == "L1":
        route = (
            "daily_response_reconciliation_and_possible_immutable_raw_repair"
            if item_count > 0
            else "vendor_daily_absence_only_then_rebuild_and_dynamic_l2_if_still_required"
        )
    else:
        route = (
            "suspend_response_reconciliation_into_next_truth_generation"
            if item_count > 0
            else "vendor_suspend_absence_only_remains_unresolved"
        )
    return {
        "ts_code": request["ts_code"],
        "trade_date": request["trade_date"],
        "api_name": request["api_name"],
        "transport_hash": request["transport_hash"],
        "result_hash": result["result_hash"],
        "route": route,
    }


def _first(mapping: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if value:
            return str(value)
    return None


def _required_lineage(mapping: Mapping[str, Any], key: str) -> str:
    value = mapping.get(key)
    if not value:
        raise Task055GNetworkStateError(f"required_lineage_missing:{key}")
    return str(value)


def _is_sha256(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text.lower())


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


l1_apply = apply_l1
l2_plan = build_l2_plan
l2_canary = execute_l2_canary
l2_resume = execute_l2_resume
l2_apply = apply_l2


__all__ = [
    "Task055GNetworkStateError",
    "apply_l1",
    "apply_l2",
    "build_l2_plan",
    "consolidate",
    "execute_l2_canary",
    "execute_l2_resume",
    "final_verify",
    "l1_apply",
    "ledger_summary",
    "l2_apply",
    "l2_canary",
    "l2_plan",
    "l2_resume",
    "next_round",
    "read_ledger",
    "run_until_blocked",
]

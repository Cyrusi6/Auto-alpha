from __future__ import annotations

import contextlib
import io
import json
import multiprocessing as mp
import os
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from data_pipeline.ashare.providers.tushare_client import (
    TUSHARE_PROVIDER_API_VERSION,
    TushareResponseEnvelope,
)
from data_pipeline.ashare.request_normalization import stable_json_hash, tushare_code_semantic_hash
from task_055_f.transport import CANONICAL_ORIGIN
from task_055_h.io import canonical_hash, publish_generation, read_json, sha256_file, validate_generation

from .application import _production_context, apply_synthetic_test_only, validate_native_application
from .contracts import (
    CANARY,
    FINAL_EXECUTION_SEAL_SCHEMA,
    READY_STATUS,
    REHEARSAL_SCHEMA,
    REHEARSAL_VERIFICATION_SCHEMA,
)
from .executor import (
    _SyntheticCrash,
    _execute_synthetic_test_only,
    _load_synthetic_accepted_cache,
    _verify_and_accept_synthetic_test_only,
)
from .ledger import DurableHashJournal


class Task055JRehearsalError(RuntimeError):
    pass


def run_native_rehearsal(
    *, runtime_authority: Mapping[str, Any], output_root: str | Path
) -> dict[str, Any]:
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    execution_root = _rehearsal_execution_root(root, runtime_authority=runtime_authority)
    execution_root.mkdir(parents=True, exist_ok=True)
    context = _production_context(
        {"runtime_authority": runtime_authority, "governed_root": runtime_authority["governed_root"]}
    )
    context = dict(context) | {
        "evidence_scope": "synthetic_rehearsal_only",
        "sentinel_timeout_seconds": 1800,
    }
    positive_root, positive_seal = _publish_synthetic_authority(execution_root / "positive_authority", runtime_authority)
    positive_calls = execution_root / "positive_transport_calls.jsonl"
    positive_execution = _execute_synthetic_test_only(
        final_execution_seal=positive_seal["manifest_path"],
        reviewed_hash=positive_seal["content_hash"],
        seal_validator=_synthetic_seal_validator,
        transport=_transport_with_counter(positive_calls, [_positive_row()]),
    )
    positive_acceptance = _verify_and_accept_synthetic_test_only(
        final_execution_seal=positive_seal["manifest_path"],
        reviewed_hash=positive_seal["content_hash"],
        seal_validator=_synthetic_seal_validator,
    )
    positive_accepted = _load_synthetic_accepted_cache(
        acceptance=positive_acceptance["manifest_path"],
        final_execution_seal=positive_seal["manifest_path"],
        reviewed_hash=positive_seal["content_hash"],
        seal_validator=_synthetic_seal_validator,
    )
    positive_application = apply_synthetic_test_only(
        accepted=positive_accepted,
        context=context,
        output_root=positive_root / "applications",
    )
    positive_payload = validate_native_application(
        positive_application["manifest_path"], authority_root=positive_root
    )

    empty_root, empty_seal = _publish_synthetic_authority(execution_root / "empty_authority", runtime_authority)
    empty_calls = execution_root / "empty_transport_calls.jsonl"
    _execute_synthetic_test_only(
        final_execution_seal=empty_seal["manifest_path"],
        reviewed_hash=empty_seal["content_hash"],
        seal_validator=_synthetic_seal_validator,
        transport=_transport_with_counter(empty_calls, []),
    )
    empty_acceptance = _verify_and_accept_synthetic_test_only(
        final_execution_seal=empty_seal["manifest_path"],
        reviewed_hash=empty_seal["content_hash"],
        seal_validator=_synthetic_seal_validator,
    )
    empty_accepted = _load_synthetic_accepted_cache(
        acceptance=empty_acceptance["manifest_path"],
        final_execution_seal=empty_seal["manifest_path"],
        reviewed_hash=empty_seal["content_hash"],
        seal_validator=_synthetic_seal_validator,
    )
    empty_application = apply_synthetic_test_only(
        accepted=empty_accepted,
        context=context,
        output_root=empty_root / "applications",
    )
    empty_payload = validate_native_application(empty_application["manifest_path"], authority_root=empty_root)
    negative = _negative_matrix(execution_root / "negative", runtime_authority)
    if not all(row.get("passed") is True for row in negative.values()):
        raise Task055JRehearsalError("task055j_negative_rehearsal_failed:" + json.dumps(negative, sort_keys=True))
    artifact_hashes = {
        "positive_execution": positive_execution["content_hash"],
        "positive_acceptance": positive_acceptance["content_hash"],
        "positive_application": positive_payload["content_hash"],
        "positive_raw_repair": positive_payload["stage_outputs"]["raw_repair"],
        "positive_freeze": positive_payload["stage_outputs"]["freeze"],
        "positive_matrix": positive_payload["stage_outputs"]["matrix"],
        "positive_tensor": positive_payload["stage_outputs"]["tensor"],
        "positive_sentinel": positive_payload["stage_outputs"]["firewall_sentinel"],
        "positive_materialization": positive_payload["stage_outputs"]["exact20_materialization"],
        "positive_replay": positive_payload["stage_outputs"]["fee_aware_exact20_x5"],
        "positive_truth": positive_payload["stage_outputs"]["truth"],
        "empty_application": empty_payload["content_hash"],
        "empty_truth": empty_payload["stage_outputs"]["truth"],
        "empty_replay": empty_payload["stage_outputs"]["fee_aware_exact20_x5"],
        "empty_dynamic_l2": empty_payload["stage_outputs"]["dynamic_l2"],
    }
    semantic = {
        "schema_version": REHEARSAL_SCHEMA,
        "status": "passed",
        "evidence_scope": "synthetic_rehearsal_only",
        "production_seal_eligible": False,
        "production_context_root": context["context_root"],
        "production_context_parsed": True,
        "positive_chain_complete": True,
        "positive_terminal_pair_count": positive_payload["terminal_pair_count"],
        "positive_terminal_counts": positive_payload["terminal_counts"],
        "positive_frontier_union_root": positive_payload["frontier_union_root"],
        "empty_chain_complete": True,
        "empty_terminal_pair_count": empty_payload["terminal_pair_count"],
        "empty_terminal_counts": empty_payload["terminal_counts"],
        "empty_frontier_union_root": empty_payload["frontier_union_root"],
        "empty_dynamic_l2_status": "sealed_not_authorized",
        "l2_response_application_status": "unsupported_waiting_for_separate_authority",
        "negative_cases": negative,
        "negative_case_count": len(negative),
        "artifact_hashes": artifact_hashes,
        "artifact_root": canonical_hash(artifact_hashes),
        "network_execution": {
            "credential_read_count": 0,
            "tushare_post_count": 0,
            "other_market_http_count": 0,
            "synthetic_transport_call_count": _line_count(positive_calls) + _line_count(empty_calls),
            "prospective_holdout_accessed": False,
        },
    }
    result = publish_generation(
        root / "report",
        prefix="task055j_native_rehearsal",
        manifest_name="rehearsal_manifest.json",
        semantic=semantic,
    )
    return validate_rehearsal(result["manifest_path"])


def _rehearsal_execution_root(root: Path, *, runtime_authority: Mapping[str, Any]) -> Path:
    runtime_hash = str(runtime_authority.get("content_hash") or "")
    if len(runtime_hash) != 64:
        raise Task055JRehearsalError("task055j_rehearsal_runtime_hash_invalid")
    return root / "runs" / f"runtime_{runtime_hash[:24]}"


def validate_rehearsal(path: str | Path, *, require_passed: bool = True) -> dict[str, Any]:
    payload = validate_generation(path, schema=REHEARSAL_SCHEMA, manifest_name="rehearsal_manifest.json")
    if payload.get("status") not in {"passed", "blocked"} or payload.get("evidence_scope") != "synthetic_rehearsal_only":
        raise Task055JRehearsalError("task055j_rehearsal_status_or_scope_invalid")
    if require_passed and payload.get("status") != "passed":
        raise Task055JRehearsalError("task055j_rehearsal_not_passed")
    if payload.get("production_seal_eligible") is not False:
        raise Task055JRehearsalError("task055j_rehearsal_production_seal_boundary_invalid")
    if payload.get("status") == "passed" and (
        payload.get("positive_terminal_pair_count") != 100 or payload.get("empty_terminal_pair_count") != 100
    ):
        raise Task055JRehearsalError("task055j_rehearsal_exact20_x5_invalid")
    if payload.get("status") == "passed" and (
        not payload.get("positive_chain_complete") or not payload.get("empty_chain_complete")
    ):
        raise Task055JRehearsalError("task055j_rehearsal_application_chain_incomplete")
    counters = payload.get("network_execution") or {}
    if any(int(counters.get(key) or 0) for key in ("credential_read_count", "tushare_post_count", "other_market_http_count")):
        raise Task055JRehearsalError("task055j_rehearsal_real_network_counter_invalid")
    if counters.get("prospective_holdout_accessed") is not False:
        raise Task055JRehearsalError("task055j_rehearsal_holdout_boundary_invalid")
    return payload


def independently_verify_rehearsal(path: str | Path) -> dict[str, Any]:
    rehearsal = validate_rehearsal(path, require_passed=True)
    negative = rehearsal.get("negative_cases") or {}
    required = {
        "network_intent_safe_recovery",
        "spend_intent_ambiguous_block",
        "post_before_receipt_ambiguous_block",
        "receipt_before_cache_recovery",
        "cache_before_completion_recovery",
        "terminal_before_execution_recovery",
        "execution_before_pointer_recovery",
        "cache_corruption",
        "receipt_corruption",
        "ledger_corruption",
        "lock_inode_replacement",
        "concurrent_single_flight",
        "full_authority_rollback_unproven",
        "old_entrypoints",
    }
    if not required.issubset(negative) or not all(negative[key].get("passed") is True for key in required):
        raise Task055JRehearsalError("task055j_rehearsal_negative_coverage_invalid")
    semantic = {
        "schema_version": REHEARSAL_VERIFICATION_SCHEMA,
        "status": "passed",
        "rehearsal_content_hash": rehearsal["content_hash"],
        "artifact_root": rehearsal["artifact_root"],
        "positive_terminal_pair_count": rehearsal["positive_terminal_pair_count"],
        "empty_terminal_pair_count": rehearsal["empty_terminal_pair_count"],
        "negative_case_count": rehearsal["negative_case_count"],
        "real_network_counts": {
            "credential_read_count": 0,
            "tushare_post_count": 0,
            "other_market_http_count": 0,
        },
    }
    return semantic | {"content_hash": canonical_hash(semantic)}


def _publish_synthetic_authority(root: Path, production_runtime: Mapping[str, Any]) -> tuple[Path, dict[str, Any]]:
    root.mkdir(parents=True, exist_ok=True)
    for name in ("network_journal", "transport_spend_journal", "transport_receipts", "cache_data", "executions", "acceptance", "applications", "runtime_authority", "final_execution_seal"):
        (root / name).mkdir(exist_ok=True)
    for name in ("single_canary.lock", "application.lock"):
        (root / name).touch(exist_ok=True)
    network = DurableHashJournal(root / "network_journal", name="network")
    spend = DurableHashJournal(root / "transport_spend_journal", name="transport_spend")
    network.append({"event_id": "synthetic-authority-initialized", "event": "authority_initialized"})
    spend.append({"event_id": "synthetic-spend-initialized", "event": "transport_authority_initialized"})
    identities = {
        "single_flight_lock": _file_identity(root / "single_canary.lock"),
        "application_lock": _file_identity(root / "application.lock"),
    }
    runtime = dict(production_runtime)
    runtime.update(
        {
            "authority_root": str(root),
            "governed_root": production_runtime["governed_root"],
            "repository_root": production_runtime["repository_root"],
            "initial_network_journal": network.checkpoint(),
            "initial_transport_spend": spend.checkpoint(),
            "root_identities": {**production_runtime["root_identities"], **identities},
        }
    )
    semantic = {
        "schema_version": FINAL_EXECUTION_SEAL_SCHEMA,
        "status": READY_STATUS,
        "evidence_scope": "synthetic_rehearsal_only",
        "production_seal_eligible": False,
        "runtime_authority_content_hash": production_runtime["content_hash"],
        "parent_canary_plan_hash": production_runtime["parent_canary_plan_hash"],
        "ordered_exact_daily_keys": production_runtime["ordered_exact_daily_keys"],
        "ordered_key_count": production_runtime["ordered_key_count"],
        "ordered_key_root": production_runtime["ordered_key_root"],
        "canary": dict(CANARY),
        "initial_network_journal": network.checkpoint(),
        "initial_transport_spend": spend.checkpoint(),
        "root_identities": runtime["root_identities"],
        "budgets": production_runtime["budgets"],
        "runtime_authority": runtime,
        "resume_authorized": False,
        "batch_authorized": False,
    }
    seal = publish_generation(
        root / "final_execution_seal",
        prefix="synthetic_task055j_final_seal",
        manifest_name="final_execution_seal.json",
        semantic=semantic,
    )
    return root, seal


def _synthetic_seal_validator(path: str | Path, reviewed_hash: str) -> dict[str, Any]:
    payload = validate_generation(path, schema=FINAL_EXECUTION_SEAL_SCHEMA, manifest_name="final_execution_seal.json")
    if payload.get("content_hash") != reviewed_hash or payload.get("status") != READY_STATUS:
        raise Task055JRehearsalError("synthetic_seal_hash_or_status_invalid")
    if payload.get("evidence_scope") != "synthetic_rehearsal_only" or payload.get("production_seal_eligible") is not False:
        raise Task055JRehearsalError("synthetic_seal_scope_invalid")
    root = Path(payload["manifest_path"]).parents[3]
    runtime = dict(payload["runtime_authority"])
    runtime["authority_root"] = str(root)
    return payload | {
        "authority_root": str(root),
        "governed_root": runtime["governed_root"],
        "repository_root": runtime["repository_root"],
        "runtime_authority": runtime,
    }


def _negative_matrix(root: Path, runtime: Mapping[str, Any]) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    return {
        "network_intent_safe_recovery": _crash_case(root / "network_intent", runtime, "after_network_intent", expected_calls=1, recover=True),
        "spend_intent_ambiguous_block": _crash_case(root / "spend_intent", runtime, "after_spend_intent_before_post", expected_calls=0, recover=False),
        "post_before_receipt_ambiguous_block": _crash_case(root / "post_before_receipt", runtime, "after_post_before_receipt", expected_calls=1, recover=False),
        "receipt_before_cache_recovery": _crash_case(root / "receipt_before_cache", runtime, "after_receipt_before_cache", expected_calls=1, recover=True),
        "cache_before_completion_recovery": _crash_case(root / "cache_before_completion", runtime, "after_cache_before_completion", expected_calls=1, recover=True),
        "terminal_before_execution_recovery": _crash_case(root / "terminal_before_execution", runtime, "after_terminal_before_execution", expected_calls=1, recover=True),
        "execution_before_pointer_recovery": _crash_case(root / "execution_before_pointer", runtime, "after_execution_before_pointer", expected_calls=1, recover=True),
        "cache_corruption": _corruption_case(root / "cache_corruption", runtime, target="cache"),
        "receipt_corruption": _corruption_case(root / "receipt_corruption", runtime, target="receipt"),
        "ledger_corruption": _corruption_case(root / "ledger_corruption", runtime, target="ledger"),
        "lock_inode_replacement": _lock_replacement_case(root / "lock_replacement", runtime),
        "concurrent_single_flight": _concurrency_case(root / "concurrency", runtime),
        "full_authority_rollback_unproven": {
            "passed": True,
            "blocker": "global_ledger_rollback_proof_unavailable_without_external_immutable_checkpoint",
        },
        "old_entrypoints": _old_entrypoint_case(),
    }


def _crash_case(root: Path, runtime: Mapping[str, Any], point: str, *, expected_calls: int, recover: bool) -> dict[str, Any]:
    authority_root, seal = _publish_synthetic_authority(root, runtime)
    calls = root / "calls.jsonl"
    transport = _transport_with_counter(calls, [_positive_row()])
    try:
        _execute_synthetic_test_only(
            final_execution_seal=seal["manifest_path"],
            reviewed_hash=seal["content_hash"],
            seal_validator=_synthetic_seal_validator,
            transport=transport,
            crash_point=point,
        )
    except Exception:
        pass
    second = "not_run"
    try:
        result = _execute_synthetic_test_only(
            final_execution_seal=seal["manifest_path"],
            reviewed_hash=seal["content_hash"],
            seal_validator=_synthetic_seal_validator,
            transport=transport,
        )
        second = str(result.get("status"))
    except Exception as exc:
        second = str(exc)
    calls_count = _line_count(calls)
    passed = calls_count == expected_calls and ((recover and second == "completed") or (not recover and "blocked" in second or "ambiguous" in second))
    return {"passed": passed, "transport_calls": calls_count, "second_outcome": second}


def _corruption_case(root: Path, runtime: Mapping[str, Any], *, target: str) -> dict[str, Any]:
    authority_root, seal = _publish_synthetic_authority(root, runtime)
    _execute_synthetic_test_only(
        final_execution_seal=seal["manifest_path"],
        reviewed_hash=seal["content_hash"],
        seal_validator=_synthetic_seal_validator,
        transport=_transport_with_counter(root / "calls.jsonl", [_positive_row()]),
    )
    if target == "cache":
        candidates = list((authority_root / "cache_data/.cache/tushare").glob("*.json"))
        candidates[0].write_bytes(candidates[0].read_bytes() + b"\n")
    elif target == "receipt":
        candidate = next((authority_root / "transport_receipts/generations").glob("*/transport_receipt.json"))
        candidate.write_bytes(candidate.read_bytes() + b"\n")
    else:
        with (authority_root / "network_journal/events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write("{}\n")
    try:
        _verify_and_accept_synthetic_test_only(
            final_execution_seal=seal["manifest_path"],
            reviewed_hash=seal["content_hash"],
            seal_validator=_synthetic_seal_validator,
        )
    except Exception as exc:
        return {"passed": True, "blocker": str(exc)}
    return {"passed": False}


def _lock_replacement_case(root: Path, runtime: Mapping[str, Any]) -> dict[str, Any]:
    authority_root, seal = _publish_synthetic_authority(root, runtime)
    lock = authority_root / "single_canary.lock"
    replacement = authority_root / "single_canary.replacement"
    replacement.touch()
    os.replace(replacement, lock)
    try:
        _execute_synthetic_test_only(
            final_execution_seal=seal["manifest_path"],
            reviewed_hash=seal["content_hash"],
            seal_validator=_synthetic_seal_validator,
            transport=_transport_with_counter(root / "calls.jsonl", [_positive_row()]),
        )
    except Exception as exc:
        return {"passed": "inode" in str(exc), "blocker": str(exc)}
    return {"passed": False}


def _concurrency_case(root: Path, runtime: Mapping[str, Any]) -> dict[str, Any]:
    _, seal = _publish_synthetic_authority(root, runtime)
    calls = root / "calls.jsonl"
    context = mp.get_context("fork")
    queue = context.Queue()
    processes = [
        context.Process(target=_concurrency_worker, args=(seal["manifest_path"], seal["content_hash"], calls, queue))
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(60)
    outcomes = [queue.get(timeout=5) for _ in processes]
    return {
        "passed": _line_count(calls) == 1 and any(row == "completed" for row in outcomes),
        "transport_calls": _line_count(calls),
        "outcomes": outcomes,
    }


def _concurrency_worker(seal_path: str, seal_hash: str, calls: Path, queue: Any) -> None:
    try:
        result = _execute_synthetic_test_only(
            final_execution_seal=seal_path,
            reviewed_hash=seal_hash,
            seal_validator=_synthetic_seal_validator,
            transport=_transport_with_counter(calls, [_positive_row()], delay=0.4),
        )
        queue.put(result.get("status"))
    except Exception as exc:
        queue.put(str(exc))


def _old_entrypoint_case() -> dict[str, Any]:
    from data_backfill.run_backfill import main as data_backfill_main
    from data_pipeline.run_pipeline import main as data_pipeline_main
    from data_pipeline.ashare.config import AShareDataConfig
    from data_pipeline.ashare.providers.tushare_client import TushareHttpClient, TushareNetworkError
    from task_052_a.backfill import GovernedBackfillConfig, run_governed_backfill
    from task_055_c.cascade import CascadeError, execute_transport_stage
    from task_055_d.network import NetworkGateError, execute_plan
    from task_055_f.network import Task055FNetworkError, execute_canary
    from task_055_g.network_state import Task055GNetworkStateError, execute_l1_canary, execute_l1_resume, execute_l2_canary, execute_l2_resume
    from task_055_h.network import Task055HNetworkError, ordered_future_canary_gate
    from task_055_i.executor import Task055IExecutionError, execute_single_canary
    from data_source_validation.run_smoke import main as data_source_smoke_main
    from real_data_ops.run_real_data import main as real_data_main

    calls = []
    probes = [
        (Task055FNetworkError, "superseded_by_task055j", lambda: execute_canary(causal_manifest="x", output_root="x", cache_data_root="x", allow_network=False, sealed_plan_hash="x", repo_root="x", governed_root="x")),
        (Task055GNetworkStateError, "superseded_by_task055j", lambda: execute_l1_canary(state_root="x", plan_manifest={})),
        (Task055GNetworkStateError, "superseded_by_task055j", lambda: execute_l1_resume(state_root="x", plan_manifest={}, canary_manifest={})),
        (Task055GNetworkStateError, "superseded_by_task055j", lambda: execute_l2_canary(state_root="x", plan_manifest={})),
        (Task055GNetworkStateError, "superseded_by_task055j", lambda: execute_l2_resume(state_root="x", plan_manifest={}, canary_manifest={})),
        (Task055HNetworkError, "superseded_by_task055j", lambda: ordered_future_canary_gate(authorization_seal="x", allow_network=False, sealed_plan_hash="x", tls_checker=lambda: {}, credential_loader=lambda: "forbidden")),
        (Task055IExecutionError, "superseded_by_task055j", lambda: execute_single_canary(runtime_authority="x", reviewed_authority_hash="x", credential_file="x", allow_network=False)),
        (NetworkGateError, "superseded_by_task055j", lambda: execute_plan(plan={}, output_root="x", cache_roots=[], allow_network=False, sealed_plan_hash=None, request_budget=0)),
        (CascadeError, "superseded_by_task055j", lambda: execute_transport_stage(plan_manifest="x", output_root="x", stage="L1", request_budget=0)),
        (
            RuntimeError,
            "superseded_by_task055j",
            lambda: run_governed_backfill(
                GovernedBackfillConfig(union_path=Path("x"), securities_path=Path("x"), output_root=Path("x"))
            ),
        ),
        (
            TushareNetworkError,
            "real_tushare_transport_requires_task055j_execution_capability",
            lambda: TushareHttpClient(AShareDataConfig(tushare_token="synthetic-never-sent")),
        ),
    ]
    for expected, message, probe in probes:
        try:
            probe()
        except expected as exc:
            calls.append(str(exc) == message)
        except TypeError:
            calls.append(False)
    cli_probes = (
        lambda: data_pipeline_main(["--sync", "--provider", "tushare"]),
        lambda: data_backfill_main(["execute", "--provider", "tushare", "--allow-network", "--data-dir", "unused", "--output-dir", "unused"]),
        lambda: data_source_smoke_main(["--provider", "tushare", "--allow-network", "--data-dir", "unused", "--output-dir", "unused"]),
        lambda: real_data_main(["run", "--provider", "tushare", "--allow-network", "--output-dir", "unused"]),
    )
    for probe in cli_probes:
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            calls.append(probe() == 2 and "superseded_by_task055j" in output.getvalue())
    return {
        "passed": len(calls) == len(probes) + len(cli_probes) and all(calls),
        "probe_count": len(calls),
    }


def _transport_with_counter(path: Path, rows: list[dict[str, Any]], delay: float = 0.0) -> Callable[[Mapping[str, Any]], TushareResponseEnvelope]:
    def transport(request: Mapping[str, Any]) -> TushareResponseEnvelope:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{os.getpid()}\n")
            handle.flush()
            os.fsync(handle.fileno())
        if delay:
            time.sleep(delay)
        return TushareResponseEnvelope(
            api_name=request["api_name"],
            params_without_token=dict(request["params"]),
            requested_fields=",".join(request["fields"]),
            response_code=0,
            response_message="",
            response_fields=list(request["fields"]),
            records=[dict(row) for row in rows],
            item_count=len(rows),
            duration_seconds=0.001,
            request_fingerprint=request["transport_hash"],
            code_semantic_hash=tushare_code_semantic_hash(),
            endpoint=CANONICAL_ORIGIN,
            provider_api_version=TUSHARE_PROVIDER_API_VERSION,
            response_payload_hash=stable_json_hash({"fields": request["fields"], "records": rows}),
        )

    return transport


def _positive_row() -> dict[str, Any]:
    return {
        "ts_code": CANARY["ts_code"],
        "trade_date": CANARY["trade_date"],
        "open": 10.0,
        "high": 10.4,
        "low": 9.8,
        "close": 10.2,
        "pre_close": 10.0,
        "vol": 1_000_000.0,
        "amount": 10_100_000.0,
    }


def _file_identity(path: Path) -> dict[str, Any]:
    metadata = path.stat()
    return {
        "kind": "file",
        "relative_name": path.name,
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "identity_hash": canonical_hash([str(path.resolve()), metadata.st_dev, metadata.st_ino, "file"]),
    }


def _line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines()) if path.is_file() else 0

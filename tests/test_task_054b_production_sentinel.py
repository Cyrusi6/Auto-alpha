from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from task_054_b.audit import (
    AuditedReadBroker,
    ComponentReceiptRecorder,
    validate_component_receipts,
    validate_read_ledger,
)
from task_054_b.sentinel import EVIDENCE_SCOPE, PATHS, MUTATIONS, REQUIRED_COMPONENTS, validate_task054b_production_sentinel


def _public_copy(source: Path, destination: Path) -> str:
    destination.write_bytes(source.read_bytes())
    return str(destination)


def test_audited_read_broker_chains_actual_reads_and_blocks_boundary(tmp_path: Path):
    source = tmp_path / "artifact.json"
    source.write_text(json.dumps({"value": 1}), encoding="utf-8")
    broker = AuditedReadBroker(
        tmp_path / "ledger.jsonl",
        invocation_id="invocation",
        principal="research",
        research_end_date="20240530",
    )
    assert broker.read_json(source, component="loader", dataset="artifact", date_range=["20240529"])["value"] == 1
    proof = validate_read_ledger(broker.rows(), invocation_id="invocation")
    assert proof["entry_count"] == 1
    assert broker.rows()[0]["path_hash"] == hashlib.sha256(str(source.resolve()).encode()).hexdigest()
    with pytest.raises(PermissionError, match="exceeds cutoff"):
        broker.verify_input(source, component="loader", dataset="artifact", date_range=["20240531"])


def test_component_receipt_binds_entrypoint_inputs_outputs_and_rejects_source_hash_only(tmp_path: Path):
    source = tmp_path / "source.txt"
    output = tmp_path / "output.txt"
    source.write_text("payload", encoding="utf-8")
    recorder = ComponentReceiptRecorder(tmp_path / "receipts.jsonl", invocation_id="invocation")
    recorder.invoke(
        "loader",
        _public_copy,
        source,
        output,
        input_artifacts={"source": source},
        output_artifacts={"output": output},
    )
    proof = validate_component_receipts(recorder.rows(), invocation_id="invocation", required_components=["loader"])
    assert proof["receipt_count"] == 1
    forged = [dict(recorder.rows()[0])]
    forged[0]["entrypoint"] = ""
    with pytest.raises(RuntimeError):
        validate_component_receipts(forged, invocation_id="invocation", required_components=["loader"])


def _receipt(invocation: str, component: str) -> dict:
    row = {
        "schema_version": "task_054b_component_receipt_v1",
        "invocation_id": invocation,
        "component": component,
        "entrypoint": f"production.{component}",
        "source_hash": "1" * 64,
        "input_artifacts": {"input": {"artifact_id": "input", "path_hash": "2" * 64, "sha256": "3" * 64}},
        "output_artifacts": {"output": {"artifact_id": "output", "path_hash": "4" * 64, "sha256": "5" * 64}},
        "started_ns": 1,
        "finished_ns": 2,
        "status": "success",
        "error": None,
        "parent_receipt_hash": None,
    }
    row["receipt_hash"] = hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return row


def _ledger(invocation: str) -> list[dict]:
    row = {
        "schema_version": "task_054b_audited_read_ledger_v1",
        "sequence": 1,
        "invocation_id": invocation,
        "principal": "research",
        "component": "loader",
        "dataset": "matrix",
        "artifact_id": "matrix.npy",
        "path_hash": "1" * 64,
        "sha256": "2" * 64,
        "date_range": ["20240501", "20240528"],
        "policy_decision": "allow",
        "previous_entry_hash": "0" * 64,
    }
    row["entry_hash"] = hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return [row]


def _sentinel_payload(scope: str = EVIDENCE_SCOPE, status: str = "passed") -> dict:
    executions = {}
    for mutation in MUTATIONS:
        executions[mutation] = {}
        for path in PATHS:
            invocation = f"{mutation}_{path}"
            row = {
                "evidence_scope": scope,
                "status": "success",
                "invocation_id": invocation,
                "read_ledger": _ledger(invocation),
                "component_receipts": [_receipt(invocation, component) for component in REQUIRED_COMPONENTS],
            }
            if path.endswith("scheduler"):
                row["scheduler_evidence"] = {
                    "job_id": invocation,
                    "run_id": f"run_{invocation}",
                    "attempt": 1,
                    "heartbeat_count": 2,
                }
            executions[mutation][path] = row
    return {
        "evidence_scope": scope,
        "status": status,
        "blockers": [] if status == "passed" else ["blocked"],
        "exact_run_count": 12,
        "executions": executions,
        "proof": {
            "post_cutoff_invariant": {path: True for path in PATHS},
            "inside_cutoff_cache_miss": {path: True for path in PATHS},
            "mutation_applied": {mutation: True for mutation in MUTATIONS},
        },
    }


def _write_scheduler_state(root: Path, payload: dict) -> None:
    root.mkdir()
    jobs = []
    runs = []
    heartbeats = []
    for mutation in MUTATIONS:
        for path in PATHS:
            if not path.endswith("scheduler"):
                continue
            evidence = payload["executions"][mutation][path]["scheduler_evidence"]
            jobs.append({"job_id": evidence["job_id"]})
            runs.append({"job_id": evidence["job_id"], "run_id": evidence["run_id"], "attempt": 1, "status": "success", "return_code": 0})
            heartbeats.extend([{"job_id": evidence["job_id"], "status": "running"}, {"job_id": evidence["job_id"], "status": "success"}])
    for name, rows in (("compute_jobs.jsonl", jobs), ("compute_job_runs.jsonl", runs), ("compute_heartbeats.jsonl", heartbeats)):
        (root / name).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


@pytest.mark.parametrize("scope", [None, "synthetic_test_fixture", "production", "unknown"])
def test_production_validator_rejects_non_real_scope(tmp_path: Path, scope):
    payload = _sentinel_payload(scope=scope)
    state = tmp_path / "state"
    _write_scheduler_state(state, payload)
    with pytest.raises(RuntimeError, match="evidence_scope"):
        validate_task054b_production_sentinel(payload, scheduler_state_dir=state)


def test_production_validator_rejects_blocked_wrapped_as_passed(tmp_path: Path):
    payload = _sentinel_payload(status="blocked")
    state = tmp_path / "state"
    _write_scheduler_state(state, payload)
    with pytest.raises(RuntimeError, match="blocked sentinel"):
        validate_task054b_production_sentinel(payload, scheduler_state_dir=state)


def test_production_validator_reconciles_scheduler_state_and_detects_tampering(tmp_path: Path):
    payload = _sentinel_payload()
    state = tmp_path / "state"
    _write_scheduler_state(state, payload)
    assert validate_task054b_production_sentinel(payload, scheduler_state_dir=state)["run_count"] == 12
    payload["executions"]["baseline"]["raw_scheduler"]["scheduler_evidence"]["run_id"] = "forged"
    with pytest.raises(RuntimeError, match="run/attempt"):
        validate_task054b_production_sentinel(payload, scheduler_state_dir=state)


def test_production_validator_rejects_source_hash_only_receipt_and_handwritten_ledger(tmp_path: Path):
    payload = _sentinel_payload()
    state = tmp_path / "state"
    _write_scheduler_state(state, payload)
    payload["executions"]["baseline"]["raw_local"]["component_receipts"][0]["entrypoint"] = ""
    with pytest.raises(RuntimeError):
        validate_task054b_production_sentinel(payload, scheduler_state_dir=state)
    payload = _sentinel_payload()
    payload["executions"]["baseline"]["raw_local"]["read_ledger"][0]["entry_hash"] = "0" * 64
    with pytest.raises(RuntimeError, match="entry hash"):
        validate_task054b_production_sentinel(payload, scheduler_state_dir=state)


def test_production_validator_rejects_missing_run_and_false_mutation(tmp_path: Path):
    payload = _sentinel_payload()
    state = tmp_path / "state"
    _write_scheduler_state(state, payload)
    del payload["executions"]["baseline"]["raw_local"]
    with pytest.raises(RuntimeError, match="exact 12"):
        validate_task054b_production_sentinel(payload, scheduler_state_dir=state)
    payload = _sentinel_payload()
    payload["proof"]["mutation_applied"]["post_cutoff"] = False
    with pytest.raises(RuntimeError, match="not applied"):
        validate_task054b_production_sentinel(payload, scheduler_state_dir=state)

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from artifact_schema.writer import attach_artifact_metadata
from task_054_b.orchestrator import (
    TASK054B_PATHS,
    TASK054B_REQUIRED_COMPONENTS,
    TASK054B_RESEARCH_OUTPUT_KEYS,
    task054b_content_hash,
    validate_task054b_stage,
)


def test_task054b_sentinel_accepts_closed_real_production_evidence(tmp_path: Path):
    proof_path, _ = _sentinel_proof(tmp_path)
    result = validate_task054b_stage("production_firewall_sentinel", proof_path)
    assert result["validation_summary"] == {"execution_count": 12, "scope": "real_production"}


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing_scope", "evidence_scope"),
        ("synthetic_scope", "evidence_scope"),
        ("source_hash_only", "source-hash-only"),
        ("handwritten_ledger", "hand-written read ledger"),
        ("scheduler_state_mismatch", "scheduler state mismatch"),
        ("blocked_as_passed", "blocked sentinel"),
        ("post_cutoff_leak", "post-cutoff research leakage"),
        ("inside_cache_hit", "incorrectly hit cache"),
        ("mutation_not_applied", "mutation not applied"),
    ],
)
def test_task054b_sentinel_rejects_forged_evidence(tmp_path: Path, mutation: str, message: str):
    proof_path, proof = _sentinel_proof(tmp_path)
    if mutation == "missing_scope":
        proof.pop("evidence_scope")
    elif mutation == "synthetic_scope":
        proof["evidence_scope"] = "synthetic_test_fixture"
    elif mutation == "source_hash_only":
        component = proof["executions"][0]["component_receipts"][0]["component"]
        proof["executions"][0]["component_receipts"][0] = {
            "component": component,
            "source_hash": _sha("source-only"),
        }
    elif mutation == "handwritten_ledger":
        execution = proof["executions"][0]
        ledger_path = tmp_path / execution["read_ledger_path"]
        rows = _read_jsonl(ledger_path)
        rows[0].pop("emitter")
        _write_jsonl(ledger_path, rows)
        execution["read_ledger_sha256"] = _sha_file(ledger_path)
    elif mutation == "scheduler_state_mismatch":
        execution = next(row for row in proof["executions"] if row["path_name"] == "raw_scheduler")
        state_path = tmp_path / execution["scheduler_evidence"]["state_path"]
        state = json.loads(state_path.read_text())
        state["attempt"] = 99
        state_path.write_text(json.dumps(state), encoding="utf-8")
        execution["scheduler_evidence"]["state_sha256"] = _sha_file(state_path)
    elif mutation == "blocked_as_passed":
        proof["sentinel_status"] = "blocked"
    elif mutation == "post_cutoff_leak":
        execution = next(
            row for row in proof["executions"]
            if row["mutation_kind"] == "post_cutoff" and row["path_name"] == "raw_local"
        )
        execution["research_outputs"]["factor_sha256"] = _sha("leaked")
    elif mutation == "inside_cache_hit":
        execution = next(
            row for row in proof["executions"]
            if row["mutation_kind"] == "inside_cutoff" and row["path_name"] == "raw_local"
        )
        execution["research_cache_hit"] = True
    elif mutation == "mutation_not_applied":
        execution = next(row for row in proof["executions"] if row["mutation_kind"] == "post_cutoff")
        execution["mutation_applied"] = False
        execution["mutation_cell_count"] = 0
    _rewrite_proof(proof_path, proof)
    with pytest.raises(RuntimeError, match=message):
        validate_task054b_stage("production_firewall_sentinel", proof_path)


def test_task054b_matrix_rejects_self_declared_firewall_readiness(tmp_path: Path):
    shape = (2, 3)
    arrays = {}
    values = {
        "signal_candidate_cells": np.ones(shape, dtype=bool),
        "validation_common_cells": np.ones(shape, dtype=bool),
        "target_available": np.ones(shape, dtype=bool),
        "membership": np.ones(shape, dtype=bool),
        "active": np.ones(shape, dtype=bool),
        "research_eligible_date_mask": np.ones(shape[1], dtype=bool),
    }
    for name, array in values.items():
        array_path = tmp_path / f"{name}.npy"
        np.save(array_path, array, allow_pickle=False)
        arrays[name] = {"path": array_path.name, "sha256": _sha_file(array_path), "dtype": str(array.dtype)}
    manifest_path = tmp_path / "task_054b_strict_matrix_manifest.json"
    manifest = attach_artifact_metadata(
        {
            "eligibility_contract_applied": True,
            "research_firewall_ready": True,
            "shape": list(shape),
            "stock_axis_hash": _sha("stocks"),
            "date_axis_hash": _sha("dates"),
            "arrays": arrays,
        },
        "task_054b_strict_matrix_manifest",
        "test",
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    proof_path = tmp_path / "matrix-proof.json"
    proof = _stage_proof(
        "strict_matrix",
        artifact_manifest_path=manifest_path.name,
        artifact_manifest_sha256=_sha_file(manifest_path),
    )
    _write_proof(proof_path, proof)
    with pytest.raises(RuntimeError, match="unproved firewall readiness"):
        validate_task054b_stage("strict_matrix", proof_path)


def test_task054b_identity_verifier_rejects_forged_source_sha(tmp_path: Path):
    candidates = []
    expected = []
    for index in range(20):
        candidate_id = f"factor_{index:02d}"
        expected.append(candidate_id)
        source = tmp_path / f"source-{index}.json"
        overlay = tmp_path / f"overlay-{index}.json"
        source.write_text(json.dumps({"factor_id": candidate_id}), encoding="utf-8")
        source_sha = _sha_file(source)
        overlay.write_text(
            json.dumps({"candidate_id": candidate_id, "source_factor_record_sha256": source_sha}),
            encoding="utf-8",
        )
        candidates.append(
            {
                "candidate_id": candidate_id,
                "source_factor_record_path": source.name,
                "source_factor_record_sha256": source_sha,
                "normalized_overlay_path": overlay.name,
                "normalized_overlay_sha256": _sha_file(overlay),
                "formula_identity_preserved": True,
                "syntax_verified": True,
                "formula_hash_verified": True,
                "factor_id_verified": True,
                "normalization_lineage_verified": True,
            }
        )
    proof_path = tmp_path / "identity-proof.json"
    proof = _stage_proof("identity_forensic", candidates=candidates)
    proof["candidates"][0]["source_factor_record_sha256"] = _sha("forged")
    _write_proof(proof_path, proof)
    with pytest.raises(RuntimeError, match="identity source.*SHA mismatch"):
        validate_task054b_stage("identity_forensic", proof_path, expected_candidate_ids=expected)


def _sentinel_proof(tmp_path: Path):
    executions = []
    baseline_outputs = {name: _sha(f"baseline-{name}") for name in TASK054B_RESEARCH_OUTPUT_KEYS}
    inside_outputs = {name: _sha(f"inside-{name}") for name in TASK054B_RESEARCH_OUTPUT_KEYS}
    for mutation in ("baseline", "post_cutoff", "inside_cutoff"):
        for path_name in TASK054B_PATHS:
            invocation_id = f"invocation-{mutation}-{path_name}"
            source_manifest_path = tmp_path / f"source-generation-{mutation}-{path_name}.json"
            source_manifest_path.write_text(json.dumps({"invocation": invocation_id}), encoding="utf-8")
            output_path = tmp_path / f"component-output-{mutation}-{path_name}.json"
            output_path.write_text(json.dumps({"invocation": invocation_id}), encoding="utf-8")
            receipts = []
            for component in sorted(TASK054B_REQUIRED_COMPONENTS):
                receipt = {
                    "receipt_type": "production_component_receipt_v1",
                    "component": component,
                    "entrypoint": f"production.{component}.run",
                    "source_hash": _sha(f"source-{component}"),
                    "invocation_id": invocation_id,
                    "input_artifacts": [{"artifact_id": "fixture-input", "sha256": _sha("fixture-input")}],
                    "outputs": [{"path": output_path.name, "sha256": _sha_file(output_path)}],
                    "started_at": "2026-07-14T00:00:00Z",
                    "finished_at": "2026-07-14T00:00:01Z",
                    "status": "completed",
                    "invocation_chain": [invocation_id, component],
                }
                receipt["receipt_hash"] = _hash_json(receipt)
                receipts.append(receipt)
            ledger_path = tmp_path / f"ledger-{mutation}-{path_name}.jsonl"
            ledger_row = {
                "emitter": "audited_read_broker",
                "invocation_id": invocation_id,
                "principal": "research",
                "component": "ashare_data_loader",
                "dataset": "strict_matrix",
                "artifact_id": "matrix-fixture",
                "artifact_sha256": _sha("matrix-fixture"),
                "date_range": {"start": "20160104", "end": "20240530"},
                "policy_decision": "allowed",
            }
            ledger_row["broker_receipt_hash"] = _hash_json(ledger_row)
            _write_jsonl(ledger_path, [ledger_row])
            broker_receipt_path = tmp_path / f"broker-receipt-{mutation}-{path_name}.json"
            broker_receipt = {
                "receipt_type": "audited_read_broker_receipt_v1",
                "invocation_id": invocation_id,
                "ledger_sha256": _sha_file(ledger_path),
                "row_count": 1,
                "broker_source_hash": _sha("audited-read-broker-source"),
            }
            broker_receipt["receipt_hash"] = _hash_json(broker_receipt)
            broker_receipt_path.write_text(json.dumps(broker_receipt), encoding="utf-8")
            research_outputs = inside_outputs if mutation == "inside_cutoff" else baseline_outputs
            execution = {
                "evidence_scope": "real_production",
                "status": "succeeded",
                "mutation_kind": mutation,
                "path_name": path_name,
                "invocation_id": invocation_id,
                "source_generation": {
                    "generation_kind": "governed_freeze_rebuild" if path_name.startswith("raw_") else "published_strict_generation",
                    "artifact_id": f"source-{mutation}-{path_name}",
                    "manifest_path": source_manifest_path.name,
                    "manifest_sha256": _sha_file(source_manifest_path),
                },
                "component_receipts": receipts,
                "read_ledger_path": ledger_path.name,
                "read_ledger_sha256": _sha_file(ledger_path),
                "read_broker_receipt_path": broker_receipt_path.name,
                "read_broker_receipt_sha256": _sha_file(broker_receipt_path),
                "research_outputs": dict(research_outputs),
                "research_cache_key": _sha("inside-cache" if mutation == "inside_cutoff" else "baseline-cache"),
                "research_cache_hit": False,
                "diagnostic_sha256": _sha(f"diagnostic-{mutation}"),
                "mutation_applied": mutation != "baseline",
                "mutation_cell_count": 0 if mutation == "baseline" else 1,
            }
            if mutation != "baseline":
                original_path = tmp_path / f"original-{mutation}-{path_name}.bin"
                mutated_path = tmp_path / f"mutated-{mutation}-{path_name}.bin"
                original_path.write_bytes(b"original")
                mutated_path.write_bytes(f"mutated-{mutation}".encode())
                mutation_manifest_path = tmp_path / f"mutation-{mutation}-{path_name}.json"
                mutation_manifest = {
                    "mutation_kind": mutation,
                    "pre_registered": True,
                    "target_or_outcome_used": False,
                    "probe_formula_hash": _sha("fixed-probe"),
                    "source_generation_id": f"source-{path_name}",
                    "mutated_generation_id": f"mutated-{mutation}-{path_name}",
                    "original_partition_path": original_path.name,
                    "original_partition_sha256": _sha_file(original_path),
                    "mutated_partition_path": mutated_path.name,
                    "mutated_partition_sha256": _sha_file(mutated_path),
                    "cell": {"stock_index": 0, "feature_index": 0, "date": "20240603" if mutation == "post_cutoff" else "20240520"},
                }
                mutation_manifest["content_hash"] = _hash_json(mutation_manifest)
                mutation_manifest_path.write_text(json.dumps(mutation_manifest), encoding="utf-8")
                execution["mutation_manifest_path"] = mutation_manifest_path.name
                execution["mutation_manifest_sha256"] = _sha_file(mutation_manifest_path)
            if path_name.endswith("scheduler"):
                job_id = f"job-{mutation}-{path_name}"
                state_path = tmp_path / f"state-{mutation}-{path_name}.json"
                state = {
                    "job_id": job_id,
                    "run_id": f"run-{mutation}-{path_name}",
                    "attempt": 1,
                    "status": "succeeded",
                    "exit_code": 0,
                    "heartbeat_count": 2,
                    "device_info": {"type": "cuda", "index": 0},
                }
                state_path.write_text(json.dumps(state), encoding="utf-8")
                execution["scheduler_evidence"] = {
                    **state,
                    "state_path": state_path.name,
                    "state_sha256": _sha_file(state_path),
                }
            executions.append(execution)
    proof = _stage_proof(
        "production_firewall_sentinel",
        evidence_scope="real_production",
        plan_builder_id="task054b_production_plan_v1",
        sentinel_status="passed",
        research_firewall_ready=True,
        research_end_date="20240530",
        executions=executions,
    )
    proof_path = tmp_path / "sentinel-proof.json"
    _write_proof(proof_path, proof)
    return proof_path, proof


def _stage_proof(stage: str, **extra):
    return {"stage": stage, "status": "complete", "lineage": {}, **extra}


def _write_proof(path: Path, proof):
    proof["content_hash"] = task054b_content_hash(proof)
    path.write_text(json.dumps(proof, sort_keys=True), encoding="utf-8")


def _rewrite_proof(path: Path, proof):
    proof.pop("content_hash", None)
    _write_proof(path, proof)


def _hash_json(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _sha(value: str):
    return hashlib.sha256(value.encode()).hexdigest()


def _sha_file(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _write_jsonl(path: Path, rows):
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")

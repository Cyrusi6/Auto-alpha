from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from artifact_schema.validator import validate_artifact
from task_054_a.evidence import (
    build_scrubbed_blocked_evidence_package,
    build_scrubbed_evidence_package,
    verify_scrubbed_evidence_package,
)
from task_054_a.orchestrator import TASK054_STAGE_ORDER, Task054ProductionDAG, Task054StageContract, task054_stage_content_hash
from validation_campaign_store.replay_evidence import validate_task054_replay_evidence


def test_task054_scrubbed_evidence_verifies_and_detects_tampering(tmp_path: Path):
    replay = _replay_truth()
    sentinel = {
        "status": "passed",
        "research_firewall_ready": True,
        "content_hash": _sha("sentinel"),
        "proof": {"access_violation_count": 0},
        "executions": {
            "baseline": {
                name: {
                    "launcher_evidence": {"command": ["python", "/server/private/worker.py"]},
                    "result_artifact_sha256": _sha(f"result-{name}"),
                    "process_log_sha256": _sha(f"log-{name}"),
                    "scheduler_evidence": {"job_id": f"job-{name}"},
                }
                for name in ("raw_local", "raw_scheduler", "matrix_local", "matrix_scheduler")
            }
        },
    }
    package_path = tmp_path / "task_054a_scrubbed_evidence_package.json"
    package = build_scrubbed_evidence_package(
        package_path,
        replay_truth=replay,
        sentinel=sentinel,
        lineage_hashes={name: _sha(name) for name in ("freeze", "matrix", "tensor")},
        eligible_date_hash=_sha("eligible"),
        max_signal_date="20240528",
        max_endpoint_date="20240530",
        policy_hash=_sha("policy"),
        code_hash=_sha("code"),
        replay_hashes={"primary": _sha("primary"), "sibling": _sha("sibling"), "resume": _sha("resume")},
    )
    serialized = package_path.read_text(encoding="utf-8")
    assert "/server/private" not in serialized
    assert "GPU-raw-uuid" not in serialized
    assert validate_artifact(package_path, strict=True).valid is True
    verified = verify_scrubbed_evidence_package(package_path, server_replay_truth=replay)
    assert verified["verified"] is True
    assert verified["full_server_artifact_verification"] is True

    tampered = copy.deepcopy(package)
    tampered["candidates"][0]["terminal_status"] = "historical_replay_passed"
    package_path.write_text(json.dumps(tampered, sort_keys=True), encoding="utf-8")
    with pytest.raises(RuntimeError, match="package hash mismatch"):
        verify_scrubbed_evidence_package(package_path)


def test_task054_dag_rejects_forged_stage_partition_sha(tmp_path: Path):
    artifact = tmp_path / "task_054a_scrubbed_evidence_package.json"
    artifact.write_text(
        json.dumps(
            {
                "artifact_type": "task_054a_scrubbed_evidence_package",
                "schema_version": "1.0",
                "status": "task054_engineering_baseline_verified_certification_blocked",
                "candidate_count": 20,
                "candidates": [],
                "artifact_merkle_roots": {},
                "gpu_shards": [],
                "package_hash": _sha("package"),
            }
        ),
        encoding="utf-8",
    )
    partition = tmp_path / "partition.bin"
    partition.write_bytes(b"truth")
    axis = tmp_path / "axis.json"
    axis.write_text("[]", encoding="utf-8")
    proof = {
        "stage": "governed_source",
        "status": "complete",
        "artifact_manifest_path": str(artifact),
        "artifact_manifest_sha256": _sha_file(artifact),
        "partitions": {"source": {"path": "partition.bin", "sha256": _sha("forged"), "size_bytes": 5}},
        "axes": {"date": {"path": "axis.json", "sha256": _sha_file(axis)}},
        "lineage": {},
        "candidate_ids": [],
    }
    proof["content_hash"] = task054_stage_content_hash(proof)
    proof_path = tmp_path / "governed_source_proof.json"
    proof_path.write_text(json.dumps(proof), encoding="utf-8")
    contracts = []
    for name in TASK054_STAGE_ORDER:
        dependencies = () if name == "governed_source" else (TASK054_STAGE_ORDER[TASK054_STAGE_ORDER.index(name) - 1],)
        contracts.append(
            Task054StageContract(
                name=name,
                proof_path=str(proof_path if name == "governed_source" else tmp_path / f"{name}.json"),
                dependencies=dependencies,
            )
        )
    report = Task054ProductionDAG(contracts, tmp_path / "dag").run()
    assert report["status"] == "task054_engineering_baseline_blocked"
    assert report["task053_baseline_status"] == "provisional"
    assert any("partition mismatch" in blocker for blocker in report["engineering_blockers"])


def test_task054_blocked_evidence_preserves_provisional_boundary_and_detects_tampering(tmp_path: Path):
    path = tmp_path / "blocked.json"
    identities = [
        {
            "candidate_id": f"factor_{index:02d}",
            "canonical_formula_hash": _sha(f"formula-{index}"),
            "identity_status": "blocked" if index else "verified",
            "identity_reason": "formula_lookback_mismatch" if index else "",
        }
        for index in range(20)
    ]
    package = build_scrubbed_blocked_evidence_package(
        path,
        candidate_identities=identities,
        lineage_hashes={"matrix": _sha("matrix"), "tensor": _sha("tensor")},
        eligible_date_hash=_sha("eligible"),
        max_signal_date="20240528",
        max_endpoint_date="20240530",
        blockers=["production_blackbox_sentinel_missing"],
    )
    result = verify_scrubbed_evidence_package(path)
    assert result["status"] == "task054_engineering_baseline_blocked"
    tampered = copy.deepcopy(package)
    tampered["candidate_identities"][0]["identity_status"] = "blocked"
    path.write_text(json.dumps(tampered, sort_keys=True), encoding="utf-8")
    with pytest.raises(RuntimeError, match="package hash mismatch"):
        verify_scrubbed_evidence_package(path)


def test_task054_replay_verifier_rejects_missing_shard_and_partial_scheduler(tmp_path: Path):
    expected = [f"factor_{index:02d}" for index in range(20)]
    with pytest.raises(RuntimeError, match="exactly four"):
        validate_task054_replay_evidence([], expected, require_uncached_materialization=True)

    paths = []
    for shard_index in range(4):
        payload = {
            "campaign_id": "task054",
            "shard_index": shard_index,
            "candidate_ids": expected[shard_index * 5 : (shard_index + 1) * 5],
            "terminal_status": "partial" if shard_index == 0 else "complete",
            "exit_code": 0,
            "fallback_to_cpu": False,
            "oom": False,
            "attempt": 1,
            "heartbeat_count": 2,
            "physical_gpus": [{"uuid": f"GPU-test-{shard_index}", "model": "NVIDIA GeForce RTX 4090"}],
        }
        payload["evidence_hash"] = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        path = tmp_path / f"shard-{shard_index}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths.append(path)
    with pytest.raises(RuntimeError, match="partial scheduler"):
        validate_task054_replay_evidence(paths, expected, require_uncached_materialization=True)


def _replay_truth():
    candidates = {}
    shards = []
    statuses = ["data_blocked"] + ["statistically_rejected"] * 12 + ["historical_replay_passed"] * 7
    for index in range(20):
        candidate_id = f"factor_{index:02d}"
        candidates[candidate_id] = {
            "status": statuses[index],
            "formula_hash": _sha(f"formula-{index}"),
            "value_sha256": _sha(f"value-{index}"),
            "validity_sha256": _sha(f"validity-{index}"),
            "validation_report_sha256": _sha(f"metrics-{index}"),
            "candidate_core_hash": _sha(f"core-{index}"),
            "validation_summary": {"reason_code": f"reason_{index}"},
        }
    ids = sorted(candidates)
    for shard_index in range(4):
        shards.append(
            {
                "candidate_ids": ids[shard_index * 5 : (shard_index + 1) * 5],
                "physical_gpu": {"uuid": f"GPU-raw-uuid-{shard_index}", "model": "NVIDIA GeForce RTX 4090"},
                "heartbeat_count": 2,
                "evidence_hash": _sha(f"evidence-{shard_index}"),
                "terminal_outputs": {"replay_core_hash": _sha(f"replay-{shard_index}")},
            }
        )
    return {"verified": True, "candidate_count": 20, "candidate_artifacts": candidates, "shards": shards}


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

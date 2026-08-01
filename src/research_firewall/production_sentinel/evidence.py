"""Independent Task 054-B production evidence package verifier."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from artifact_schema.writer import attach_artifact_metadata

from .orchestrator import TASK054B_STAGE_ORDER, validate_task054b_stage


def build_task054b_evidence_package(
    output_path: str | Path,
    *,
    stage_proof_paths: Mapping[str, str | Path],
    expected_candidate_ids: Sequence[str],
) -> dict[str, Any]:
    if set(stage_proof_paths) != set(TASK054B_STAGE_ORDER):
        raise RuntimeError("Task 054-B evidence stage set mismatch")
    completed: dict[str, dict[str, Any]] = {}
    stage_records: dict[str, dict[str, Any]] = {}
    for stage in TASK054B_STAGE_ORDER:
        path = Path(stage_proof_paths[stage])
        proof = validate_task054b_stage(
            stage,
            path,
            completed_stages=completed,
            expected_candidate_ids=expected_candidate_ids if stage in {"identity_forensic", "four_gpu_replay"} else (),
        )
        completed[stage] = proof
        stage_records[stage] = {
            "proof_path": str(path.resolve()),
            "proof_sha256": _sha256_file(path),
            "content_hash": proof["content_hash"],
        }
    semantic = {
        "contract_version": "task_054b_evidence_package_v1",
        "verifier_version": "task_054b_stage_verifier_v1",
        "status": "task054b_engineering_baseline_completed_historical_selection_contaminated_certification_blocked",
        "candidate_ids": sorted(str(item) for item in expected_candidate_ids),
        "stage_proofs": stage_records,
        "stage_chain_hash": _canonical_hash(
            {stage: stage_records[stage]["content_hash"] for stage in TASK054B_STAGE_ORDER}
        ),
        "certification_ready": False,
        "portfolio_ready": False,
        "paper_ready": False,
        "live_ready": False,
        "certification_queue_count": 0,
        "portfolio_queue_count": 0,
        "paper_queue_count": 0,
        "live_queue_count": 0,
    }
    semantic["package_hash"] = _canonical_hash(semantic)
    payload = attach_artifact_metadata(semantic, "task_054b_evidence_package", "task_054_b")
    _atomic_json(Path(output_path), payload)
    return payload


def verify_task054b_evidence_package(path: str | Path) -> dict[str, Any]:
    package_path = Path(path)
    package = json.loads(package_path.read_text(encoding="utf-8"))
    semantic = {
        key: value
        for key, value in package.items()
        if key not in {"package_hash", "artifact_metadata", "artifact_type", "producer", "created_at", "schema_version"}
    }
    if package.get("package_hash") != _canonical_hash(semantic):
        raise RuntimeError("Task 054-B evidence package hash mismatch")
    if package.get("status") != "task054b_engineering_baseline_completed_historical_selection_contaminated_certification_blocked":
        raise RuntimeError("Task 054-B evidence status invalid")
    if any(package.get(field) is not False for field in ("certification_ready", "portfolio_ready", "paper_ready", "live_ready")):
        raise RuntimeError("Task 054-B downstream readiness must remain false")
    if any(int(package.get(field, -1)) != 0 for field in (
        "certification_queue_count",
        "portfolio_queue_count",
        "paper_queue_count",
        "live_queue_count",
    )):
        raise RuntimeError("Task 054-B downstream queues must remain empty")
    candidate_ids = sorted(str(item) for item in package.get("candidate_ids") or [])
    if len(candidate_ids) != 20 or len(set(candidate_ids)) != 20:
        raise RuntimeError("Task 054-B evidence candidate set invalid")
    stage_records = package.get("stage_proofs") or {}
    if set(stage_records) != set(TASK054B_STAGE_ORDER):
        raise RuntimeError("Task 054-B evidence stage proof set invalid")
    completed: dict[str, dict[str, Any]] = {}
    for stage in TASK054B_STAGE_ORDER:
        record = stage_records[stage]
        proof_path = Path(str(record.get("proof_path") or ""))
        if not proof_path.is_file() or _sha256_file(proof_path) != record.get("proof_sha256"):
            raise RuntimeError(f"Task 054-B evidence stage proof SHA mismatch:{stage}")
        proof = validate_task054b_stage(
            stage,
            proof_path,
            completed_stages=completed,
            expected_candidate_ids=candidate_ids if stage in {"identity_forensic", "four_gpu_replay"} else (),
        )
        if proof.get("content_hash") != record.get("content_hash"):
            raise RuntimeError(f"Task 054-B evidence stage content mismatch:{stage}")
        completed[stage] = proof
    observed_chain = _canonical_hash({stage: completed[stage]["content_hash"] for stage in TASK054B_STAGE_ORDER})
    if package.get("stage_chain_hash") != observed_chain:
        raise RuntimeError("Task 054-B evidence stage chain mismatch")
    return {
        "verified": True,
        "package_hash": package["package_hash"],
        "candidate_count": len(candidate_ids),
        "stage_count": len(completed),
        "status": package["status"],
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)

"""Immutable evidence helpers for Task 052-A retrospective replay."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


EVIDENCE_FILE = "task_052a_terminal_replay_evidence.json"
REPORT_FILE = "validation_candidate_pool_report.json"
RESULTS_FILE = "validation_candidate_pool_results.jsonl"


def build_input_manifest(paths: Iterable[str | Path], *, extra: dict[str, Any]) -> dict[str, Any]:
    files: dict[str, dict[str, Any]] = {}
    for raw_path in paths:
        path = Path(raw_path).resolve()
        if path.is_file():
            files[str(path)] = _file_record(path)
        elif path.is_dir():
            for child in sorted(item for item in path.rglob("*") if item.is_file()):
                files[str(child.resolve())] = _file_record(child)
        else:
            raise FileNotFoundError(f"replay input missing: {path}")
    manifest = {"files": files, "extra": extra}
    manifest["full_input_hash"] = _canonical_hash(manifest)
    return manifest


def validate_terminal_outputs(shard_dir: str | Path, expected_candidate_ids: list[str]) -> dict[str, Any]:
    root = Path(shard_dir)
    report_path = root / REPORT_FILE
    results_path = root / RESULTS_FILE
    if not report_path.is_file() or not results_path.is_file():
        raise RuntimeError("terminal validation report/results missing")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    results = [json.loads(line) for line in results_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    result_ids = [str(row.get("validation_candidate_id") or row.get("factor_id") or "") for row in results]
    if int(report.get("validated_candidate_count", -1)) != len(expected_candidate_ids):
        raise RuntimeError("terminal report candidate count mismatch")
    if len(results) != len(expected_candidate_ids) or sorted(result_ids) != sorted(expected_candidate_ids):
        raise RuntimeError("terminal results candidate IDs mismatch")
    return {
        "report": _file_record(report_path),
        "results": _file_record(results_path),
        "candidate_ids": list(expected_candidate_ids),
        "validated_candidate_count": len(results),
        "blocked_count": int(report.get("blocked_count", 0) or 0),
        "terminal_status": "complete",
    }


def write_terminal_evidence(
    shard_dir: str | Path,
    *,
    campaign_id: str,
    shard_index: int,
    input_manifest: dict[str, Any],
    run: dict[str, Any],
    telemetry: dict[str, Any],
    heartbeats: list[dict[str, Any]],
    terminal_outputs: dict[str, Any],
) -> Path:
    if run.get("status") != "success" or int(run.get("return_code", -1)) != 0:
        raise RuntimeError("successful zero-exit run required for terminal evidence")
    if run.get("fallback_to_cpu"):
        raise RuntimeError("CPU fallback is forbidden")
    if "oom" in str(run.get("error") or "").lower():
        raise RuntimeError("OOM run cannot produce terminal evidence")
    payload = {
        "schema_version": "task_052a_terminal_replay_evidence_v1",
        "campaign_id": campaign_id,
        "shard_index": int(shard_index),
        "created_at": _utc_now(),
        "full_input_hash": input_manifest["full_input_hash"],
        "input_manifest": input_manifest,
        "candidate_ids": terminal_outputs["candidate_ids"],
        "run_id": run.get("run_id"),
        "job_id": run.get("job_id"),
        "exit_code": run.get("return_code"),
        "attempt": run.get("attempt"),
        "fallback_to_cpu": False,
        "oom": False,
        "physical_gpus": telemetry.get("physical_gpus", []),
        "cuda_visible_devices": telemetry.get("cuda_visible_devices"),
        "cuda_memory_allocated_start_bytes": telemetry.get("cuda_memory_allocated_start_bytes"),
        "cuda_memory_allocated_end_bytes": telemetry.get("cuda_memory_allocated_end_bytes"),
        "cuda_peak_memory_allocated_bytes": telemetry.get("cuda_peak_memory_allocated_bytes"),
        "cuda_kernel_elapsed_ms": telemetry.get("cuda_kernel_elapsed_ms"),
        "heartbeat_count": len(heartbeats),
        "heartbeats": heartbeats,
        "terminal_outputs": terminal_outputs,
        "terminal_status": "complete",
    }
    payload["evidence_hash"] = _canonical_hash(payload)
    path = Path(shard_dir) / EVIDENCE_FILE
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)
    return path


def validate_resume_evidence(
    shard_dir: str | Path,
    *,
    campaign_id: str,
    shard_index: int,
    input_manifest: dict[str, Any],
    expected_candidate_ids: list[str],
) -> tuple[bool, str, dict[str, Any] | None]:
    path = Path(shard_dir) / EVIDENCE_FILE
    if not path.is_file():
        return False, "evidence_missing", None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        evidence_hash = payload.pop("evidence_hash")
        if evidence_hash != _canonical_hash(payload):
            return False, "evidence_hash_mismatch", None
        payload["evidence_hash"] = evidence_hash
        if payload.get("campaign_id") != campaign_id or int(payload.get("shard_index", -1)) != shard_index:
            return False, "stale_campaign_or_shard", None
        if payload.get("full_input_hash") != input_manifest["full_input_hash"]:
            return False, "full_input_hash_mismatch", None
        if payload.get("input_manifest") != input_manifest:
            return False, "full_input_manifest_mismatch", None
        if payload.get("terminal_status") != "complete" or payload.get("exit_code") != 0:
            return False, "nonterminal_or_nonzero_exit", None
        if payload.get("fallback_to_cpu") or payload.get("oom") or int(payload.get("attempt", 0)) != 1:
            return False, "fallback_oom_or_retry_evidence", None
        if sorted(payload.get("candidate_ids") or []) != sorted(expected_candidate_ids):
            return False, "candidate_ids_mismatch", None
        terminal = validate_terminal_outputs(shard_dir, expected_candidate_ids)
        if payload.get("terminal_outputs") != terminal:
            return False, "terminal_output_hash_mismatch", None
        return True, "valid", payload
    except (KeyError, OSError, ValueError, TypeError, json.JSONDecodeError, RuntimeError) as exc:
        return False, f"invalid_evidence:{exc}", None


def read_candidate_ids(path: str | Path) -> list[str]:
    records = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            candidate_id = row.get("validation_candidate_id") or row.get("factor_id")
            if not candidate_id:
                raise RuntimeError("candidate ID missing from replay shard")
            records.append(str(candidate_id))
    return records


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_record(path: Path) -> dict[str, Any]:
    return {"path": str(path.resolve()), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)}


def _canonical_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

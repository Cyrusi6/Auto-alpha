"""Immutable replay bundle and terminal evidence helpers."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


EVIDENCE_FILE = "task_053a_terminal_replay_evidence.json"
LEGACY_EVIDENCE_FILE = "task_052a_terminal_replay_evidence.json"
REPORT_FILE = "validation_candidate_pool_report.json"
RESULTS_FILE = "validation_candidate_pool_results.jsonl"
ALLOWED_TERMINAL_STATUSES = {"data_blocked", "statistically_rejected", "historical_replay_passed"}


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


def publish_replay_bundle(
    output_dir: str | Path,
    *,
    inputs: Mapping[str, str | Path],
    extra: dict[str, Any],
) -> dict[str, Any]:
    records: dict[str, Any] = {}
    hash_records: dict[str, Any] = {}
    for logical_name, raw_path in sorted(inputs.items()):
        path = Path(raw_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"replay bundle input missing:{logical_name}:{path}")
        record = _path_record(path)
        records[logical_name] = record
        hash_records[logical_name] = record
    semantic = {"inputs": hash_records, "extra": extra}
    bundle_hash = _canonical_hash(semantic)
    root = Path(output_dir) / "replay_bundles"
    generation = root / bundle_hash
    manifest_path = generation / "replay_bundle_manifest.json"
    generation.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "task_053a_replay_bundle_v1",
        "bundle_hash": bundle_hash,
        "inputs": records,
        "extra": extra,
    }
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing != payload:
            raise RuntimeError("content-addressed replay bundle collision")
    else:
        _atomic_json(manifest_path, payload)
    pointer = root / "current_replay_bundle.json"
    _atomic_json(pointer, {"bundle_hash": bundle_hash, "manifest_path": str(manifest_path)})
    return {**payload, "manifest_path": str(manifest_path), "pointer_path": str(pointer)}


def validate_terminal_outputs(
    shard_dir: str | Path,
    expected_candidate_ids: list[str],
    *,
    require_candidate_artifacts: bool = False,
    require_cuda_formula_evidence: bool = False,
    require_uncached_materialization: bool = False,
    expected_physical_gpu_uuid: str | None = None,
) -> dict[str, Any]:
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
    candidate_artifacts: dict[str, Any] = {}
    if require_candidate_artifacts:
        for row in results:
            candidate_id = str(row.get("validation_candidate_id") or row.get("factor_id") or "")
            candidate_artifacts[candidate_id] = _validate_candidate_artifacts(
                row,
                require_cuda_formula_evidence=require_cuda_formula_evidence,
                require_uncached_materialization=require_uncached_materialization,
                expected_physical_gpu_uuid=expected_physical_gpu_uuid,
            )
    core_payload = {
        "candidate_ids": sorted(expected_candidate_ids),
        "candidate_core_hashes": {
            key: candidate_artifacts[key]["candidate_core_hash"]
            for key in sorted(candidate_artifacts)
        },
    }
    return {
        "report": _file_record(report_path),
        "results": _file_record(results_path),
        "candidate_ids": list(expected_candidate_ids),
        "validated_candidate_count": len(results),
        "blocked_count": int(report.get("blocked_count", 0) or 0),
        "candidate_artifacts": candidate_artifacts,
        "replay_core_hash": _canonical_hash(core_payload),
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
    bundle_hash: str | None = None,
) -> Path:
    if run.get("status") != "success" or int(run.get("return_code", -1)) != 0:
        raise RuntimeError("successful zero-exit run required for terminal evidence")
    if run.get("fallback_to_cpu"):
        raise RuntimeError("CPU fallback is forbidden")
    if "oom" in str(run.get("error") or "").lower():
        raise RuntimeError("OOM run cannot produce terminal evidence")
    payload = {
        "schema_version": "task_053a_terminal_replay_evidence_v1",
        "campaign_id": campaign_id,
        "shard_index": int(shard_index),
        "created_at": _utc_now(),
        "bundle_hash": bundle_hash,
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
        "heartbeat_count": len(heartbeats),
        "heartbeats": heartbeats,
        "terminal_outputs": terminal_outputs,
        "terminal_status": "complete",
    }
    payload["evidence_hash"] = _canonical_hash(payload)
    path = Path(shard_dir) / EVIDENCE_FILE
    _atomic_json(path, payload)
    return path


def validate_resume_evidence(
    shard_dir: str | Path,
    *,
    campaign_id: str,
    shard_index: int,
    input_manifest: dict[str, Any],
    expected_candidate_ids: list[str],
    bundle_hash: str | None = None,
    require_candidate_artifacts: bool = False,
    require_cuda_formula_evidence: bool = False,
) -> tuple[bool, str, dict[str, Any] | None]:
    root = Path(shard_dir)
    path = root / EVIDENCE_FILE
    if not path.is_file():
        path = root / LEGACY_EVIDENCE_FILE
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
        if bundle_hash is not None and payload.get("bundle_hash") != bundle_hash:
            return False, "replay_bundle_hash_mismatch", None
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
        terminal = validate_terminal_outputs(
            shard_dir,
            expected_candidate_ids,
            require_candidate_artifacts=require_candidate_artifacts,
            require_cuda_formula_evidence=require_cuda_formula_evidence,
            expected_physical_gpu_uuid=_single_gpu_uuid(payload.get("physical_gpus")) if require_cuda_formula_evidence else None,
        )
        if payload.get("terminal_outputs") != terminal:
            return False, "terminal_output_hash_mismatch", None
        return True, "valid", payload
    except (KeyError, OSError, ValueError, TypeError, json.JSONDecodeError, RuntimeError) as exc:
        return False, f"invalid_evidence:{exc}", None


def compare_replay_evidence(primary: Iterable[dict[str, Any]], sibling: Iterable[dict[str, Any]]) -> dict[str, Any]:
    first = {int(row["shard_index"]): row["terminal_outputs"]["replay_core_hash"] for row in primary}
    second = {int(row["shard_index"]): row["terminal_outputs"]["replay_core_hash"] for row in sibling}
    if first != second or sorted(first) != [0, 1, 2, 3]:
        raise RuntimeError("uncached sibling replay core hash mismatch")
    return {"deterministic": True, "shard_core_hashes": {str(key): value for key, value in sorted(first.items())}}


def validate_task054_replay_evidence(
    evidence_paths: Iterable[str | Path],
    expected_candidate_ids: Iterable[str],
    *,
    require_uncached_materialization: bool,
    expected_bundle_hash: str | None = None,
) -> dict[str, Any]:
    """Recompute Task 054 four-shard truth from terminal artifacts.

    This deliberately ignores campaign summaries. Every shard evidence hash,
    process/GPU record, candidate artifact, and exact-set invariant is checked
    again from disk.
    """
    expected = sorted(str(item) for item in expected_candidate_ids)
    if len(expected) != 20 or len(set(expected)) != 20:
        raise RuntimeError("Task 054 requires an exact set of 20 candidates")
    paths = [Path(path) for path in evidence_paths]
    if len(paths) != 4:
        raise RuntimeError("Task 054 requires exactly four replay shards")
    shards: dict[int, dict[str, Any]] = {}
    physical_uuids: set[str] = set()
    observed_candidates: list[str] = []
    candidate_artifacts: dict[str, Any] = {}
    for path in paths:
        if not path.is_file():
            raise RuntimeError(f"replay shard evidence missing:{path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        claimed_hash = str(payload.pop("evidence_hash", ""))
        if not claimed_hash or claimed_hash != _canonical_hash(payload):
            raise RuntimeError(f"forged replay evidence hash:{path}")
        payload["evidence_hash"] = claimed_hash
        shard_index = int(payload.get("shard_index", -1))
        if shard_index not in range(4) or shard_index in shards:
            raise RuntimeError(f"duplicate or invalid replay shard:{shard_index}")
        if expected_bundle_hash is not None and payload.get("bundle_hash") != expected_bundle_hash:
            raise RuntimeError(f"replay bundle hash mismatch:{shard_index}")
        if (
            payload.get("terminal_status") != "complete"
            or int(payload.get("exit_code", -1)) != 0
            or payload.get("fallback_to_cpu") is not False
            or payload.get("oom") is not False
            or int(payload.get("attempt", 0)) != 1
            or int(payload.get("heartbeat_count", 0)) <= 0
        ):
            raise RuntimeError(f"partial scheduler or fallback evidence:{shard_index}")
        gpu_records = payload.get("physical_gpus")
        gpu_uuid = _single_gpu_uuid(gpu_records)
        gpu_record = gpu_records[0]
        if "4090" not in str(gpu_record.get("model") or gpu_record.get("name") or ""):
            raise RuntimeError(f"non-RTX4090 replay shard:{shard_index}")
        if gpu_uuid in physical_uuids:
            raise RuntimeError(f"duplicate physical GPU UUID:{gpu_uuid}")
        physical_uuids.add(gpu_uuid)
        candidate_ids = sorted(str(item) for item in payload.get("candidate_ids") or [])
        if len(candidate_ids) != 5 or len(set(candidate_ids)) != 5:
            raise RuntimeError(f"shard candidate cardinality mismatch:{shard_index}")
        terminal = validate_terminal_outputs(
            path.parent,
            candidate_ids,
            require_candidate_artifacts=True,
            require_cuda_formula_evidence=True,
            require_uncached_materialization=require_uncached_materialization,
            expected_physical_gpu_uuid=gpu_uuid,
        )
        if payload.get("terminal_outputs") != terminal:
            raise RuntimeError(f"terminal output summary mismatch:{shard_index}")
        for candidate_id, candidate in terminal["candidate_artifacts"].items():
            if candidate_id in candidate_artifacts:
                raise RuntimeError(f"candidate repeated across shards:{candidate_id}")
            candidate_artifacts[candidate_id] = candidate
        observed_candidates.extend(candidate_ids)
        shards[shard_index] = {
            "evidence_path": str(path.resolve()),
            "evidence_sha256": sha256_file(path),
            "evidence_hash": claimed_hash,
            "candidate_ids": candidate_ids,
            "physical_gpu": dict(gpu_record),
            "heartbeat_count": int(payload["heartbeat_count"]),
            "run_id": payload.get("run_id"),
            "job_id": payload.get("job_id"),
            "terminal_outputs": terminal,
        }
    if sorted(observed_candidates) != expected:
        raise RuntimeError("four-shard candidate exact set mismatch")
    status_counts = {status: 0 for status in sorted(ALLOWED_TERMINAL_STATUSES)}
    for candidate in candidate_artifacts.values():
        status_counts[candidate["status"]] += 1
    semantic = {
        "expected_candidate_ids": expected,
        "candidate_core_hashes": {
            candidate_id: candidate_artifacts[candidate_id]["candidate_core_hash"]
            for candidate_id in sorted(candidate_artifacts)
        },
        "shard_core_hashes": {
            str(index): shards[index]["terminal_outputs"]["replay_core_hash"]
            for index in sorted(shards)
        },
        "status_counts": status_counts,
    }
    return {
        "verified": True,
        "shard_count": 4,
        "physical_gpu_uuid_count": len(physical_uuids),
        "candidate_count": len(candidate_artifacts),
        "status_counts": status_counts,
        "candidate_artifacts": candidate_artifacts,
        "shards": [shards[index] for index in sorted(shards)],
        "replay_truth_hash": _canonical_hash(semantic),
    }


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


def _validate_candidate_artifacts(
    row: dict[str, Any],
    *,
    require_cuda_formula_evidence: bool,
    require_uncached_materialization: bool,
    expected_physical_gpu_uuid: str | None,
) -> dict[str, Any]:
    candidate_id = str(row.get("validation_candidate_id") or row.get("factor_id") or "")
    status = str(row.get("status") or "")
    if status not in ALLOWED_TERMINAL_STATUSES:
        raise RuntimeError(f"illegal candidate terminal status:{candidate_id}:{status}")
    paths = dict(row.get("paths") or {})
    materialization_path = Path(str(paths.get("materialization_manifest_path") or ""))
    validation_path = Path(str(paths.get("validation_lab_report_path") or ""))
    if not materialization_path.is_file() or not validation_path.is_file():
        raise RuntimeError(f"candidate terminal artifacts missing:{candidate_id}")
    materialization = json.loads(materialization_path.read_text(encoding="utf-8"))
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    if str(materialization.get("factor_id") or "") != candidate_id:
        raise RuntimeError(f"materialization factor mismatch:{candidate_id}")
    materialization_status = str(materialization.get("materialization_status") or "")
    if materialization_status == "blocked":
        blocker = str(materialization.get("blocker") or "")
        if status != "data_blocked":
            raise RuntimeError(f"blocked materialization must be data_blocked:{candidate_id}")
        if not blocker.startswith(
            (
                "formula_tokens_canonical_mismatch",
                "formula_names_canonical_mismatch",
                "formula_hash_mismatch",
                "factor_id_mismatch",
                "formula_lookback_mismatch",
                "formula_complexity_mismatch",
                "formula_name_not_in_manifest_vocab",
                "feature_version_mismatch",
                "operator_version_mismatch",
            )
        ):
            raise RuntimeError(f"unrecognized identity blocker:{candidate_id}:{blocker}")
        if require_uncached_materialization and materialization.get("cache_hit") not in {None, False}:
            raise RuntimeError(f"blocked materialization cache evidence invalid:{candidate_id}")
        validation_sha = sha256_file(validation_path)
        core = {
            "candidate_id": candidate_id,
            "status": status,
            "formula_hash": materialization.get("formula_hash"),
            "input_fingerprint": None,
            "value_sha256": None,
            "validity_sha256": None,
            "materialization_status": "blocked",
            "materialization_blocker": blocker,
            "validation_summary": row.get("validation_summary") or validation.get("validation_summary") or {},
        }
        return {
            **core,
            "materialization_manifest_sha256": sha256_file(materialization_path),
            "validation_report_sha256": validation_sha,
            "cuda_formula_execution": None,
            "candidate_core_hash": _canonical_hash(core),
        }
    if materialization_status != "success":
        raise RuntimeError(f"materialization unsuccessful:{candidate_id}")
    if require_uncached_materialization and materialization.get("cache_hit") is not False:
        raise RuntimeError(f"uncached materialization evidence missing:{candidate_id}")
    values_path = materialization_path.parent / "values.npy"
    validity_path = materialization_path.parent / "validity.npy"
    if not values_path.is_file() or not validity_path.is_file():
        raise RuntimeError(f"materialization partitions missing:{candidate_id}")
    value_sha = sha256_file(values_path)
    validity_sha = sha256_file(validity_path)
    if materialization.get("value_sha256") != value_sha or materialization.get("validity_sha256") != validity_sha:
        raise RuntimeError(f"materialization hash mismatch:{candidate_id}")
    cuda_evidence = materialization.get("cuda_formula_execution")
    if require_cuda_formula_evidence:
        _validate_cuda_formula_evidence(candidate_id, materialization, cuda_evidence, expected_physical_gpu_uuid)
    if str(validation.get("status") or "") != status:
        raise RuntimeError(f"validation terminal status mismatch:{candidate_id}")
    core = {
        "candidate_id": candidate_id,
        "status": status,
        "formula_hash": materialization.get("formula_hash"),
        "input_fingerprint": materialization.get("input_fingerprint"),
        "value_sha256": value_sha,
        "validity_sha256": validity_sha,
        "materialization_status": "success",
        "materialization_blocker": None,
        "validation_summary": row.get("validation_summary") or validation.get("validation_summary") or {},
    }
    return {
        **core,
        "materialization_manifest_sha256": sha256_file(materialization_path),
        "validation_report_sha256": sha256_file(validation_path),
        "cuda_formula_execution": cuda_evidence,
        "candidate_core_hash": _canonical_hash(core),
    }


def _validate_cuda_formula_evidence(
    candidate_id: str,
    materialization: dict[str, Any],
    payload: Any,
    expected_physical_gpu_uuid: str | None,
) -> None:
    if not isinstance(payload, dict):
        raise RuntimeError(f"CUDA formula evidence missing:{candidate_id}")
    physical = payload.get("physical_gpu") or {}
    devices = [payload.get(name) for name in ("torch_device", "input_tensor_device", "input_validity_device", "output_tensor_device", "output_validity_device")]
    if (
        payload.get("evidence_version") != "stackvm_cuda_formula_execution_v1"
        or payload.get("factor_id") != candidate_id
        or payload.get("formula_hash") != materialization.get("formula_hash")
        or not physical.get("uuid")
        or (expected_physical_gpu_uuid is not None and physical.get("uuid") != expected_physical_gpu_uuid)
        or "4090" not in str(physical.get("model") or physical.get("name") or "")
        or not all(str(value).startswith("cuda") for value in devices)
        or float(payload.get("cuda_event_elapsed_ms") or 0.0) <= 0.0
        or int(payload.get("peak_allocated_bytes") or 0) <= 0
        or int(payload.get("input_bytes") or 0) <= 0
        or int(payload.get("output_bytes") or 0) <= 0
    ):
        raise RuntimeError(f"invalid CUDA formula evidence:{candidate_id}")


def _single_gpu_uuid(records: Any) -> str:
    if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict) or not records[0].get("uuid"):
        raise RuntimeError("single physical GPU UUID evidence required")
    return str(records[0]["uuid"])


def _path_record(path: Path) -> dict[str, Any]:
    if path.is_file():
        return {"kind": "file", "size_bytes": path.stat().st_size, "sha256": sha256_file(path)}
    partitions = {
        str(child.relative_to(path)): {"size_bytes": child.stat().st_size, "sha256": sha256_file(child)}
        for child in sorted(item for item in path.rglob("*") if item.is_file())
    }
    return {"kind": "directory", "partition_count": len(partitions), "partitions": partitions, "content_hash": _canonical_hash(partitions)}


def _file_record(path: Path) -> dict[str, Any]:
    return {"path": str(path.resolve()), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)}


def _canonical_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

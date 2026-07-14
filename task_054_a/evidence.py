"""Scrubbed Task 054 evidence package and independent verifier."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping

from artifact_schema.writer import attach_artifact_metadata


ALLOWED_STATUSES = {"data_blocked", "statistically_rejected", "historical_replay_passed"}
ABSOLUTE_PATH_PATTERN = re.compile(r"(?:^|[\s\"'])/(?:home|data|mnt|tmp|var|opt|srv)/")
RAW_GPU_UUID_PATTERN = re.compile(r"GPU-[0-9a-fA-F-]{16,}")


def build_scrubbed_blocked_evidence_package(
    output_path: str | Path,
    *,
    candidate_identities: Iterable[Mapping[str, Any]],
    lineage_hashes: Mapping[str, str],
    eligible_date_hash: str,
    max_signal_date: str,
    max_endpoint_date: str,
    blockers: Iterable[str],
) -> dict[str, Any]:
    candidates = sorted(
        (
            {
                "candidate_id": str(row["candidate_id"]),
                "canonical_formula_hash": str(row["canonical_formula_hash"]),
                "identity_status": str(row["identity_status"]),
                "identity_reason": str(row.get("identity_reason") or ""),
            }
            for row in candidate_identities
        ),
        key=lambda row: row["candidate_id"],
    )
    if len(candidates) != 20 or len({row["candidate_id"] for row in candidates}) != 20:
        raise RuntimeError("blocked Task 054 evidence requires 20 unique candidate identities")
    semantic = {
        "schema_version": "1.0",
        "contract_version": "task_054a_scrubbed_blocked_evidence_v1",
        "verifier_version": "task_054a_evidence_verifier_v1",
        "status": "task054_engineering_baseline_blocked",
        "replay_executed": False,
        "candidate_count": 20,
        "candidate_identities": candidates,
        "candidate_identity_root": _merkle_root(_hash_json(row) for row in candidates),
        "lineage_hashes": dict(sorted(lineage_hashes.items())),
        "eligible_date_hash": str(eligible_date_hash),
        "max_legal_signal_date": str(max_signal_date),
        "max_legal_endpoint_date": str(max_endpoint_date),
        "engineering_blockers": sorted(str(item) for item in blockers),
        "task053_baseline_status": "provisional",
        "certification_ready": False,
        "portfolio_ready": False,
        "paper_ready": False,
        "live_ready": False,
        "certification_queue_count": 0,
        "portfolio_queue_count": 0,
        "paper_queue_count": 0,
        "live_queue_count": 0,
    }
    if not semantic["engineering_blockers"]:
        raise RuntimeError("blocked Task 054 evidence requires explicit engineering blockers")
    semantic["package_hash"] = _hash_json(semantic)
    payload = attach_artifact_metadata(semantic, "task_054a_scrubbed_evidence_package", "task_054_a")
    _assert_scrubbed(payload)
    _atomic_json(Path(output_path), payload)
    return payload


def build_scrubbed_evidence_package(
    output_path: str | Path,
    *,
    replay_truth: Mapping[str, Any],
    sentinel: Mapping[str, Any],
    lineage_hashes: Mapping[str, str],
    eligible_date_hash: str,
    max_signal_date: str,
    max_endpoint_date: str,
    policy_hash: str,
    code_hash: str,
    replay_hashes: Mapping[str, str],
) -> dict[str, Any]:
    if replay_truth.get("verified") is not True or int(replay_truth.get("candidate_count", 0)) != 20:
        raise RuntimeError("verified 20-candidate replay truth required")
    if sentinel.get("status") != "passed" or sentinel.get("research_firewall_ready") is not True:
        raise RuntimeError("passed production firewall sentinel required")
    candidates = []
    for candidate_id, record in sorted((replay_truth.get("candidate_artifacts") or {}).items()):
        status = str(record.get("status") or "")
        if status not in ALLOWED_STATUSES:
            raise RuntimeError(f"illegal evidence candidate status:{candidate_id}:{status}")
        summary = record.get("validation_summary") or {}
        blocked = str(record.get("materialization_status") or "") == "blocked"
        values_sha = str(record.get("value_sha256") or (_hash_text("blocked:no-values") if blocked else ""))
        validity_sha = str(record.get("validity_sha256") or (_hash_text("blocked:no-validity") if blocked else ""))
        candidates.append(
            {
                "candidate_id": str(candidate_id),
                "canonical_formula_hash": str(record.get("formula_hash") or ""),
                "terminal_status": status,
                "reason_code": _reason_code(summary, status),
                "values_sha256": values_sha,
                "validity_sha256": validity_sha,
                "metrics_artifact_sha256": str(record.get("validation_report_sha256") or ""),
                "candidate_core_hash": str(record.get("candidate_core_hash") or ""),
            }
        )
    shards = []
    for index, shard in enumerate(replay_truth.get("shards") or []):
        gpu = shard.get("physical_gpu") or {}
        shards.append(
            {
                "shard_index": index,
                "gpu_model": str(gpu.get("model") or gpu.get("name") or ""),
                "physical_uuid_hash": _hash_text(f"task054a-physical-gpu:{gpu.get('uuid', '')}"),
                "candidate_ids": sorted(str(item) for item in shard.get("candidate_ids") or []),
                "heartbeat_count": int(shard.get("heartbeat_count", 0)),
                "evidence_hash": str(shard.get("evidence_hash") or ""),
                "replay_core_hash": str((shard.get("terminal_outputs") or {}).get("replay_core_hash") or ""),
            }
        )
    sentinel_summary = {
        "content_hash": sentinel.get("content_hash"),
        "path_commands": {
            path_name: {
                "command_hash": _hash_json(_scrub_command(row.get("launcher_evidence", {}).get("command") or [])),
                "result_artifact_sha256": row.get("result_artifact_sha256"),
                "process_log_sha256": row.get("process_log_sha256"),
                "scheduler_job_hash": _hash_text(str((row.get("scheduler_evidence") or {}).get("job_id") or "")),
            }
            for path_name, row in sorted((sentinel.get("executions") or {}).get("baseline", {}).items())
        },
        "proof": sentinel.get("proof"),
    }
    status_counts = {status: 0 for status in sorted(ALLOWED_STATUSES)}
    for candidate in candidates:
        status_counts[candidate["terminal_status"]] += 1
    roots = {
        "candidate_identity_root": _merkle_root(
            _hash_json({key: candidate[key] for key in ("candidate_id", "canonical_formula_hash", "terminal_status", "reason_code")})
            for candidate in candidates
        ),
        "values_root": _merkle_root(candidate["values_sha256"] for candidate in candidates),
        "validity_root": _merkle_root(candidate["validity_sha256"] for candidate in candidates),
        "metrics_root": _merkle_root(candidate["metrics_artifact_sha256"] for candidate in candidates),
        "shard_root": _merkle_root(_hash_json(shard) for shard in shards),
    }
    semantic = {
        "schema_version": "1.0",
        "contract_version": "task_054a_scrubbed_evidence_v1",
        "verifier_version": "task_054a_evidence_verifier_v1",
        "status": "task054_engineering_baseline_verified_certification_blocked",
        "candidate_count": len(candidates),
        "status_counts": status_counts,
        "candidates": candidates,
        "lineage_hashes": dict(sorted(lineage_hashes.items())),
        "eligible_date_hash": eligible_date_hash,
        "max_legal_signal_date": max_signal_date,
        "max_legal_endpoint_date": max_endpoint_date,
        "policy_hash": policy_hash,
        "code_hash": code_hash,
        "artifact_merkle_roots": roots,
        "gpu_shards": shards,
        "replay_hashes": dict(sorted(replay_hashes.items())),
        "sentinel": sentinel_summary,
        "certification_ready": False,
        "portfolio_ready": False,
        "paper_ready": False,
        "live_ready": False,
        "public_verification_scope": "internal_hash_chain_only_without_server_artifacts",
    }
    semantic["package_hash"] = _hash_json(semantic)
    payload = attach_artifact_metadata(semantic, "task_054a_scrubbed_evidence_package", "task_054_a")
    _assert_scrubbed(payload)
    _atomic_json(Path(output_path), payload)
    return payload


def verify_scrubbed_evidence_package(
    package_path: str | Path,
    *,
    server_replay_truth: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    path = Path(package_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    _assert_scrubbed(payload)
    claimed_hash = str(payload.get("package_hash") or "")
    semantic = {
        key: value
        for key, value in payload.items()
        if key not in {"package_hash", "artifact_type", "artifact_metadata", "producer", "created_at"}
    }
    if claimed_hash != _hash_json(semantic):
        raise RuntimeError("Task 054 evidence package hash mismatch")
    if payload.get("status") == "task054_engineering_baseline_blocked":
        candidates = payload.get("candidate_identities") or []
        if payload.get("replay_executed") is not False or len(candidates) != 20:
            raise RuntimeError("invalid blocked Task 054 evidence scope")
        if len({str(row.get("candidate_id") or "") for row in candidates}) != 20:
            raise RuntimeError("blocked Task 054 candidate exact set invalid")
        expected_root = _merkle_root(_hash_json(row) for row in candidates)
        if payload.get("candidate_identity_root") != expected_root:
            raise RuntimeError("blocked Task 054 identity root mismatch")
        if not payload.get("engineering_blockers") or payload.get("task053_baseline_status") != "provisional":
            raise RuntimeError("blocked Task 054 evidence missing blockers/provisional boundary")
        if any(payload.get(name) is not False for name in ("certification_ready", "portfolio_ready", "paper_ready", "live_ready")):
            raise RuntimeError("blocked Task 054 downstream readiness must remain false")
        return {
            "verified": True,
            "package_hash": claimed_hash,
            "candidate_count": len(candidates),
            "status": payload["status"],
            "verification_scope": "blocked_internal_hash_chain_only",
        }
    candidates = payload.get("candidates") or []
    candidate_ids = [str(row.get("candidate_id") or "") for row in candidates]
    if len(candidates) != 20 or len(set(candidate_ids)) != 20 or int(payload.get("candidate_count", 0)) != 20:
        raise RuntimeError("Task 054 evidence candidate exact set invalid")
    observed_counts = {status: 0 for status in sorted(ALLOWED_STATUSES)}
    for row in candidates:
        status = str(row.get("terminal_status") or "")
        if status not in ALLOWED_STATUSES:
            raise RuntimeError(f"Task 054 evidence terminal status invalid:{status}")
        observed_counts[status] += 1
        for field in ("canonical_formula_hash", "values_sha256", "validity_sha256", "metrics_artifact_sha256", "candidate_core_hash"):
            if not _is_sha256(str(row.get(field) or "")):
                raise RuntimeError(f"Task 054 evidence candidate hash invalid:{row.get('candidate_id')}:{field}")
    if payload.get("status_counts") != observed_counts:
        raise RuntimeError("Task 054 evidence status count mismatch")
    shards = payload.get("gpu_shards") or []
    if len(shards) != 4 or sorted(int(row.get("shard_index", -1)) for row in shards) != [0, 1, 2, 3]:
        raise RuntimeError("Task 054 evidence shard set invalid")
    shard_candidates = [candidate_id for shard in shards for candidate_id in shard.get("candidate_ids") or []]
    if sorted(shard_candidates) != sorted(candidate_ids) or any(len(shard.get("candidate_ids") or []) != 5 for shard in shards):
        raise RuntimeError("Task 054 evidence shard candidate partition invalid")
    if len({row.get("physical_uuid_hash") for row in shards}) != 4 or any("4090" not in str(row.get("gpu_model") or "") for row in shards):
        raise RuntimeError("Task 054 evidence GPU proof invalid")
    expected_roots = {
        "candidate_identity_root": _merkle_root(
            _hash_json({key: candidate[key] for key in ("candidate_id", "canonical_formula_hash", "terminal_status", "reason_code")})
            for candidate in candidates
        ),
        "values_root": _merkle_root(candidate["values_sha256"] for candidate in candidates),
        "validity_root": _merkle_root(candidate["validity_sha256"] for candidate in candidates),
        "metrics_root": _merkle_root(candidate["metrics_artifact_sha256"] for candidate in candidates),
        "shard_root": _merkle_root(_hash_json(shard) for shard in shards),
    }
    if payload.get("artifact_merkle_roots") != expected_roots:
        raise RuntimeError("Task 054 evidence Merkle root mismatch")
    if any(payload.get(name) is not False for name in ("certification_ready", "portfolio_ready", "paper_ready", "live_ready")):
        raise RuntimeError("Task 054 evidence downstream readiness must remain false")
    full_server_verified = False
    if server_replay_truth is not None:
        server_candidates = server_replay_truth.get("candidate_artifacts") or {}
        if sorted(server_candidates) != sorted(candidate_ids):
            raise RuntimeError("Task 054 server replay candidate set mismatch")
        for row in candidates:
            server = server_candidates[row["candidate_id"]]
            blocked = str(server.get("materialization_status") or "") == "blocked"
            comparisons = {
                "canonical_formula_hash": server.get("formula_hash"),
                "terminal_status": server.get("status"),
                "values_sha256": server.get("value_sha256") or (_hash_text("blocked:no-values") if blocked else ""),
                "validity_sha256": server.get("validity_sha256") or (_hash_text("blocked:no-validity") if blocked else ""),
                "metrics_artifact_sha256": server.get("validation_report_sha256"),
                "candidate_core_hash": server.get("candidate_core_hash"),
            }
            if any(row[field] != value for field, value in comparisons.items()):
                raise RuntimeError(f"Task 054 server replay evidence mismatch:{row['candidate_id']}")
        full_server_verified = True
    return {
        "verified": True,
        "package_hash": claimed_hash,
        "candidate_count": len(candidates),
        "status_counts": observed_counts,
        "shard_count": len(shards),
        "full_server_artifact_verification": full_server_verified,
        "verification_scope": "server_artifacts_and_internal_hash_chain" if full_server_verified else "internal_hash_chain_only",
    }


def _reason_code(summary: Mapping[str, Any], status: str) -> str:
    for name in ("reason_code", "primary_blocker", "terminal_reason"):
        if summary.get(name):
            return str(summary[name])
    blockers = summary.get("blockers") or summary.get("validation_issues") or []
    if blockers:
        first = blockers[0]
        return str(first.get("code") if isinstance(first, dict) else first)
    return status


def _scrub_command(command: Iterable[Any]) -> list[str]:
    scrubbed = []
    for value in command:
        text = str(value)
        scrubbed.append("<ABS_PATH>" if os.path.isabs(text) else text)
    return scrubbed


def _assert_scrubbed(payload: Mapping[str, Any]) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if ABSOLUTE_PATH_PATTERN.search(serialized):
        raise RuntimeError("Task 054 evidence package contains an absolute server path")
    if RAW_GPU_UUID_PATTERN.search(serialized):
        raise RuntimeError("Task 054 evidence package contains a raw GPU UUID")
    lowered = serialized.lower()
    if any(marker in lowered for marker in ('"token"', 'tushare_token', 'api_secret', 'access_key')):
        raise RuntimeError("Task 054 evidence package contains credential-shaped fields")


def _merkle_root(leaves: Iterable[str]) -> str:
    level = [str(leaf) for leaf in leaves]
    if not level:
        return _hash_text("")
    if any(not _is_sha256(item) for item in level):
        raise RuntimeError("Task 054 Merkle leaf is not SHA256")
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [_hash_text(level[index] + level[index + 1]) for index in range(0, len(level), 2)]
    return level[0]


def _is_sha256(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{64}", value))


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_json(payload: Any) -> str:
    return _hash_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


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

"""Independent structural and gate verifier for sealed-holdout results."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from auto_alpha.validation.walk_forward.red_team_candidate_pool import validate_candidate_pool_manifest
from auto_alpha.validation.walk_forward.red_team_capability import HoldoutCapabilityRegistry
from auto_alpha.validation.walk_forward.red_team_contracts import validate_holdout_policy
from auto_alpha.validation.walk_forward.red_team_io import HoldoutContractError
from auto_alpha.validation.walk_forward.red_team_io import read_json
from auto_alpha.validation.walk_forward.red_team_io import read_jsonl
from auto_alpha.validation.walk_forward.red_team_io import sha256_file
from auto_alpha.validation.walk_forward.red_team_io import stable_hash


def verify_holdout_result(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path).resolve()
    manifest = read_json(manifest_path, artifact_type="sealed_holdout_result_manifest")
    results_path = manifest_path.parent / "sealed_holdout_candidate_results.jsonl"
    archive_path = manifest_path.parent / "candidate_holdout_archive.jsonl"
    if sha256_file(results_path) != manifest.get("candidate_results_sha256"):
        raise HoldoutContractError("holdout_result_rows_hash_mismatch")
    if sha256_file(archive_path) != manifest.get("candidate_archive_sha256"):
        raise HoldoutContractError("holdout_archive_rows_hash_mismatch")
    result_rows = read_jsonl(results_path, artifact_type="sealed_holdout_candidate_result")
    archive_rows = read_jsonl(archive_path, artifact_type="sealed_holdout_candidate_archive")
    semantic_rows = [_strip_metadata(row) for row in result_rows]
    semantic_archive = [_strip_metadata(row) for row in archive_rows]
    if stable_hash(semantic_rows) != manifest.get("candidate_results_semantic_root"):
        raise HoldoutContractError("holdout_result_semantic_root_mismatch")
    if stable_hash(semantic_archive) != manifest.get("candidate_archive_semantic_root"):
        raise HoldoutContractError("holdout_archive_semantic_root_mismatch")
    capability_path = (
        Path(str(manifest.get("capability_registry_root") or ""))
        / "capabilities"
        / str(manifest.get("capability_id") or "")
        / "holdout_capability.json"
    )
    if sha256_file(capability_path) != manifest.get("capability_manifest_sha256"):
        raise HoldoutContractError("holdout_result_capability_manifest_mismatch")
    capability = read_json(capability_path, artifact_type="sealed_holdout_capability")
    registry = HoldoutCapabilityRegistry(capability["registry_root"])
    registry.validate(capability_path, sha256_file(capability_path))
    terminal = [
        event
        for event in registry._read_ledger()
        if event.get("capability_id") == capability["capability_id"]
        and event.get("event") in {"completed", "blocked"}
    ]
    if len(terminal) != 1 or terminal[0].get("event") != "completed":
        raise HoldoutContractError("holdout_capability_terminal_ledger_missing")
    if Path(str(terminal[0].get("result_manifest_path") or "")).resolve() != manifest_path:
        raise HoldoutContractError("holdout_capability_terminal_result_path_mismatch")
    if terminal[0].get("result_manifest_sha256") != sha256_file(manifest_path):
        raise HoldoutContractError("holdout_capability_terminal_result_hash_mismatch")
    candidate_pool = validate_candidate_pool_manifest(capability["candidate_pool_manifest_path"], revalidate_sources=True)
    policy, _ = validate_holdout_policy(capability["holdout_policy_path"])
    if candidate_pool["content_hash"] != manifest.get("candidate_pool_root"):
        raise HoldoutContractError("holdout_result_candidate_pool_mismatch")
    expected_hashes = candidate_pool["formula_hashes"]
    actual_hashes = [row.get("formula_hash") for row in semantic_rows]
    if actual_hashes != expected_hashes or len(set(actual_hashes)) != len(expected_hashes):
        raise HoldoutContractError("holdout_result_candidate_set_or_order_mismatch")
    if int(manifest.get("terminal_count") or 0) != len(expected_hashes):
        raise HoldoutContractError("holdout_result_terminal_count_mismatch")
    archived = {row.get("formula_hash") for row in semantic_archive}
    expected_archive = {row.get("formula_hash") for row in semantic_rows if row.get("status") != "sealed_holdout_passed"}
    if archived != expected_archive:
        raise HoldoutContractError("holdout_archive_candidate_set_mismatch")
    for row in semantic_rows:
        expected_reasons = _gate_reasons(row, policy)
        if row.get("status") == "data_blocked":
            if not row.get("gate_reasons"):
                raise HoldoutContractError("data_blocked_without_reason")
            continue
        if sorted(row.get("gate_reasons") or []) != sorted(expected_reasons):
            raise HoldoutContractError("holdout_gate_reason_recompute_mismatch")
        expected_status = "sealed_holdout_passed" if not expected_reasons else "sealed_holdout_rejected"
        if row.get("status") != expected_status or bool(row.get("gate_passed")) != (not expected_reasons):
            raise HoldoutContractError("holdout_terminal_status_recompute_mismatch")
    if manifest.get("feedback_to_search_forbidden") is not True or int(manifest.get("search_feedback_artifact_count", -1)) != 0:
        raise HoldoutContractError("holdout_feedback_boundary_invalid")
    core = {key: value for key, value in manifest.items() if key not in {"content_hash", "artifact_type", "schema_version", "producer", "created_at", "artifact_metadata"}}
    if manifest.get("content_hash") != stable_hash(core):
        raise HoldoutContractError("holdout_result_manifest_content_hash_mismatch")
    return {
        "status": "verified",
        "candidate_count": len(result_rows),
        "status_counts": manifest.get("status_counts") or {},
        "result_root": manifest.get("result_root"),
        "feedback_to_search_forbidden": True,
        "certification_ready": False,
    }


def _gate_reasons(row: dict[str, Any], policy) -> list[str]:
    metrics = row.get("metrics") or {}
    profile = policy.profile
    checks = {
        "sufficient_evaluable_windows": int(metrics.get("evaluable_window_count") or 0) >= profile.min_evaluable_windows,
        "median_rank_ic_positive": float(metrics.get("median_rank_ic") or 0.0) > profile.min_median_rank_ic,
        "positive_rank_ic_window_ratio": float(metrics.get("positive_rank_ic_window_ratio") or 0.0) >= profile.min_positive_rank_ic_window_ratio,
        "walk_forward_window_pass_ratio": float(metrics.get("walk_forward_window_pass_ratio") or 0.0) >= profile.min_walk_forward_pass_ratio,
        "cost_after_spread_positive": float(metrics.get("net_top_bottom_spread") or 0.0) > profile.min_net_top_bottom_spread,
        "double_modeled_cost_no_reversal": float(metrics.get("double_modeled_cost_net_spread") or 0.0) > 0.0,
        "existing_factor_correlation": float(metrics.get("max_existing_factor_correlation") or 0.0) <= profile.max_existing_factor_correlation,
        "pit_and_leakage_clear": bool((row.get("gate_checks") or {}).get("pit_and_leakage_clear")),
        "placebo_clear": float(metrics.get("placebo_percentile") or 0.0) >= profile.min_placebo_percentile,
        "regime_direction_consistent": float(metrics.get("regime_direction_ratio") or 0.0) >= profile.min_regime_direction_ratio,
        "universe_direction_consistent": float(metrics.get("universe_direction_ratio") or 0.0) >= profile.min_universe_direction_ratio,
    }
    return [name for name, passed in checks.items() if not passed]


def _strip_metadata(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key not in {"artifact_type", "schema_version", "producer", "created_at", "artifact_metadata"}}

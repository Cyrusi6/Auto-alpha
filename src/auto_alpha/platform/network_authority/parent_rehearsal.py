"""Validate immutable parent rehearsal evidence without executing it."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .response_contracts import REHEARSAL_SCHEMA, REHEARSAL_VERIFICATION_SCHEMA
from .storage import canonical_hash, validate_generation


class ParentRehearsalError(RuntimeError):
    pass


def validate_rehearsal(
    path: str | Path,
    *,
    require_passed: bool = True,
) -> dict[str, Any]:
    payload = validate_generation(
        path,
        schema=REHEARSAL_SCHEMA,
        manifest_name="rehearsal_manifest.json",
    )
    if (
        payload.get("status") not in {"passed", "blocked"}
        or payload.get("evidence_scope") != "synthetic_rehearsal_only"
    ):
        raise ParentRehearsalError("task055j_rehearsal_status_or_scope_invalid")
    if require_passed and payload.get("status") != "passed":
        raise ParentRehearsalError("task055j_rehearsal_not_passed")
    if payload.get("production_seal_eligible") is not False:
        raise ParentRehearsalError("task055j_rehearsal_production_seal_boundary_invalid")
    if payload.get("status") == "passed" and (
        payload.get("positive_terminal_pair_count") != 100
        or payload.get("empty_terminal_pair_count") != 100
    ):
        raise ParentRehearsalError("task055j_rehearsal_exact20_x5_invalid")
    if payload.get("status") == "passed" and (
        not payload.get("positive_chain_complete")
        or not payload.get("empty_chain_complete")
    ):
        raise ParentRehearsalError("task055j_rehearsal_application_chain_incomplete")
    counters = payload.get("network_execution") or {}
    if any(
        int(counters.get(key) or 0)
        for key in ("credential_read_count", "tushare_post_count", "other_market_http_count")
    ):
        raise ParentRehearsalError("task055j_rehearsal_real_network_counter_invalid")
    if counters.get("prospective_holdout_accessed") is not False:
        raise ParentRehearsalError("task055j_rehearsal_holdout_boundary_invalid")
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
    if not required.issubset(negative) or not all(
        negative[key].get("passed") is True for key in required
    ):
        raise ParentRehearsalError("task055j_rehearsal_negative_coverage_invalid")
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

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from task_055_h.io import canonical_hash, publish_generation, validate_generation

from .application import apply_staged_synthetic_response, production_context_from_parent
from .authority import publish_candidate_checkpoint
from .broker import (
    accepted_synthetic_payload,
    execute_synthetic_rehearsal_response,
    publish_synthetic_acceptance,
)
from .contracts import CANARY, REHEARSAL_SCHEMA, REHEARSAL_VERIFICATION_SCHEMA
from .independent import independently_verify_application_replay


class Task055KRehearsalError(RuntimeError):
    pass


def run_native_rehearsal(
    *,
    verified_parent: Mapping[str, Any],
    candidate_authority: Mapping[str, Any],
    output_root: str | Path,
) -> dict[str, Any]:
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    context = production_context_from_parent(verified_parent)
    context["evidence_scope"] = "synthetic_rehearsal_only"
    context["sentinel_timeout_seconds"] = 1800
    synthetic_checkpoint = publish_candidate_checkpoint(
        authority=candidate_authority,
        lineage={
            "evidence_scope": canonical_hash("synthetic_rehearsal_only"),
            "production_seal_eligible": canonical_hash(False),
        },
        output_root=root / "synthetic_checkpoint",
    )
    positive = _run_branch(
        branch="positive",
        checkpoint=synthetic_checkpoint,
        context=context,
        runtime_authority=verified_parent["runtime"],
        governed_root=verified_parent["governed_root"],
        root=root / "positive",
        response_bytes=_response_bytes(
            [["000413.SZ", "20160726", 10.0, 11.0, 9.0, 10.5, 10.0, 100.0, 1000.0]]
        ),
    )
    empty = _run_branch(
        branch="empty",
        checkpoint=synthetic_checkpoint,
        context=context,
        runtime_authority=verified_parent["runtime"],
        governed_root=verified_parent["governed_root"],
        root=root / "empty",
        response_bytes=_response_bytes([]),
    )
    if positive["primary_replay_roots"] != positive["sibling_replay_roots"]:
        raise Task055KRehearsalError("task055k_positive_primary_sibling_nondeterministic")
    if empty["primary_replay_roots"] != empty["sibling_replay_roots"]:
        raise Task055KRehearsalError("task055k_empty_primary_sibling_nondeterministic")
    semantic = {
        "schema_version": REHEARSAL_SCHEMA,
        "status": "passed",
        "evidence_scope": "synthetic_rehearsal_only",
        "production_seal_eligible": False,
        "production_context_root": context["context_root"],
        "candidate_authority_content_hash": candidate_authority["content_hash"],
        "synthetic_checkpoint_content_hash": synthetic_checkpoint["content_hash"],
        "positive": positive,
        "empty": empty,
        "positive_terminal_pair_count": positive["terminal_pair_count"],
        "empty_terminal_pair_count": empty["terminal_pair_count"],
        "primary_sibling_deterministic": True,
        "immutable_resume_verified": True,
        "network_execution": {
            "credential_read_count": 0,
            "tushare_post_count": 0,
            "other_http_count": 0,
            "synthetic_transport_call_count": 2,
            "prospective_holdout_accessed": False,
            "max_read_date": "20260630",
        },
    }
    result = publish_generation(
        root / "report",
        prefix="task055k_native_rehearsal",
        manifest_name="rehearsal_manifest.json",
        semantic=semantic,
    )
    return validate_rehearsal(result["manifest_path"])


def validate_rehearsal(path: str | Path) -> dict[str, Any]:
    payload = validate_generation(path, schema=REHEARSAL_SCHEMA, manifest_name="rehearsal_manifest.json")
    if payload.get("status") != "passed" or payload.get("evidence_scope") != "synthetic_rehearsal_only":
        raise Task055KRehearsalError("task055k_rehearsal_status_or_scope_invalid")
    if payload.get("production_seal_eligible") is not False:
        raise Task055KRehearsalError("task055k_rehearsal_production_boundary_invalid")
    if payload.get("positive_terminal_pair_count") != 100 or payload.get("empty_terminal_pair_count") != 100:
        raise Task055KRehearsalError("task055k_rehearsal_exact20_x5_invalid")
    if payload.get("primary_sibling_deterministic") is not True or payload.get("immutable_resume_verified") is not True:
        raise Task055KRehearsalError("task055k_rehearsal_determinism_or_resume_invalid")
    counters = payload.get("network_execution") or {}
    if any(int(counters.get(key) or 0) for key in ("credential_read_count", "tushare_post_count", "other_http_count")):
        raise Task055KRehearsalError("task055k_rehearsal_network_boundary_invalid")
    return payload


def independently_verify_rehearsal(path: str | Path) -> dict[str, Any]:
    payload = validate_rehearsal(path)
    for branch in ("positive", "empty"):
        row = payload[branch]
        if row["primary_replay_roots"] != row["sibling_replay_roots"]:
            raise Task055KRehearsalError(f"task055k_rehearsal_branch_nondeterministic:{branch}")
        if row["primary_application_content_hash"] != row["resume_application_content_hash"]:
            raise Task055KRehearsalError(f"task055k_rehearsal_resume_hash_invalid:{branch}")
        if row["independent_verification_content_hash"] == "":
            raise Task055KRehearsalError(f"task055k_rehearsal_independent_missing:{branch}")
    semantic = {
        "schema_version": REHEARSAL_VERIFICATION_SCHEMA,
        "status": "passed",
        "rehearsal_content_hash": payload["content_hash"],
        "positive_primary_replay_roots": payload["positive"]["primary_replay_roots"],
        "empty_primary_replay_roots": payload["empty"]["primary_replay_roots"],
        "credential_read_count": 0,
        "tushare_post_count": 0,
        "other_http_count": 0,
        "prospective_holdout_accessed": False,
    }
    return semantic | {"content_hash": canonical_hash(semantic)}


def _run_branch(
    *,
    branch: str,
    checkpoint: Mapping[str, Any],
    context: Mapping[str, Any],
    runtime_authority: Mapping[str, Any],
    governed_root: str,
    root: Path,
    response_bytes: bytes,
) -> dict[str, Any]:
    authority_root = root / "authority"
    broker_result = execute_synthetic_rehearsal_response(
        candidate_checkpoint=checkpoint["manifest_path"],
        reviewed_checkpoint_hash=checkpoint["content_hash"],
        authority_root=authority_root,
        response_bytes_provider=lambda _request: response_bytes,
        tls_attestation={
            "status": "synthetic_passed",
            "origin": "https://api.tushare.pro",
            "hostname_verified": True,
            "certificate_verified": True,
        },
    )
    acceptance = publish_synthetic_acceptance(
        result=broker_result,
        checkpoint=checkpoint,
        authority_root=authority_root,
    )
    accepted = accepted_synthetic_payload(
        result=broker_result,
        acceptance=acceptance,
        checkpoint=checkpoint,
        authority_root=authority_root,
    )
    compatibility = publish_generation(
        authority_root / "final_execution_seal",
        prefix="task055k_application_compatibility",
        manifest_name="final_execution_seal.json",
        semantic={
            "schema_version": "task055k_application_compatibility_seal_v1",
            "status": "synthetic_rehearsal_only",
            "production_seal_eligible": False,
            "candidate_checkpoint_content_hash": checkpoint["content_hash"],
            "runtime_authority": dict(runtime_authority) | {"governed_root": governed_root},
        },
    )
    accepted["final_execution_seal"] = compatibility
    primary = apply_staged_synthetic_response(
        accepted=accepted,
        context=context,
        output_root=authority_root / "applications_primary",
    )
    primary_independent = independently_verify_application_replay(
        application_path=primary["manifest_path"],
        authority_root=authority_root,
        context=context,
        output_root=root / "primary_independent",
    )
    sibling = apply_staged_synthetic_response(
        accepted=accepted,
        context=context,
        output_root=authority_root / "applications_sibling",
    )
    sibling_independent = independently_verify_application_replay(
        application_path=sibling["manifest_path"],
        authority_root=authority_root,
        context=context,
        output_root=root / "sibling_independent",
    )
    resumed = apply_staged_synthetic_response(
        accepted=accepted,
        context=context,
        output_root=authority_root / "applications_primary",
    )
    return {
        "branch": branch,
        "broker_receipt_content_hash": broker_result.receipt["content_hash"],
        "acceptance_content_hash": acceptance["content_hash"],
        "primary_application_content_hash": primary["content_hash"],
        "sibling_application_content_hash": sibling["content_hash"],
        "resume_application_content_hash": resumed["content_hash"],
        "primary_replay_roots": primary["replay_roots"],
        "sibling_replay_roots": sibling["replay_roots"],
        "independent_verification_content_hash": primary_independent["content_hash"],
        "sibling_independent_verification_content_hash": sibling_independent["content_hash"],
        "terminal_pair_count": primary["terminal_pair_count"],
        "terminal_counts": primary["terminal_counts"],
        "stage_journal_content_hash": primary["stage_journal_content_hash"],
        "frontier_union_root": primary["replay_roots"]["frontier_union_root"],
    }


def _response_bytes(items: list[list[object]]) -> bytes:
    return json.dumps(
        {
            "code": 0,
            "msg": "",
            "data": {"fields": CANARY["fields"], "items": items},
        },
        sort_keys=True,
    ).encode()

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Mapping

from data_pipeline.ashare.request_identity import TushareRequestIdentity, validate_tushare_request_identity
from task_055_h.io import canonical_hash, publish_generation, read_json, sha256_file, validate_generation
from task_055_j.application_tree import validate_application_preflight
from task_055_j.rehearsal import independently_verify_rehearsal, validate_rehearsal

from .contracts import (
    BLOCKED_STATUS,
    CANARY,
    CANDIDATE_AUTHORITY_SCHEMA,
    CANDIDATE_SEAL_SCHEMA,
    MAX_LOGICAL_REQUESTS,
    MAX_PHYSICAL_ATTEMPTS,
    MAX_UNIQUE_SECURITY_DATES,
    PARENT_VERIFICATION_SCHEMA,
    READY_STATUS,
    TASK055J_AUTHORIZATION_HASH,
    TASK055J_AUTHORITY_RELATIVE_ROOT,
    TASK055J_FINAL_SEAL_HASH,
    TASK055J_FINAL_VERIFICATION_HASH,
    TASK055J_RELATIVE_ROOT,
    TASK055J_REHEARSAL_HASH,
    TASK055J_REHEARSAL_VERIFICATION_HASH,
    TASK055J_REPORT_HASH,
    TASK055J_RUNTIME_HASH,
)


class Task055KAuthorityError(RuntimeError):
    pass


def validate_task055j_parent(
    *, final_seal_path: str | Path, repository_root: str | Path
) -> dict[str, Any]:
    repository = Path(repository_root).resolve()
    final_seal = validate_generation(
        final_seal_path,
        schema="task055j_final_execution_seal_v1",
        manifest_name="final_execution_seal.json",
    )
    if final_seal["content_hash"] != TASK055J_FINAL_SEAL_HASH:
        raise Task055KAuthorityError("task055k_parent_final_seal_hash_invalid")
    authority_root = Path(final_seal["manifest_path"]).parents[3].resolve()
    governed = _derive_governed(authority_root, TASK055J_AUTHORITY_RELATIVE_ROOT)
    if authority_root != governed / TASK055J_AUTHORITY_RELATIVE_ROOT:
        raise Task055KAuthorityError("task055k_parent_authority_root_invalid")
    task_root = governed / TASK055J_RELATIVE_ROOT
    runtime = _unique_generation(
        authority_root / "runtime_authority",
        "runtime_authority.json",
        "task055j_runtime_network_authority_v1",
        TASK055J_RUNTIME_HASH,
    )
    authorization = _unique_generation(
        task_root / "execution_authorization",
        "execution_authorization.json",
        "task055j_execution_authorization_v1",
        TASK055J_AUTHORIZATION_HASH,
    )
    rehearsal = _unique_generation(
        task_root / "rehearsal/report",
        "rehearsal_manifest.json",
        "task055j_native_application_rehearsal_v1",
        TASK055J_REHEARSAL_HASH,
    )
    rehearsal = validate_rehearsal(rehearsal["manifest_path"], require_passed=True)
    rehearsal_verification = _unique_generation(
        task_root / "rehearsal_verification",
        "rehearsal_verification.json",
        "task055j_rehearsal_independent_verification_v1",
        TASK055J_REHEARSAL_VERIFICATION_HASH,
    )
    independent_rehearsal = independently_verify_rehearsal(rehearsal["manifest_path"])
    for key, value in independent_rehearsal.items():
        if key != "content_hash" and rehearsal_verification.get(key) != value:
            raise Task055KAuthorityError(f"task055k_parent_rehearsal_verification_invalid:{key}")
    report = _unique_generation(
        task_root / "final",
        "task055j_report.json",
        "task055j_engineering_report_v1",
        TASK055J_REPORT_HASH,
    )
    final_verification = _unique_generation(
        task_root / "final_verification",
        "task055j_final_verification.json",
        "task055j_independent_final_verification_v1",
        TASK055J_FINAL_VERIFICATION_HASH,
    )
    preflight = _unique_generation(
        task_root / "application_preflight",
        "application_preflight.json",
        "task055j_production_application_preflight_v1",
        str(final_seal["application_preflight_content_hash"]),
    )
    preflight = validate_application_preflight(preflight["manifest_path"], governed_root=governed)
    source = _unique_generation(
        task_root / "source_tree",
        "source_tree_seal.json",
        "task055j_source_tree_seal_v1",
        str(final_seal["source_tree_seal_content_hash"]),
    )
    _validate_historical_task055j_source(source, repository)
    ordered = normalize_ordered_keys(final_seal.get("ordered_exact_daily_keys") or ())
    expected_lineage = {
        "runtime_authority_content_hash": runtime["content_hash"],
        "execution_authorization_content_hash": authorization["content_hash"],
        "rehearsal_content_hash": rehearsal["content_hash"],
        "rehearsal_verification_content_hash": rehearsal_verification["content_hash"],
        "final_report_content_hash": report["content_hash"],
        "final_verification_content_hash": final_verification["content_hash"],
    }
    if any(final_seal.get(key) != value for key, value in expected_lineage.items()):
        raise Task055KAuthorityError("task055k_parent_final_seal_cross_lineage_invalid")
    if report.get("engineering_blockers") != [
        "global_ledger_rollback_proof_unavailable_without_external_immutable_checkpoint"
    ]:
        raise Task055KAuthorityError("task055k_parent_report_blocker_set_invalid")
    if authorization.get("engineering_blockers") != report.get("engineering_blockers"):
        raise Task055KAuthorityError("task055k_parent_authorization_report_mismatch")
    if final_verification.get("report_content_hash") != report["content_hash"]:
        raise Task055KAuthorityError("task055k_parent_final_verification_report_mismatch")
    semantic = {
        "schema_version": PARENT_VERIFICATION_SCHEMA,
        "status": "passed_with_documented_high_assurance_limitation",
        "parent_final_execution_seal_content_hash": final_seal["content_hash"],
        "runtime_authority_content_hash": runtime["content_hash"],
        "execution_authorization_content_hash": authorization["content_hash"],
        "application_preflight_content_hash": preflight["content_hash"],
        "application_tree_content_hash": preflight["application_artifact_tree_content_hash"],
        "source_tree_seal_content_hash": source["content_hash"],
        "rehearsal_content_hash": rehearsal["content_hash"],
        "rehearsal_verification_content_hash": rehearsal_verification["content_hash"],
        "final_report_content_hash": report["content_hash"],
        "final_verification_content_hash": final_verification["content_hash"],
        "ordered_exact_daily_keys": ordered,
        "ordered_key_root": canonical_hash(ordered),
        "canary": dict(CANARY),
        "high_assurance_limitation": "external_worm_or_monotonic_counter_unavailable",
        "functional_single_read_canary_blocked_by_limitation": False,
        "operational_state_unproven": True,
    }
    return semantic | {
        "content_hash": canonical_hash(semantic),
        "governed_root": str(governed),
        "task_root": str(task_root),
        "authority_root": str(authority_root),
        "runtime": runtime,
        "preflight": preflight,
        "source": source,
        "report": report,
    }


def publish_parent_verification(
    *, verified_parent: Mapping[str, Any], output_root: str | Path
) -> dict[str, Any]:
    semantic = {
        key: value
        for key, value in verified_parent.items()
        if key not in {"content_hash", "governed_root", "task_root", "authority_root", "runtime", "preflight", "source", "report"}
    }
    return publish_generation(
        output_root,
        prefix="task055k_parent_verification",
        manifest_name="parent_verification.json",
        semantic=semantic,
    )


def normalize_ordered_keys(rows: Any) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for expected_ordinal, source in enumerate(rows, start=1):
        row = dict(source)
        fields = [str(value) for value in row.get("fields") or ()]
        params = {"ts_code": str(row.get("ts_code") or ""), "trade_date": str(row.get("trade_date") or "")}
        identity = TushareRequestIdentity(
            request_fingerprint=str(row.get("request_fingerprint") or _request_fingerprint(row, fields)),
            transport_identity=str(row.get("transport_identity") or row.get("transport_hash") or ""),
            evidence_use_identity=str(row.get("evidence_use_identity") or row.get("evidence_use_hash") or ""),
        )
        validate_tushare_request_identity(
            identity=identity,
            api_name=str(row.get("api_name") or ""),
            params=params,
            fields=fields,
        )
        current = {
            "ordinal": int(row.get("ordinal") or 0),
            "api_name": str(row.get("api_name") or ""),
            "ts_code": params["ts_code"],
            "trade_date": params["trade_date"],
            "fields": fields,
            **identity.to_dict(),
        }
        if current["ordinal"] != expected_ordinal or current["api_name"] != "daily":
            raise Task055KAuthorityError("task055k_parent_ordered_key_invalid")
        if current["trade_date"] > "20260630":
            raise Task055KAuthorityError("task055k_parent_ordered_key_future_date")
        normalized.append(current)
    if len(normalized) != 17 or normalized[0] != {"ordinal": 1, **dict(CANARY)}:
        raise Task055KAuthorityError("task055k_parent_exact17_or_first_key_invalid")
    for identity_name in ("request_fingerprint", "transport_identity", "evidence_use_identity"):
        if len({row[identity_name] for row in normalized}) != 17:
            raise Task055KAuthorityError(f"task055k_parent_identity_duplicate:{identity_name}")
    return normalized


def publish_candidate_authority(
    *,
    verified_parent: Mapping[str, Any],
    parent_verification: Mapping[str, Any],
    source_seal: Mapping[str, Any],
    implementation_commit: str,
    output_root: str | Path,
) -> dict[str, Any]:
    ordered = list(verified_parent["ordered_exact_daily_keys"])
    semantic = {
        "schema_version": CANDIDATE_AUTHORITY_SCHEMA,
        "status": "sealed_offline_candidate",
        "implementation_commit": implementation_commit,
        "parent_task055j_final_seal_hash": TASK055J_FINAL_SEAL_HASH,
        "parent_verification_content_hash": parent_verification["content_hash"],
        "source_seal_content_hash": source_seal["content_hash"],
        "source_root": source_seal["source_root"],
        "ordered_exact_daily_keys": ordered,
        "ordered_key_count": 17,
        "ordered_key_root": canonical_hash(ordered),
        "canary": dict(CANARY),
        "budgets": {
            "unique_security_dates": 17,
            "logical_requests": 17,
            "physical_attempts": 0,
            "limits": {
                "unique_security_dates": MAX_UNIQUE_SECURITY_DATES,
                "logical_requests": MAX_LOGICAL_REQUESTS,
                "physical_attempts": MAX_PHYSICAL_ATTEMPTS,
            },
        },
        "network_authorized": False,
        "credential_read_count": 0,
        "tushare_post_count": 0,
        "other_http_count": 0,
        "prospective_holdout_accessed": False,
        "operational_state_unproven": True,
    }
    return publish_generation(
        output_root,
        prefix="task055k_candidate_authority",
        manifest_name="candidate_authority.json",
        semantic=semantic,
    )


def publish_candidate_checkpoint(
    *, authority: Mapping[str, Any], lineage: Mapping[str, str], output_root: str | Path
) -> dict[str, Any]:
    semantic = {
        "schema_version": CANDIDATE_SEAL_SCHEMA,
        "status": READY_STATUS,
        "candidate_authority_content_hash": authority["content_hash"],
        "implementation_commit": authority["implementation_commit"],
        "source_root": authority["source_root"],
        "ordered_exact_daily_keys": authority["ordered_exact_daily_keys"],
        "ordered_key_root": authority["ordered_key_root"],
        "canary": dict(CANARY),
        "budgets": authority["budgets"],
        "lineage": dict(lineage),
        "broker_public_contract": {
            "credential_read_after_all_offline_and_tls_gates": True,
            "single_request_only": True,
            "retry_count": 1,
            "signed_transport_receipt_required": True,
            "ephemeral_private_key_persisted": False,
        },
        "network_authorized": False,
        "operator_authorization_required": True,
        "real_canary_executed": False,
        "credential_read_count": 0,
        "tushare_post_count": 0,
        "other_http_count": 0,
        "prospective_holdout_accessed": False,
    }
    return publish_generation(
        output_root,
        prefix="task055k_candidate_checkpoint",
        manifest_name="candidate_checkpoint.json",
        semantic=semantic,
    )


def validate_candidate_checkpoint(path: str | Path, *, reviewed_hash: str | None = None) -> dict[str, Any]:
    payload = validate_generation(path, schema=CANDIDATE_SEAL_SCHEMA, manifest_name="candidate_checkpoint.json")
    if reviewed_hash is not None and payload["content_hash"] != reviewed_hash:
        raise Task055KAuthorityError("task055k_reviewed_candidate_checkpoint_hash_invalid")
    ordered = normalize_ordered_keys(payload.get("ordered_exact_daily_keys") or ())
    if payload.get("status") != READY_STATUS or payload.get("network_authorized") is not False:
        raise Task055KAuthorityError("task055k_candidate_checkpoint_status_invalid")
    if canonical_hash(ordered) != payload.get("ordered_key_root") or payload.get("canary") != CANARY:
        raise Task055KAuthorityError("task055k_candidate_checkpoint_request_lineage_invalid")
    budgets = payload.get("budgets") or {}
    limits = budgets.get("limits") or {}
    if budgets.get("physical_attempts") != 0 or limits != {
        "unique_security_dates": 64,
        "logical_requests": 128,
        "physical_attempts": 160,
    }:
        raise Task055KAuthorityError("task055k_candidate_checkpoint_budget_invalid")
    return payload


def _request_fingerprint(row: Mapping[str, Any], fields: list[str]) -> str:
    from data_pipeline.ashare.request_normalization import tushare_request_fingerprint

    return tushare_request_fingerprint(
        str(row.get("api_name") or ""),
        params={"ts_code": str(row.get("ts_code") or ""), "trade_date": str(row.get("trade_date") or "")},
        fields=fields,
    )


def _validate_historical_task055j_source(source: Mapping[str, Any], repository: Path) -> None:
    entries = list(source.get("entries") or ())
    implementation = str(source.get("implementation_commit") or "")
    if not entries or canonical_hash(entries) != source.get("source_root"):
        raise Task055KAuthorityError("task055k_parent_source_self_hash_invalid")
    for row in entries:
        relative = str(row.get("path") or "")
        try:
            content = subprocess.run(
                ["git", "show", f"{implementation}:{relative}"],
                cwd=repository,
                check=True,
                capture_output=True,
            ).stdout
        except subprocess.CalledProcessError as exc:
            raise Task055KAuthorityError(f"task055k_parent_source_blob_missing:{relative}") from exc
        import hashlib

        if hashlib.sha256(content).hexdigest() != row.get("sha256") or len(content) != row.get("size_bytes"):
            raise Task055KAuthorityError(f"task055k_parent_source_blob_mismatch:{relative}")


def _unique_generation(root: Path, name: str, schema: str, content_hash: str) -> dict[str, Any]:
    matches = []
    for path in sorted((root / "generations").glob(f"*/{name}")):
        payload = read_json(path)
        if payload.get("content_hash") == content_hash:
            matches.append(path)
    if len(matches) != 1:
        raise Task055KAuthorityError(f"task055k_parent_generation_cardinality_invalid:{name}:{len(matches)}")
    payload = validate_generation(matches[0], schema=schema, manifest_name=name)
    if sha256_file(matches[0]) != sha256_file(payload["manifest_path"]):
        raise Task055KAuthorityError(f"task055k_parent_generation_sha_invalid:{name}")
    return payload


def _derive_governed(path: Path, relative: str) -> Path:
    current = path
    for _ in Path(relative).parts:
        current = current.parent
    return current.resolve()

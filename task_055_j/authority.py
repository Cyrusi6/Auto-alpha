from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Mapping

from task_055_h.io import atomic_json, canonical_hash, publish_generation, read_json, sha256_file, validate_generation
from task_055_i.authority import (
    validate_execution_authorization as validate_task055i_execution_authorization,
    validate_runtime_authority as validate_task055i_runtime_authority,
)
from task_055_i.run import verify_task055i_report
from task_055_i.verifier import verify_scrubbed_evidence as verify_task055i_scrubbed

from .application_tree import validate_application_preflight, validate_application_tree_seal
from .contracts import (
    APPLICATION_PREFLIGHT_SCHEMA,
    AUTHORITY_RELATIVE_ROOT,
    AUTHORITY_SCHEMA,
    AUTHORIZATION_SCHEMA,
    BASELINE_COMMIT,
    BLOCKED_STATUS,
    CANARY,
    FINAL_EXECUTION_SEAL_SCHEMA,
    MAX_LOGICAL_REQUESTS,
    MAX_PHYSICAL_ATTEMPTS,
    MAX_UNIQUE_SECURITY_DATES,
    PARENT_AUTHORIZATION_SEAL_HASH,
    PARENT_CANARY_PLAN_HASH,
    PARENT_EXECUTION_AUTHORIZATION_HASH,
    PARENT_FINAL_VERIFICATION_HASH,
    PARENT_REPORT_HASH,
    PARENT_RUNTIME_AUTHORITY_HASH,
    PARENT_TASK055I_RELATIVE_ROOT,
    READY_STATUS,
    REHEARSAL_SCHEMA,
    REHEARSAL_VERIFICATION_SCHEMA,
    SOURCE_TREE_SCHEMA,
    TASK055J_RELATIVE_ROOT,
)
from .ledger import DurableHashJournal
from .source_tree import validate_source_tree_seal


class Task055JAuthorityError(RuntimeError):
    pass


def resolve_and_validate_parent(
    *, repository_root: str | Path, parent_task055i_report: str | Path
) -> dict[str, Any]:
    repository = Path(repository_root).resolve()
    report_path = Path(parent_task055i_report).resolve()
    task_root = report_path.parents[3]
    if task_root.name != Path(PARENT_TASK055I_RELATIVE_ROOT).name:
        raise Task055JAuthorityError("task055j_parent_task055i_root_invalid")
    governed = task_root.parent.parent.resolve()
    expected_task_root = governed / PARENT_TASK055I_RELATIVE_ROOT
    if task_root != expected_task_root or task_root.is_symlink() or governed.is_symlink():
        raise Task055JAuthorityError("task055j_parent_task055i_canonical_root_invalid")
    runtime_path = _current(
        governed / "governance/network_authority/task055h_single_canary_v1/runtime_authority",
        "runtime_authority.json",
    )
    runtime_raw = read_json(runtime_path)
    implementation = str(runtime_raw.get("implementation_commit") or "")
    runtime = validate_task055i_runtime_authority(
        runtime_path,
        require_pristine=True,
        historical_source_commit=implementation,
    )
    authorization_path = _current(task_root / "execution_authorization", "execution_authorization.json")
    authorization = validate_task055i_execution_authorization(authorization_path)
    scrubbed_path = task_root / "scrubbed_evidence/task055i_scrubbed_evidence.json"
    scrubbed = verify_task055i_scrubbed(scrubbed_path)
    rehearsal_path = _current(task_root / "rehearsal/report", "rehearsal_manifest.json")
    final_path = _current(task_root / "final_verification", "task055i_final_verification.json")
    verified = verify_task055i_report(
        report_path,
        runtime_authority=runtime_path,
        execution_authorization=authorization_path,
        scrubbed_evidence=scrubbed_path,
        rehearsal_manifest=rehearsal_path,
        historical_source_commit=implementation,
    )
    final = validate_generation(
        final_path,
        schema="task055i_independent_final_verification_v1",
        manifest_name="task055i_final_verification.json",
    )
    expected = {
        "runtime": PARENT_RUNTIME_AUTHORITY_HASH,
        "authorization": PARENT_EXECUTION_AUTHORIZATION_HASH,
        "report": PARENT_REPORT_HASH,
        "final": PARENT_FINAL_VERIFICATION_HASH,
    }
    actual = {
        "runtime": runtime["content_hash"],
        "authorization": authorization["content_hash"],
        "report": read_json(report_path)["content_hash"],
        "final": final["content_hash"],
    }
    if actual != expected or canonical_hash(verified) != final["content_hash"]:
        raise Task055JAuthorityError(f"task055j_parent_task055i_hash_mismatch:{actual}")
    if runtime.get("parent_authorization_seal_hash") != PARENT_AUTHORIZATION_SEAL_HASH:
        raise Task055JAuthorityError("task055j_parent_task055h_seal_mismatch")
    if runtime.get("single_request_plan_hash") != PARENT_CANARY_PLAN_HASH:
        raise Task055JAuthorityError("task055j_parent_canary_plan_mismatch")
    ordered = _validate_ordered_keys(runtime.get("ordered_exact_daily_keys") or ())
    return {
        "repository": repository,
        "governed": governed,
        "task_root": task_root,
        "runtime": runtime,
        "authorization": authorization,
        "scrubbed": scrubbed,
        "report": read_json(report_path),
        "final": final,
        "ordered_keys": ordered,
        "paths": {
            "runtime": runtime_path,
            "authorization": authorization_path,
            "scrubbed": scrubbed_path,
            "rehearsal": rehearsal_path,
            "report": report_path,
            "final": final_path,
        },
    }


def publish_runtime_authority(
    *,
    parent: Mapping[str, Any],
    source_tree_seal: str | Path,
    application_preflight: str | Path,
    implementation_commit: str,
) -> dict[str, Any]:
    repository = Path(parent["repository"])
    governed = Path(parent["governed"])
    if _git(repository, "rev-parse", "HEAD") != implementation_commit or _git(repository, "status", "--porcelain"):
        raise Task055JAuthorityError("task055j_authority_requires_clean_implementation_commit")
    source = validate_source_tree_seal(
        source_tree_seal,
        repository_root=repository,
        require_clean=True,
        allow_evidence_only_descendant=False,
    )
    preflight = validate_application_preflight(application_preflight, governed_root=governed)
    authority_root = governed / AUTHORITY_RELATIVE_ROOT
    authority_root.mkdir(parents=True, exist_ok=True)
    if authority_root.is_symlink():
        raise Task055JAuthorityError("task055j_authority_root_symlink")
    subroots = (
        "network_journal",
        "transport_spend_journal",
        "transport_receipts",
        "cache_data",
        "executions",
        "acceptance",
        "applications",
        "application_journal",
        "runtime_authority",
        "final_execution_seal",
    )
    for name in subroots:
        path = authority_root / name
        path.mkdir(exist_ok=True)
        if path.is_symlink():
            raise Task055JAuthorityError(f"task055j_authority_subroot_symlink:{name}")
    lock_path = authority_root / "single_canary.lock"
    lock_path.touch(exist_ok=True)
    if lock_path.is_symlink() or not lock_path.is_file():
        raise Task055JAuthorityError("task055j_single_flight_lock_invalid")
    application_lock = authority_root / "application.lock"
    application_lock.touch(exist_ok=True)
    if application_lock.is_symlink() or not application_lock.is_file():
        raise Task055JAuthorityError("task055j_application_lock_invalid")
    network = DurableHashJournal(authority_root / "network_journal", name="network")
    spend = DurableHashJournal(authority_root / "transport_spend_journal", name="transport_spend")
    network.append(
        {
            "event_id": canonical_hash([PARENT_RUNTIME_AUTHORITY_HASH, "task055j_authority_initialized"]),
            "event": "authority_initialized",
            "parent_runtime_authority_hash": PARENT_RUNTIME_AUTHORITY_HASH,
        }
    )
    for row in parent["ordered_keys"]:
        network.append(
            {
                "event_id": canonical_hash([PARENT_CANARY_PLAN_HASH, "registered", row["ordinal"]]),
                "event": "request_registered",
                **{key: row[key] for key in ("ordinal", "api_name", "ts_code", "trade_date", "transport_hash", "evidence_use_hash")},
            }
        )
    spend.append(
        {
            "event_id": canonical_hash([PARENT_RUNTIME_AUTHORITY_HASH, "task055j_transport_initialized"]),
            "event": "transport_authority_initialized",
            "parent_runtime_authority_hash": PARENT_RUNTIME_AUTHORITY_HASH,
        }
    )
    initial_network = network.checkpoint()
    initial_spend = spend.checkpoint()
    if _physical_attempts(spend.rows()) != 0:
        raise Task055JAuthorityError("task055j_new_authority_not_pristine")
    root_identities = {
        "repository": _root_identity(repository),
        "governed": _root_identity(governed),
        "authority": _root_identity(authority_root),
        "network_journal": _root_identity(authority_root / "network_journal"),
        "transport_spend": _root_identity(authority_root / "transport_spend_journal"),
        "cache": _root_identity(authority_root / "cache_data"),
        "receipts": _root_identity(authority_root / "transport_receipts"),
        "applications": _root_identity(authority_root / "applications"),
        "single_flight_lock": _file_identity(lock_path),
        "application_lock": _file_identity(application_lock),
    }
    registry_semantic = {
        "schema_version": "task055j_network_authority_registry_v1",
        "status": "canonical",
        "authority_relative_root": AUTHORITY_RELATIVE_ROOT,
        "task_output_relative_root": TASK055J_RELATIVE_ROOT,
        "parent_runtime_authority_hash": PARENT_RUNTIME_AUTHORITY_HASH,
        "ordered_key_root": canonical_hash(parent["ordered_keys"]),
        "ordered_key_count": len(parent["ordered_keys"]),
        "canary": dict(CANARY),
        "limits": {
            "unique_security_dates": MAX_UNIQUE_SECURITY_DATES,
            "logical_requests": MAX_LOGICAL_REQUESTS,
            "physical_attempts": MAX_PHYSICAL_ATTEMPTS,
        },
        "root_identities": root_identities,
        "budget_reset_allowed": False,
        "root_override_allowed": False,
        "rollback_detection_anchor": "externally_reviewed_final_execution_seal_hash",
    }
    registry = registry_semantic | {"content_hash": canonical_hash(registry_semantic)}
    registry_path = authority_root / "authority_registry.json"
    if registry_path.exists():
        if read_json(registry_path) != registry:
            raise Task055JAuthorityError("task055j_authority_registry_drift")
    else:
        atomic_json(registry_path, registry)
    ordered = parent["ordered_keys"]
    runtime_semantic = {
        "schema_version": AUTHORITY_SCHEMA,
        "status": "sealed_offline_no_network",
        "baseline_commit": BASELINE_COMMIT,
        "implementation_commit": implementation_commit,
        "parent_runtime_authority_hash": PARENT_RUNTIME_AUTHORITY_HASH,
        "parent_execution_authorization_hash": PARENT_EXECUTION_AUTHORIZATION_HASH,
        "parent_report_hash": PARENT_REPORT_HASH,
        "parent_final_verification_hash": PARENT_FINAL_VERIFICATION_HASH,
        "parent_authorization_seal_hash": PARENT_AUTHORIZATION_SEAL_HASH,
        "parent_canary_plan_hash": PARENT_CANARY_PLAN_HASH,
        "authority_relative_root": AUTHORITY_RELATIVE_ROOT,
        "task_output_relative_root": TASK055J_RELATIVE_ROOT,
        "canonical_subroots": {
            "network_journal": "network_journal",
            "transport_spend": "transport_spend_journal",
            "transport_receipts": "transport_receipts",
            "cache_data": "cache_data",
            "executions": "executions",
            "acceptance": "acceptance",
            "applications": "applications",
            "application_journal": "application_journal",
            "single_flight_lock": "single_canary.lock",
            "application_lock": "application.lock",
            "registry": "authority_registry.json",
        },
        "registry_sha256": sha256_file(registry_path),
        "registry_content_hash": registry["content_hash"],
        "root_identities": root_identities,
        "ordered_exact_daily_keys": ordered,
        "ordered_key_count": len(ordered),
        "ordered_key_root": canonical_hash(ordered),
        "canary": dict(CANARY),
        "single_request_plan": parent["runtime"]["single_request_plan"],
        "single_request_plan_hash": PARENT_CANARY_PLAN_HASH,
        "retry_count": 1,
        "resume_authorized": False,
        "batch_authorized": False,
        "budgets": {
            "unique_security_dates": len({(row["ts_code"], row["trade_date"]) for row in ordered}),
            "logical_requests": len(ordered),
            "physical_attempts": 0,
            "limits": registry["limits"],
        },
        "initial_network_journal": initial_network,
        "initial_transport_spend": initial_spend,
        "source_tree_seal_content_hash": source["content_hash"],
        "source_root": source["source_root"],
        "application_preflight_content_hash": preflight["content_hash"],
        "application_tree_content_hash": preflight["application_artifact_tree_content_hash"],
        "application_tree_root": preflight["application_artifact_tree_root"],
        "max_validated_source_date": preflight["max_validated_source_date"],
        "application_artifacts": parent["runtime"]["application_artifacts"],
        "network_execution": {
            "credential_read_count": 0,
            "tushare_post_count": 0,
            "other_market_http_count": 0,
            "prospective_holdout_accessed": False,
            "max_read_date": preflight["max_validated_source_date"],
        },
    }
    result = publish_generation(
        authority_root / "runtime_authority",
        prefix="task055j_runtime_authority",
        manifest_name="runtime_authority.json",
        semantic=runtime_semantic,
    )
    return validate_runtime_authority(
        result["manifest_path"],
        repository_root=repository,
        require_pristine=True,
        allow_evidence_only_descendant=False,
    )


def validate_runtime_authority(
    path: str | Path,
    *,
    repository_root: str | Path,
    require_pristine: bool,
    allow_evidence_only_descendant: bool,
) -> dict[str, Any]:
    repository = Path(repository_root).resolve()
    payload = validate_generation(path, schema=AUTHORITY_SCHEMA, manifest_name="runtime_authority.json")
    authority_root = Path(payload["manifest_path"]).parents[3]
    governed = _derive_governed(authority_root)
    if authority_root != governed / AUTHORITY_RELATIVE_ROOT:
        raise Task055JAuthorityError("task055j_runtime_authority_root_invalid")
    if payload.get("parent_runtime_authority_hash") != PARENT_RUNTIME_AUTHORITY_HASH:
        raise Task055JAuthorityError("task055j_runtime_parent_invalid")
    ordered = _validate_ordered_keys(payload.get("ordered_exact_daily_keys") or ())
    if payload.get("ordered_key_count") != 17 or canonical_hash(ordered) != payload.get("ordered_key_root"):
        raise Task055JAuthorityError("task055j_runtime_ordered_key_root_invalid")
    if payload.get("canary") != CANARY or payload.get("single_request_plan_hash") != PARENT_CANARY_PLAN_HASH:
        raise Task055JAuthorityError("task055j_runtime_canary_invalid")
    source_manifest = _find_manifest_by_hash(
        governed / TASK055J_RELATIVE_ROOT / "source_tree",
        "source_tree_seal.json",
        payload["source_tree_seal_content_hash"],
    )
    source = validate_source_tree_seal(
        source_manifest,
        repository_root=repository,
        require_clean=True,
        allow_evidence_only_descendant=allow_evidence_only_descendant,
    )
    if source["source_root"] != payload.get("source_root"):
        raise Task055JAuthorityError("task055j_runtime_source_root_invalid")
    preflight_manifest = _find_manifest_by_hash(
        governed / TASK055J_RELATIVE_ROOT / "application_preflight",
        "application_preflight.json",
        payload["application_preflight_content_hash"],
    )
    preflight = validate_application_preflight(preflight_manifest, governed_root=governed)
    if preflight["application_artifact_tree_content_hash"] != payload.get("application_tree_content_hash"):
        raise Task055JAuthorityError("task055j_runtime_application_tree_invalid")
    if preflight["max_validated_source_date"] != payload.get("max_validated_source_date"):
        raise Task055JAuthorityError("task055j_runtime_max_source_date_invalid")
    if (payload.get("network_execution") or {}).get("max_read_date") != preflight["max_validated_source_date"]:
        raise Task055JAuthorityError("task055j_runtime_read_date_lineage_invalid")
    registry_path = authority_root / "authority_registry.json"
    registry = read_json(registry_path)
    if sha256_file(registry_path) != payload.get("registry_sha256") or registry.get("content_hash") != payload.get("registry_content_hash"):
        raise Task055JAuthorityError("task055j_runtime_registry_invalid")
    _validate_root_identities(payload, repository, governed, authority_root)
    network = DurableHashJournal(authority_root / "network_journal", name="network")
    spend = DurableHashJournal(authority_root / "transport_spend_journal", name="transport_spend")
    network.assert_ancestor(payload["initial_network_journal"])
    spend.assert_ancestor(payload["initial_transport_spend"])
    physical = _physical_attempts(spend.rows())
    if physical > MAX_PHYSICAL_ATTEMPTS or (require_pristine and physical != 0):
        raise Task055JAuthorityError("task055j_runtime_physical_budget_invalid")
    if payload.get("resume_authorized") is not False or payload.get("batch_authorized") is not False:
        raise Task055JAuthorityError("task055j_runtime_resume_boundary_invalid")
    return payload | {
        "authority_root": str(authority_root),
        "governed_root": str(governed),
        "repository_root": str(repository),
        "current_network_journal": network.checkpoint(),
        "current_transport_spend": spend.checkpoint(),
        "current_physical_attempt_count": physical,
        "application_preflight_manifest": str(preflight_manifest),
        "source_tree_manifest": str(source_manifest),
    }


def publish_execution_authorization(
    *, runtime_authority: Mapping[str, Any], rehearsal: Mapping[str, Any], rehearsal_verification: Mapping[str, Any]
) -> dict[str, Any]:
    blockers: list[str] = [
        "global_ledger_rollback_proof_unavailable_without_external_immutable_checkpoint"
    ]
    if rehearsal.get("schema_version") != REHEARSAL_SCHEMA or rehearsal.get("status") != "passed":
        blockers.append("native_rehearsal_not_passed")
    if rehearsal.get("evidence_scope") != "synthetic_rehearsal_only" or rehearsal.get("production_seal_eligible") is not False:
        blockers.append("native_rehearsal_scope_invalid")
    if rehearsal_verification.get("schema_version") != REHEARSAL_VERIFICATION_SCHEMA or rehearsal_verification.get("status") != "passed":
        blockers.append("native_rehearsal_independent_verification_failed")
    if rehearsal_verification.get("rehearsal_content_hash") != rehearsal.get("content_hash"):
        blockers.append("native_rehearsal_lineage_invalid")
    status = READY_STATUS if not blockers else BLOCKED_STATUS
    semantic = {
        "schema_version": AUTHORIZATION_SCHEMA,
        "status": status,
        "runtime_authority_content_hash": runtime_authority["content_hash"],
        "source_root": runtime_authority["source_root"],
        "application_preflight_content_hash": runtime_authority["application_preflight_content_hash"],
        "rehearsal_content_hash": rehearsal.get("content_hash"),
        "rehearsal_verification_content_hash": rehearsal_verification.get("content_hash"),
        "ordered_key_root": runtime_authority["ordered_key_root"],
        "canary": dict(CANARY),
        "single_request_plan_hash": PARENT_CANARY_PLAN_HASH,
        "engineering_blockers": blockers,
        "resume_authorized": False,
        "batch_authorized": False,
        "operational_state_proven": False,
        "operational_state_unproven": True,
        "operational_blockers": ["operational_state_unproven:legacy_writer_roots_not_globally_enforced"],
        "network_execution": {
            "credential_read_count": 0,
            "tushare_post_count": 0,
            "other_market_http_count": 0,
            "prospective_holdout_accessed": False,
        },
        "certification_ready": False,
        "portfolio_ready": False,
        "optimizer_ready": False,
        "paper_ready": False,
        "live_ready": False,
    }
    output = Path(runtime_authority["governed_root"]) / TASK055J_RELATIVE_ROOT / "execution_authorization"
    return publish_generation(
        output,
        prefix="task055j_execution_authorization",
        manifest_name="execution_authorization.json",
        semantic=semantic,
    )


def validate_execution_authorization(path: str | Path) -> dict[str, Any]:
    payload = validate_generation(path, schema=AUTHORIZATION_SCHEMA, manifest_name="execution_authorization.json")
    if payload.get("status") not in {READY_STATUS, BLOCKED_STATUS}:
        raise Task055JAuthorityError("task055j_execution_authorization_status_invalid")
    if payload.get("canary") != CANARY or payload.get("single_request_plan_hash") != PARENT_CANARY_PLAN_HASH:
        raise Task055JAuthorityError("task055j_execution_authorization_canary_invalid")
    if any(payload.get(key) is not False for key in ("certification_ready", "portfolio_ready", "optimizer_ready", "paper_ready", "live_ready")):
        raise Task055JAuthorityError("task055j_execution_authorization_downstream_invalid")
    return payload


def publish_final_execution_seal(
    *,
    runtime_authority: Mapping[str, Any],
    execution_authorization: Mapping[str, Any],
    rehearsal: Mapping[str, Any],
    rehearsal_verification: Mapping[str, Any],
    final_report: Mapping[str, Any],
    final_verification: Mapping[str, Any],
) -> dict[str, Any]:
    blockers = list(execution_authorization.get("engineering_blockers") or ())
    if execution_authorization.get("status") != READY_STATUS:
        blockers.append("execution_authorization_not_ready")
    expected_verification_status = (
        "passed" if execution_authorization.get("status") == READY_STATUS else "blocked_verified"
    )
    if final_verification.get("status") != expected_verification_status:
        blockers.append("final_independent_verification_not_passed")
    status = READY_STATUS if not blockers else BLOCKED_STATUS
    semantic = {
        "schema_version": FINAL_EXECUTION_SEAL_SCHEMA,
        "status": status,
        "implementation_commit": runtime_authority["implementation_commit"],
        "runtime_authority_content_hash": runtime_authority["content_hash"],
        "execution_authorization_content_hash": execution_authorization["content_hash"],
        "application_preflight_content_hash": runtime_authority["application_preflight_content_hash"],
        "application_tree_content_hash": runtime_authority["application_tree_content_hash"],
        "application_tree_root": runtime_authority["application_tree_root"],
        "source_tree_seal_content_hash": runtime_authority["source_tree_seal_content_hash"],
        "source_root": runtime_authority["source_root"],
        "rehearsal_content_hash": rehearsal["content_hash"],
        "rehearsal_verification_content_hash": rehearsal_verification["content_hash"],
        "final_report_content_hash": final_report["content_hash"],
        "final_verification_content_hash": final_verification["content_hash"],
        "parent_authorization_seal_hash": PARENT_AUTHORIZATION_SEAL_HASH,
        "parent_canary_plan_hash": PARENT_CANARY_PLAN_HASH,
        "ordered_exact_daily_keys": runtime_authority["ordered_exact_daily_keys"],
        "ordered_key_count": runtime_authority["ordered_key_count"],
        "ordered_key_root": runtime_authority["ordered_key_root"],
        "canary": dict(CANARY),
        "budgets": runtime_authority["budgets"],
        "root_identities": runtime_authority["root_identities"],
        "registry_content_hash": runtime_authority["registry_content_hash"],
        "initial_network_journal": runtime_authority["initial_network_journal"],
        "initial_transport_spend": runtime_authority["initial_transport_spend"],
        "engineering_blockers": sorted(set(blockers)),
        "review_required_before_execution": True,
        "resume_authorized": False,
        "batch_authorized": False,
        "real_canary_executed": False,
        "network_execution": {
            "credential_read_count": 0,
            "tushare_post_count": 0,
            "other_market_http_count": 0,
            "prospective_holdout_accessed": False,
        },
    }
    result = publish_generation(
        Path(runtime_authority["authority_root"]) / "final_execution_seal",
        prefix="task055j_final_execution_seal",
        manifest_name="final_execution_seal.json",
        semantic=semantic,
    )
    return result


def validate_final_execution_seal(
    path: str | Path,
    *,
    reviewed_hash: str,
    repository_root: str | Path,
    require_ready: bool,
    require_pristine: bool,
) -> dict[str, Any]:
    payload = validate_generation(path, schema=FINAL_EXECUTION_SEAL_SCHEMA, manifest_name="final_execution_seal.json")
    if payload["content_hash"] != reviewed_hash:
        raise Task055JAuthorityError("task055j_reviewed_final_execution_seal_hash_invalid")
    if require_ready and payload.get("status") != READY_STATUS:
        raise Task055JAuthorityError("task055j_final_execution_seal_not_ready")
    authority_root = Path(payload["manifest_path"]).parents[3]
    runtime_manifest = _find_manifest_by_hash(
        authority_root / "runtime_authority", "runtime_authority.json", payload["runtime_authority_content_hash"]
    )
    runtime = validate_runtime_authority(
        runtime_manifest,
        repository_root=repository_root,
        require_pristine=require_pristine,
        allow_evidence_only_descendant=True,
    )
    if runtime["source_root"] != payload.get("source_root") or runtime["application_tree_root"] != payload.get("application_tree_root"):
        raise Task055JAuthorityError("task055j_final_execution_seal_runtime_lineage_invalid")
    ordered = _validate_ordered_keys(payload.get("ordered_exact_daily_keys") or ())
    if payload.get("ordered_key_count") != 17 or canonical_hash(ordered) != payload.get("ordered_key_root"):
        raise Task055JAuthorityError("task055j_final_execution_seal_keys_invalid")
    if payload.get("canary") != CANARY or ordered[0] != _canonical_ordered_canary():
        raise Task055JAuthorityError("task055j_final_execution_seal_canary_invalid")
    if payload.get("root_identities") != runtime.get("root_identities"):
        raise Task055JAuthorityError("task055j_final_execution_seal_root_binding_invalid")
    return payload | {
        "authority_root": str(authority_root),
        "governed_root": runtime["governed_root"],
        "repository_root": runtime["repository_root"],
        "runtime_authority": runtime,
    }


def _validate_ordered_keys(raw: Any) -> list[dict[str, Any]]:
    rows = [dict(row) for row in raw]
    if len(rows) != 17 or [int(row.get("ordinal") or 0) for row in rows] != list(range(1, 18)):
        raise Task055JAuthorityError("task055j_ordered_keys_cardinality_or_ordinal_invalid")
    if rows[0] != _canonical_ordered_canary():
        raise Task055JAuthorityError("task055j_first_canary_invalid")
    if len({row.get("transport_hash") for row in rows}) != len(rows):
        raise Task055JAuthorityError("task055j_ordered_transport_duplicate")
    for row in rows:
        if str(row.get("trade_date") or "") > "20260630" or row.get("api_name") != "daily":
            raise Task055JAuthorityError("task055j_ordered_key_boundary_invalid")
    return rows


def _canonical_ordered_canary() -> dict[str, Any]:
    return {"ordinal": 1, **dict(CANARY)}


def _single_request_plan(first: Mapping[str, Any]) -> dict[str, Any]:
    request = {
        "api_name": first["api_name"],
        "params": {"ts_code": first["ts_code"], "trade_date": first["trade_date"]},
        "fields": list(first["fields"]),
        "ts_code": first["ts_code"],
        "trade_date": first["trade_date"],
        "transport_hash": first["transport_hash"],
        "evidence_use_hash": first["evidence_use_hash"],
    }
    return {
        "schema_version": "task055j_single_exact_daily_plan_v1",
        "status": "sealed_single_request_only",
        "requests": [request],
        "request_count": 1,
        "retry_count": 1,
        "must_stop_after_canary": True,
        "resume_authorized": False,
        "batch_authorized": False,
        "plan_hash": PARENT_CANARY_PLAN_HASH,
    }


def _validate_root_identities(payload: Mapping[str, Any], repository: Path, governed: Path, authority: Path) -> None:
    expected = {
        "repository": _root_identity(repository),
        "governed": _root_identity(governed),
        "authority": _root_identity(authority),
        "network_journal": _root_identity(authority / "network_journal"),
        "transport_spend": _root_identity(authority / "transport_spend_journal"),
        "cache": _root_identity(authority / "cache_data"),
        "receipts": _root_identity(authority / "transport_receipts"),
        "applications": _root_identity(authority / "applications"),
        "single_flight_lock": _file_identity(authority / "single_canary.lock"),
        "application_lock": _file_identity(authority / "application.lock"),
    }
    if payload.get("root_identities") != expected:
        raise Task055JAuthorityError("task055j_runtime_root_identity_drift")


def _root_identity(path: Path) -> dict[str, Any]:
    metadata = path.stat()
    return {
        "kind": "directory",
        "relative_name": path.name,
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "identity_hash": canonical_hash([str(path.resolve()), metadata.st_dev, metadata.st_ino, "directory"]),
    }


def _file_identity(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise Task055JAuthorityError("task055j_lock_file_invalid")
    metadata = path.stat()
    return {
        "kind": "file",
        "relative_name": path.name,
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "identity_hash": canonical_hash([str(path.resolve()), metadata.st_dev, metadata.st_ino, "file"]),
    }


def _derive_governed(authority_root: Path) -> Path:
    parts = Path(AUTHORITY_RELATIVE_ROOT).parts
    current = authority_root
    for _ in parts:
        current = current.parent
    return current.resolve()


def _find_manifest_by_hash(root: Path, name: str, content_hash: str) -> Path:
    candidates = sorted((root / "generations").glob(f"*/{name}"))
    matches = [path for path in candidates if read_json(path).get("content_hash") == content_hash]
    if len(matches) != 1:
        raise Task055JAuthorityError(f"task055j_manifest_hash_resolution_invalid:{name}:{len(matches)}")
    return matches[0]


def _current(root: Path, name: str) -> Path:
    pointer = read_json(root / "current.json")
    relative = Path(str(pointer.get("manifest") or ""))
    path = (root / relative).resolve()
    if root.resolve() not in path.parents or path.name != name or not path.is_file() or path.is_symlink():
        raise Task055JAuthorityError(f"task055j_current_pointer_invalid:{name}")
    return path


def _physical_attempts(rows: list[Mapping[str, Any]]) -> int:
    return sum(row.get("event") == "physical_post_intent" for row in rows)


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repository, check=True, text=True, capture_output=True).stdout.strip()

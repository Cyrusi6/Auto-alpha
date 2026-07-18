from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Mapping

from task_055_h.authorization import validate_authorization_seal, verify_scrubbed_evidence_package
from task_055_h.io import atomic_json, canonical_hash, publish_generation, read_json, sha256_file, validate_generation
from task_055_h.run import verify_task055h_report

from .contracts import (
    BASELINE_COMMIT,
    BLOCKED_STATUS,
    CANARY,
    EXECUTION_AUTHORIZATION_SCHEMA,
    GLOBAL_AUTHORITY_RELATIVE_ROOT,
    MAX_LOGICAL_REQUESTS,
    MAX_PHYSICAL_ATTEMPTS,
    MAX_UNIQUE_SECURITY_DATES,
    PARENT_AUTHORIZATION_SEAL_HASH,
    PARENT_CANARY_PLAN_HASH,
    PARENT_GIT_EVIDENCE_HASH,
    PARENT_TASK055H_RELATIVE_ROOT,
    READY_STATUS,
    RUNTIME_AUTHORITY_SCHEMA,
    SEMANTIC_SOURCE_PATHS,
)
from .ledger import HashChainLedger, count_events


class Task055IAuthorityError(RuntimeError):
    pass


def publish_task055i_authority(
    *,
    repository_root: str | Path,
    governed_root: str | Path,
    output_root: str | Path,
    implementation_commit: str,
    rehearsal_manifest: str | Path | None = None,
) -> dict[str, Any]:
    repository = Path(repository_root).resolve()
    governed = Path(governed_root).resolve()
    output = Path(output_root).resolve()
    if repository.is_symlink() or governed.is_symlink() or output.is_symlink():
        raise Task055IAuthorityError("task055i_root_symlink_forbidden")
    if _git(repository, "rev-parse", "HEAD") != implementation_commit:
        raise Task055IAuthorityError("task055i_implementation_commit_not_head")
    if _git(repository, "status", "--porcelain"):
        raise Task055IAuthorityError("task055i_authority_requires_clean_source_tree")
    parent = _validate_parent(repository, governed)
    application = _resolve_application_artifacts(governed, parent)
    authority_root = governed / GLOBAL_AUTHORITY_RELATIVE_ROOT
    authority_root.mkdir(parents=True, exist_ok=True)
    for name in ("network_ledger", "transport_spend", "cache_data", "executions", "acceptance", "applications", "runtime_authority"):
        path = authority_root / name
        path.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            raise Task055IAuthorityError(f"task055i_authority_subroot_symlink:{name}")
    lock_path = authority_root / "single_flight.lock"
    lock_path.touch(exist_ok=True)
    if lock_path.is_symlink():
        raise Task055IAuthorityError("task055i_single_flight_lock_symlink")

    network = HashChainLedger(authority_root / "network_ledger", name="network")
    transport = HashChainLedger(authority_root / "transport_spend", name="transport")
    network.append({
        "event_id": canonical_hash([PARENT_AUTHORIZATION_SEAL_HASH, "authority_initialized"]),
        "event": "authority_initialized",
        "parent_authorization_seal_hash": PARENT_AUTHORIZATION_SEAL_HASH,
        "parent_canary_plan_hash": PARENT_CANARY_PLAN_HASH,
    })
    ordered = list(parent["seal"]["ordered_exact_daily_keys"])
    for request in ordered:
        network.append({
            "event_id": canonical_hash([PARENT_AUTHORIZATION_SEAL_HASH, "request_registered", request["transport_hash"]]),
            "event": "request_registered",
            "api_name": request["api_name"],
            "ts_code": request["ts_code"],
            "trade_date": request["trade_date"],
            "transport_hash": request["transport_hash"],
            "evidence_use_hash": request["evidence_use_hash"],
            "ordinal": request["ordinal"],
        })
    transport.append({
        "event_id": canonical_hash([PARENT_AUTHORIZATION_SEAL_HASH, "transport_authority_initialized"]),
        "event": "transport_authority_initialized",
        "parent_authorization_seal_hash": PARENT_AUTHORIZATION_SEAL_HASH,
    })
    network_rows = network.rows()
    transport_rows = transport.rows()
    if count_events(network_rows, "physical_attempt_started") or count_events(transport_rows, "physical_post_started"):
        raise Task055IAuthorityError("task055i_authority_cannot_seed_after_network_attempt")

    source_hashes = _source_hashes(repository)
    semantic_source_root = canonical_hash(source_hashes)
    root_identities = {
        "repository": _root_identity(repository),
        "governed": _root_identity(governed),
        "authority": _root_identity(authority_root),
        "state": _root_identity(authority_root / "network_ledger"),
        "cache": _root_identity(authority_root / "cache_data"),
        "spend": _root_identity(authority_root / "transport_spend"),
    }
    registry_path = authority_root / "network_authority_registry.json"
    registry_semantic = {
        "schema_version": "task055i_network_authority_registry_v1",
        "parent_authorization_seal_hash": PARENT_AUTHORIZATION_SEAL_HASH,
        "parent_canary_plan_hash": PARENT_CANARY_PLAN_HASH,
        "governed_relative_root": GLOBAL_AUTHORITY_RELATIVE_ROOT,
        "root_identities": root_identities,
        "canary": dict(CANARY),
        "ordered_key_root": canonical_hash(ordered),
        "limits": {
            "unique_security_dates": MAX_UNIQUE_SECURITY_DATES,
            "logical_requests": MAX_LOGICAL_REQUESTS,
            "physical_attempts": MAX_PHYSICAL_ATTEMPTS,
        },
        "budget_reset_allowed": False,
        "state_root_override_allowed": False,
        "cache_root_override_allowed": False,
        "output_root_override_allowed": False,
    }
    registry_payload = registry_semantic | {"content_hash": canonical_hash(registry_semantic)}
    if registry_path.exists():
        if read_json(registry_path) != registry_payload:
            raise Task055IAuthorityError("task055i_network_authority_registry_drift")
    else:
        atomic_json(registry_path, registry_payload)
    runtime_semantic = {
        "schema_version": RUNTIME_AUTHORITY_SCHEMA,
        "status": "sealed_offline_no_network",
        "baseline_commit": BASELINE_COMMIT,
        "implementation_commit": implementation_commit,
        "parent_authorization_seal_hash": PARENT_AUTHORIZATION_SEAL_HASH,
        "parent_git_evidence_hash": PARENT_GIT_EVIDENCE_HASH,
        "parent_canary_plan_hash": PARENT_CANARY_PLAN_HASH,
        "parent_task055h_report_hash": parent["report"]["content_hash"],
        "parent_task055h_final_verification_hash": parent["final"]["content_hash"],
        "parent_task055h_access_journal_hash": parent["access"]["content_hash"],
        "parent_task055h_fee_attestation_hash": parent["fee"]["content_hash"],
        "parent_task055h_causal_attestation_hash": canonical_hash(parent["seal"].get("independent_causal_attestation") or {}),
        "governed_relative_root": GLOBAL_AUTHORITY_RELATIVE_ROOT,
        "parent_task055h_relative_root": PARENT_TASK055H_RELATIVE_ROOT,
        "canonical_subroots": {
            "network_ledger": "network_ledger",
            "transport_spend": "transport_spend",
            "cache_data": "cache_data",
            "executions": "executions",
            "acceptance": "acceptance",
            "applications": "applications",
            "single_flight_lock": "single_flight.lock",
            "registry": "network_authority_registry.json",
        },
        "network_authority_registry_sha256": sha256_file(registry_path),
        "network_authority_registry_content_hash": registry_payload["content_hash"],
        "root_identities": root_identities,
        "ordered_exact_daily_keys": ordered,
        "ordered_key_root": canonical_hash(ordered),
        "canary": dict(CANARY),
        "single_request_plan": parent["seal"]["canary_execution_plan"],
        "single_request_plan_hash": PARENT_CANARY_PLAN_HASH,
        "retry_count": 1,
        "resume_authorized": False,
        "batch_authorized": False,
        "budgets": {
            "unique_security_dates": len({(row["ts_code"], row["trade_date"]) for row in ordered}),
            "logical_requests": len(ordered),
            "physical_attempts": 0,
            "limits": {
                "unique_security_dates": MAX_UNIQUE_SECURITY_DATES,
                "logical_requests": MAX_LOGICAL_REQUESTS,
                "physical_attempts": MAX_PHYSICAL_ATTEMPTS,
            },
        },
        "initial_network_ledger": {"sequence": len(network_rows), "root": network.root_hash()},
        "initial_transport_spend": {"sequence": len(transport_rows), "root": transport.root_hash()},
        "application_artifacts": application,
        "semantic_source_hashes": source_hashes,
        "semantic_source_root": semantic_source_root,
        "credential_policy": {
            "inline_environment_token_allowed": False,
            "credential_file_absolute": True,
            "credential_file_owner_current_uid": True,
            "credential_file_permissions": ["0400", "0600"],
            "credential_read_after_tls_only": True,
        },
        "network_execution": {
            "credential_read_count": 0,
            "tushare_request_count": 0,
            "other_network_request_count": 0,
            "prospective_holdout_accessed": False,
        },
        "production_cli": "python -m task_055_i.network_cli canary",
        "resume_cli_available": False,
    }
    runtime = publish_generation(
        authority_root / "runtime_authority",
        prefix="runtime_authority",
        manifest_name="runtime_authority.json",
        semantic=runtime_semantic,
    )
    validate_runtime_authority(runtime["manifest_path"], require_pristine=True)

    rehearsal = _load_rehearsal(rehearsal_manifest)
    blockers: list[str] = []
    if rehearsal.get("status") != "passed" or rehearsal.get("production_seal_eligible") is not False:
        blockers.append("native_rehearsal_not_verified")
    if rehearsal.get("positive_chain_complete") is not True:
        blockers.append("positive_response_application_chain_unreachable")
    if rehearsal.get("negative_case_count", 0) < 8:
        blockers.append("negative_rehearsal_coverage_incomplete")
    operational_state_proven = False
    operational_blockers = ["operational_state_unproven:writer_cli_roots_not_globally_enforced"]
    status = READY_STATUS if not blockers else BLOCKED_STATUS
    authorization_semantic = {
        "schema_version": EXECUTION_AUTHORIZATION_SCHEMA,
        "status": status,
        "runtime_authority_content_hash": runtime["content_hash"],
        "runtime_authority_manifest_sha256": sha256_file(runtime["manifest_path"]),
        "reviewed_authority_hash": runtime["content_hash"],
        "parent_authorization_seal_hash": PARENT_AUTHORIZATION_SEAL_HASH,
        "parent_git_evidence_hash": PARENT_GIT_EVIDENCE_HASH,
        "single_request_plan_hash": PARENT_CANARY_PLAN_HASH,
        "canary": dict(CANARY),
        "rehearsal_content_hash": rehearsal.get("content_hash"),
        "rehearsal_artifact_root": rehearsal.get("artifact_root"),
        "semantic_source_root": semantic_source_root,
        "network_execution": {
            "credential_read_count": 0,
            "tushare_request_count": 0,
            "other_network_request_count": 0,
            "prospective_holdout_accessed": False,
        },
        "engineering_blockers": blockers,
        "operational_state_proven": operational_state_proven,
        "operational_state_unproven": True,
        "operational_blockers": operational_blockers,
        "resume_authorized": False,
        "certification_ready": False,
        "portfolio_ready": False,
        "paper_ready": False,
        "live_ready": False,
    }
    published = publish_generation(
        output / "execution_authorization",
        prefix="task055i_execution_authorization",
        manifest_name="execution_authorization.json",
        semantic=authorization_semantic,
    )
    return published | {"runtime_authority": runtime, "rehearsal": rehearsal}


def validate_runtime_authority(
    path: str | Path,
    *,
    require_pristine: bool,
    historical_source_commit: str | None = None,
) -> dict[str, Any]:
    payload = validate_generation(path, schema=RUNTIME_AUTHORITY_SCHEMA, manifest_name="runtime_authority.json")
    manifest_path = Path(payload["manifest_path"]).resolve()
    authority_root = manifest_path.parents[3]
    governed = _derive_governed(authority_root, str(payload.get("governed_relative_root") or ""))
    repository = Path(__file__).resolve().parents[1]
    if authority_root != governed / GLOBAL_AUTHORITY_RELATIVE_ROOT:
        raise Task055IAuthorityError("task055i_runtime_authority_path_invalid")
    if payload.get("parent_authorization_seal_hash") != PARENT_AUTHORIZATION_SEAL_HASH:
        raise Task055IAuthorityError("task055i_parent_authorization_hash_invalid")
    if payload.get("parent_git_evidence_hash") != PARENT_GIT_EVIDENCE_HASH:
        raise Task055IAuthorityError("task055i_parent_git_evidence_hash_invalid")
    if payload.get("single_request_plan_hash") != PARENT_CANARY_PLAN_HASH or payload.get("canary") != CANARY:
        raise Task055IAuthorityError("task055i_canary_identity_invalid")
    _validate_root_identities(payload, repository, governed, authority_root)
    registry = authority_root / str((payload.get("canonical_subroots") or {}).get("registry") or "")
    if (
        not registry.is_file()
        or registry.is_symlink()
        or sha256_file(registry) != payload.get("network_authority_registry_sha256")
        or read_json(registry).get("content_hash") != payload.get("network_authority_registry_content_hash")
    ):
        raise Task055IAuthorityError("task055i_network_authority_registry_invalid")
    _validate_source_state(payload, repository, historical_source_commit=historical_source_commit)
    _validate_parent(repository, governed)
    network = HashChainLedger(authority_root / "network_ledger", name="network")
    spend = HashChainLedger(authority_root / "transport_spend", name="transport")
    initial_network = payload.get("initial_network_ledger") or {}
    initial_spend = payload.get("initial_transport_spend") or {}
    network.assert_ancestor(sequence=int(initial_network.get("sequence") or 0), event_hash=str(initial_network.get("root") or ""))
    spend.assert_ancestor(sequence=int(initial_spend.get("sequence") or 0), event_hash=str(initial_spend.get("root") or ""))
    network_rows = network.rows()
    spend_rows = spend.rows()
    physical = count_events(spend_rows, "physical_post_started")
    if physical != count_events(network_rows, "physical_attempt_started"):
        raise Task055IAuthorityError("task055i_network_spend_attempt_mismatch")
    if physical > MAX_PHYSICAL_ATTEMPTS:
        raise Task055IAuthorityError("task055i_physical_attempt_budget_exceeded")
    if require_pristine and physical != 0:
        raise Task055IAuthorityError("task055i_authority_not_pristine")
    if payload.get("resume_authorized") is not False or payload.get("batch_authorized") is not False:
        raise Task055IAuthorityError("task055i_resume_or_batch_boundary_invalid")
    return payload | {
        "authority_root": str(authority_root),
        "governed_root": str(governed),
        "repository_root": str(repository),
        "current_network_ledger_root": network.root_hash(),
        "current_transport_spend_root": spend.root_hash(),
        "current_physical_attempt_count": physical,
    }


def validate_execution_authorization(path: str | Path) -> dict[str, Any]:
    payload = validate_generation(
        path,
        schema=EXECUTION_AUTHORIZATION_SCHEMA,
        manifest_name="execution_authorization.json",
    )
    if payload.get("status") not in {READY_STATUS, BLOCKED_STATUS}:
        raise Task055IAuthorityError("task055i_execution_authorization_status_invalid")
    if payload.get("parent_authorization_seal_hash") != PARENT_AUTHORIZATION_SEAL_HASH:
        raise Task055IAuthorityError("task055i_execution_authorization_parent_invalid")
    if payload.get("canary") != CANARY or payload.get("single_request_plan_hash") != PARENT_CANARY_PLAN_HASH:
        raise Task055IAuthorityError("task055i_execution_authorization_canary_invalid")
    counters = payload.get("network_execution") or {}
    if any(int(counters.get(key) or 0) for key in ("credential_read_count", "tushare_request_count", "other_network_request_count")):
        raise Task055IAuthorityError("task055i_execution_authorization_offline_counter_invalid")
    if counters.get("prospective_holdout_accessed") is not False or payload.get("resume_authorized") is not False:
        raise Task055IAuthorityError("task055i_execution_authorization_boundary_invalid")
    return payload


def _validate_parent(repository: Path, governed: Path) -> dict[str, Any]:
    root = governed / PARENT_TASK055H_RELATIVE_ROOT
    seal_path = _current(root / "authorization_seal", "authorization_seal.json")
    scrubbed_path = _current(root / "scrubbed_evidence", "scrubbed_authorization_evidence.json")
    report_path = _current(root / "final", "task055h_report.json")
    final_path = _current(root / "final_verification", "task055h_final_verification.json")
    access_path = _current(root / "access_journal", "access_journal.json")
    fee_path = _current(root / "fee_attestation", "fee_attestation.json")
    seal = validate_authorization_seal(seal_path, require_ready=True)
    if seal["content_hash"] != PARENT_AUTHORIZATION_SEAL_HASH:
        raise Task055IAuthorityError("task055i_parent_authorization_seal_drift")
    scrubbed = verify_scrubbed_evidence_package(scrubbed_path)
    if scrubbed["package_content_hash"] != PARENT_GIT_EVIDENCE_HASH:
        raise Task055IAuthorityError("task055i_parent_git_evidence_drift")
    verified = verify_task055h_report(
        report_path,
        authorization_seal=seal_path,
        scrubbed_evidence=scrubbed_path,
    )
    final = read_json(final_path)
    final_semantic = {key: value for key, value in final.items() if key not in {"content_hash", "generation_id"}}
    if canonical_hash(final_semantic) != final.get("content_hash") or final.get("content_hash") != canonical_hash(verified):
        raise Task055IAuthorityError("task055i_parent_final_verification_drift")
    report = read_json(report_path)
    access = read_json(access_path)
    fee = read_json(fee_path)
    if report.get("authorization_seal_content_hash") != seal["content_hash"]:
        raise Task055IAuthorityError("task055i_parent_report_lineage_invalid")
    return {
        "root": root,
        "seal": seal,
        "scrubbed": scrubbed,
        "report": report,
        "final": final,
        "access": access,
        "fee": fee,
        "paths": {
            "seal": seal_path,
            "scrubbed": scrubbed_path,
            "report": report_path,
            "final": final_path,
            "access": access_path,
            "fee": fee_path,
        },
    }


def _resolve_application_artifacts(governed: Path, parent: Mapping[str, Any]) -> dict[str, Any]:
    g_root = governed / "validation_runs/task_055_g_20260716_v3"
    g_current = read_json(g_root / "current.json")
    g_report = read_json(g_root / str(g_current["manifest"]))
    artifacts = g_report.get("artifacts") or {}
    access_path = g_root / str(artifacts["access_plan"])
    access = read_json(access_path)
    bundle_rows = [
        row for row in access.get("entries") or ()
        if row.get("dataset_role") == "task055a_simulation_bundle_manifest"
    ]
    if len(bundle_rows) != 1:
        raise Task055IAuthorityError("task055i_simulation_bundle_access_entry_invalid")
    bundle_path = governed / str(bundle_rows[0]["relative_path"])
    if sha256_file(bundle_path) != bundle_rows[0]["expected_sha256"]:
        raise Task055IAuthorityError("task055i_simulation_bundle_sha_invalid")
    bundle = read_json(bundle_path)
    bundle_root = bundle_path.parent
    canonical_entry = (bundle.get("artifacts") or {}).get("evidence:canonical_bundle") or {}
    canonical_path = bundle_root / str(canonical_entry.get("path") or "")
    if not canonical_path.is_file() or sha256_file(canonical_path) != canonical_entry.get("sha256"):
        raise Task055IAuthorityError("task055i_canonical_bundle_snapshot_invalid")
    canonical = read_json(canonical_path)
    result_paths = {
        "simulation_bundle": bundle_path,
        "canonical_bundle_snapshot": canonical_path,
        "fee_schedule": g_root / str(artifacts["fee_schedule_v2"]),
        "truth_v2": g_root / str(artifacts["truth_v2"]),
        "causal_frontier": g_root / str(artifacts["causal_frontier"]),
        "freeze_manifest": Path(canonical["artifact_paths"]["freeze_manifest"]),
        "universe_manifest": Path(canonical["artifact_paths"]["universe_manifest"]),
        "matrix_root": Path(canonical["artifact_paths"]["matrix_root"]),
        "tensor_root": Path(canonical["artifact_paths"]["tensor_root"]),
        "normalized_store_root": Path(canonical["artifact_paths"]["normalized_store_root"]),
        "promotion_policy": Path(canonical["artifact_paths"]["promotion_policy"]),
    }
    catalog = []
    for role, path in result_paths.items():
        if role.endswith("root"):
            if not path.is_dir() or path.is_symlink():
                raise Task055IAuthorityError(f"task055i_application_root_invalid:{role}")
            identity = canonical_hash([str(path.resolve()), path.stat().st_dev, path.stat().st_ino])
            catalog.append({"role": role, "relative_path": _relative_to_governed(path, governed), "root_identity": identity})
        else:
            if not path.is_file() or path.is_symlink():
                raise Task055IAuthorityError(f"task055i_application_file_invalid:{role}")
            catalog.append({"role": role, "relative_path": _relative_to_governed(path, governed), "sha256": sha256_file(path), "size_bytes": path.stat().st_size})
    feature_manifest = Path(canonical["artifact_paths"]["freeze_manifest"]).parent / "artifacts/feature_manifest/feature_set_manifest.json"
    if not feature_manifest.is_file():
        raise Task055IAuthorityError("task055i_feature_manifest_unresolved")
    catalog.append({"role": "feature_manifest", "relative_path": _relative_to_governed(feature_manifest, governed), "sha256": sha256_file(feature_manifest), "size_bytes": feature_manifest.stat().st_size})
    return {
        "catalog": catalog,
        "catalog_root": canonical_hash(catalog),
        "canonical_bundle_content_hash": canonical["content_hash"],
        "exact20_identity_root": canonical["exact20_identity_root"],
        "exact20_ids": list(canonical["exact20_ids"]),
        "feature_manifest_relative_path": _relative_to_governed(feature_manifest, governed),
        "research_cutoff": "20240530",
        "target_endpoint_horizon": 2,
    }


def _load_rehearsal(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {"status": "missing", "production_seal_eligible": False}
    payload = read_json(path)
    semantic = {key: value for key, value in payload.items() if key not in {"content_hash", "generation_id"}}
    if canonical_hash(semantic) != payload.get("content_hash"):
        raise Task055IAuthorityError("task055i_rehearsal_hash_invalid")
    return payload | {"manifest_path": str(Path(path).resolve())}


def _source_hashes(repository: Path) -> dict[str, str]:
    result = {}
    for relative in SEMANTIC_SOURCE_PATHS:
        path = repository / relative
        if not path.is_file():
            raise Task055IAuthorityError(f"task055i_semantic_source_missing:{relative}")
        result[relative] = sha256_file(path)
    return result


def _validate_source_state(
    payload: Mapping[str, Any],
    repository: Path,
    *,
    historical_source_commit: str | None = None,
) -> None:
    current = (
        _source_hashes_at_commit(repository, historical_source_commit)
        if historical_source_commit
        else _source_hashes(repository)
    )
    if current != payload.get("semantic_source_hashes") or canonical_hash(current) != payload.get("semantic_source_root"):
        raise Task055IAuthorityError("task055i_semantic_source_drift")
    implementation = str(payload.get("implementation_commit") or "")
    head = _git(repository, "rev-parse", "HEAD")
    if subprocess.run(["git", "merge-base", "--is-ancestor", implementation, head], cwd=repository).returncode != 0:
        raise Task055IAuthorityError("task055i_implementation_commit_not_ancestor")
    changed = _git(repository, "diff", "--name-only", f"{implementation}..{head}", "--", *SEMANTIC_SOURCE_PATHS)
    if changed and historical_source_commit is None:
        raise Task055IAuthorityError("task055i_source_changed_after_authority_seal")


def _source_hashes_at_commit(repository: Path, commit: str) -> dict[str, str]:
    import hashlib

    result: dict[str, str] = {}
    for relative in SEMANTIC_SOURCE_PATHS:
        completed = subprocess.run(
            ["git", "show", f"{commit}:{relative}"],
            cwd=repository,
            check=True,
            capture_output=True,
        )
        result[relative] = hashlib.sha256(completed.stdout).hexdigest()
    return result


def _validate_root_identities(payload: Mapping[str, Any], repository: Path, governed: Path, authority: Path) -> None:
    expected = {
        "repository": repository,
        "governed": governed,
        "authority": authority,
        "state": authority / "network_ledger",
        "cache": authority / "cache_data",
        "spend": authority / "transport_spend",
    }
    identities = payload.get("root_identities") or {}
    for role, path in expected.items():
        if path.is_symlink() or not path.is_dir():
            raise Task055IAuthorityError(f"task055i_root_missing_or_symlink:{role}")
        if identities.get(role) != _root_identity(path):
            raise Task055IAuthorityError(f"task055i_root_identity_mismatch:{role}")


def _derive_governed(authority_root: Path, relative: str) -> Path:
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or ".." in parts:
        raise Task055IAuthorityError("task055i_governed_relative_root_invalid")
    candidate = authority_root
    for _ in parts:
        candidate = candidate.parent
    if candidate / Path(relative) != authority_root:
        raise Task055IAuthorityError("task055i_governed_root_derivation_failed")
    return candidate


def _relative_to_governed(path: Path, governed: Path) -> str:
    resolved = path.resolve()
    if resolved != governed and governed not in resolved.parents:
        raise Task055IAuthorityError(f"task055i_artifact_outside_governed_root:{path.name}")
    return resolved.relative_to(governed).as_posix()


def _current(root: Path, manifest_name: str) -> Path:
    pointer = read_json(root / "current.json")
    relative = Path(str(pointer.get("manifest") or ""))
    if relative.is_absolute() or ".." in relative.parts:
        raise Task055IAuthorityError("task055i_parent_pointer_invalid")
    path = (root / relative).resolve()
    if root.resolve() not in path.parents or path.name != manifest_name or not path.is_file() or path.is_symlink():
        raise Task055IAuthorityError("task055i_parent_pointer_target_invalid")
    payload = read_json(path)
    if pointer.get("content_hash") != payload.get("content_hash"):
        raise Task055IAuthorityError("task055i_parent_pointer_hash_invalid")
    return path


def _root_identity(path: Path) -> dict[str, Any]:
    metadata = path.stat()
    return {
        "identity_hash": canonical_hash([str(path.resolve()), metadata.st_dev, metadata.st_ino]),
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
    }


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repository, check=True, text=True, capture_output=True).stdout.strip()

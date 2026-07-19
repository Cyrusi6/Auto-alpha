from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any, Callable, Mapping

from data_pipeline.ashare.cache import TushareResponseCache
from data_pipeline.ashare.providers.tushare_client import TUSHARE_PROVIDER_API_VERSION
from task_055_f.network import _validate_records
from task_055_f.transport import CANONICAL_ORIGIN
from task_055_g.network_state import read_ledger_read_only

from .authorization import validate_authorization_seal
from .contracts import CANARY_ACCEPTANCE_SCHEMA, RESUME_AUTHORIZATION_SCHEMA
from .io import canonical_hash, publish_generation, read_json, sha256_file, validate_generation


class Task055HNetworkError(RuntimeError):
    pass


def load_file_credential_after_offline_gates(
    *,
    credential_file: str | Path,
    forbidden_root_identities: Mapping[str, Path],
) -> str:
    raise Task055HNetworkError("superseded_by_task055k_transport_broker")
    path = Path(credential_file)
    if not path.is_absolute() or path.is_symlink():
        raise Task055HNetworkError("credential_file_absolute_non_symlink_required")
    resolved = path.resolve(strict=True)
    for root in forbidden_root_identities.values():
        boundary = Path(root).resolve()
        if resolved == boundary or boundary in resolved.parents:
            raise Task055HNetworkError("credential_file_inside_sealed_root")
    metadata = resolved.stat()
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) not in {0o400, 0o600}:
        raise Task055HNetworkError("credential_file_owner_or_permissions_invalid")
    credential_value = resolved.read_text(encoding="utf-8").strip()
    if not credential_value:
        raise Task055HNetworkError("credential_file_empty")
    return credential_value


def ordered_future_canary_gate(
    *,
    authorization_seal: str | Path,
    allow_network: bool,
    sealed_plan_hash: str,
    tls_checker: Callable[[], Mapping[str, Any]],
    credential_loader: Callable[[], str],
) -> dict[str, Any]:
    raise Task055HNetworkError("superseded_by_task055k_transport_broker")
    seal = validate_authorization_seal(authorization_seal, require_ready=True)
    if not allow_network or sealed_plan_hash != seal["canary_execution_plan_hash"]:
        raise Task055HNetworkError("canary_explicit_authorization_invalid")
    if seal.get("resume_authorized") is not False or int(seal["budgets"]["physical_attempts"]) != 0:
        raise Task055HNetworkError("canary_stage_or_budget_invalid")
    tls = dict(tls_checker())
    if tls.get("status") != "passed" or tls.get("origin") != CANONICAL_ORIGIN or tls.get("hostname_verified") is not True or tls.get("certificate_verified") is not True:
        raise Task055HNetworkError("canary_tls_preflight_failed")
    credential_value = credential_loader()
    return {"authorization": seal, "tls": tls, "credential": credential_value}


def verify_and_accept_canary(
    *,
    authorization_seal: str | Path,
    canary_execution_manifest: str | Path,
    output_root: str | Path,
) -> dict[str, Any]:
    seal = validate_authorization_seal(authorization_seal, require_ready=True, verify_current_budget=False)
    task_root = _task_root_from_seal(authorization_seal)
    state_root = task_root / str(seal["canonical_roots"]["state_relative_to_output"])
    cache_data_root = task_root / str(seal["canonical_roots"]["cache_data_relative_to_output"])
    transport_spend_root = task_root / str(seal["canonical_roots"]["transport_spend_relative_to_output"])
    transport_spend_manifest = _current_manifest(transport_spend_root, "network_spend_ledger.json")
    execution = _native_execution(canary_execution_manifest, Path(state_root))
    results = list(execution.get("results") or ())
    if execution.get("status") != "canary_completed" or len(results) != 1 or execution.get("must_stop_after_canary") is not True or execution.get("batch_started") is not False:
        raise Task055HNetworkError("canary_execution_contract_invalid")
    if execution.get("plan_hash") != seal["canary_execution_plan_hash"]:
        raise Task055HNetworkError("canary_execution_plan_hash_invalid")
    request = dict((results[0].get("request") or {}))
    expected_canary = seal["canary"]
    actual_identity = {
        "api_name": request.get("api_name"),
        "ts_code": (request.get("params") or {}).get("ts_code"),
        "trade_date": (request.get("params") or {}).get("trade_date"),
        "fields": request.get("fields"),
        "transport_hash": request.get("transport_hash"),
        "evidence_use_hash": request.get("evidence_use_hash"),
    }
    expected_identity = {
        "api_name": expected_canary["api_name"],
        "ts_code": expected_canary["ts_code"],
        "trade_date": expected_canary["trade_date"],
        "fields": expected_canary["fields"],
        "transport_hash": expected_canary["transport_hash"],
        "evidence_use_hash": expected_canary["evidence_use_hash"],
    }
    if actual_identity != expected_identity:
        raise Task055HNetworkError("canary_transport_not_authorized")
    cache = TushareResponseCache(cache_data_root, enabled=True)
    expected_path = cache.cache_path(request["api_name"], params=request["params"], fields=request["fields"])
    if expected_path.is_symlink() or cache.root_dir.resolve() not in expected_path.resolve().parents:
        raise Task055HNetworkError("canary_cache_containment_invalid")
    result = results[0]
    if result.get("outcome") not in {"positive_response", "negative_vendor_response"}:
        raise Task055HNetworkError("canary_physical_response_outcome_invalid")
    if not expected_path.is_file() or sha256_file(expected_path) != result.get("cache_sha256"):
        raise Task055HNetworkError("canary_cache_sha_mismatch")
    envelope = read_json(expected_path)
    provider = envelope.get("provider") or {}
    if provider.get("endpoint") != CANONICAL_ORIGIN or provider.get("api_version") != TUSHARE_PROVIDER_API_VERSION:
        raise Task055HNetworkError("canary_provider_origin_or_version_invalid")
    schema_proof = envelope.get("endpoint_schema_proof")
    reread = cache.read(request["api_name"], params=request["params"], fields=request["fields"], endpoint_schema_proof=schema_proof, allow_legacy_source_semantics=False)
    if reread is None or not reread.hit:
        raise Task055HNetworkError("canary_cache_authoritative_reread_failed")
    _validate_records(request, reread.records)
    if not reread.records:
        _verify_schema_proof(cache, schema_proof, request)
    network = read_ledger_read_only(state_root)
    spend = _validate_spend(transport_spend_manifest)
    started = [row for row in network["events"] if row.get("event") == "physical_attempt_started" and row.get("transport_hash") == request["transport_hash"]]
    finished = [row for row in network["events"] if row.get("event") == "physical_attempt_finished" and row.get("transport_hash") == request["transport_hash"]]
    terminals = [row for row in network["events"] if row.get("event") == "request_terminal" and row.get("transport_hash") == request["transport_hash"]]
    posts = [row for row in spend.get("events") or () if row.get("event") == "physical_post_started" and row.get("transport_hash") == request["transport_hash"]]
    completed_posts = [row for row in spend.get("events") or () if row.get("event") == "physical_post_completed" and row.get("transport_hash") == request["transport_hash"]]
    if (
        len(started) != 1
        or len(finished) != 1
        or len(terminals) != 1
        or len(posts) != 1
        or len(completed_posts) != 1
        or int(network.get("physical_attempt_count") or 0) != 1
        or int(spend.get("physical_attempt_count") or 0) != 1
    ):
        raise Task055HNetworkError("canary_attempt_ledger_mismatch")
    semantic = {
        "schema_version": CANARY_ACCEPTANCE_SCHEMA,
        "status": "accepted",
        "authorization_seal_content_hash": seal["content_hash"],
        "canary_execution_content_hash": execution["content_hash"],
        "transport_hash": request["transport_hash"],
        "cache_sha256": sha256_file(expected_path),
        "item_count": len(reread.records),
        "network_ledger_root": network["events"][-1]["event_hash"],
        "transport_spend_root": spend["content_hash"],
        "physical_post_count": 1,
        "resume_authorized": False,
        "crash_recovery_cache_verified": True,
    }
    return publish_generation(output_root, prefix="canary_acceptance", manifest_name="canary_acceptance.json", semantic=semantic)


def publish_resume_authorization(
    *,
    canary_acceptance: str | Path,
    reviewed_acceptance_hash: str,
    output_root: str | Path,
) -> dict[str, Any]:
    acceptance = validate_generation(canary_acceptance, schema=CANARY_ACCEPTANCE_SCHEMA, manifest_name="canary_acceptance.json")
    if acceptance["content_hash"] != reviewed_acceptance_hash or acceptance.get("resume_authorized") is not False:
        raise Task055HNetworkError("resume_review_hash_invalid")
    semantic = {
        "schema_version": RESUME_AUTHORIZATION_SCHEMA,
        "status": "authorized",
        "canary_acceptance_content_hash": acceptance["content_hash"],
        "reviewed_acceptance_hash": reviewed_acceptance_hash,
        "resume_authorized": True,
    }
    return publish_generation(output_root, prefix="resume_authorization", manifest_name="resume_authorization.json", semantic=semantic)


def recover_cache_before_retry(
    *,
    request: Mapping[str, Any],
    cache_data_root: str | Path,
) -> dict[str, Any] | None:
    cache = TushareResponseCache(cache_data_root, enabled=True)
    path = cache.cache_path(str(request["api_name"]), params=dict(request["params"]), fields=list(request["fields"]))
    if not path.is_file():
        return None
    envelope = read_json(path)
    proof = envelope.get("endpoint_schema_proof")
    reread = cache.read(str(request["api_name"]), params=dict(request["params"]), fields=list(request["fields"]), endpoint_schema_proof=proof, allow_legacy_source_semantics=False)
    if reread is None or not reread.hit:
        raise Task055HNetworkError("crash_recovery_cache_invalid")
    _validate_records(request, reread.records)
    return {
        "request": dict(request),
        "outcome": "validated_cache_hit",
        "item_count": len(reread.records),
        "cache_relative_path": str(path.relative_to(Path(cache_data_root))),
        "cache_sha256": sha256_file(path),
        "physical_attempt_count": 0,
    }


def _native_execution(path: str | Path, state_root: Path) -> dict[str, Any]:
    manifest = Path(path).resolve()
    root = state_root.resolve()
    allowed = {
        "l1_canary": "l1_canary_manifest.json",
        "l2_canary": "l2_canary_manifest.json",
    }
    matched = [
        stage
        for stage, name in allowed.items()
        if manifest.name == name and (root / "artifacts" / stage / "generations").resolve() in manifest.parents
    ]
    if len(matched) != 1 or not manifest.is_file() or manifest.is_symlink():
        raise Task055HNetworkError("external_execution_manifest_rejected")
    payload = read_json(manifest)
    semantic = {key: value for key, value in payload.items() if key not in {"content_hash", "generation_id"}}
    if canonical_hash(semantic) != payload.get("content_hash"):
        raise Task055HNetworkError("canary_execution_hash_invalid")
    pointer = read_json(root / "artifacts" / matched[0] / "current.json")
    pointed = (root / "artifacts" / matched[0] / str(pointer.get("manifest") or "")).resolve()
    if pointed != manifest or pointer.get("content_hash") != payload.get("content_hash"):
        raise Task055HNetworkError("canary_execution_pointer_mismatch")
    return payload


def _verify_schema_proof(cache: TushareResponseCache, proof: Any, request: Mapping[str, Any]) -> None:
    if not isinstance(proof, dict):
        raise Task055HNetworkError("canary_empty_schema_proof_missing")
    fingerprint = str(proof.get("source_request_fingerprint") or "")
    source = cache.root_dir / f"{fingerprint}.json"
    if not source.is_file() or source.is_symlink() or sha256_file(source) != proof.get("source_cache_sha256"):
        raise Task055HNetworkError("canary_schema_proof_source_invalid")
    envelope = read_json(source)
    source_request = envelope.get("request") or {}
    source_read = cache.read(str(source_request.get("api_name")), params=dict(source_request.get("params") or {}), fields=list(source_request.get("fields") or ()), allow_legacy_source_semantics=False)
    if source_read is None or not source_read.hit or not source_read.records:
        raise Task055HNetworkError("canary_schema_proof_positive_source_invalid")
    if proof.get("api_name") != request["api_name"] or proof.get("requested_fields") != request["fields"]:
        raise Task055HNetworkError("canary_schema_proof_contract_mismatch")


def _validate_spend(path: str | Path) -> dict[str, Any]:
    payload = read_json(path)
    semantic = {key: value for key, value in payload.items() if key not in {"content_hash", "generation_id"}}
    if canonical_hash(semantic) != payload.get("content_hash"):
        raise Task055HNetworkError("transport_spend_hash_invalid")
    return payload


def _task_root_from_seal(path: str | Path) -> Path:
    manifest = Path(path).resolve()
    if manifest.name != "authorization_seal.json" or len(manifest.parents) < 4:
        raise Task055HNetworkError("authorization_seal_path_invalid")
    return manifest.parents[3]


def _current_manifest(root: Path, expected_name: str) -> Path:
    pointer = read_json(root / "current.json")
    relative = Path(str(pointer.get("manifest") or ""))
    if relative.is_absolute() or ".." in relative.parts:
        raise Task055HNetworkError("native_pointer_invalid")
    path = (root / relative).resolve()
    if root.resolve() not in path.parents or path.name != expected_name or not path.is_file():
        raise Task055HNetworkError("native_pointer_target_invalid")
    return path

from __future__ import annotations

import fcntl
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from data_pipeline.ashare.cache import TushareResponseCache
from data_pipeline.ashare.config import AShareDataConfig
from data_pipeline.ashare.providers.tushare_client import (
    TUSHARE_PROVIDER_API_VERSION,
    TushareHttpClient,
    TushareResponseEnvelope,
)
from data_pipeline.ashare.request_normalization import tushare_code_semantic_hash
from data_pipeline.ashare.security import tls_preflight
from task_055_f.network import ENDPOINT_ROW_CAPS, _validate_records
from task_055_f.transport import CANONICAL_ORIGIN, evidence_use_identity, transport_identity
from task_055_h.io import canonical_hash, publish_generation, read_json, sha256_file, validate_generation

from .authority import validate_runtime_authority
from .contracts import (
    CANARY,
    CANARY_ACCEPTANCE_SCHEMA,
    CANARY_EXECUTION_SCHEMA,
    MAX_DATE,
    MAX_PHYSICAL_ATTEMPTS,
    RUNTIME_AUTHORITY_SCHEMA,
)
from .ledger import HashChainLedger, count_events, terminal_for_transport


class Task055IExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class _Secret:
    value: str

    def __repr__(self) -> str:
        return "_Secret([REDACTED])"

    def __str__(self) -> str:
        return "[REDACTED]"


def execute_single_canary(
    *,
    runtime_authority: str | Path,
    reviewed_authority_hash: str,
    credential_file: str | Path,
    allow_network: bool,
) -> dict[str, Any]:
    raise Task055IExecutionError("superseded_by_task055k_transport_broker")


def verify_and_accept_canary(
    *,
    runtime_authority: str | Path,
    reviewed_authority_hash: str,
) -> dict[str, Any]:
    return _verify_and_accept_canary(
        runtime_authority=runtime_authority,
        reviewed_authority_hash=reviewed_authority_hash,
        authority_validator=validate_runtime_authority,
        production=True,
    )


def _verify_and_accept_canary(
    *,
    runtime_authority: str | Path,
    reviewed_authority_hash: str,
    authority_validator: Callable[..., dict[str, Any]],
    production: bool,
) -> dict[str, Any]:
    authority = authority_validator(runtime_authority, require_pristine=False)
    if authority["content_hash"] != reviewed_authority_hash:
        raise Task055IExecutionError("task055i_reviewed_authority_hash_invalid")
    authority_root = Path(authority["authority_root"])
    execution_path = _current_manifest(authority_root / "executions", "canary_execution.json")
    execution = validate_generation(
        execution_path,
        schema=CANARY_EXECUTION_SCHEMA,
        manifest_name="canary_execution.json",
    )
    if execution.get("runtime_authority_content_hash") != authority["content_hash"]:
        raise Task055IExecutionError("task055i_execution_authority_mismatch")
    if execution.get("status") != "completed" or execution.get("canary") != CANARY:
        raise Task055IExecutionError("task055i_canary_execution_invalid")
    if execution.get("resume_authorized") is not False or execution.get("batch_started") is not False:
        raise Task055IExecutionError("task055i_canary_execution_boundary_invalid")
    request = _request_from_authority(authority)
    cache, cache_path, records, envelope = _validated_cache(authority_root, request)
    if sha256_file(cache_path) != execution.get("cache_sha256"):
        raise Task055IExecutionError("task055i_canary_cache_sha_mismatch")
    if not records:
        _verify_empty_schema_proof(cache, envelope.get("endpoint_schema_proof"), request)
    network = HashChainLedger(authority_root / "network_ledger", name="network")
    spend = HashChainLedger(authority_root / "transport_spend", name="transport")
    network_rows = network.rows()
    spend_rows = spend.rows()
    network.assert_ancestor(**_ancestor_args(authority["initial_network_ledger"]))
    spend.assert_ancestor(**_ancestor_args(authority["initial_transport_spend"]))
    transport_hash = request["transport_hash"]
    started = [row for row in network_rows if row.get("event") == "physical_attempt_started" and row.get("transport_hash") == transport_hash]
    finished = [row for row in network_rows if row.get("event") == "physical_attempt_finished" and row.get("transport_hash") == transport_hash]
    terminals = terminal_for_transport(network_rows, transport_hash)
    post_started = [row for row in spend_rows if row.get("event") == "physical_post_started" and row.get("transport_hash") == transport_hash]
    post_finished = [row for row in spend_rows if row.get("event") == "physical_post_completed" and row.get("transport_hash") == transport_hash]
    if not all(len(rows) == 1 for rows in (started, finished, terminals, post_started, post_finished)):
        raise Task055IExecutionError("task055i_canary_attempt_ledger_mismatch")
    if count_events(spend_rows, "physical_post_started") != 1 or count_events(network_rows, "physical_attempt_started") != 1:
        raise Task055IExecutionError("task055i_canary_not_exactly_one_physical_post")
    if terminals[0].get("cache_sha256") != sha256_file(cache_path):
        raise Task055IExecutionError("task055i_canary_terminal_cache_mismatch")
    semantic = {
        "schema_version": CANARY_ACCEPTANCE_SCHEMA,
        "status": "accepted",
        "runtime_authority_content_hash": authority["content_hash"],
        "canary_execution_content_hash": execution["content_hash"],
        "canary": dict(CANARY),
        "cache_relative_path": cache_path.relative_to(authority_root).as_posix(),
        "cache_sha256": sha256_file(cache_path),
        "item_count": len(records),
        "response_outcome": "positive_response" if records else "negative_vendor_response",
        "network_ledger_root": network.root_hash(),
        "transport_spend_root": spend.root_hash(),
        "authorization_initial_network_root": authority["initial_network_ledger"]["root"],
        "authorization_initial_spend_root": authority["initial_transport_spend"]["root"],
        "physical_post_count": 1,
        "resume_authorized": False,
        "batch_authorized": False,
        "native_execution_only": True,
    }
    result = publish_generation(
        authority_root / "acceptance",
        prefix="canary_acceptance",
        manifest_name="canary_acceptance.json",
        semantic=semantic,
    )
    if production:
        validate_canary_acceptance(result["manifest_path"], runtime_authority=runtime_authority)
    else:
        _validate_canary_acceptance_with_authority(result["manifest_path"], authority)
    return result


def validate_canary_acceptance(
    path: str | Path,
    *,
    runtime_authority: str | Path,
) -> dict[str, Any]:
    authority = validate_runtime_authority(runtime_authority, require_pristine=False)
    return _validate_canary_acceptance_with_authority(path, authority)


def load_verified_canary_cache(
    *,
    runtime_authority: str | Path,
    canary_acceptance: str | Path,
) -> dict[str, Any]:
    authority = validate_runtime_authority(runtime_authority, require_pristine=False)
    acceptance = _validate_canary_acceptance_with_authority(canary_acceptance, authority)
    request = _request_from_authority(authority)
    cache, cache_path, records, envelope = _validated_cache(Path(authority["authority_root"]), request)
    if sha256_file(cache_path) != acceptance.get("cache_sha256"):
        raise Task055IExecutionError("task055i_application_cache_acceptance_mismatch")
    if not records:
        _verify_empty_schema_proof(cache, envelope.get("endpoint_schema_proof"), request)
    return {
        "authority": authority,
        "acceptance": acceptance,
        "request": request,
        "cache_path": str(cache_path),
        "cache_sha256": sha256_file(cache_path),
        "records": records,
        "envelope": envelope,
    }


def load_verified_canary_cache_rehearsal(
    *,
    runtime_authority: str | Path,
    canary_acceptance: str | Path,
) -> dict[str, Any]:
    authority = _validate_rehearsal_runtime_authority(runtime_authority, require_pristine=False)
    acceptance = _validate_canary_acceptance_with_authority(canary_acceptance, authority)
    request = _request_from_authority(authority)
    cache, cache_path, records, envelope = _validated_cache(Path(authority["authority_root"]), request)
    if sha256_file(cache_path) != acceptance.get("cache_sha256"):
        raise Task055IExecutionError("task055i_rehearsal_application_cache_mismatch")
    if not records:
        _verify_empty_schema_proof(cache, envelope.get("endpoint_schema_proof"), request)
    return {
        "authority": authority,
        "acceptance": acceptance,
        "request": request,
        "cache_path": str(cache_path),
        "cache_sha256": sha256_file(cache_path),
        "records": records,
        "envelope": envelope,
    }


def _validate_canary_acceptance_with_authority(
    path: str | Path,
    authority: Mapping[str, Any],
) -> dict[str, Any]:
    authority_root = Path(authority["authority_root"])
    manifest = Path(path).resolve()
    if (authority_root / "acceptance" / "generations").resolve() not in manifest.parents:
        raise Task055IExecutionError("task055i_external_canary_acceptance_rejected")
    payload = validate_generation(manifest, schema=CANARY_ACCEPTANCE_SCHEMA, manifest_name="canary_acceptance.json")
    if payload.get("status") != "accepted" or payload.get("runtime_authority_content_hash") != authority["content_hash"]:
        raise Task055IExecutionError("task055i_canary_acceptance_lineage_invalid")
    if payload.get("canary") != CANARY or payload.get("physical_post_count") != 1:
        raise Task055IExecutionError("task055i_canary_acceptance_request_invalid")
    if payload.get("resume_authorized") is not False or payload.get("batch_authorized") is not False:
        raise Task055IExecutionError("task055i_canary_acceptance_resume_boundary_invalid")
    cache_path = (authority_root / str(payload.get("cache_relative_path") or "")).resolve()
    if authority_root not in cache_path.parents or cache_path.is_symlink() or not cache_path.is_file():
        raise Task055IExecutionError("task055i_canary_acceptance_cache_containment_invalid")
    if sha256_file(cache_path) != payload.get("cache_sha256"):
        raise Task055IExecutionError("task055i_canary_acceptance_cache_drift")
    return payload


def _execute_single_canary(
    *,
    runtime_authority: str | Path,
    reviewed_authority_hash: str,
    credential_file: str | Path | None,
    allow_network: bool,
    rehearsal_transport: Callable[[Mapping[str, Any]], TushareResponseEnvelope] | None,
    rehearsal_tls: Mapping[str, Any] | None,
    rehearsal_secret: _Secret | None,
    authority_validator: Callable[..., dict[str, Any]] = validate_runtime_authority,
    rehearsal_crash_after_cache: bool = False,
) -> dict[str, Any]:
    authority = authority_validator(runtime_authority, require_pristine=False)
    if authority["content_hash"] != reviewed_authority_hash:
        raise Task055IExecutionError("task055i_reviewed_authority_hash_invalid")
    if not allow_network:
        raise Task055IExecutionError("task055i_explicit_allow_network_required")
    if rehearsal_transport is None and "TUSHARE_TOKEN" in os.environ:
        raise Task055IExecutionError("task055i_inline_tushare_token_forbidden")
    authority_root = Path(authority["authority_root"])
    lock_path = authority_root / authority["canonical_subroots"]["single_flight_lock"]
    with lock_path.open("r+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Task055IExecutionError("task055i_canary_single_flight_busy") from exc
        try:
            authority = authority_validator(runtime_authority, require_pristine=False)
            request = _request_from_authority(authority)
            recovered = _recover_after_cache(authority, request)
            if recovered is not None:
                return recovered
            _assert_canary_budget_pristine(authority)
            tls = dict(rehearsal_tls or tls_preflight())
            if (
                tls.get("status") != "passed"
                or tls.get("origin") != CANONICAL_ORIGIN
                or tls.get("hostname_verified") is not True
                or tls.get("certificate_verified") is not True
            ):
                raise Task055IExecutionError("task055i_tls_preflight_failed")
            if rehearsal_transport is None:
                if credential_file is None:
                    raise Task055IExecutionError("task055i_credential_file_required")
                secret = _load_credential_file(Path(credential_file), authority)
                credential_reads = 1
            else:
                secret = rehearsal_secret or _Secret("synthetic-rehearsal-secret")
                credential_reads = 0
            network = HashChainLedger(authority_root / "network_ledger", name="network")
            spend = HashChainLedger(authority_root / "transport_spend", name="transport")
            attempt_id = canonical_hash([authority["content_hash"], request["transport_hash"], "attempt", 1])
            network.append({
                "event_id": f"network-start:{attempt_id}",
                "event": "physical_attempt_started",
                "attempt_id": attempt_id,
                "transport_hash": request["transport_hash"],
                "evidence_use_hash": request["evidence_use_hash"],
                "trade_date": request["trade_date"],
            })
            spend.append({
                "event_id": f"spend-start:{attempt_id}",
                "event": "physical_post_started",
                "attempt_id": attempt_id,
                "transport_hash": request["transport_hash"],
                "evidence_use_hash": request["evidence_use_hash"],
                "trade_date": request["trade_date"],
            })
            try:
                envelope = (
                    rehearsal_transport(request)
                    if rehearsal_transport is not None
                    else _post_once(request, secret, authority_root / "cache_data")
                )
                cache_path, records = _publish_response_cache(authority_root, request, envelope)
                if rehearsal_crash_after_cache:
                    raise _RehearsalCrashAfterCache("synthetic_crash_after_atomic_cache")
            except Exception as exc:
                if isinstance(exc, _RehearsalCrashAfterCache):
                    raise
                spend.append({
                    "event_id": f"spend-failed:{attempt_id}",
                    "event": "physical_post_failed",
                    "attempt_id": attempt_id,
                    "transport_hash": request["transport_hash"],
                    "error_class": type(exc).__name__,
                })
                network.append({
                    "event_id": f"network-failed:{attempt_id}",
                    "event": "physical_attempt_failed",
                    "attempt_id": attempt_id,
                    "transport_hash": request["transport_hash"],
                    "error_class": type(exc).__name__,
                })
                raise Task055IExecutionError(f"task055i_canary_post_failed:{type(exc).__name__}") from exc
            cache_sha = sha256_file(cache_path)
            spend.append({
                "event_id": f"spend-completed:{attempt_id}",
                "event": "physical_post_completed",
                "attempt_id": attempt_id,
                "transport_hash": request["transport_hash"],
                "cache_sha256": cache_sha,
                "item_count": len(records),
            })
            network.append({
                "event_id": f"network-finished:{attempt_id}",
                "event": "physical_attempt_finished",
                "attempt_id": attempt_id,
                "transport_hash": request["transport_hash"],
                "cache_sha256": cache_sha,
                "item_count": len(records),
            })
            network.append({
                "event_id": f"network-terminal:{attempt_id}",
                "event": "request_terminal",
                "attempt_id": attempt_id,
                "transport_hash": request["transport_hash"],
                "terminal_status": "succeeded",
                "cache_sha256": cache_sha,
                "item_count": len(records),
            })
            return _publish_execution(
                authority,
                request,
                cache_path,
                records,
                tls=tls,
                credential_read_count=credential_reads,
                recovered=False,
                attempt_id=attempt_id,
            )
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _recover_after_cache(authority: Mapping[str, Any], request: Mapping[str, Any]) -> dict[str, Any] | None:
    authority_root = Path(authority["authority_root"])
    cache = TushareResponseCache(authority_root / "cache_data", enabled=True)
    path = cache.cache_path(request["api_name"], params=request["params"], fields=request["fields"])
    network = HashChainLedger(authority_root / "network_ledger", name="network")
    spend = HashChainLedger(authority_root / "transport_spend", name="transport")
    network_rows = network.rows()
    spend_rows = spend.rows()
    terminals = terminal_for_transport(network_rows, request["transport_hash"])
    if terminals:
        if len(terminals) != 1 or not path.is_file() or terminals[0].get("cache_sha256") != sha256_file(path):
            raise Task055IExecutionError("task055i_existing_terminal_or_cache_conflict")
        current = _current_manifest(authority_root / "executions", "canary_execution.json")
        return validate_generation(current, schema=CANARY_EXECUTION_SCHEMA, manifest_name="canary_execution.json")
    if not path.exists():
        if any(row.get("transport_hash") == request["transport_hash"] and row.get("event") in {"physical_attempt_started", "physical_post_started"} for row in [*network_rows, *spend_rows]):
            raise Task055IExecutionError("task055i_incomplete_attempt_without_cache_blocked")
        return None
    _, cache_path, records, _ = _validated_cache(authority_root, request)
    network_started = [row for row in network_rows if row.get("event") == "physical_attempt_started" and row.get("transport_hash") == request["transport_hash"]]
    post_started = [row for row in spend_rows if row.get("event") == "physical_post_started" and row.get("transport_hash") == request["transport_hash"]]
    if len(network_started) != 1 or len(post_started) != 1:
        raise Task055IExecutionError("task055i_unattributed_cache_recovery_rejected")
    attempt_id = str(network_started[0]["attempt_id"])
    post_completed = [row for row in spend_rows if row.get("event") == "physical_post_completed" and row.get("attempt_id") == attempt_id]
    network_finished = [row for row in network_rows if row.get("event") == "physical_attempt_finished" and row.get("attempt_id") == attempt_id]
    cache_sha = sha256_file(cache_path)
    if not post_completed:
        spend.append({
            "event_id": f"spend-completed:{attempt_id}",
            "event": "physical_post_completed",
            "attempt_id": attempt_id,
            "transport_hash": request["transport_hash"],
            "cache_sha256": cache_sha,
            "item_count": len(records),
            "recovered_after_cache": True,
        })
    elif len(post_completed) != 1 or post_completed[0].get("cache_sha256") != cache_sha:
        raise Task055IExecutionError("task055i_recovery_spend_conflict")
    if not network_finished:
        network.append({
            "event_id": f"network-finished:{attempt_id}",
            "event": "physical_attempt_finished",
            "attempt_id": attempt_id,
            "transport_hash": request["transport_hash"],
            "cache_sha256": cache_sha,
            "item_count": len(records),
            "recovered_after_cache": True,
        })
    elif len(network_finished) != 1 or network_finished[0].get("cache_sha256") != cache_sha:
        raise Task055IExecutionError("task055i_recovery_network_conflict")
    network.append({
        "event_id": f"network-terminal:{attempt_id}",
        "event": "request_terminal",
        "attempt_id": attempt_id,
        "transport_hash": request["transport_hash"],
        "terminal_status": "succeeded",
        "cache_sha256": cache_sha,
        "item_count": len(records),
        "recovered_after_cache": True,
    })
    return _publish_execution(
        authority,
        request,
        cache_path,
        records,
        tls={"status": "not_repeated_crash_recovery"},
        credential_read_count=0,
        recovered=True,
        attempt_id=attempt_id,
    )


def _publish_execution(
    authority: Mapping[str, Any],
    request: Mapping[str, Any],
    cache_path: Path,
    records: list[dict[str, Any]],
    *,
    tls: Mapping[str, Any],
    credential_read_count: int,
    recovered: bool,
    attempt_id: str,
) -> dict[str, Any]:
    authority_root = Path(authority["authority_root"])
    network = HashChainLedger(authority_root / "network_ledger", name="network")
    spend = HashChainLedger(authority_root / "transport_spend", name="transport")
    semantic = {
        "schema_version": CANARY_EXECUTION_SCHEMA,
        "status": "completed",
        "runtime_authority_content_hash": authority["content_hash"],
        "single_request_plan_hash": authority["single_request_plan_hash"],
        "canary": dict(CANARY),
        "attempt_id": attempt_id,
        "outcome": "positive_response" if records else "negative_vendor_response",
        "item_count": len(records),
        "cache_relative_path": cache_path.relative_to(authority_root).as_posix(),
        "cache_sha256": sha256_file(cache_path),
        "network_ledger_root": network.root_hash(),
        "transport_spend_root": spend.root_hash(),
        "physical_post_count": count_events(spend.rows(), "physical_post_started"),
        "credential_read_count": credential_read_count,
        "tls_preflight": dict(tls),
        "crash_recovered": recovered,
        "must_stop_after_canary": True,
        "batch_started": False,
        "resume_authorized": False,
    }
    return publish_generation(
        authority_root / "executions",
        prefix="canary_execution",
        manifest_name="canary_execution.json",
        semantic=semantic,
    )


def _publish_response_cache(
    authority_root: Path,
    request: Mapping[str, Any],
    envelope: TushareResponseEnvelope,
) -> tuple[Path, list[dict[str, Any]]]:
    if envelope.endpoint != CANONICAL_ORIGIN or envelope.provider_api_version != TUSHARE_PROVIDER_API_VERSION:
        raise Task055IExecutionError("task055i_response_provider_invalid")
    if envelope.response_code != 0 or envelope.item_count != len(envelope.records):
        raise Task055IExecutionError("task055i_response_code_or_count_invalid")
    if envelope.item_count >= ENDPOINT_ROW_CAPS[request["api_name"]]:
        raise Task055IExecutionError("task055i_response_row_cap_reached")
    records = [dict(row) for row in envelope.records]
    _validate_records(request, records)
    requested_fields = set(request["fields"])
    if records and not requested_fields.issubset(set(envelope.response_fields)):
        raise Task055IExecutionError("task055i_response_fields_missing")
    cache = TushareResponseCache(authority_root / "cache_data", enabled=True)
    proof = None
    if not records and not envelope.response_fields:
        proof = cache.build_endpoint_schema_proof(
            request["api_name"],
            request["fields"],
            code_semantic_hash=tushare_code_semantic_hash(),
        )
        if proof is None:
            raise Task055IExecutionError("task055i_empty_response_schema_proof_missing")
    path = cache.write(
        request["api_name"],
        params=request["params"],
        fields=request["fields"],
        records=records,
        response_code=envelope.response_code,
        response_message="",
        response_fields=envelope.response_fields,
        item_count=envelope.item_count,
        response_fields_observed=bool(envelope.response_fields),
        endpoint_schema_proof=proof,
        endpoint=envelope.endpoint,
        provider_api_version=envelope.provider_api_version,
    )
    reread = cache.read(
        request["api_name"],
        params=request["params"],
        fields=request["fields"],
        endpoint_schema_proof=proof,
        allow_legacy_source_semantics=False,
    )
    if reread is None or not reread.hit:
        raise Task055IExecutionError("task055i_atomic_cache_reread_failed")
    _validate_records(request, reread.records)
    return path, list(reread.records)


def _validated_cache(
    authority_root: Path,
    request: Mapping[str, Any],
) -> tuple[TushareResponseCache, Path, list[dict[str, Any]], dict[str, Any]]:
    cache = TushareResponseCache(authority_root / "cache_data", enabled=True)
    path = cache.cache_path(request["api_name"], params=request["params"], fields=request["fields"])
    if not path.is_file() or path.is_symlink() or cache.root_dir.resolve() not in path.resolve().parents:
        raise Task055IExecutionError("task055i_canary_cache_missing_or_escape")
    envelope = read_json(path)
    provider = envelope.get("provider") or {}
    if provider.get("endpoint") != CANONICAL_ORIGIN or provider.get("api_version") != TUSHARE_PROVIDER_API_VERSION:
        raise Task055IExecutionError("task055i_canary_cache_provider_invalid")
    reread = cache.read(
        request["api_name"],
        params=request["params"],
        fields=request["fields"],
        endpoint_schema_proof=envelope.get("endpoint_schema_proof"),
        allow_legacy_source_semantics=False,
    )
    if reread is None or not reread.hit:
        raise Task055IExecutionError("task055i_canary_cache_reread_failed")
    _validate_records(request, reread.records)
    return cache, path, list(reread.records), envelope


def _verify_empty_schema_proof(
    cache: TushareResponseCache,
    proof: Any,
    request: Mapping[str, Any],
) -> None:
    if proof is None:
        envelope = read_json(cache.cache_path(request["api_name"], params=request["params"], fields=request["fields"]))
        if (envelope.get("response") or {}).get("fields_observed") is True:
            return
        raise Task055IExecutionError("task055i_empty_schema_proof_missing")
    if not isinstance(proof, dict):
        raise Task055IExecutionError("task055i_empty_schema_proof_invalid")
    fingerprint = str(proof.get("source_request_fingerprint") or "")
    source = cache.root_dir / f"{fingerprint}.json"
    if not source.is_file() or source.is_symlink() or sha256_file(source) != proof.get("source_cache_sha256"):
        raise Task055IExecutionError("task055i_empty_schema_proof_source_invalid")
    source_envelope = read_json(source)
    source_request = source_envelope.get("request") or {}
    source_read = cache.read(
        str(source_request.get("api_name")),
        params=dict(source_request.get("params") or {}),
        fields=list(source_request.get("fields") or ()),
        allow_legacy_source_semantics=False,
    )
    if source_read is None or not source_read.hit or not source_read.records:
        raise Task055IExecutionError("task055i_empty_schema_proof_positive_source_invalid")


def _post_once(
    request: Mapping[str, Any],
    secret: _Secret,
    cache_root: Path,
) -> TushareResponseEnvelope:
    config = AShareDataConfig(
        tushare_token=secret.value,
        tushare_api_url=CANONICAL_ORIGIN,
        tushare_retry_count=1,
        data_dir=cache_root,
    )
    client = TushareHttpClient(config)
    if client.retry_count != 1:
        raise Task055IExecutionError("task055i_unobservable_retry_forbidden")
    try:
        return client.post_with_metadata(
            request["api_name"],
            params=dict(request["params"]),
            fields=list(request["fields"]),
        )
    except Exception as exc:
        raise Task055IExecutionError(f"task055i_provider_request_failed:{type(exc).__name__}") from exc


def _load_credential_file(path: Path, authority: Mapping[str, Any]) -> _Secret:
    if not path.is_absolute() or path.is_symlink():
        raise Task055IExecutionError("task055i_credential_file_absolute_non_symlink_required")
    resolved = path.resolve(strict=True)
    forbidden = [
        Path(authority["repository_root"]).resolve(),
        Path(authority["governed_root"]).resolve(),
        Path(authority["authority_root"]).resolve(),
    ]
    if any(resolved == root or root in resolved.parents for root in forbidden):
        raise Task055IExecutionError("task055i_credential_inside_sealed_root")
    metadata = resolved.stat()
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) not in {0o400, 0o600}:
        raise Task055IExecutionError("task055i_credential_owner_or_permissions_invalid")
    value = resolved.read_text(encoding="utf-8").strip()
    if not value:
        raise Task055IExecutionError("task055i_credential_file_empty")
    return _Secret(value)


def _assert_canary_budget_pristine(authority: Mapping[str, Any]) -> None:
    if int(authority.get("current_physical_attempt_count") or 0) != 0:
        raise Task055IExecutionError("task055i_canary_physical_budget_not_pristine")
    if int((authority.get("budgets") or {}).get("physical_attempts") or 0) != 0:
        raise Task055IExecutionError("task055i_authority_physical_budget_seed_invalid")
    if MAX_PHYSICAL_ATTEMPTS < 1:
        raise Task055IExecutionError("task055i_physical_budget_invalid")


def _request_from_authority(authority: Mapping[str, Any]) -> dict[str, Any]:
    plan = authority.get("single_request_plan") or {}
    requests = list(plan.get("requests") or ())
    if len(requests) != 1 or plan.get("plan_hash") != authority.get("single_request_plan_hash"):
        raise Task055IExecutionError("task055i_single_request_plan_invalid")
    request = dict(requests[0])
    expected_transport = transport_identity("daily", request["params"], request["fields"])
    original_lineage = dict(plan.get("lineage") or {})
    original_lineage.pop("parent_task055g_plan_hash", None)
    expected_use = evidence_use_identity(
        stage="task055g_l1_exact",
        parent_plan_hash=canonical_hash(original_lineage),
        frontier_root=str(plan.get("frontier_root") or ""),
        transport_hash=expected_transport,
    )
    actual = {
        "api_name": request.get("api_name"),
        "ts_code": request.get("ts_code"),
        "trade_date": request.get("trade_date"),
        "fields": request.get("fields"),
        "transport_hash": request.get("transport_hash"),
        "evidence_use_hash": request.get("evidence_use_hash"),
    }
    if actual != CANARY or expected_transport != CANARY["transport_hash"] or expected_use != CANARY["evidence_use_hash"]:
        raise Task055IExecutionError("task055i_recomputed_canary_identity_mismatch")
    if request["trade_date"] > MAX_DATE:
        raise Task055IExecutionError("task055i_canary_date_exceeds_boundary")
    return request


def _current_manifest(root: Path, name: str) -> Path:
    pointer = read_json(root / "current.json")
    relative = Path(str(pointer.get("manifest") or ""))
    if relative.is_absolute() or ".." in relative.parts:
        raise Task055IExecutionError("task055i_native_pointer_invalid")
    path = (root / relative).resolve()
    if root.resolve() not in path.parents or path.name != name or not path.is_file() or path.is_symlink():
        raise Task055IExecutionError("task055i_native_pointer_target_invalid")
    payload = read_json(path)
    if pointer.get("content_hash") != payload.get("content_hash"):
        raise Task055IExecutionError("task055i_native_pointer_hash_mismatch")
    return path


def _ancestor_args(value: Mapping[str, Any]) -> dict[str, Any]:
    return {"sequence": int(value.get("sequence") or 0), "event_hash": str(value.get("root") or "")}


def execute_canary_rehearsal(
    *,
    runtime_authority: str | Path,
    reviewed_authority_hash: str,
    transport: Callable[[Mapping[str, Any]], TushareResponseEnvelope],
    crash_after_cache: bool = False,
) -> dict[str, Any]:
    return _execute_single_canary(
        runtime_authority=runtime_authority,
        reviewed_authority_hash=reviewed_authority_hash,
        credential_file=None,
        allow_network=True,
        rehearsal_transport=transport,
        rehearsal_tls={
            "status": "passed",
            "origin": CANONICAL_ORIGIN,
            "hostname_verified": True,
            "certificate_verified": True,
            "evidence_scope": "synthetic_rehearsal_only",
        },
        rehearsal_secret=_Secret("synthetic-rehearsal-secret"),
        authority_validator=_validate_rehearsal_runtime_authority,
        rehearsal_crash_after_cache=crash_after_cache,
    )


def verify_and_accept_canary_rehearsal(
    *,
    runtime_authority: str | Path,
    reviewed_authority_hash: str,
) -> dict[str, Any]:
    return _verify_and_accept_canary(
        runtime_authority=runtime_authority,
        reviewed_authority_hash=reviewed_authority_hash,
        authority_validator=_validate_rehearsal_runtime_authority,
        production=False,
    )


class _RehearsalCrashAfterCache(RuntimeError):
    pass


def _validate_rehearsal_runtime_authority(
    path: str | Path,
    *,
    require_pristine: bool,
) -> dict[str, Any]:
    payload = validate_generation(
        path,
        schema=RUNTIME_AUTHORITY_SCHEMA,
        manifest_name="runtime_authority.json",
    )
    if payload.get("evidence_scope") != "synthetic_rehearsal_only":
        raise Task055IExecutionError("task055i_rehearsal_authority_scope_invalid")
    manifest_path = Path(payload["manifest_path"]).resolve()
    authority_root = manifest_path.parents[3]
    if payload.get("canary") != CANARY or payload.get("single_request_plan_hash") != payload.get("reviewed_plan_hash"):
        raise Task055IExecutionError("task055i_rehearsal_canary_identity_invalid")
    if Path(str(payload.get("authority_root") or "")).resolve() != authority_root:
        raise Task055IExecutionError("task055i_rehearsal_authority_root_invalid")
    fixture_binding = authority_root / "fixture_binding.json"
    if (
        not fixture_binding.is_file()
        or fixture_binding.is_symlink()
        or sha256_file(fixture_binding) != payload.get("fixture_binding_sha256")
        or read_json(fixture_binding).get("application_fixture_root") != payload.get("application_fixture_root")
    ):
        raise Task055IExecutionError("task055i_rehearsal_source_binding_invalid")
    identities = payload.get("root_identities") or {}
    for role, relative in {
        "authority": ".",
        "state": "network_ledger",
        "cache": "cache_data",
        "spend": "transport_spend",
    }.items():
        candidate = (authority_root / relative).resolve()
        metadata = candidate.stat()
        expected = canonical_hash([str(candidate), metadata.st_dev, metadata.st_ino])
        if identities.get(role) != expected or candidate.is_symlink():
            raise Task055IExecutionError(f"task055i_rehearsal_root_identity_invalid:{role}")
    network = HashChainLedger(authority_root / "network_ledger", name="network")
    spend = HashChainLedger(authority_root / "transport_spend", name="transport")
    network.assert_ancestor(**_ancestor_args(payload["initial_network_ledger"]))
    spend.assert_ancestor(**_ancestor_args(payload["initial_transport_spend"]))
    physical = count_events(spend.rows(), "physical_post_started")
    if physical != count_events(network.rows(), "physical_attempt_started"):
        raise Task055IExecutionError("task055i_rehearsal_ledger_attempt_mismatch")
    if require_pristine and physical:
        raise Task055IExecutionError("task055i_rehearsal_authority_not_pristine")
    return payload | {
        "authority_root": str(authority_root),
        "repository_root": str(Path(__file__).resolve().parents[1]),
        "governed_root": str(authority_root.parent),
        "current_network_ledger_root": network.root_hash(),
        "current_transport_spend_root": spend.root_hash(),
        "current_physical_attempt_count": physical,
    }

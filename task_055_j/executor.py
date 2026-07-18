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
from data_pipeline.ashare.network_capability import _issue_task055j_execution_capability
from data_pipeline.ashare.providers.tushare_client import (
    TUSHARE_PROVIDER_API_VERSION,
    TushareHttpClient,
    TushareResponseEnvelope,
)
from data_pipeline.ashare.request_normalization import (
    stable_json_hash,
    tushare_code_semantic_hash,
    tushare_request_fingerprint,
)
from data_pipeline.ashare.security import tls_preflight
from task_055_f.network import ENDPOINT_ROW_CAPS, _validate_records
from task_055_f.transport import CANONICAL_ORIGIN, evidence_use_identity, transport_identity
from task_055_h.io import atomic_json, canonical_hash, publish_generation, read_json, sha256_file, validate_generation

from .authority import validate_final_execution_seal
from .contracts import (
    ACCEPTANCE_SCHEMA,
    CANARY,
    EXECUTION_SCHEMA,
    MAX_DATE,
    MAX_PHYSICAL_ATTEMPTS,
    TRANSPORT_RECEIPT_SCHEMA,
)
from .ledger import DurableHashJournal, event_rows


class Task055JExecutionError(RuntimeError):
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
    final_execution_seal: str | Path,
    reviewed_final_execution_seal_hash: str,
    credential_file: str | Path,
    allow_network: bool,
) -> dict[str, Any]:
    return _execute_single_canary(
        final_execution_seal=final_execution_seal,
        reviewed_final_execution_seal_hash=reviewed_final_execution_seal_hash,
        credential_file=credential_file,
        allow_network=allow_network,
        synthetic_transport=None,
        synthetic_tls=None,
        crash_point=None,
    )


def verify_and_accept_canary(
    *, final_execution_seal: str | Path, reviewed_final_execution_seal_hash: str
) -> dict[str, Any]:
    return _verify_and_accept(
        final_execution_seal=final_execution_seal,
        reviewed_hash=reviewed_final_execution_seal_hash,
        seal_validator=None,
    )


def _verify_and_accept(
    *,
    final_execution_seal: str | Path,
    reviewed_hash: str,
    seal_validator: Callable[[str | Path, str], dict[str, Any]] | None,
) -> dict[str, Any]:
    seal = _validate_seal(final_execution_seal, reviewed_hash, seal_validator, require_pristine=False)
    authority_root = Path(seal["authority_root"])
    execution = _load_execution(authority_root, seal)
    receipt = _load_receipt(authority_root, execution["transport_receipt_content_hash"])
    request = _request_from_seal(seal)
    cache, cache_path, records, envelope = _validated_cache(authority_root, request)
    _verify_receipt_cache(receipt, cache_path, envelope, records, request)
    _verify_execution_events(authority_root, seal, execution, receipt, cache_path, records)
    semantic = {
        "schema_version": ACCEPTANCE_SCHEMA,
        "status": "accepted",
        "final_execution_seal_content_hash": seal["content_hash"],
        "runtime_authority_content_hash": seal["runtime_authority_content_hash"],
        "execution_content_hash": execution["content_hash"],
        "attempt_id": execution["attempt_id"],
        "canary": dict(CANARY),
        "transport_receipt_content_hash": receipt["content_hash"],
        "transport_receipt_sha256": sha256_file(receipt["manifest_path"]),
        "cache_relative_path": cache_path.relative_to(authority_root).as_posix(),
        "cache_sha256": sha256_file(cache_path),
        "item_count": len(records),
        "response_fields": list((envelope.get("response") or {}).get("fields") or ()),
        "physical_post_count": 1,
        "network_journal_root": execution["network_journal_root"],
        "transport_spend_root": execution["transport_spend_root"],
        "resume_authorized": False,
        "batch_authorized": False,
    }
    result = publish_generation(
        authority_root / "acceptance",
        prefix="task055j_canary_acceptance",
        manifest_name="canary_acceptance.json",
        semantic=semantic,
    )
    if seal_validator is not None:
        accepted = _load_synthetic_accepted_cache(
            acceptance=result["manifest_path"],
            final_execution_seal=final_execution_seal,
            reviewed_hash=reviewed_hash,
            seal_validator=seal_validator,
        )
        return accepted["acceptance"] | {"manifest_path": result["manifest_path"]}
    return validate_canary_acceptance(
        result["manifest_path"],
        final_execution_seal=final_execution_seal,
        reviewed_final_execution_seal_hash=reviewed_hash,
    )


def validate_canary_acceptance(
    path: str | Path,
    *,
    final_execution_seal: str | Path,
    reviewed_final_execution_seal_hash: str,
) -> dict[str, Any]:
    seal = validate_final_execution_seal(
        final_execution_seal,
        reviewed_hash=reviewed_final_execution_seal_hash,
        repository_root=Path(__file__).resolve().parents[1],
        require_ready=True,
        require_pristine=False,
    )
    payload = validate_generation(path, schema=ACCEPTANCE_SCHEMA, manifest_name="canary_acceptance.json")
    authority_root = Path(seal["authority_root"])
    manifest = Path(payload["manifest_path"]).resolve()
    if (authority_root / "acceptance" / "generations").resolve() not in manifest.parents:
        raise Task055JExecutionError("task055j_external_acceptance_rejected")
    if payload.get("status") != "accepted" or payload.get("final_execution_seal_content_hash") != seal["content_hash"]:
        raise Task055JExecutionError("task055j_acceptance_lineage_invalid")
    if payload.get("canary") != CANARY or payload.get("physical_post_count") != 1:
        raise Task055JExecutionError("task055j_acceptance_canary_invalid")
    if payload.get("resume_authorized") is not False or payload.get("batch_authorized") is not False:
        raise Task055JExecutionError("task055j_acceptance_resume_boundary_invalid")
    execution = _load_execution(authority_root, seal)
    receipt = _load_receipt(authority_root, payload["transport_receipt_content_hash"])
    request = _request_from_seal(seal)
    _, cache_path, records, envelope = _validated_cache(authority_root, request)
    if payload.get("execution_content_hash") != execution["content_hash"]:
        raise Task055JExecutionError("task055j_acceptance_execution_hash_invalid")
    if payload.get("cache_sha256") != sha256_file(cache_path) or payload.get("item_count") != len(records):
        raise Task055JExecutionError("task055j_acceptance_cache_invalid")
    _verify_receipt_cache(receipt, cache_path, envelope, records, request)
    _verify_execution_events(authority_root, seal, execution, receipt, cache_path, records)
    return payload | {
        "authority_root": str(authority_root),
        "final_execution_seal": seal,
        "execution": execution,
        "transport_receipt": receipt,
        "cache_path": str(cache_path),
        "records": records,
        "cache_envelope": envelope,
        "request": request,
    }


def load_accepted_canary_cache(
    *,
    final_execution_seal: str | Path,
    reviewed_final_execution_seal_hash: str,
    canary_acceptance: str | Path,
) -> dict[str, Any]:
    return validate_canary_acceptance(
        canary_acceptance,
        final_execution_seal=final_execution_seal,
        reviewed_final_execution_seal_hash=reviewed_final_execution_seal_hash,
    )


def _execute_single_canary(
    *,
    final_execution_seal: str | Path,
    reviewed_final_execution_seal_hash: str,
    credential_file: str | Path | None,
    allow_network: bool,
    synthetic_transport: Callable[[Mapping[str, Any]], TushareResponseEnvelope] | None,
    synthetic_tls: Mapping[str, Any] | None,
    crash_point: str | None,
    seal_validator: Callable[[str | Path, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    seal = _validate_seal(
        final_execution_seal,
        reviewed_final_execution_seal_hash,
        seal_validator,
        require_pristine=False,
    )
    if not allow_network:
        raise Task055JExecutionError("task055j_explicit_allow_network_required")
    if synthetic_transport is None and "TUSHARE_TOKEN" in os.environ:
        raise Task055JExecutionError("task055j_inline_tushare_token_forbidden")
    authority_root = Path(seal["authority_root"])
    lock_path = authority_root / "single_canary.lock"
    _verify_lock_identity(lock_path, seal)
    with lock_path.open("r+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Task055JExecutionError("task055j_single_canary_lock_busy") from exc
        try:
            _verify_lock_identity(lock_path, seal)
            seal = _validate_seal(
                final_execution_seal,
                reviewed_final_execution_seal_hash,
                seal_validator,
                require_pristine=False,
            )
            request = _request_from_seal(seal)
            recovered = _recover(authority_root, seal, request)
            if recovered is not None:
                return recovered
            _assert_budget(authority_root, seal)
            tls = dict(synthetic_tls or tls_preflight())
            if (
                tls.get("status") != "passed"
                or tls.get("origin") != CANONICAL_ORIGIN
                or tls.get("hostname_verified") is not True
                or tls.get("certificate_verified") is not True
            ):
                raise Task055JExecutionError("task055j_tls_preflight_failed")
            if synthetic_transport is None:
                if credential_file is None:
                    raise Task055JExecutionError("task055j_credential_file_required")
                secret = _load_credential_file(Path(credential_file), seal)
                credential_read_count = 1
            else:
                secret = _Secret("synthetic-rehearsal-only")
                credential_read_count = 0
            attempt_id = _attempt_id(seal, request)
            network = DurableHashJournal(authority_root / "network_journal", name="network")
            spend = DurableHashJournal(authority_root / "transport_spend_journal", name="transport_spend")
            network.append(
                {
                    "event_id": f"attempt-intent:{attempt_id}",
                    "event": "attempt_intent",
                    "attempt_id": attempt_id,
                    "transport_hash": request["transport_hash"],
                    "evidence_use_hash": request["evidence_use_hash"],
                    "trade_date": request["trade_date"],
                    "final_execution_seal_hash": seal["content_hash"],
                }
            )
            _crash(crash_point, "after_network_intent")
            spend.append(
                {
                    "event_id": f"post-intent:{attempt_id}",
                    "event": "physical_post_intent",
                    "attempt_id": attempt_id,
                    "transport_hash": request["transport_hash"],
                    "evidence_use_hash": request["evidence_use_hash"],
                    "trade_date": request["trade_date"],
                    "final_execution_seal_hash": seal["content_hash"],
                }
            )
            _crash(crash_point, "after_spend_intent_before_post")
            try:
                envelope = (
                    synthetic_transport(request)
                    if synthetic_transport is not None
                    else _post_once(request, secret, seal, attempt_id)
                )
            except Exception as exc:
                network.append(
                    {
                        "event_id": f"attempt-failed:{attempt_id}",
                        "event": "attempt_failed",
                        "attempt_id": attempt_id,
                        "transport_hash": request["transport_hash"],
                        "error_class": type(exc).__name__,
                        "automatic_retry_forbidden": True,
                    }
                )
                raise Task055JExecutionError(f"task055j_canary_post_failed:{type(exc).__name__}") from exc
            _crash(crash_point, "after_post_before_receipt")
            receipt = _publish_transport_receipt(authority_root, seal, request, attempt_id, tls, envelope)
            _crash(crash_point, "after_receipt_before_cache")
            cache_path, records = _publish_validated_cache(authority_root, request, receipt)
            _crash(crash_point, "after_cache_before_completion")
            _complete_journals(authority_root, seal, request, attempt_id, receipt, cache_path, records)
            _crash(crash_point, "after_terminal_before_execution")
            execution = _publish_execution(
                authority_root,
                seal,
                request,
                attempt_id,
                receipt,
                cache_path,
                records,
                credential_read_count=credential_read_count,
                recovered=False,
                publish_pointer=crash_point != "after_execution_before_pointer",
            )
            if crash_point == "after_execution_before_pointer":
                raise _SyntheticCrash("after_execution_before_pointer")
            return execution
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _recover(authority_root: Path, seal: Mapping[str, Any], request: Mapping[str, Any]) -> dict[str, Any] | None:
    attempt_id = _attempt_id(seal, request)
    network = DurableHashJournal(authority_root / "network_journal", name="network")
    spend = DurableHashJournal(authority_root / "transport_spend_journal", name="transport_spend")
    network.assert_ancestor(seal["initial_network_journal"])
    spend.assert_ancestor(seal["initial_transport_spend"])
    network_rows = network.rows()
    spend_rows = spend.rows()
    intent = event_rows(network_rows, event="attempt_intent", attempt_id=attempt_id)
    post_intent = event_rows(spend_rows, event="physical_post_intent", attempt_id=attempt_id)
    receipt = _optional_receipt(authority_root, attempt_id)
    cache_path = _cache_path(authority_root, request)
    terminal = event_rows(network_rows, event="request_terminal", attempt_id=attempt_id)
    execution = _optional_execution(authority_root, seal)
    if execution is not None:
        if receipt is None or not cache_path.is_file() or len(terminal) != 1:
            raise Task055JExecutionError("task055j_existing_execution_lineage_incomplete")
        _, _, records, envelope = _validated_cache(authority_root, request)
        _verify_receipt_cache(receipt, cache_path, envelope, records, request)
        _verify_execution_events(authority_root, seal, execution, receipt, cache_path, records)
        _repair_execution_pointer(authority_root, execution)
        return execution | {"crash_recovered": True}
    if not intent and not post_intent and receipt is None and not cache_path.exists() and not terminal:
        return None
    if len(intent) > 1 or len(post_intent) > 1 or len(terminal) > 1:
        raise Task055JExecutionError("task055j_recovery_event_cardinality_invalid")
    if post_intent and receipt is None:
        raise Task055JExecutionError("task055j_ambiguous_post_without_transport_receipt_permanently_blocked")
    if cache_path.exists() and receipt is None:
        raise Task055JExecutionError("task055j_unattributed_cache_recovery_rejected")
    if receipt is None:
        if intent and not post_intent:
            return None
        raise Task055JExecutionError("task055j_recovery_state_unprovable")
    records = [dict(row) for row in receipt["records"]]
    if not cache_path.exists():
        cache_path, records = _publish_validated_cache(authority_root, request, receipt)
    else:
        _, _, cached, envelope = _validated_cache(authority_root, request)
        _verify_receipt_cache(receipt, cache_path, envelope, cached, request)
        records = cached
    _complete_journals(authority_root, seal, request, attempt_id, receipt, cache_path, records)
    return _publish_execution(
        authority_root,
        seal,
        request,
        attempt_id,
        receipt,
        cache_path,
        records,
        credential_read_count=0,
        recovered=True,
        publish_pointer=True,
    )


def _publish_transport_receipt(
    authority_root: Path,
    seal: Mapping[str, Any],
    request: Mapping[str, Any],
    attempt_id: str,
    tls: Mapping[str, Any],
    envelope: TushareResponseEnvelope,
) -> dict[str, Any]:
    _validate_envelope(request, envelope)
    semantic = {
        "schema_version": TRANSPORT_RECEIPT_SCHEMA,
        "status": "response_received",
        "attempt_id": attempt_id,
        "final_execution_seal_content_hash": seal["content_hash"],
        "transport_hash": request["transport_hash"],
        "evidence_use_hash": request["evidence_use_hash"],
        "api_name": request["api_name"],
        "params": request["params"],
        "fields": request["fields"],
        "tls_attestation": dict(tls),
        "endpoint": envelope.endpoint,
        "provider_api_version": envelope.provider_api_version,
        "response_code": envelope.response_code,
        "response_payload_hash": envelope.response_payload_hash,
        "response_fields": list(envelope.response_fields),
        "item_count": envelope.item_count,
        "records_hash": stable_json_hash(envelope.records),
        "records": [dict(row) for row in envelope.records],
        "contains_credential": False,
    }
    result = publish_generation(
        authority_root / "transport_receipts",
        prefix=f"task055j_transport_receipt_{attempt_id[:12]}",
        manifest_name="transport_receipt.json",
        semantic=semantic,
    )
    return validate_transport_receipt(result["manifest_path"], seal=seal, request=request, attempt_id=attempt_id)


def validate_transport_receipt(
    path: str | Path,
    *,
    seal: Mapping[str, Any],
    request: Mapping[str, Any],
    attempt_id: str,
) -> dict[str, Any]:
    payload = validate_generation(path, schema=TRANSPORT_RECEIPT_SCHEMA, manifest_name="transport_receipt.json")
    if payload.get("status") != "response_received" or payload.get("attempt_id") != attempt_id:
        raise Task055JExecutionError("task055j_transport_receipt_identity_invalid")
    expected = {
        "final_execution_seal_content_hash": seal["content_hash"],
        "transport_hash": request["transport_hash"],
        "evidence_use_hash": request["evidence_use_hash"],
        "api_name": request["api_name"],
        "params": request["params"],
        "fields": request["fields"],
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise Task055JExecutionError("task055j_transport_receipt_request_lineage_invalid")
    if payload.get("endpoint") != CANONICAL_ORIGIN or payload.get("provider_api_version") != TUSHARE_PROVIDER_API_VERSION:
        raise Task055JExecutionError("task055j_transport_receipt_provider_invalid")
    records = list(payload.get("records") or ())
    if payload.get("response_code") != 0 or payload.get("item_count") != len(records):
        raise Task055JExecutionError("task055j_transport_receipt_response_invalid")
    if payload.get("records_hash") != stable_json_hash(records):
        raise Task055JExecutionError("task055j_transport_receipt_records_hash_invalid")
    if not set(request["fields"]).issubset(set(payload.get("response_fields") or ())):
        raise Task055JExecutionError("task055j_transport_receipt_response_fields_incomplete")
    _validate_records(request, records)
    return payload


def _publish_validated_cache(
    authority_root: Path, request: Mapping[str, Any], receipt: Mapping[str, Any]
) -> tuple[Path, list[dict[str, Any]]]:
    records = [dict(row) for row in receipt["records"]]
    schema_proof = _endpoint_schema_proof(receipt, request) if not records else None
    cache = TushareResponseCache(authority_root / "cache_data", enabled=True)
    path = cache.write(
        request["api_name"],
        params=request["params"],
        fields=request["fields"],
        records=records,
        response_code=0,
        response_message="",
        response_fields=receipt["response_fields"],
        item_count=len(records),
        response_fields_observed=True,
        endpoint_schema_proof=schema_proof,
        endpoint=receipt["endpoint"],
        provider_api_version=receipt["provider_api_version"],
    )
    reread = cache.read(
        request["api_name"],
        params=request["params"],
        fields=request["fields"],
        allow_legacy_source_semantics=False,
    )
    if reread is None or not reread.hit or list(reread.records) != records:
        raise Task055JExecutionError("task055j_validated_cache_reread_failed")
    return path, records


def _complete_journals(
    authority_root: Path,
    seal: Mapping[str, Any],
    request: Mapping[str, Any],
    attempt_id: str,
    receipt: Mapping[str, Any],
    cache_path: Path,
    records: list[dict[str, Any]],
) -> None:
    network = DurableHashJournal(authority_root / "network_journal", name="network")
    spend = DurableHashJournal(authority_root / "transport_spend_journal", name="transport_spend")
    common = {
        "attempt_id": attempt_id,
        "transport_hash": request["transport_hash"],
        "evidence_use_hash": request["evidence_use_hash"],
        "transport_receipt_content_hash": receipt["content_hash"],
        "transport_receipt_sha256": sha256_file(receipt["manifest_path"]),
        "cache_sha256": sha256_file(cache_path),
        "item_count": len(records),
        "final_execution_seal_hash": seal["content_hash"],
    }
    spend.append({"event_id": f"post-completed:{attempt_id}", "event": "physical_post_completed", **common})
    network.append({"event_id": f"attempt-completed:{attempt_id}", "event": "attempt_completed", **common})
    network.append(
        {
            "event_id": f"request-terminal:{attempt_id}",
            "event": "request_terminal",
            "terminal_status": "succeeded",
            **common,
        }
    )


def _publish_execution(
    authority_root: Path,
    seal: Mapping[str, Any],
    request: Mapping[str, Any],
    attempt_id: str,
    receipt: Mapping[str, Any],
    cache_path: Path,
    records: list[dict[str, Any]],
    *,
    credential_read_count: int,
    recovered: bool,
    publish_pointer: bool,
) -> dict[str, Any]:
    network = DurableHashJournal(authority_root / "network_journal", name="network")
    spend = DurableHashJournal(authority_root / "transport_spend_journal", name="transport_spend")
    semantic = {
        "schema_version": EXECUTION_SCHEMA,
        "status": "completed",
        "final_execution_seal_content_hash": seal["content_hash"],
        "runtime_authority_content_hash": seal["runtime_authority_content_hash"],
        "canary": dict(CANARY),
        "attempt_id": attempt_id,
        "transport_receipt_content_hash": receipt["content_hash"],
        "transport_receipt_sha256": sha256_file(receipt["manifest_path"]),
        "cache_relative_path": cache_path.relative_to(authority_root).as_posix(),
        "cache_sha256": sha256_file(cache_path),
        "outcome": "positive_response" if records else "negative_vendor_response",
        "item_count": len(records),
        "network_journal_root": network.checkpoint()["root"],
        "transport_spend_root": spend.checkpoint()["root"],
        "physical_post_count": len(event_rows(spend.rows(), event="physical_post_intent", attempt_id=attempt_id)),
        "credential_read_count": credential_read_count,
        "crash_recovered": recovered,
        "must_stop_after_canary": True,
        "resume_authorized": False,
        "batch_started": False,
    }
    result = publish_generation(
        authority_root / "executions",
        prefix="task055j_canary_execution",
        manifest_name="canary_execution.json",
        semantic=semantic,
    )
    if not publish_pointer:
        pointer = authority_root / "executions/current.json"
        pointer.unlink(missing_ok=True)
    return result


def _load_execution(authority_root: Path, seal: Mapping[str, Any]) -> dict[str, Any]:
    execution = _optional_execution(authority_root, seal)
    if execution is None:
        raise Task055JExecutionError("task055j_canary_execution_missing")
    return execution


def _optional_execution(authority_root: Path, seal: Mapping[str, Any]) -> dict[str, Any] | None:
    manifests = sorted((authority_root / "executions/generations").glob("*/canary_execution.json"))
    matches = []
    for manifest in manifests:
        payload = validate_generation(manifest, schema=EXECUTION_SCHEMA, manifest_name="canary_execution.json")
        if payload.get("final_execution_seal_content_hash") == seal["content_hash"]:
            matches.append(payload)
    if len(matches) > 1:
        raise Task055JExecutionError("task055j_execution_generation_cardinality_invalid")
    return matches[0] if matches else None


def _repair_execution_pointer(authority_root: Path, execution: Mapping[str, Any]) -> None:
    pointer = authority_root / "executions/current.json"
    expected = {
        "schema_version": "task055j_canary_execution_pointer_v1",
        "content_hash": execution["content_hash"],
        "generation_id": execution["generation_id"],
        "manifest": f"generations/{execution['generation_id']}/canary_execution.json",
    }
    if pointer.exists():
        current = read_json(pointer)
        if current.get("content_hash") != execution["content_hash"]:
            raise Task055JExecutionError("task055j_execution_pointer_conflict")
        return
    atomic_json(pointer, expected)


def _optional_receipt(authority_root: Path, attempt_id: str) -> dict[str, Any] | None:
    manifests = sorted((authority_root / "transport_receipts/generations").glob("*/transport_receipt.json"))
    matches = []
    for path in manifests:
        payload = validate_generation(path, schema=TRANSPORT_RECEIPT_SCHEMA, manifest_name="transport_receipt.json")
        if payload.get("attempt_id") == attempt_id:
            matches.append(payload)
    if len(matches) > 1:
        raise Task055JExecutionError("task055j_transport_receipt_cardinality_invalid")
    return matches[0] if matches else None


def _load_receipt(authority_root: Path, content_hash: str) -> dict[str, Any]:
    matches = []
    for path in sorted((authority_root / "transport_receipts/generations").glob("*/transport_receipt.json")):
        payload = validate_generation(path, schema=TRANSPORT_RECEIPT_SCHEMA, manifest_name="transport_receipt.json")
        if payload.get("content_hash") == content_hash:
            matches.append(payload)
    if len(matches) != 1:
        raise Task055JExecutionError("task055j_transport_receipt_hash_resolution_invalid")
    return matches[0]


def _validated_cache(
    authority_root: Path, request: Mapping[str, Any]
) -> tuple[TushareResponseCache, Path, list[dict[str, Any]], dict[str, Any]]:
    cache = TushareResponseCache(authority_root / "cache_data", enabled=True)
    path = cache.cache_path(request["api_name"], params=request["params"], fields=request["fields"])
    if not path.is_file() or path.is_symlink() or cache.root_dir.resolve() not in path.resolve().parents:
        raise Task055JExecutionError("task055j_cache_missing_or_escape")
    envelope = read_json(path)
    read = cache.read(
        request["api_name"],
        params=request["params"],
        fields=request["fields"],
        allow_legacy_source_semantics=False,
    )
    if read is None or not read.hit:
        raise Task055JExecutionError("task055j_cache_native_validation_failed")
    provider = envelope.get("provider") or {}
    if provider.get("endpoint") != CANONICAL_ORIGIN or provider.get("api_version") != TUSHARE_PROVIDER_API_VERSION:
        raise Task055JExecutionError("task055j_cache_provider_invalid")
    response_fields = list((envelope.get("response") or {}).get("fields") or ())
    if not set(request["fields"]).issubset(response_fields):
        raise Task055JExecutionError("task055j_cache_response_fields_incomplete")
    records = list(read.records)
    _validate_records(request, records)
    return cache, path, records, envelope


def _verify_receipt_cache(
    receipt: Mapping[str, Any],
    cache_path: Path,
    envelope: Mapping[str, Any],
    records: list[dict[str, Any]],
    request: Mapping[str, Any],
) -> None:
    if receipt.get("records_hash") != stable_json_hash(records) or receipt.get("item_count") != len(records):
        raise Task055JExecutionError("task055j_receipt_cache_record_mismatch")
    response = envelope.get("response") or {}
    if response.get("records_sha256") != receipt.get("records_hash") or response.get("item_count") != len(records):
        raise Task055JExecutionError("task055j_cache_response_receipt_mismatch")
    if envelope.get("request_fingerprint") != tushare_request_fingerprint(
        request["api_name"], params=request["params"], fields=request["fields"]
    ):
        raise Task055JExecutionError("task055j_cache_transport_identity_mismatch")
    if not cache_path.is_file() or cache_path.is_symlink():
        raise Task055JExecutionError("task055j_cache_path_invalid")
    if not records:
        proof = envelope.get("endpoint_schema_proof")
        if proof != _endpoint_schema_proof(receipt, request):
            raise Task055JExecutionError("task055j_empty_cache_endpoint_schema_proof_invalid")
        negative = envelope.get("negative_attestation") or {}
        if (
            negative.get("assertion") != "provider_returned_zero_rows"
            or negative.get("request_fingerprint") != request["transport_hash"]
            or negative.get("response_code") != 0
            or negative.get("item_count") != 0
        ):
            raise Task055JExecutionError("task055j_empty_cache_negative_attestation_invalid")


def _endpoint_schema_proof(
    receipt: Mapping[str, Any], request: Mapping[str, Any]
) -> dict[str, Any]:
    unsigned = {
        "api_name": request["api_name"],
        "requested_fields": list(request["fields"]),
        "response_fields": list(receipt["response_fields"]),
        "code_semantic_hash": tushare_code_semantic_hash(),
        "source_cache_sha256": sha256_file(receipt["manifest_path"]),
        "source_request_fingerprint": request["transport_hash"],
        "source_kind": "task055j_executor_native_transport_receipt",
        "source_receipt_content_hash": receipt["content_hash"],
        "response_payload_hash": receipt["response_payload_hash"],
    }
    return {**unsigned, "proof_hash": stable_json_hash(unsigned)}


def _verify_execution_events(
    authority_root: Path,
    seal: Mapping[str, Any],
    execution: Mapping[str, Any],
    receipt: Mapping[str, Any],
    cache_path: Path,
    records: list[dict[str, Any]],
) -> None:
    attempt_id = str(execution["attempt_id"])
    network = DurableHashJournal(authority_root / "network_journal", name="network")
    spend = DurableHashJournal(authority_root / "transport_spend_journal", name="transport_spend")
    network.assert_ancestor(seal["initial_network_journal"])
    spend.assert_ancestor(seal["initial_transport_spend"])
    network_rows = network.rows()
    spend_rows = spend.rows()
    expected_network = ("attempt_intent", "attempt_completed", "request_terminal")
    expected_spend = ("physical_post_intent", "physical_post_completed")
    for event in expected_network:
        rows = event_rows(network_rows, event=event, attempt_id=attempt_id)
        if len(rows) != 1:
            raise Task055JExecutionError(f"task055j_network_event_cardinality_invalid:{event}")
    for event in expected_spend:
        rows = event_rows(spend_rows, event=event, attempt_id=attempt_id)
        if len(rows) != 1:
            raise Task055JExecutionError(f"task055j_spend_event_cardinality_invalid:{event}")
    cache_sha = sha256_file(cache_path)
    receipt_sha = sha256_file(receipt["manifest_path"])
    for row in [
        event_rows(network_rows, event="attempt_completed", attempt_id=attempt_id)[0],
        event_rows(network_rows, event="request_terminal", attempt_id=attempt_id)[0],
        event_rows(spend_rows, event="physical_post_completed", attempt_id=attempt_id)[0],
    ]:
        if (
            row.get("transport_hash") != CANARY["transport_hash"]
            or row.get("evidence_use_hash") != CANARY["evidence_use_hash"]
            or row.get("transport_receipt_content_hash") != receipt["content_hash"]
            or row.get("transport_receipt_sha256") != receipt_sha
            or row.get("cache_sha256") != cache_sha
            or row.get("item_count") != len(records)
            or row.get("final_execution_seal_hash") != seal["content_hash"]
        ):
            raise Task055JExecutionError("task055j_execution_event_lineage_invalid")
    if (
        execution.get("physical_post_count") != 1
        or execution.get("cache_sha256") != cache_sha
        or execution.get("transport_receipt_sha256") != receipt_sha
    ):
        raise Task055JExecutionError("task055j_execution_summary_invalid")


def _validate_envelope(request: Mapping[str, Any], envelope: TushareResponseEnvelope) -> None:
    if envelope.endpoint != CANONICAL_ORIGIN or envelope.provider_api_version != TUSHARE_PROVIDER_API_VERSION:
        raise Task055JExecutionError("task055j_response_provider_invalid")
    if envelope.response_code != 0 or envelope.item_count != len(envelope.records):
        raise Task055JExecutionError("task055j_response_code_or_count_invalid")
    if envelope.item_count >= ENDPOINT_ROW_CAPS[request["api_name"]]:
        raise Task055JExecutionError("task055j_response_cap_reached")
    if envelope.request_fingerprint != request["transport_hash"]:
        raise Task055JExecutionError("task055j_response_transport_identity_invalid")
    if not set(request["fields"]).issubset(set(envelope.response_fields)):
        raise Task055JExecutionError("task055j_response_fields_incomplete")
    records = [dict(row) for row in envelope.records]
    _validate_records(request, records)


def _post_once(
    request: Mapping[str, Any], secret: _Secret, seal: Mapping[str, Any], attempt_id: str
) -> TushareResponseEnvelope:
    capability = _issue_task055j_execution_capability(
        authority_content_hash=seal["runtime_authority_content_hash"],
        final_execution_seal_hash=seal["content_hash"],
        api_name=request["api_name"],
        params=request["params"],
        fields=request["fields"],
        transport_hash=request["transport_hash"],
        attempt_id=attempt_id,
    )
    config = AShareDataConfig(
        tushare_token=secret.value,
        tushare_api_url=CANONICAL_ORIGIN,
        tushare_retry_count=1,
        data_dir=Path(seal["authority_root"]) / "cache_data",
    )
    client = TushareHttpClient(config, execution_capability=capability)
    if client.retry_count != 1:
        raise Task055JExecutionError("task055j_retry_contract_invalid")
    return client.post_with_metadata(
        request["api_name"], params=request["params"], fields=request["fields"]
    )


def _load_credential_file(path: Path, seal: Mapping[str, Any]) -> _Secret:
    if not path.is_absolute() or path.is_symlink():
        raise Task055JExecutionError("task055j_credential_absolute_regular_file_required")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise Task055JExecutionError("task055j_credential_file_unavailable") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise Task055JExecutionError("task055j_credential_regular_file_required")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise Task055JExecutionError("task055j_credential_file_unavailable") from exc
    forbidden = [
        Path(seal["repository_root"]).resolve(),
        Path(seal["governed_root"]).resolve(),
        Path(seal["authority_root"]).resolve(),
    ]
    if any(resolved == root or root in resolved.parents for root in forbidden):
        raise Task055JExecutionError("task055j_credential_inside_forbidden_root")
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) not in {0o400, 0o600}:
        raise Task055JExecutionError("task055j_credential_owner_or_mode_invalid")
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise Task055JExecutionError("task055j_credential_file_unreadable") from exc
    if not value:
        raise Task055JExecutionError("task055j_credential_file_empty")
    return _Secret(value)


def _assert_budget(authority_root: Path, seal: Mapping[str, Any]) -> None:
    network = DurableHashJournal(authority_root / "network_journal", name="network")
    spend = DurableHashJournal(authority_root / "transport_spend_journal", name="transport_spend")
    network.assert_ancestor(seal["initial_network_journal"])
    spend.assert_ancestor(seal["initial_transport_spend"])
    attempts = len(event_rows(spend.rows(), event="physical_post_intent"))
    if attempts != 0 or attempts >= MAX_PHYSICAL_ATTEMPTS:
        raise Task055JExecutionError("task055j_canary_budget_not_pristine")


def _request_from_seal(seal: Mapping[str, Any]) -> dict[str, Any]:
    ordered = list(seal["ordered_exact_daily_keys"])
    if len(ordered) != 17 or ordered[0] != {"ordinal": 1, **dict(CANARY)}:
        raise Task055JExecutionError("task055j_final_seal_first_key_invalid")
    plan = seal["runtime_authority"].get("single_request_plan") or {}
    requests = list(plan.get("requests") or ())
    if len(requests) != 1 or plan.get("plan_hash") != seal.get("parent_canary_plan_hash"):
        raise Task055JExecutionError("task055j_single_request_plan_invalid")
    request = dict(requests[0])
    if request["trade_date"] > MAX_DATE:
        raise Task055JExecutionError("task055j_canary_date_boundary_invalid")
    if transport_identity(request["api_name"], request["params"], request["fields"]) != request["transport_hash"]:
        raise Task055JExecutionError("task055j_canary_transport_recompute_invalid")
    lineage = dict(plan.get("lineage") or {})
    lineage.pop("parent_task055g_plan_hash", None)
    recomputed_evidence = evidence_use_identity(
        stage="task055g_l1_exact",
        parent_plan_hash=canonical_hash(lineage),
        frontier_root=str(plan.get("frontier_root") or ""),
        transport_hash=request["transport_hash"],
    )
    if recomputed_evidence != request["evidence_use_hash"]:
        raise Task055JExecutionError("task055j_canary_evidence_identity_invalid")
    actual = {
        "api_name": request.get("api_name"),
        "ts_code": request.get("ts_code"),
        "trade_date": request.get("trade_date"),
        "fields": request.get("fields"),
        "transport_hash": request.get("transport_hash"),
        "evidence_use_hash": request.get("evidence_use_hash"),
    }
    if actual != CANARY:
        raise Task055JExecutionError("task055j_canary_request_identity_invalid")
    return request


def _attempt_id(seal: Mapping[str, Any], request: Mapping[str, Any]) -> str:
    return canonical_hash([seal["content_hash"], request["transport_hash"], "single_physical_attempt", 1])


def _cache_path(authority_root: Path, request: Mapping[str, Any]) -> Path:
    return TushareResponseCache(authority_root / "cache_data", enabled=True).cache_path(
        request["api_name"], params=request["params"], fields=request["fields"]
    )


def _verify_lock_identity(lock_path: Path, seal: Mapping[str, Any]) -> None:
    expected = (seal.get("root_identities") or {}).get("single_flight_lock") or {}
    if not lock_path.is_file() or lock_path.is_symlink():
        raise Task055JExecutionError("task055j_single_flight_lock_invalid")
    metadata = lock_path.stat()
    if metadata.st_dev != expected.get("device") or metadata.st_ino != expected.get("inode"):
        raise Task055JExecutionError("task055j_single_flight_lock_inode_drift")


def _crash(selected: str | None, point: str) -> None:
    if selected == point:
        raise _SyntheticCrash(point)


class _SyntheticCrash(RuntimeError):
    pass


def _execute_synthetic_test_only(
    *,
    final_execution_seal: str | Path,
    reviewed_hash: str,
    transport: Callable[[Mapping[str, Any]], TushareResponseEnvelope],
    crash_point: str | None = None,
    seal_validator: Callable[[str | Path, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return _execute_single_canary(
        final_execution_seal=final_execution_seal,
        reviewed_final_execution_seal_hash=reviewed_hash,
        credential_file=None,
        allow_network=True,
        synthetic_transport=transport,
        synthetic_tls={
            "status": "passed",
            "origin": CANONICAL_ORIGIN,
            "hostname_verified": True,
            "certificate_verified": True,
            "evidence_scope": "synthetic_rehearsal_only",
        },
        crash_point=crash_point,
        seal_validator=seal_validator,
    )


def _verify_and_accept_synthetic_test_only(
    *,
    final_execution_seal: str | Path,
    reviewed_hash: str,
    seal_validator: Callable[[str | Path, str], dict[str, Any]],
) -> dict[str, Any]:
    return _verify_and_accept(
        final_execution_seal=final_execution_seal,
        reviewed_hash=reviewed_hash,
        seal_validator=seal_validator,
    )


def _load_synthetic_accepted_cache(
    *,
    acceptance: str | Path,
    final_execution_seal: str | Path,
    reviewed_hash: str,
    seal_validator: Callable[[str | Path, str], dict[str, Any]],
) -> dict[str, Any]:
    seal = _validate_seal(final_execution_seal, reviewed_hash, seal_validator, require_pristine=False)
    payload = validate_generation(acceptance, schema=ACCEPTANCE_SCHEMA, manifest_name="canary_acceptance.json")
    authority_root = Path(seal["authority_root"])
    execution = _load_execution(authority_root, seal)
    receipt = _load_receipt(authority_root, payload["transport_receipt_content_hash"])
    request = _request_from_seal(seal)
    _, cache_path, records, envelope = _validated_cache(authority_root, request)
    _verify_receipt_cache(receipt, cache_path, envelope, records, request)
    _verify_execution_events(authority_root, seal, execution, receipt, cache_path, records)
    return {
        "authority_root": str(authority_root),
        "final_execution_seal": seal,
        "acceptance": payload,
        "execution": execution,
        "transport_receipt": receipt,
        "cache_path": str(cache_path),
        "cache_sha256": sha256_file(cache_path),
        "records": records,
        "cache_envelope": envelope,
        "request": request,
    }


def _validate_seal(
    path: str | Path,
    reviewed_hash: str,
    validator: Callable[[str | Path, str], dict[str, Any]] | None,
    *,
    require_pristine: bool,
) -> dict[str, Any]:
    if validator is not None:
        return validator(path, reviewed_hash)
    return validate_final_execution_seal(
        path,
        reviewed_hash=reviewed_hash,
        repository_root=Path(__file__).resolve().parents[1],
        require_ready=True,
        require_pristine=require_pristine,
    )

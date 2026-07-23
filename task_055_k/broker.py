from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from data_pipeline.ashare.cache import TushareResponseCache
from data_pipeline.ashare.providers.tushare_client import (
    TUSHARE_PROVIDER_API_VERSION,
    TushareResponseEnvelope,
)
from data_pipeline.ashare.request_identity import TushareRequestIdentity, validate_tushare_request_identity
from data_pipeline.ashare.request_normalization import stable_json_hash, tushare_code_semantic_hash
from task_055_f.network import ENDPOINT_ROW_CAPS, _validate_records
from task_055_f.transport import CANONICAL_ORIGIN
from task_055_h.io import canonical_hash, publish_generation, read_json, sha256_file, validate_generation
from task_055_j.ledger import DurableHashJournal, event_rows

from .authority import validate_candidate_checkpoint, validate_final_candidate_seal
from .contracts import (
    ACCEPTANCE_SCHEMA,
    ATTEMPT_RESERVATION_SCHEMA,
    CANARY,
    OPERATOR_AUTHORIZATION_SCHEMA,
    TRANSPORT_RECEIPT_SCHEMA,
)
from .signing import EphemeralReceiptSigner, verify_signature


class Task055KBrokerError(RuntimeError):
    pass


@dataclass(frozen=True)
class AcceptedResponse:
    scope: str
    authority_root: Path
    checkpoint: dict[str, Any]
    final_candidate_seal: dict[str, Any] | None
    operator_authorization: dict[str, Any] | None
    acceptance: dict[str, Any]
    reservation: dict[str, Any]
    receipt: dict[str, Any]
    cache_path: Path
    request: dict[str, Any]
    records: tuple[dict[str, Any], ...]


def broker_contract_hash() -> str:
    return canonical_hash(
        {
            "contract": "canonical_single_exact_daily_signed_receipt_v2",
            "final_https_revalidates_canonical_trust": True,
            "caller_capability_is_not_trust_anchor": True,
            "reservation_public_key_precedes_transport": True,
            "receipt_public_key_derived_from_canonical_reservation": True,
            "post_intent_without_canonical_receipt_is_ambiguous": True,
            "tls_preflight_precedes_credential": True,
            "credential_precedes_post_intent": True,
            "credential_read_intent_is_single_use": True,
            "credential_read_ambiguity_blocks": True,
            "retry_count": 1,
            "credential_reads": 1,
        }
    )


def request_from_checkpoint(checkpoint: Mapping[str, Any]) -> dict[str, Any]:
    first = dict((checkpoint.get("ordered_exact_daily_keys") or [])[0])
    request = {
        "api_name": first["api_name"],
        "ts_code": first["ts_code"],
        "trade_date": first["trade_date"],
        "params": {"ts_code": first["ts_code"], "trade_date": first["trade_date"]},
        "fields": list(first["fields"]),
        "request_fingerprint": first["request_fingerprint"],
        "transport_identity": first["transport_identity"],
        "evidence_use_identity": first["evidence_use_identity"],
    }
    if {key: request[key] for key in CANARY} != CANARY:
        raise Task055KBrokerError("task055k_checkpoint_first_request_invalid")
    validate_tushare_request_identity(
        identity=TushareRequestIdentity(
            request["request_fingerprint"],
            request["transport_identity"],
            request["evidence_use_identity"],
        ),
        api_name=request["api_name"],
        params=request["params"],
        fields=request["fields"],
    )
    return request


def publish_attempt_reservation(
    *,
    checkpoint: Mapping[str, Any],
    authority_root: str | Path,
    attempt_id: str,
    public_key_pem: bytes,
    evidence_scope: str,
    final_candidate_seal_hash: str | None,
    operator_authorization_hash: str | None,
) -> dict[str, Any]:
    if evidence_scope not in {"real_production", "synthetic_rehearsal_only"}:
        raise Task055KBrokerError("task055k_attempt_scope_invalid")
    request = request_from_checkpoint(checkpoint)
    semantic = {
        "schema_version": ATTEMPT_RESERVATION_SCHEMA,
        "status": "reserved_before_transport",
        "evidence_scope": evidence_scope,
        "production_seal_eligible": evidence_scope == "real_production",
        "candidate_checkpoint_content_hash": checkpoint["content_hash"],
        "final_candidate_seal_content_hash": final_candidate_seal_hash,
        "operator_authorization_content_hash": operator_authorization_hash,
        "attempt_id": attempt_id,
        "request_fingerprint": request["request_fingerprint"],
        "transport_identity": request["transport_identity"],
        "evidence_use_identity": request["evidence_use_identity"],
        "broker_contract_hash": broker_contract_hash(),
        "broker_public_key_pem_b64": base64.b64encode(public_key_pem).decode("ascii"),
        "broker_public_key_sha256": stable_json_hash(public_key_pem.decode("ascii")),
        "private_key_persisted": False,
    }
    return publish_generation(
        Path(authority_root) / "attempt_reservations",
        prefix=f"task055kr_attempt_{attempt_id[:16]}",
        manifest_name="attempt_reservation.json",
        semantic=semantic,
    )


def publish_signed_transport_receipt(
    *,
    reservation: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
    envelope: TushareResponseEnvelope,
    signer: EphemeralReceiptSigner,
    authority_root: str | Path,
    tls_attestation: Mapping[str, Any],
) -> dict[str, Any]:
    request = request_from_checkpoint(checkpoint)
    _validate_envelope(request, envelope)
    if reservation.get("candidate_checkpoint_content_hash") != checkpoint.get("content_hash"):
        raise Task055KBrokerError("task055k_reservation_checkpoint_invalid")
    response_payload = dict(envelope.response_payload or {})
    if not response_payload:
        response_payload = {
            "code": envelope.response_code,
            "msg": envelope.response_message,
            "data": {
                "fields": list(envelope.response_fields),
                "items": [
                    [record.get(field) for field in envelope.response_fields]
                    for record in envelope.records
                ],
            },
        }
    if stable_json_hash(response_payload) != envelope.response_payload_hash:
        raise Task055KBrokerError("task055k_transport_payload_reconstruction_mismatch")
    semantic = {
        "schema_version": TRANSPORT_RECEIPT_SCHEMA,
        "status": "transport_completed",
        "evidence_scope": reservation["evidence_scope"],
        "production_seal_eligible": reservation["evidence_scope"] == "real_production",
        "candidate_checkpoint_content_hash": checkpoint["content_hash"],
        "final_candidate_seal_content_hash": reservation.get("final_candidate_seal_content_hash"),
        "operator_authorization_content_hash": reservation.get("operator_authorization_content_hash"),
        "attempt_reservation_content_hash": reservation["content_hash"],
        "attempt_id": reservation["attempt_id"],
        "request_fingerprint": request["request_fingerprint"],
        "transport_identity": request["transport_identity"],
        "evidence_use_identity": request["evidence_use_identity"],
        "api_name": request["api_name"],
        "params": request["params"],
        "fields": request["fields"],
        "tls_attestation": dict(tls_attestation),
        "endpoint": CANONICAL_ORIGIN,
        "provider_api_version": TUSHARE_PROVIDER_API_VERSION,
        "response_code": envelope.response_code,
        "response_message": envelope.response_message,
        "response_payload_hash": envelope.response_payload_hash,
        "response_payload": response_payload,
        "response_fields": envelope.response_fields,
        "item_count": envelope.item_count,
        "records_hash": stable_json_hash(envelope.records),
        "records": envelope.records,
        "empty_response_semantics": "vendor_absence_only" if not envelope.records else None,
        "contains_credential": False,
        "broker_contract_hash": broker_contract_hash(),
    }
    signature = signer.sign(_canonical_bytes(semantic))
    result = publish_generation(
        Path(authority_root) / "transport_receipts",
        prefix=f"task055kr_receipt_{reservation['attempt_id'][:16]}",
        manifest_name="transport_receipt.json",
        semantic=semantic | {"signature": signature},
    )
    return _validate_receipt_against_reservation(
        result["manifest_path"],
        checkpoint=checkpoint,
        reservation=reservation,
    )


def publish_validated_cache(
    *, authority_root: str | Path, checkpoint: Mapping[str, Any], receipt: Mapping[str, Any]
) -> Path:
    request = request_from_checkpoint(checkpoint)
    proof = None
    if not receipt["records"]:
        unsigned = {
            "api_name": request["api_name"],
            "requested_fields": request["fields"],
            "response_fields": receipt["response_fields"],
            "code_semantic_hash": tushare_code_semantic_hash(),
            "source_cache_sha256": sha256_file(receipt["manifest_path"]),
            "source_request_fingerprint": request["request_fingerprint"],
            "source_kind": "task055kr_signed_transport_receipt",
            "source_receipt_content_hash": receipt["content_hash"],
            "response_payload_hash": receipt["response_payload_hash"],
        }
        proof = unsigned | {"proof_hash": stable_json_hash(unsigned)}
    cache = TushareResponseCache(Path(authority_root) / "cache_data", enabled=True)
    path = cache.write(
        request["api_name"],
        params=request["params"],
        fields=request["fields"],
        records=[dict(row) for row in receipt["records"]],
        response_code=0,
        response_message=str(receipt.get("response_message") or ""),
        response_fields=receipt["response_fields"],
        item_count=receipt["item_count"],
        response_fields_observed=True,
        endpoint_schema_proof=proof,
        endpoint=CANONICAL_ORIGIN,
        provider_api_version=TUSHARE_PROVIDER_API_VERSION,
    )
    read = cache.read(request["api_name"], params=request["params"], fields=request["fields"])
    if read is None or not read.hit or read.records != receipt["records"]:
        raise Task055KBrokerError("task055k_validated_cache_reread_failed")
    return path


def publish_canary_acceptance(
    *,
    authority_root: str | Path,
    checkpoint: Mapping[str, Any],
    reservation: Mapping[str, Any],
    receipt: Mapping[str, Any],
    cache_path: str | Path,
) -> dict[str, Any]:
    root = Path(authority_root).resolve()
    cache = Path(cache_path).resolve()
    if root not in cache.parents or cache.is_symlink() or not cache.is_file():
        raise Task055KBrokerError("task055k_acceptance_cache_escape")
    request = request_from_checkpoint(checkpoint)
    semantic = {
        "schema_version": ACCEPTANCE_SCHEMA,
        "status": "accepted",
        "evidence_scope": receipt["evidence_scope"],
        "production_seal_eligible": receipt["evidence_scope"] == "real_production",
        "candidate_checkpoint_content_hash": checkpoint["content_hash"],
        "final_candidate_seal_content_hash": receipt.get("final_candidate_seal_content_hash"),
        "operator_authorization_content_hash": receipt.get("operator_authorization_content_hash"),
        "attempt_reservation_content_hash": reservation["content_hash"],
        "transport_receipt_content_hash": receipt["content_hash"],
        "transport_receipt_sha256": sha256_file(receipt["manifest_path"]),
        "cache_relative_path": cache.relative_to(root).as_posix(),
        "cache_sha256": sha256_file(cache),
        "request": request,
        "item_count": receipt["item_count"],
        "empty_response_semantics": receipt.get("empty_response_semantics"),
        "resume_authorized": False,
    }
    return publish_generation(
        root / "acceptance",
        prefix="task055kr_canary_acceptance",
        manifest_name="canary_acceptance.json",
        semantic=semantic,
    )


def load_accepted_response(
    *,
    acceptance_path: str | Path,
    repository_root: str | Path,
    final_candidate_seal_path: str | Path | None = None,
    synthetic_checkpoint_path: str | Path | None = None,
    synthetic_authority_root: str | Path | None = None,
) -> AcceptedResponse:
    acceptance = validate_generation(
        acceptance_path,
        schema=ACCEPTANCE_SCHEMA,
        manifest_name="canary_acceptance.json",
    )
    scope = str(acceptance.get("evidence_scope") or "")
    if scope == "real_production":
        if final_candidate_seal_path is None:
            raise Task055KBrokerError("task055k_production_acceptance_final_seal_required")
        final_seal = validate_final_candidate_seal(
            final_candidate_seal_path,
            repository_root=repository_root,
        )
        authority_root = Path(final_seal["authority_root"])
        checkpoint = final_seal["resolved_lineage"]["candidate_checkpoint"]
        operator = _find_operator_authorization(
            authority_root,
            str(acceptance.get("operator_authorization_content_hash") or ""),
            final_seal,
        )
    elif scope == "synthetic_rehearsal_only":
        if synthetic_checkpoint_path is None or synthetic_authority_root is None:
            raise Task055KBrokerError("task055k_synthetic_acceptance_context_required")
        final_seal = None
        operator = None
        authority_root = Path(synthetic_authority_root).resolve()
        checkpoint = validate_candidate_checkpoint(synthetic_checkpoint_path)
        if acceptance.get("production_seal_eligible") is not False:
            raise Task055KBrokerError("task055k_synthetic_acceptance_scope_invalid")
    else:
        raise Task055KBrokerError("task055k_acceptance_scope_invalid")
    if Path(acceptance["manifest_path"]).resolve().parents[2] != authority_root / "acceptance":
        raise Task055KBrokerError("task055k_acceptance_noncanonical_root")
    reservation = _find_generation_by_hash(
        authority_root / "attempt_reservations",
        "attempt_reservation.json",
        ATTEMPT_RESERVATION_SCHEMA,
        str(acceptance["attempt_reservation_content_hash"]),
    )
    receipt = _find_generation_by_hash(
        authority_root / "transport_receipts",
        "transport_receipt.json",
        TRANSPORT_RECEIPT_SCHEMA,
        str(acceptance["transport_receipt_content_hash"]),
    )
    receipt = _validate_receipt_against_reservation(
        receipt["manifest_path"], checkpoint=checkpoint, reservation=reservation
    )
    acceptance_expected = {
        "status": "accepted",
        "candidate_checkpoint_content_hash": checkpoint["content_hash"],
        "final_candidate_seal_content_hash": receipt.get(
            "final_candidate_seal_content_hash"
        ),
        "operator_authorization_content_hash": receipt.get(
            "operator_authorization_content_hash"
        ),
        "attempt_reservation_content_hash": reservation["content_hash"],
        "transport_receipt_content_hash": receipt["content_hash"],
        "transport_receipt_sha256": sha256_file(receipt["manifest_path"]),
        "request": request_from_checkpoint(checkpoint),
        "item_count": receipt["item_count"],
        "empty_response_semantics": receipt.get("empty_response_semantics"),
        "resume_authorized": False,
    }
    if any(acceptance.get(key) != value for key, value in acceptance_expected.items()):
        raise Task055KBrokerError("task055k_acceptance_cross_lineage_invalid")
    cache_relative = Path(str(acceptance.get("cache_relative_path") or ""))
    if cache_relative.is_absolute() or ".." in cache_relative.parts:
        raise Task055KBrokerError("task055k_acceptance_cache_path_invalid")
    cache_path = (authority_root / cache_relative).resolve()
    if authority_root not in cache_path.parents or cache_path.is_symlink() or not cache_path.is_file():
        raise Task055KBrokerError("task055k_acceptance_cache_missing_or_escape")
    if sha256_file(cache_path) != acceptance.get("cache_sha256"):
        raise Task055KBrokerError("task055k_acceptance_cache_sha_invalid")
    request = request_from_checkpoint(checkpoint)
    cache = TushareResponseCache(authority_root / "cache_data", enabled=True)
    cached = cache.read(request["api_name"], params=request["params"], fields=request["fields"])
    if cached is None or not cached.hit or cached.records != receipt["records"]:
        raise Task055KBrokerError("task055k_acceptance_cache_semantics_invalid")
    if scope == "real_production":
        _validate_production_journals(
            authority_root=authority_root,
            final_seal=final_seal,
            acceptance=acceptance,
            reservation=reservation,
            receipt=receipt,
        )
    return AcceptedResponse(
        scope=scope,
        authority_root=authority_root,
        checkpoint=checkpoint,
        final_candidate_seal=final_seal,
        operator_authorization=operator,
        acceptance=acceptance,
        reservation=reservation,
        receipt=receipt,
        cache_path=cache_path,
        request=request,
        records=tuple(dict(row) for row in receipt["records"]),
    )


def _validate_receipt_against_reservation(
    path: str | Path,
    *,
    checkpoint: Mapping[str, Any],
    reservation: Mapping[str, Any],
) -> dict[str, Any]:
    payload = validate_generation(
        path,
        schema=TRANSPORT_RECEIPT_SCHEMA,
        manifest_name="transport_receipt.json",
    )
    request = request_from_checkpoint(checkpoint)
    expected = {
        "candidate_checkpoint_content_hash": checkpoint["content_hash"],
        "attempt_reservation_content_hash": reservation["content_hash"],
        "attempt_id": reservation["attempt_id"],
        "request_fingerprint": request["request_fingerprint"],
        "transport_identity": request["transport_identity"],
        "evidence_use_identity": request["evidence_use_identity"],
        "api_name": request["api_name"],
        "params": request["params"],
        "fields": request["fields"],
        "broker_contract_hash": broker_contract_hash(),
        "final_candidate_seal_content_hash": reservation.get(
            "final_candidate_seal_content_hash"
        ),
        "operator_authorization_content_hash": reservation.get(
            "operator_authorization_content_hash"
        ),
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise Task055KBrokerError("task055k_transport_receipt_lineage_invalid")
    if payload.get("evidence_scope") != reservation.get("evidence_scope"):
        raise Task055KBrokerError("task055k_transport_receipt_scope_invalid")
    if payload.get("endpoint") != CANONICAL_ORIGIN or payload.get("provider_api_version") != TUSHARE_PROVIDER_API_VERSION:
        raise Task055KBrokerError("task055k_transport_receipt_provider_invalid")
    tls = payload.get("tls_attestation") or {}
    expected_tls_status = (
        "passed" if payload.get("evidence_scope") == "real_production" else "synthetic_passed"
    )
    if (
        tls.get("status") != expected_tls_status
        or tls.get("origin") != CANONICAL_ORIGIN
        or tls.get("hostname_verified") is not True
        or tls.get("certificate_verified") is not True
    ):
        raise Task055KBrokerError("task055k_transport_receipt_tls_invalid")
    records = [dict(row) for row in payload.get("records") or ()]
    if payload.get("response_code") != 0 or payload.get("item_count") != len(records):
        raise Task055KBrokerError("task055k_transport_receipt_response_invalid")
    if payload.get("records_hash") != stable_json_hash(records):
        raise Task055KBrokerError("task055k_transport_receipt_records_hash_invalid")
    response_payload = payload.get("response_payload")
    if not isinstance(response_payload, dict) or stable_json_hash(response_payload) != payload.get(
        "response_payload_hash"
    ):
        raise Task055KBrokerError("task055k_transport_receipt_payload_hash_invalid")
    data = response_payload.get("data") or {}
    rebuilt_records = [
        dict(zip(data.get("fields") or (), item)) for item in data.get("items") or ()
    ]
    if (
        response_payload.get("code") != payload.get("response_code")
        or str(response_payload.get("msg") or "") != str(payload.get("response_message") or "")
        or data.get("fields") != payload.get("response_fields")
        or rebuilt_records != records
    ):
        raise Task055KBrokerError("task055k_transport_receipt_payload_semantics_invalid")
    if not set(request["fields"]).issubset(set(payload.get("response_fields") or ())):
        raise Task055KBrokerError("task055k_transport_receipt_response_fields_incomplete")
    if not records and payload.get("empty_response_semantics") != "vendor_absence_only":
        raise Task055KBrokerError("task055k_transport_receipt_empty_semantics_invalid")
    signed = {
        key: value
        for key, value in payload.items()
        if key not in {"signature", "content_hash", "generation_id", "manifest_path"}
    }
    public_key = base64.b64decode(str(reservation["broker_public_key_pem_b64"]), validate=True)
    if stable_json_hash(public_key.decode("ascii")) != reservation.get("broker_public_key_sha256"):
        raise Task055KBrokerError("task055k_reservation_public_key_hash_invalid")
    verify_signature(
        public_key_pem=public_key,
        payload=_canonical_bytes(signed),
        signature_b64=str(payload.get("signature") or ""),
    )
    if len(records) >= ENDPOINT_ROW_CAPS[request["api_name"]]:
        raise Task055KBrokerError("task055k_transport_receipt_row_cap_reached")
    _validate_records(request, records)
    return payload


def _find_operator_authorization(
    authority_root: Path, content_hash: str, final_seal: Mapping[str, Any]
) -> dict[str, Any]:
    payload = _find_generation_by_hash(
        authority_root / "operator_authorization",
        "operator_authorization.json",
        OPERATOR_AUTHORIZATION_SCHEMA,
        content_hash,
    )
    if (
        payload.get("status") != "reviewed_single_canary_authorized"
        or payload.get("network_authorized") is not True
        or payload.get("final_candidate_seal_content_hash") != final_seal["content_hash"]
        or payload.get("canary") != CANARY
        or payload.get("resume_authorized") is not False
        or payload.get("batch_authorized") is not False
    ):
        raise Task055KBrokerError("task055k_operator_authorization_invalid")
    return payload


def _find_generation_by_hash(
    root: Path, manifest_name: str, schema: str, content_hash: str
) -> dict[str, Any]:
    matches = []
    for path in sorted((root / "generations").glob(f"*/{manifest_name}")):
        row = read_json(path)
        if row.get("content_hash") == content_hash:
            matches.append(path)
    if len(matches) != 1:
        raise Task055KBrokerError(f"task055k_canonical_generation_cardinality_invalid:{manifest_name}:{len(matches)}")
    return validate_generation(matches[0], schema=schema, manifest_name=manifest_name)


def _validate_envelope(request: Mapping[str, Any], envelope: TushareResponseEnvelope) -> None:
    if envelope.request_fingerprint != request["request_fingerprint"]:
        raise Task055KBrokerError("task055k_envelope_request_fingerprint_invalid")
    if envelope.transport_identity != request["transport_identity"]:
        raise Task055KBrokerError("task055k_envelope_transport_identity_invalid")
    if envelope.evidence_use_identity != request["evidence_use_identity"]:
        raise Task055KBrokerError("task055k_envelope_evidence_identity_invalid")
    if envelope.endpoint != CANONICAL_ORIGIN or envelope.provider_api_version != TUSHARE_PROVIDER_API_VERSION:
        raise Task055KBrokerError("task055k_envelope_provider_invalid")
    if len(envelope.records) >= ENDPOINT_ROW_CAPS[request["api_name"]]:
        raise Task055KBrokerError("task055k_envelope_row_cap_reached")
    _validate_records(request, envelope.records)


def _validate_production_journals(
    *,
    authority_root: Path,
    final_seal: Mapping[str, Any],
    acceptance: Mapping[str, Any],
    reservation: Mapping[str, Any],
    receipt: Mapping[str, Any],
) -> None:
    network = DurableHashJournal(authority_root / "network_journal", name="task055kr_network")
    spend = DurableHashJournal(
        authority_root / "transport_spend_journal", name="task055kr_spend"
    )
    network.checkpoint()
    spend.checkpoint()
    network.assert_ancestor(final_seal["initial_network_journal"])
    spend.assert_ancestor(final_seal["initial_transport_spend"])
    attempt_id = reservation["attempt_id"]
    credential_reads = event_rows(
        network.rows(), event="credential_read_intent", attempt_id=attempt_id
    )
    intents = event_rows(network.rows(), event="attempt_intent", attempt_id=attempt_id)
    terminals = event_rows(network.rows(), event="request_terminal", attempt_id=attempt_id)
    starts = event_rows(spend.rows(), event="physical_post_started", attempt_id=attempt_id)
    completions = event_rows(
        spend.rows(), event="physical_post_completed", attempt_id=attempt_id
    )
    if not all(
        len(rows) == 1
        for rows in (credential_reads, intents, terminals, starts, completions)
    ):
        raise Task055KBrokerError("task055k_acceptance_attempt_event_cardinality_invalid")
    if len(event_rows(spend.rows(), event="physical_post_started")) != 1:
        raise Task055KBrokerError("task055k_acceptance_global_single_post_invalid")
    common = {
        "attempt_id": attempt_id,
        "request_fingerprint": receipt["request_fingerprint"],
        "transport_identity": receipt["transport_identity"],
        "evidence_use_identity": receipt["evidence_use_identity"],
        "final_candidate_seal_content_hash": final_seal["content_hash"],
        "operator_authorization_content_hash": receipt[
            "operator_authorization_content_hash"
        ],
        "attempt_reservation_content_hash": reservation["content_hash"],
    }
    for row in (intents[0], starts[0], terminals[0], completions[0]):
        if any(row.get(key) != value for key, value in common.items()):
            raise Task055KBrokerError("task055k_acceptance_attempt_event_lineage_invalid")
    credential_expected = {
        key: value
        for key, value in common.items()
        if key != "attempt_reservation_content_hash"
    }
    if any(
        credential_reads[0].get(key) != value
        for key, value in credential_expected.items()
    ) or credential_reads[0].get("credential_phase") != 1:
        raise Task055KBrokerError("task055k_acceptance_credential_event_invalid")
    expected_phases = ((intents[0], 1), (starts[0], 2), (completions[0], 3), (terminals[0], 4))
    if any(row.get("attempt_phase") != phase for row, phase in expected_phases):
        raise Task055KBrokerError("task055k_acceptance_attempt_phase_invalid")
    terminal_expected = {
        "transport_receipt_content_hash": receipt["content_hash"],
        "transport_receipt_sha256": sha256_file(receipt["manifest_path"]),
        "cache_sha256": acceptance["cache_sha256"],
        "canary_acceptance_content_hash": acceptance["content_hash"],
        "item_count": receipt["item_count"],
    }
    for row in (terminals[0], completions[0]):
        if any(row.get(key) != value for key, value in terminal_expected.items()):
            raise Task055KBrokerError("task055k_acceptance_terminal_event_invalid")
    if not (
        credential_reads[0]["sequence"]
        < intents[0]["sequence"]
        < terminals[0]["sequence"]
        and starts[0]["sequence"] < completions[0]["sequence"]
    ):
        raise Task055KBrokerError("task055k_acceptance_event_order_invalid")


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()

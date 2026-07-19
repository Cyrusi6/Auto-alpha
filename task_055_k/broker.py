from __future__ import annotations

import base64
import fcntl
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from data_pipeline.ashare.cache import TushareResponseCache
from data_pipeline.ashare.config import AShareDataConfig
from data_pipeline.ashare.network_capability import _validated_task055k_execution_capability
from data_pipeline.ashare.providers.tushare_client import (
    TUSHARE_PROVIDER_API_VERSION,
    TushareHttpClient,
    TushareResponseEnvelope,
    parse_tushare_response_payload,
    serialize_tushare_request,
)
from data_pipeline.ashare.request_identity import TushareRequestIdentity, validate_tushare_request_identity
from data_pipeline.ashare.request_normalization import stable_json_hash, tushare_code_semantic_hash
from data_pipeline.ashare.security import tls_preflight
from task_055_f.network import ENDPOINT_ROW_CAPS, _validate_records
from task_055_f.transport import CANONICAL_ORIGIN
from task_055_h.io import canonical_hash, publish_generation, read_json, sha256_file, validate_generation
from task_055_j.ledger import DurableHashJournal

from .authority import validate_candidate_checkpoint
from .contracts import ATTEMPT_RESERVATION_SCHEMA, CANARY, TRANSPORT_RECEIPT_SCHEMA
from .signing import EphemeralReceiptSigner, verify_signature


class Task055KBrokerError(RuntimeError):
    pass


@dataclass(frozen=True)
class BrokerExecutionResult:
    reservation: dict[str, Any]
    receipt: dict[str, Any]
    cache_path: Path
    envelope: TushareResponseEnvelope
    network_checkpoint: dict[str, Any]
    spend_checkpoint: dict[str, Any]


def publish_synthetic_acceptance(
    *,
    result: BrokerExecutionResult,
    checkpoint: Mapping[str, Any],
    authority_root: str | Path,
) -> dict[str, Any]:
    root = Path(authority_root).resolve()
    request = _request_from_checkpoint(checkpoint)
    validated = validate_transport_receipt(
        result.receipt["manifest_path"],
        request=request,
        checkpoint_content_hash=str(checkpoint["content_hash"]),
        reservation=result.reservation,
    )
    cache = TushareResponseCache(root / "cache_data", enabled=True)
    read = cache.read(request["api_name"], params=request["params"], fields=request["fields"])
    if read is None or not read.hit or list(read.records) != list(validated["records"]):
        raise Task055KBrokerError("task055k_acceptance_cache_validation_failed")
    semantic = {
        "schema_version": "task055k_synthetic_canary_acceptance_v1",
        "status": "accepted",
        "evidence_scope": "synthetic_rehearsal_only",
        "production_seal_eligible": False,
        "candidate_checkpoint_content_hash": checkpoint["content_hash"],
        "attempt_reservation_content_hash": result.reservation["content_hash"],
        "transport_receipt_content_hash": validated["content_hash"],
        "cache_relative_path": result.cache_path.relative_to(root).as_posix(),
        "cache_sha256": sha256_file(result.cache_path),
        "request_fingerprint": request["request_fingerprint"],
        "transport_identity": request["transport_identity"],
        "evidence_use_identity": request["evidence_use_identity"],
        "item_count": len(read.records),
        "response_fields": validated["response_fields"],
        "physical_post_count": 0,
        "synthetic_transport_call_count": 1,
        "network_authorized": False,
    }
    return publish_generation(
        root / "acceptance",
        prefix="task055k_synthetic_acceptance",
        manifest_name="canary_acceptance.json",
        semantic=semantic,
    )


def accepted_synthetic_payload(
    *,
    result: BrokerExecutionResult,
    acceptance: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
    authority_root: str | Path,
) -> dict[str, Any]:
    request = _request_from_checkpoint(checkpoint)
    return {
        "acceptance": dict(acceptance),
        "request": request,
        "records": [dict(row) for row in result.envelope.records],
        "transport_receipt": dict(result.receipt),
        "final_execution_seal": dict(checkpoint),
        "authority_root": str(Path(authority_root).resolve()),
    }


def broker_contract_hash() -> str:
    return canonical_hash(
        {
            "source_sha256": sha256_file(__file__),
            "contract": "single_exact_daily_signed_receipt_v1",
            "origin": CANONICAL_ORIGIN,
            "provider_api_version": TUSHARE_PROVIDER_API_VERSION,
            "retry_count": 1,
        }
    )


def execute_synthetic_rehearsal_response(
    *,
    candidate_checkpoint: str | Path,
    reviewed_checkpoint_hash: str,
    authority_root: str | Path,
    response_bytes_provider: Callable[[bytes], bytes],
    tls_attestation: Mapping[str, Any],
    evidence_scope: str = "synthetic_rehearsal_only",
) -> BrokerExecutionResult:
    if evidence_scope != "synthetic_rehearsal_only":
        raise Task055KBrokerError("task055k_synthetic_broker_scope_invalid")
    checkpoint = validate_candidate_checkpoint(candidate_checkpoint, reviewed_hash=reviewed_checkpoint_hash)
    def envelope_provider(
        request: Mapping[str, Any], capability: Any
    ) -> TushareResponseEnvelope:
        capability.authorize(request["api_name"], request["params"], request["fields"])
        serialized = serialize_tushare_request(
            endpoint=CANONICAL_ORIGIN,
            api_name=request["api_name"],
            token="SYNTHETIC_REHEARSAL_TOKEN_NEVER_PERSISTED",
            params=request["params"],
            fields=request["fields"],
        )
        response_bytes = response_bytes_provider(bytes(serialized.data or b""))
        try:
            response_payload = json.loads(response_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise Task055KBrokerError("task055k_response_payload_not_json") from exc
        return parse_tushare_response_payload(
            response_payload,
            api_name=request["api_name"],
            params=request["params"],
            requested_fields=request["fields"],
            identity=capability.identity,
            duration_seconds=0.0,
            endpoint=CANONICAL_ORIGIN,
        )
    return _execute_one_response(
        checkpoint=checkpoint,
        authority_root=Path(authority_root),
        envelope_provider=envelope_provider,
        tls_provider=lambda: dict(tls_attestation),
        evidence_scope=evidence_scope,
    )


def execute_operator_authorized_single_canary(
    *,
    candidate_checkpoint: str | Path,
    reviewed_checkpoint_hash: str,
    operator_authorization: str | Path,
    reviewed_operator_authorization_hash: str,
    credential_file: str | Path,
    authority_root: str | Path,
) -> BrokerExecutionResult:
    checkpoint = validate_candidate_checkpoint(candidate_checkpoint, reviewed_hash=reviewed_checkpoint_hash)
    authorization = validate_generation(
        operator_authorization,
        schema="task055k_operator_single_canary_authorization_v1",
        manifest_name="operator_authorization.json",
    )
    if (
        authorization["content_hash"] != reviewed_operator_authorization_hash
        or authorization.get("status") != "authorized_for_exactly_one_canary"
        or authorization.get("candidate_checkpoint_content_hash") != checkpoint["content_hash"]
        or authorization.get("network_authorized") is not True
        or authorization.get("canary") != CANARY
    ):
        raise Task055KBrokerError("task055k_operator_authorization_invalid")
    canonical_authority = Path(str(authorization.get("canonical_authority_root") or ""))
    if not canonical_authority.is_absolute() or canonical_authority.resolve() != Path(authority_root).resolve():
        raise Task055KBrokerError("task055k_operator_authority_root_invalid")

    def envelope_provider(
        request: Mapping[str, Any], capability: Any
    ) -> TushareResponseEnvelope:
        secret = _load_credential_file(
            Path(credential_file),
            forbidden_roots=[Path(value) for value in authorization.get("credential_forbidden_roots") or ()],
        )
        config = AShareDataConfig(
            tushare_token=secret,
            tushare_api_url=CANONICAL_ORIGIN,
            tushare_retry_count=1,
            data_dir=canonical_authority / "cache_data",
        )
        client = TushareHttpClient(config, execution_capability=capability)
        return client.post_with_metadata(
            request["api_name"],
            params=request["params"],
            fields=request["fields"],
        )

    return _execute_one_response(
        checkpoint=checkpoint,
        authority_root=canonical_authority,
        envelope_provider=envelope_provider,
        tls_provider=tls_preflight,
        evidence_scope="real_production",
    )


def _execute_one_response(
    *,
    checkpoint: Mapping[str, Any],
    authority_root: Path,
    envelope_provider: Callable[[Mapping[str, Any], Any], TushareResponseEnvelope],
    tls_provider: Callable[[], Mapping[str, Any]],
    evidence_scope: str,
) -> BrokerExecutionResult:
    if checkpoint.get("network_authorized") is not False:
        raise Task055KBrokerError("task055k_candidate_checkpoint_network_boundary_invalid")
    request = _request_from_checkpoint(checkpoint)
    _initialize_authority_root(authority_root)
    lock_path = authority_root / "single_canary.lock"
    with lock_path.open("r+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Task055KBrokerError("task055k_single_flight_lock_busy") from exc
        network = DurableHashJournal(authority_root / "network_journal", name="task055k_network")
        spend = DurableHashJournal(authority_root / "transport_spend_journal", name="task055k_transport_spend")
        if any(row.get("event") == "physical_post_intent" for row in spend.rows()):
            raise Task055KBrokerError("task055k_single_attempt_already_reserved")
        tls_attestation = dict(tls_provider())
        _validate_tls(tls_attestation, synthetic=evidence_scope == "synthetic_rehearsal_only")
        signer = EphemeralReceiptSigner.generate()
        attempt_id = canonical_hash(
            [checkpoint["content_hash"], request["transport_identity"], "single_attempt", 1]
        )
        reservation = _publish_reservation(
            authority_root=authority_root,
            checkpoint=checkpoint,
            request=request,
            attempt_id=attempt_id,
            public_key_pem=signer.public_key_pem,
            evidence_scope=evidence_scope,
        )
        common = {
            "attempt_id": attempt_id,
            "request_fingerprint": request["request_fingerprint"],
            "transport_identity": request["transport_identity"],
            "evidence_use_identity": request["evidence_use_identity"],
            "candidate_checkpoint_content_hash": checkpoint["content_hash"],
            "attempt_reservation_content_hash": reservation["content_hash"],
        }
        network.append({"event_id": f"intent:{attempt_id}", "event": "attempt_intent", **common})
        spend.append({"event_id": f"post:{attempt_id}", "event": "physical_post_intent", **common})
        capability = _validated_task055k_execution_capability(
            authority_content_hash=str(checkpoint["candidate_authority_content_hash"]),
            final_execution_seal_hash=str(checkpoint["content_hash"]),
            api_name=request["api_name"],
            params=request["params"],
            fields=request["fields"],
            identity=TushareRequestIdentity(
                request["request_fingerprint"],
                request["transport_identity"],
                request["evidence_use_identity"],
            ),
            attempt_id=attempt_id,
            broker_contract_hash=broker_contract_hash(),
        )
        envelope = envelope_provider(request, capability)
        _validate_envelope(request, envelope)
        receipt = _publish_signed_receipt(
            authority_root=authority_root,
            checkpoint=checkpoint,
            reservation=reservation,
            request=request,
            tls_attestation=tls_attestation,
            envelope=envelope,
            signer=signer,
            evidence_scope=evidence_scope,
        )
        cache_path = _publish_cache(authority_root, request, receipt)
        terminal = {
            **common,
            "transport_receipt_content_hash": receipt["content_hash"],
            "transport_receipt_sha256": sha256_file(receipt["manifest_path"]),
            "cache_sha256": sha256_file(cache_path),
            "item_count": envelope.item_count,
        }
        spend.append({"event_id": f"post-complete:{attempt_id}", "event": "physical_post_completed", **terminal})
        network.append({"event_id": f"complete:{attempt_id}", "event": "attempt_completed", **terminal})
        network.append({"event_id": f"terminal:{attempt_id}", "event": "request_terminal", **terminal})
        return BrokerExecutionResult(
            reservation=reservation,
            receipt=receipt,
            cache_path=cache_path,
            envelope=envelope,
            network_checkpoint=network.checkpoint(),
            spend_checkpoint=spend.checkpoint(),
        )


def validate_transport_receipt(
    path: str | Path,
    *,
    request: Mapping[str, Any],
    checkpoint_content_hash: str,
    reservation: Mapping[str, Any],
) -> dict[str, Any]:
    payload = validate_generation(path, schema=TRANSPORT_RECEIPT_SCHEMA, manifest_name="transport_receipt.json")
    expected = {
        "candidate_checkpoint_content_hash": checkpoint_content_hash,
        "attempt_reservation_content_hash": reservation["content_hash"],
        "attempt_id": reservation["attempt_id"],
        "request_fingerprint": request["request_fingerprint"],
        "transport_identity": request["transport_identity"],
        "evidence_use_identity": request["evidence_use_identity"],
        "api_name": request["api_name"],
        "params": request["params"],
        "fields": request["fields"],
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise Task055KBrokerError("task055k_transport_receipt_lineage_invalid")
    if payload.get("endpoint") != CANONICAL_ORIGIN or payload.get("provider_api_version") != TUSHARE_PROVIDER_API_VERSION:
        raise Task055KBrokerError("task055k_transport_receipt_provider_invalid")
    records = [dict(row) for row in payload.get("records") or ()]
    if payload.get("response_code") != 0 or payload.get("item_count") != len(records):
        raise Task055KBrokerError("task055k_transport_receipt_response_invalid")
    if payload.get("records_hash") != stable_json_hash(records):
        raise Task055KBrokerError("task055k_transport_receipt_records_hash_invalid")
    if not set(request["fields"]).issubset(set(payload.get("response_fields") or ())):
        raise Task055KBrokerError("task055k_transport_receipt_response_fields_incomplete")
    signed = {
        key: value
        for key, value in payload.items()
        if key not in {"signature", "content_hash", "generation_id", "manifest_path"}
    }
    public_key = base64.b64decode(str(reservation["broker_public_key_pem_b64"]), validate=True)
    verify_signature(
        public_key_pem=public_key,
        payload=_canonical_bytes(signed),
        signature_b64=str(payload.get("signature") or ""),
    )
    _validate_records(request, records)
    return payload


def _publish_reservation(
    *,
    authority_root: Path,
    checkpoint: Mapping[str, Any],
    request: Mapping[str, Any],
    attempt_id: str,
    public_key_pem: bytes,
    evidence_scope: str,
) -> dict[str, Any]:
    semantic = {
        "schema_version": ATTEMPT_RESERVATION_SCHEMA,
        "status": "reserved_before_transport",
        "evidence_scope": evidence_scope,
        "candidate_checkpoint_content_hash": checkpoint["content_hash"],
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
        authority_root / "attempt_reservations",
        prefix=f"task055k_attempt_{attempt_id[:16]}",
        manifest_name="attempt_reservation.json",
        semantic=semantic,
    )


def _publish_signed_receipt(
    *,
    authority_root: Path,
    checkpoint: Mapping[str, Any],
    reservation: Mapping[str, Any],
    request: Mapping[str, Any],
    tls_attestation: Mapping[str, Any],
    envelope: TushareResponseEnvelope,
    signer: EphemeralReceiptSigner,
    evidence_scope: str,
) -> dict[str, Any]:
    semantic = {
        "schema_version": TRANSPORT_RECEIPT_SCHEMA,
        "status": "response_received",
        "evidence_scope": evidence_scope,
        "candidate_checkpoint_content_hash": checkpoint["content_hash"],
        "attempt_reservation_content_hash": reservation["content_hash"],
        "attempt_id": reservation["attempt_id"],
        "request_fingerprint": request["request_fingerprint"],
        "transport_identity": request["transport_identity"],
        "evidence_use_identity": request["evidence_use_identity"],
        "api_name": request["api_name"],
        "params": request["params"],
        "fields": request["fields"],
        "tls_attestation": dict(tls_attestation),
        "endpoint": envelope.endpoint,
        "provider_api_version": envelope.provider_api_version,
        "response_code": envelope.response_code,
        "response_payload_hash": envelope.response_payload_hash,
        "response_fields": list(envelope.response_fields),
        "item_count": envelope.item_count,
        "records_hash": stable_json_hash(envelope.records),
        "records": [dict(row) for row in envelope.records],
        "empty_response_semantics": "vendor_absence_only" if not envelope.records else "not_applicable",
        "contains_credential": False,
        "broker_contract_hash": broker_contract_hash(),
    }
    signature = signer.sign(_canonical_bytes(semantic))
    result = publish_generation(
        authority_root / "transport_receipts",
        prefix=f"task055k_receipt_{reservation['attempt_id'][:16]}",
        manifest_name="transport_receipt.json",
        semantic=semantic | {"signature": signature},
    )
    return validate_transport_receipt(
        result["manifest_path"],
        request=request,
        checkpoint_content_hash=str(checkpoint["content_hash"]),
        reservation=reservation,
    )


def _publish_cache(authority_root: Path, request: Mapping[str, Any], receipt: Mapping[str, Any]) -> Path:
    proof = None
    if not receipt["records"]:
        unsigned = {
            "api_name": request["api_name"],
            "requested_fields": request["fields"],
            "response_fields": receipt["response_fields"],
            "code_semantic_hash": tushare_code_semantic_hash(),
            "source_cache_sha256": sha256_file(receipt["manifest_path"]),
            "source_request_fingerprint": request["request_fingerprint"],
            "source_kind": "task055k_signed_transport_receipt",
            "source_receipt_content_hash": receipt["content_hash"],
            "response_payload_hash": receipt["response_payload_hash"],
        }
        proof = unsigned | {"proof_hash": stable_json_hash(unsigned)}
    cache = TushareResponseCache(authority_root / "cache_data", enabled=True)
    path = cache.write(
        request["api_name"],
        params=request["params"],
        fields=request["fields"],
        records=[dict(row) for row in receipt["records"]],
        response_code=0,
        response_fields=receipt["response_fields"],
        item_count=receipt["item_count"],
        response_fields_observed=True,
        endpoint_schema_proof=proof,
        endpoint=CANONICAL_ORIGIN,
        provider_api_version=TUSHARE_PROVIDER_API_VERSION,
    )
    read = cache.read(request["api_name"], params=request["params"], fields=request["fields"])
    if read is None or not read.hit:
        raise Task055KBrokerError("task055k_validated_cache_reread_failed")
    return path


def _request_from_checkpoint(checkpoint: Mapping[str, Any]) -> dict[str, Any]:
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
        "transport_hash": first["transport_identity"],
        "evidence_use_hash": first["evidence_use_identity"],
    }
    if {key: request[key] for key in CANARY} != CANARY:
        raise Task055KBrokerError("task055k_broker_first_request_invalid")
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


def _validate_envelope(request: Mapping[str, Any], envelope: TushareResponseEnvelope) -> None:
    if envelope.request_fingerprint != request["request_fingerprint"]:
        raise Task055KBrokerError("task055k_envelope_request_fingerprint_invalid")
    if envelope.transport_identity != request["transport_identity"]:
        raise Task055KBrokerError("task055k_envelope_transport_identity_invalid")
    if envelope.evidence_use_identity != request["evidence_use_identity"]:
        raise Task055KBrokerError("task055k_envelope_evidence_use_identity_invalid")
    if envelope.response_code != 0 or envelope.item_count != len(envelope.records):
        raise Task055KBrokerError("task055k_envelope_response_invalid")
    if envelope.item_count >= ENDPOINT_ROW_CAPS[request["api_name"]]:
        raise Task055KBrokerError("task055k_envelope_row_cap_reached")
    if not set(request["fields"]).issubset(envelope.response_fields):
        raise Task055KBrokerError("task055k_envelope_fields_incomplete")
    _validate_records(request, envelope.records)


def _validate_tls(tls: Mapping[str, Any], *, synthetic: bool) -> None:
    expected_status = "synthetic_passed" if synthetic else "passed"
    if (
        tls.get("status") != expected_status
        or tls.get("origin") != CANONICAL_ORIGIN
        or tls.get("hostname_verified") is not True
        or tls.get("certificate_verified") is not True
    ):
        raise Task055KBrokerError("task055k_tls_attestation_invalid")


def _initialize_authority_root(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name in (
        "network_journal",
        "transport_spend_journal",
        "attempt_reservations",
        "transport_receipts",
        "cache_data",
    ):
        path = root / name
        path.mkdir(exist_ok=True)
        if path.is_symlink():
            raise Task055KBrokerError(f"task055k_authority_subroot_symlink:{name}")
    for name in ("single_canary.lock", "application.lock"):
        lock = root / name
        lock.touch(exist_ok=True)
        if lock.is_symlink() or not lock.is_file():
            raise Task055KBrokerError(f"task055k_lock_invalid:{name}")


def _load_credential_file(path: Path, *, forbidden_roots: list[Path]) -> str:
    if "TUSHARE_TOKEN" in os.environ:
        raise Task055KBrokerError("task055k_inline_tushare_token_forbidden")
    if not path.is_absolute() or path.is_symlink():
        raise Task055KBrokerError("task055k_credential_absolute_regular_file_required")
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise Task055KBrokerError("task055k_credential_owner_regular_file_required")
    if stat.S_IMODE(metadata.st_mode) not in {0o400, 0o600}:
        raise Task055KBrokerError("task055k_credential_permissions_invalid")
    resolved = path.resolve(strict=True)
    for root in forbidden_roots:
        forbidden = root.resolve()
        if resolved == forbidden or forbidden in resolved.parents:
            raise Task055KBrokerError("task055k_credential_inside_forbidden_root")
    value = resolved.read_text(encoding="utf-8").strip()
    if not value:
        raise Task055KBrokerError("task055k_credential_empty")
    return value


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

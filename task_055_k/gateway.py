from __future__ import annotations

import base64
import fcntl
import gzip
import json
import os
import stat
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from data_pipeline.ashare.cache import TushareResponseCache
from data_pipeline.ashare.providers.tushare_client import (
    TUSHARE_PROVIDER_API_VERSION,
    TushareNetworkError,
    parse_tushare_response_payload,
    serialize_tushare_request,
)
from data_pipeline.ashare.request_identity import TushareRequestIdentity
from data_pipeline.ashare.request_normalization import stable_json_hash
from data_pipeline.ashare.security import tls_preflight
from task_055_f.transport import CANONICAL_ORIGIN
from task_055_h.io import canonical_hash, read_json, sha256_file, validate_generation
from task_055_j.ledger import DurableHashJournal, event_rows

from .authority import validate_final_candidate_seal
from .broker import (
    AcceptedResponse,
    _find_operator_authorization,
    _validate_receipt_against_reservation,
    broker_contract_hash,
    load_accepted_response,
    publish_attempt_reservation,
    publish_canary_acceptance,
    publish_signed_transport_receipt,
    publish_validated_cache,
    request_from_checkpoint,
)
from .contracts import OPERATOR_AUTHORIZATION_SCHEMA
from .signing import EphemeralReceiptSigner


class Task055KGatewayError(RuntimeError):
    pass


def execute_operator_authorized_single_canary(
    *,
    final_candidate_seal: str | Path,
    reviewed_final_candidate_seal_hash: str,
    operator_authorization: str | Path,
    reviewed_operator_authorization_hash: str,
    credential_file: str | Path,
    repository_root: str | Path,
) -> AcceptedResponse:
    trust = _validate_execution_trust(
        final_candidate_seal=final_candidate_seal,
        reviewed_final_candidate_seal_hash=reviewed_final_candidate_seal_hash,
        operator_authorization=operator_authorization,
        reviewed_operator_authorization_hash=reviewed_operator_authorization_hash,
        repository_root=repository_root,
    )
    authority_root = Path(trust["authority_root"])
    lock_path = authority_root / "single_canary.lock"
    _validate_lock_identity(lock_path, trust["final_seal"])
    with lock_path.open("r+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            _validate_lock_identity(lock_path, trust["final_seal"], open_fd=lock.fileno())
            network = DurableHashJournal(authority_root / "network_journal", name="task055kr_network")
            spend = DurableHashJournal(authority_root / "transport_spend_journal", name="task055kr_spend")
            network.checkpoint()
            spend.checkpoint()
            network.assert_ancestor(trust["final_seal"]["initial_network_journal"])
            spend.assert_ancestor(trust["final_seal"]["initial_transport_spend"])
            request = request_from_checkpoint(trust["checkpoint"])
            attempt_id = canonical_hash(
                [trust["final_seal"]["content_hash"], request["transport_identity"], "physical_attempt", 1]
            )
            recovered = _recover_if_possible(
                trust=trust,
                attempt_id=attempt_id,
                network=network,
                spend=spend,
                repository_root=repository_root,
            )
            if recovered is not None:
                return recovered
            if event_rows(network.rows(), event="attempt_intent", attempt_id=attempt_id):
                raise Task055KGatewayError("task055k_post_intent_without_canonical_receipt_ambiguous")
            if event_rows(
                network.rows(), event="credential_read_intent", attempt_id=attempt_id
            ):
                raise Task055KGatewayError(
                    "task055k_credential_read_intent_without_transport_ambiguous"
                )
            _assert_budget(network, spend)
            tls = tls_preflight()
            _validate_tls(tls)
            network.append(
                {
                    "event_id": f"credential:{attempt_id}",
                    "event": "credential_read_intent",
                    "credential_phase": 1,
                    "attempt_id": attempt_id,
                    "request_fingerprint": request["request_fingerprint"],
                    "transport_identity": request["transport_identity"],
                    "evidence_use_identity": request["evidence_use_identity"],
                    "final_candidate_seal_content_hash": trust["final_seal"][
                        "content_hash"
                    ],
                    "operator_authorization_content_hash": trust[
                        "operator_authorization"
                    ]["content_hash"],
                }
            )
            secret = _load_credential_file(
                Path(credential_file),
                forbidden_roots=[
                    Path(repository_root).resolve(),
                    Path(trust["governed_root"]).resolve(),
                    authority_root,
                ],
            )
            signer = EphemeralReceiptSigner.generate()
            reservation = publish_attempt_reservation(
                checkpoint=trust["checkpoint"],
                authority_root=authority_root,
                attempt_id=attempt_id,
                public_key_pem=signer.public_key_pem,
                evidence_scope="real_production",
                final_candidate_seal_hash=trust["final_seal"]["content_hash"],
                operator_authorization_hash=trust["operator_authorization"]["content_hash"],
            )
            common = {
                "attempt_id": attempt_id,
                "request_fingerprint": request["request_fingerprint"],
                "transport_identity": request["transport_identity"],
                "evidence_use_identity": request["evidence_use_identity"],
                "final_candidate_seal_content_hash": trust["final_seal"]["content_hash"],
                "operator_authorization_content_hash": trust["operator_authorization"]["content_hash"],
                "attempt_reservation_content_hash": reservation["content_hash"],
            }
            network.append(
                {
                    "event_id": f"intent:{attempt_id}",
                    "event": "attempt_intent",
                    "attempt_phase": 1,
                    **common,
                }
            )
            spend.append(
                {
                    "event_id": f"spend:{attempt_id}",
                    "event": "physical_post_started",
                    "attempt_phase": 2,
                    **common,
                }
            )
            try:
                revalidated = _validate_execution_trust(
                    final_candidate_seal=trust["final_seal"]["manifest_path"],
                    reviewed_final_candidate_seal_hash=trust["final_seal"]["content_hash"],
                    operator_authorization=trust["operator_authorization"]["manifest_path"],
                    reviewed_operator_authorization_hash=trust["operator_authorization"]["content_hash"],
                    repository_root=repository_root,
                )
                canonical_reservation = _canonical_reservation(
                    Path(revalidated["authority_root"]),
                    str(reservation.get("content_hash") or ""),
                )
                _validate_reservation_for_transport(
                    canonical_reservation,
                    checkpoint=revalidated["checkpoint"],
                    final_seal=revalidated["final_seal"],
                    operator_authorization=revalidated["operator_authorization"],
                    attempt_id=attempt_id,
                )
                _validate_tls(tls)
                canonical_request = request_from_checkpoint(revalidated["checkpoint"])
                serialized = serialize_tushare_request(
                    endpoint=CANONICAL_ORIGIN,
                    api_name=canonical_request["api_name"],
                    token=secret,
                    params=canonical_request["params"],
                    fields=canonical_request["fields"],
                )
                started = time.perf_counter()
                try:
                    with urllib.request.build_opener(_NoRedirect).open(
                        serialized, timeout=30
                    ) as response:
                        raw = response.read()
                        headers = getattr(response, "headers", None)
                        encoding = (
                            str(headers.get("Content-Encoding", "") or "").lower()
                            if headers
                            else ""
                        )
                        if "gzip" in encoding or raw.startswith(b"\x1f\x8b"):
                            raw = gzip.decompress(raw)
                        decoded = raw.decode("utf-8")
                        if secret and secret in decoded:
                            raise TushareNetworkError(
                                "Tushare response contained credential material"
                            )
                        payload = json.loads(decoded)
                except (
                    OSError,
                    TimeoutError,
                    urllib.error.URLError,
                    json.JSONDecodeError,
                ) as exc:
                    raise TushareNetworkError(
                        "Tushare HTTPS request failed after canonical Task055-KR authorization"
                    ) from exc
                if not isinstance(payload, dict):
                    raise TushareNetworkError(
                        "Tushare HTTPS response must be a JSON object"
                    )
                envelope = parse_tushare_response_payload(
                    payload,
                    api_name=canonical_request["api_name"],
                    params=canonical_request["params"],
                    requested_fields=canonical_request["fields"],
                    identity=TushareRequestIdentity(
                        canonical_request["request_fingerprint"],
                        canonical_request["transport_identity"],
                        canonical_request["evidence_use_identity"],
                    ),
                    duration_seconds=max(0.0, time.perf_counter() - started),
                    endpoint=CANONICAL_ORIGIN,
                )
            except Exception as exc:
                raise Task055KGatewayError(_scrub_exception(exc, secret)) from None
            receipt = publish_signed_transport_receipt(
                reservation=reservation,
                checkpoint=trust["checkpoint"],
                envelope=envelope,
                signer=signer,
                authority_root=authority_root,
                tls_attestation=tls,
            )
            cache = publish_validated_cache(
                authority_root=authority_root,
                checkpoint=trust["checkpoint"],
                receipt=receipt,
            )
            acceptance = publish_canary_acceptance(
                authority_root=authority_root,
                checkpoint=trust["checkpoint"],
                reservation=reservation,
                receipt=receipt,
                cache_path=cache,
            )
            terminal = {
                **common,
                "transport_receipt_content_hash": receipt["content_hash"],
                "transport_receipt_sha256": sha256_file(receipt["manifest_path"]),
                "cache_sha256": sha256_file(cache),
                "canary_acceptance_content_hash": acceptance["content_hash"],
                "item_count": receipt["item_count"],
            }
            spend.append(
                {
                    "event_id": f"post-complete:{attempt_id}",
                    "event": "physical_post_completed",
                    "attempt_phase": 3,
                    **terminal,
                }
            )
            network.append(
                {
                    "event_id": f"terminal:{attempt_id}",
                    "event": "request_terminal",
                    "attempt_phase": 4,
                    **terminal,
                }
            )
            return load_accepted_response(
                acceptance_path=acceptance["manifest_path"],
                final_candidate_seal_path=final_candidate_seal,
                repository_root=repository_root,
            )
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _validate_execution_trust(
    *,
    final_candidate_seal: str | Path,
    reviewed_final_candidate_seal_hash: str,
    operator_authorization: str | Path,
    reviewed_operator_authorization_hash: str,
    repository_root: str | Path,
) -> dict[str, Any]:
    final_seal = validate_final_candidate_seal(
        final_candidate_seal,
        repository_root=repository_root,
        reviewed_hash=reviewed_final_candidate_seal_hash,
    )
    operator = validate_generation(
        operator_authorization,
        schema=OPERATOR_AUTHORIZATION_SCHEMA,
        manifest_name="operator_authorization.json",
    )
    if operator["content_hash"] != reviewed_operator_authorization_hash:
        raise Task055KGatewayError("task055k_reviewed_operator_authorization_hash_invalid")
    canonical = _find_operator_authorization(
        Path(final_seal["authority_root"]), operator["content_hash"], final_seal
    )
    if canonical["manifest_path"] != operator["manifest_path"]:
        raise Task055KGatewayError("task055k_operator_authorization_noncanonical")
    return {
        "final_seal": final_seal,
        "operator_authorization": operator,
        "checkpoint": final_seal["resolved_lineage"]["candidate_checkpoint"],
        "authority_root": final_seal["authority_root"],
        "governed_root": final_seal["governed_root"],
    }


def _recover_if_possible(
    *,
    trust: Mapping[str, Any],
    attempt_id: str,
    network: DurableHashJournal,
    spend: DurableHashJournal,
    repository_root: str | Path,
) -> AcceptedResponse | None:
    intents = event_rows(network.rows(), event="attempt_intent", attempt_id=attempt_id)
    if not intents:
        return None
    reservations = list(
        (Path(trust["authority_root"]) / "attempt_reservations" / "generations").glob(
            f"task055kr_attempt_{attempt_id[:16]}_*/attempt_reservation.json"
        )
    )
    if len(reservations) != 1:
        raise Task055KGatewayError("task055k_ambiguous_attempt_missing_canonical_reservation")
    reservation = _canonical_reservation(
        Path(trust["authority_root"]), read_json(reservations[0])["content_hash"]
    )
    receipts = list(
        (Path(trust["authority_root"]) / "transport_receipts" / "generations").glob(
            f"task055kr_receipt_{attempt_id[:16]}_*/transport_receipt.json"
        )
    )
    if len(receipts) != 1:
        raise Task055KGatewayError("task055k_ambiguous_attempt_missing_canonical_receipt")
    receipt = _validate_receipt_against_reservation(
        receipts[0], checkpoint=trust["checkpoint"], reservation=reservation
    )
    request = request_from_checkpoint(trust["checkpoint"])
    cache_store = TushareResponseCache(
        Path(trust["authority_root"]) / "cache_data", enabled=True
    )
    cached = cache_store.read(
        request["api_name"], params=request["params"], fields=request["fields"]
    )
    if cached is None or not cached.hit:
        cache_path = publish_validated_cache(
            authority_root=trust["authority_root"],
            checkpoint=trust["checkpoint"],
            receipt=receipt,
        )
    else:
        if cached.records != receipt["records"]:
            raise Task055KGatewayError("task055k_recovery_cache_receipt_conflict")
        cache_path = cached.path
    acceptances = list(
        (Path(trust["authority_root"]) / "acceptance" / "generations").glob(
            "*/canary_acceptance.json"
        )
    )
    matches = [
        path
        for path in acceptances
        if read_json(path).get("attempt_reservation_content_hash") == reservation["content_hash"]
    ]
    if len(matches) > 1:
        raise Task055KGatewayError("task055k_ambiguous_attempt_duplicate_acceptance")
    if not matches:
        acceptance = publish_canary_acceptance(
            authority_root=trust["authority_root"],
            checkpoint=trust["checkpoint"],
            reservation=reservation,
            receipt=receipt,
            cache_path=cache_path,
        )
        matches = [Path(acceptance["manifest_path"])]
    acceptance_payload = read_json(matches[0])
    request = request_from_checkpoint(trust["checkpoint"])
    terminal = {
        "attempt_id": attempt_id,
        "request_fingerprint": request["request_fingerprint"],
        "transport_identity": request["transport_identity"],
        "evidence_use_identity": request["evidence_use_identity"],
        "final_candidate_seal_content_hash": trust["final_seal"]["content_hash"],
        "operator_authorization_content_hash": trust["operator_authorization"]["content_hash"],
        "attempt_reservation_content_hash": reservation["content_hash"],
        "transport_receipt_content_hash": receipt["content_hash"],
        "transport_receipt_sha256": sha256_file(receipt["manifest_path"]),
        "cache_sha256": sha256_file(cache_path),
        "canary_acceptance_content_hash": acceptance_payload["content_hash"],
        "item_count": receipt["item_count"],
        "recovered_without_new_post": True,
    }
    if not event_rows(spend.rows(), event="physical_post_started", attempt_id=attempt_id):
        raise Task055KGatewayError("task055k_recovery_spend_start_missing")
    if not event_rows(spend.rows(), event="physical_post_completed", attempt_id=attempt_id):
        spend.append(
            {
                "event_id": f"post-complete:{attempt_id}",
                "event": "physical_post_completed",
                "attempt_phase": 3,
                **terminal,
            }
        )
    if not event_rows(network.rows(), event="request_terminal", attempt_id=attempt_id):
        network.append(
            {
                "event_id": f"terminal:{attempt_id}",
                "event": "request_terminal",
                "attempt_phase": 4,
                **terminal,
            }
        )
    return load_accepted_response(
        acceptance_path=matches[0],
        final_candidate_seal_path=trust["final_seal"]["manifest_path"],
        repository_root=repository_root,
    )


def _canonical_reservation(authority_root: Path, content_hash: str) -> dict[str, Any]:
    matches = []
    for path in (authority_root / "attempt_reservations" / "generations").glob(
        "*/attempt_reservation.json"
    ):
        row = read_json(path)
        if row.get("content_hash") == content_hash:
            matches.append(path)
    if len(matches) != 1:
        raise Task055KGatewayError("task055k_final_transport_reservation_cardinality_invalid")
    return validate_generation(
        matches[0],
        schema="task055kr_single_attempt_reservation_v2",
        manifest_name="attempt_reservation.json",
    )


def _validate_reservation_for_transport(
    reservation: Mapping[str, Any],
    *,
    checkpoint: Mapping[str, Any],
    final_seal: Mapping[str, Any],
    operator_authorization: Mapping[str, Any],
    attempt_id: str,
) -> None:
    request = request_from_checkpoint(checkpoint)
    expected = {
        "status": "reserved_before_transport",
        "evidence_scope": "real_production",
        "production_seal_eligible": True,
        "candidate_checkpoint_content_hash": checkpoint["content_hash"],
        "final_candidate_seal_content_hash": final_seal["content_hash"],
        "operator_authorization_content_hash": operator_authorization["content_hash"],
        "attempt_id": attempt_id,
        "request_fingerprint": request["request_fingerprint"],
        "transport_identity": request["transport_identity"],
        "evidence_use_identity": request["evidence_use_identity"],
        "broker_contract_hash": broker_contract_hash(),
        "private_key_persisted": False,
    }
    if any(reservation.get(key) != value for key, value in expected.items()):
        raise Task055KGatewayError("task055k_final_transport_reservation_invalid")
    encoded = str(reservation.get("broker_public_key_pem_b64") or "")
    public_key_hash = str(reservation.get("broker_public_key_sha256") or "")
    try:
        public_key = base64.b64decode(encoded, validate=True).decode("ascii")
    except (ValueError, UnicodeDecodeError):
        raise Task055KGatewayError(
            "task055k_final_transport_public_key_reservation_invalid"
        ) from None
    if stable_json_hash(public_key) != public_key_hash:
        raise Task055KGatewayError("task055k_final_transport_public_key_reservation_invalid")


def _assert_budget(network: DurableHashJournal, spend: DurableHashJournal) -> None:
    if (
        event_rows(network.rows(), event="credential_read_intent")
        or event_rows(network.rows(), event="request_terminal")
        or event_rows(spend.rows(), event="physical_post_started")
    ):
        raise Task055KGatewayError("task055k_single_canary_budget_already_consumed")


def _validate_lock_identity(path: Path, final_seal: Mapping[str, Any], *, open_fd: int | None = None) -> None:
    if not path.is_file() or path.is_symlink():
        raise Task055KGatewayError("task055k_single_flight_lock_invalid")
    actual = path.stat()
    expected = final_seal.get("root_identities", {}).get("single_canary_lock") or {}
    if actual.st_dev != expected.get("st_dev") or actual.st_ino != expected.get("st_ino"):
        raise Task055KGatewayError("task055k_single_flight_lock_inode_drift")
    if open_fd is not None:
        opened = os.fstat(open_fd)
        if opened.st_dev != actual.st_dev or opened.st_ino != actual.st_ino:
            raise Task055KGatewayError("task055k_single_flight_open_lock_replaced")


def _validate_tls(payload: Mapping[str, Any]) -> None:
    if (
        payload.get("status") != "passed"
        or payload.get("origin") != CANONICAL_ORIGIN
        or payload.get("hostname_verified") is not True
        or payload.get("certificate_verified") is not True
    ):
        raise Task055KGatewayError("task055k_tls_attestation_invalid")


def _load_credential_file(path: Path, *, forbidden_roots: list[Path]) -> str:
    if not path.is_absolute() or path.is_symlink():
        raise Task055KGatewayError("task055k_credential_absolute_regular_file_required")
    try:
        initial = path.lstat()
    except OSError:
        raise Task055KGatewayError("task055k_credential_read_failed") from None
    if not stat.S_ISREG(initial.st_mode):
        raise Task055KGatewayError("task055k_credential_owner_regular_file_required")
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        raise Task055KGatewayError("task055k_credential_read_failed") from None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise Task055KGatewayError("task055k_credential_owner_regular_file_required")
        if stat.S_IMODE(metadata.st_mode) not in {0o400, 0o600}:
            raise Task055KGatewayError("task055k_credential_permissions_invalid")
        resolved = Path(os.readlink(f"/proc/self/fd/{descriptor}")).resolve()
        for root in forbidden_roots:
            root = root.resolve()
            if resolved == root or root in resolved.parents:
                raise Task055KGatewayError("task055k_credential_inside_forbidden_root")
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            value = handle.read().strip()
    except OSError:
        raise Task055KGatewayError("task055k_credential_read_failed") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not value:
        raise Task055KGatewayError("task055k_credential_empty")
    return value


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Task055KGatewayError("task055k_https_redirect_forbidden")
def _scrub_exception(error: Exception, secret: str) -> str:
    message = str(error).replace("\n", " ")
    if secret:
        message = message.replace(secret, "[REDACTED]")
    return message or "task055k_transport_failed"

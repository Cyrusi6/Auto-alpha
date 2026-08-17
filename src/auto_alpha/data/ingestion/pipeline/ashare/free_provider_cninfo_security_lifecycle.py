"""Exact CNINFO source-document capture for identity and lifecycle adjudication.

The profile is deliberately finite.  It acquires five official PDF bytes and
does not derive a security alias, lifecycle interval, suspension interval, or
any research/trading authorization from them.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping, Sequence

from auto_alpha.platform.artifacts.storage import (
    canonical_hash,
    read_json,
    sha256_file,
)
from auto_alpha.platform.governance.network.signing import PersistentReceiptSigner

from . import free_provider_backfill as capture_module
from . import free_provider_http_backfill as http_module
from . import run_provider_probe as probe_module
from .free_provider_backfill import (
    SAFETY_FLAGS,
    BackfillResourceBudget,
    BackfillTransport,
    CaptureSigner,
    FreeProviderBackfillContract,
    NormalizedArtifact,
    replay_normalized_artifacts,
    run_free_provider_backfill,
    validate_free_provider_backfill,
)
from .free_provider_http_backfill import (
    CNINFO_DOCUMENT_BODY_MAX_BYTES,
    _content_length_matches,
    _content_type_compatible,
    _decode_cninfo_official_http_envelope,
    _document_block_reason,
    _document_format,
    _document_structure_valid,
)
from .provider_probe import ProviderProbeRequest
from .provider_probe import ProviderProbeObservation
from .run_provider_probe import OfficialHttpProbeTransport, USER_AGENT


PROFILE_ID = "cninfo_security_identity_lifecycle_exact_v1"
KNOWN_AT_SEMANTICS = "official_announcement_publication_date_only"
NORMALIZATION_SCHEMA = (
    "cninfo_security_identity_lifecycle_document_normalization_v1"
)
ADAPTER_ID = "cninfo_security_identity_lifecycle_exact_signed_http_capture_v1"
ACTIVITY_NAME = "free_domestic_cninfo_security_identity_lifecycle_exact_v1"
HTTP_ADAPTER_ID = "python_urllib_no_redirect_v1"
DEFAULT_OUTPUT_ROOT = Path(
    "/home/lijunsi/data/auto-alpha/ashare_lake/staging/data_admission/"
    "dap_d785714ef1b912a20c0f19ca/"
    "research_20120101_20191231_asof_20191231/"
    "cninfo/security_identity_lifecycle_documents"
)
DEFAULT_CAPTURE_KEY = Path(
    "/home/lijunsi/data/auto-alpha/ashare_lake/governance/capture_keys/"
    "free_domestic_backfill_20260816.pem"
)
DEFAULT_PERMISSION_CONTEXT = (
    "human_authorization_20260816_free_domestic_missing_data_backfill_v1"
)
AUTHORIZATION_POLICY_ID = (
    "cninfo_security_identity_lifecycle_human_authorized_capture_v1"
)
OPERATOR_AUTHORIZATION_SEMANTICS = (
    "approved_operator_permission_context_and_local_capture_key_only"
)
APPROVED_CAPTURE_KEY_SHA256 = (
    "0afef940a253b9ef0f3702af5eb099c4ed48209975bc4f1991a471e4c50f446f"
)
LOCKED_MINIMUM_DELAY_SECONDS = 2.0
LOCKED_TIMEOUT_SECONDS = 30.0
LOCKED_MAX_RETRIES = 2
LOCKED_MAX_RESPONSE_BYTES = 132 * 1024 * 1024
LOCKED_MAX_TOTAL_RESPONSE_BYTES = 2 * 1024 * 1024 * 1024
LOCKED_ALLOWED_HOSTS = ("static.cninfo.com.cn",)
LOCKED_SCOPE = {
    "date_start": "20120101",
    "date_end": "20191231",
    "request_start": "20110101",
    "request_end": "20191231",
}
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_NON_ADMISSION_BLOCKERS = (
    "provider_origin_not_attested",
    "capture_runtime_isolation_not_verified",
    "official_publication_timestamp_receipt_not_bound",
    "official_document_text_derivation_not_run",
    "pit_security_identity_timeline_derivation_not_run",
    "suspension_and_lifecycle_adjudication_not_run",
)

_CONTRACT_KEYS = frozenset(
    {
        "schema_version",
        "activity_name",
        "provider",
        "output_namespace_id",
        "permission_context_id",
        "population_root",
        "capture_public_key_sha256",
        "capture_public_key_pem_b64",
        "scope",
        "allowed_hosts",
        "budget",
        "adapter_identity",
        "source_profile_id",
        "mode",
        "capture_before_normalization",
        "old_lake_mutated",
        "safety",
    }
)
_ADAPTER_IDENTITY_KEYS = frozenset(
    {
        "adapter",
        "http",
        "implementation_root",
        "profile_id",
        "request_plan_hash",
        "known_at_semantics",
        "pit_timeline_adjudicated",
        "authorization_policy",
    }
)
_PLAN_KEYS = frozenset({"schema_version", "request_plan_hash", "requests"})

_REQUIRED_CHECKS = (
    "http_envelope_schema_exact",
    "response_headers_shape_exact",
    "elapsed_seconds_valid",
    "body_not_truncated",
    "redirect_chain_absent",
    "request_method_bound",
    "http_status_success",
    "redirect_not_followed",
    "request_url_bound",
    "body_sha256_matches",
    "nonempty_document",
    "pdf_magic_valid",
    "content_length_matches",
    "content_type_compatible",
    "pdf_structure_valid",
)
_OFFICIAL_HTTP_ENVELOPE_KEYS = frozenset(
    {
        "schema_version",
        "url",
        "method",
        "status_code",
        "response_headers",
        "body_base64",
        "body_sha256",
        "elapsed_seconds",
        "redirect_followed",
    }
)

_LOCKED_DOCUMENTS: tuple[
    tuple[str, str, str, tuple[str, ...], str], ...
] = (
    (
        "1205690369",
        "2018-12-26",
        "https://static.cninfo.com.cn/finalpage/2018-12-26/1205690369.PDF",
        ("000022", "001872"),
        "security_code_identity_candidate",
    ),
    (
        "1207164397",
        "2019-12-16",
        "https://static.cninfo.com.cn/finalpage/2019-12-16/1207164397.PDF",
        ("000043", "001914"),
        "security_code_identity_candidate",
    ),
    (
        "1204831387",
        "2018-04-28",
        "https://static.cninfo.com.cn/finalpage/2018-04-28/1204831387.PDF",
        ("600680",),
        "suspension_state_candidate",
    ),
    (
        "1204983113",
        "2018-05-23",
        "https://static.cninfo.com.cn/finalpage/2018-05-23/1204983113.PDF",
        ("600680",),
        "security_lifecycle_candidate",
    ),
    (
        "1206282885",
        "2019-05-18",
        "https://static.cninfo.com.cn/finalpage/2019-05-18/1206282885.PDF",
        ("600680",),
        "security_lifecycle_candidate",
    ),
)
POPULATION_ROOT = (
    "58e2cdfe4cfb8f71fced3955b44a2928a570fc41004af12256996823eaacde4a"
)
REQUEST_PLAN_HASH = (
    "8fdb2a82225f2e810feb36e86fbf14643e664b8e9df46d11f1984886b8c986ad"
)


class CNINFOSecurityIdentityLifecycleDocumentTransport:
    """Apply strict official-envelope and PDF checks to the locked requests."""

    def __init__(
        self,
        *,
        minimum_delay_seconds: float,
        transport: BackfillTransport | None = None,
    ) -> None:
        self.minimum_delay_seconds = minimum_delay_seconds
        self._transport: BackfillTransport = transport or OfficialHttpProbeTransport(
            minimum_delay_seconds=minimum_delay_seconds,
            max_response_bytes=CNINFO_DOCUMENT_BODY_MAX_BYTES,
        )

    def __call__(
        self,
        request: ProviderProbeRequest,
        timeout_seconds: float,
    ) -> ProviderProbeObservation:
        _population, locked_requests = _locked_plan_without_validation()
        expected = {
            item.request_id: item.semantic() for item in locked_requests
        }.get(request.request_id)
        if expected is None or request.semantic() != expected:
            raise ValueError(
                "cninfo_security_identity_lifecycle_plan_exact_closure_invalid"
            )
        probe_request = ProviderProbeRequest(
            **{
                **request.__dict__,
                "metadata": dict(request.metadata) | {"case": "cninfo_pdf"},
            }
        )
        observation = self._transport(probe_request, timeout_seconds)
        if observation.terminal_state == "error" or observation.status_code != 200:
            return observation
        try:
            official = _decode_exact_json_object(observation.raw_payload)
            encoded_body = official.get("body_base64")
            if type(encoded_body) is not str:
                raise ValueError("official_envelope_body_base64_string_required")
            body = base64.b64decode(
                encoded_body,
                validate=True,
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return ProviderProbeObservation(
                terminal_state="error",
                raw_payload=observation.raw_payload,
                row_count=None,
                status_code=observation.status_code,
                error_code="cninfo_security_lifecycle_http_envelope_invalid",
                diagnostics=dict(observation.diagnostics)
                | {"envelope_error_type": type(exc).__name__},
                checks={"http_envelope_schema_exact": False},
                transport_exchange_count=observation.transport_exchange_count,
            )
        checks, _response_headers = _official_envelope_checks(
            official,
            body=body,
            request=request,
        )
        document_format = _document_format(
            body,
            adjunct_url=str(request.metadata.get("url") or ""),
        )
        blocked = _document_block_reason(body)
        accepted = all(checks.get(name) is True for name in _REQUIRED_CHECKS)
        return ProviderProbeObservation(
            terminal_state="positive" if accepted else "error",
            raw_payload=observation.raw_payload,
            row_count=1 if accepted else None,
            status_code=observation.status_code,
            error_code=(
                None
                if accepted
                else "cninfo_security_lifecycle_pdf_or_envelope_invalid"
            ),
            diagnostics=dict(observation.diagnostics)
            | {
                "document_format": document_format,
                "document_sha256": hashlib.sha256(body).hexdigest(),
                "document_block_reason": blocked,
            },
            checks=checks,
            transport_exchange_count=observation.transport_exchange_count,
        )


def build_cninfo_security_identity_lifecycle_plan(
) -> tuple[list[dict[str, Any]], list[ProviderProbeRequest]]:
    """Return the exact five-document population and provider request plan."""

    population, requests = _locked_plan_without_validation()
    validate_cninfo_security_identity_lifecycle_plan(population, requests)
    return population, requests


def validate_cninfo_security_identity_lifecycle_plan(
    population: Sequence[Mapping[str, Any]],
    requests: Sequence[ProviderProbeRequest],
) -> None:
    """Reject any deviation from the profile before transport is reachable."""

    expected_population, expected_requests = _locked_plan_without_validation()
    if (
        [dict(row) for row in population] != expected_population
        or [request.semantic() for request in requests]
        != [request.semantic() for request in expected_requests]
        or canonical_hash([dict(row) for row in population]) != POPULATION_ROOT
        or canonical_hash([request.semantic() for request in requests])
        != REQUEST_PLAN_HASH
    ):
        raise ValueError(
            "cninfo_security_identity_lifecycle_plan_exact_closure_invalid"
        )


def capture_cninfo_security_identity_lifecycle_documents(
    *,
    output_root: str | Path,
    signer: CaptureSigner,
    transport: CNINFOSecurityIdentityLifecycleDocumentTransport,
    permission_context_id: str = DEFAULT_PERMISSION_CONTEXT,
    minimum_delay_seconds: float = 2.0,
    timeout_seconds: float = 30.0,
    max_retries: int = 2,
) -> dict[str, Any]:
    """Run the exact plan through the signed physical-capture engine."""

    if (
        permission_context_id != DEFAULT_PERMISSION_CONTEXT
        or minimum_delay_seconds != LOCKED_MINIMUM_DELAY_SECONDS
        or timeout_seconds != LOCKED_TIMEOUT_SECONDS
        or max_retries != LOCKED_MAX_RETRIES
    ):
        raise ValueError("cninfo_security_lifecycle_contract_controls_invalid")
    if type(transport) is not CNINFOSecurityIdentityLifecycleDocumentTransport:
        raise ValueError("cninfo_security_lifecycle_transport_invalid")
    if transport.minimum_delay_seconds != minimum_delay_seconds:
        raise ValueError("cninfo_security_lifecycle_transport_delay_mismatch")
    if (
        type(transport._transport) is not OfficialHttpProbeTransport
        or transport._transport.minimum_delay_seconds
        != LOCKED_MINIMUM_DELAY_SECONDS
        or transport._transport.max_response_bytes
        != CNINFO_DOCUMENT_BODY_MAX_BYTES
    ):
        raise ValueError("cninfo_security_lifecycle_http_transport_invalid")
    capture_public_key_sha256 = _capture_public_key_hash(signer.public_key_pem)
    if capture_public_key_sha256 != APPROVED_CAPTURE_KEY_SHA256:
        raise ValueError("cninfo_security_lifecycle_capture_key_not_approved")
    population, requests = build_cninfo_security_identity_lifecycle_plan()
    validate_cninfo_security_identity_lifecycle_plan(population, requests)
    implementation_root = cninfo_security_identity_lifecycle_implementation_root()
    contract = _locked_contract(
        output_root=output_root,
        signer=signer,
        implementation_root=implementation_root,
    )
    return run_free_provider_backfill(
        contract,
        requests,
        transport=transport,
        signer=signer,
        normalizer=normalize_cninfo_security_identity_lifecycle_documents,
        runtime_implementation_root=implementation_root,
    )


def normalize_cninfo_security_identity_lifecycle_documents(
    run_root: Path,
    requests: Sequence[ProviderProbeRequest],
    terminal: Mapping[str, Mapping[str, Any]],
) -> Sequence[NormalizedArtifact]:
    population, _expected = _locked_plan_without_validation()
    validate_cninfo_security_identity_lifecycle_plan(population, requests)
    if set(terminal) != {request.request_id for request in requests}:
        raise ValueError("cninfo_security_lifecycle_terminal_closure_invalid")
    output, output_fd = _open_normalized_output(run_root)
    index_path = output / "document_index.jsonl"
    rows: list[dict[str, Any]] = []
    try:
        for request in requests:
            receipt = terminal[request.request_id]
            wrapper = _read_confined_json(
                run_root,
                str(receipt["raw_envelope_relative_path"]),
            )
            official, body = _decode_cninfo_official_http_envelope(
                wrapper,
                request=request,
                terminal=receipt,
            )
            raw_payload = base64.b64decode(
                str(wrapper.get("raw_payload_base64") or ""),
                validate=True,
            )
            exact_official = _decode_exact_json_object(raw_payload)
            if exact_official != official:
                raise ValueError(
                    "cninfo_security_lifecycle_http_envelope_decode_mismatch"
                )
            official = exact_official
            envelope_checks, response_headers = _official_envelope_checks(
                official,
                body=body,
                request=request,
            )
            document_format = _document_format(
                body,
                adjunct_url=str(request.metadata["url"]),
            )
            if (
                any(
                    envelope_checks.get(name) is not True
                    for name in _REQUIRED_CHECKS
                )
                or document_format != "pdf"
                or _document_block_reason(body) is not None
                or not _content_length_matches(
                    response_headers.get("content-length"), len(body)
                )
                or not _content_type_compatible(
                    "pdf", response_headers.get("content-type")
                )
                or not _document_structure_valid(
                    body,
                    document_format="pdf",
                    announcement_id=str(request.metadata["announcement_id"]),
                    announcement_time=None,
                )
            ):
                raise ValueError(
                    "cninfo_security_lifecycle_pdf_or_envelope_invalid:"
                    f"{request.metadata.get('announcement_id')}"
                )
            rows.append(
                {
                    "profile_id": PROFILE_ID,
                    "announcement_id": request.metadata["announcement_id"],
                    "announcement_date": request.metadata["announcement_date"],
                    "known_at_date": request.metadata["known_at_date"],
                    "known_at_semantics": request.metadata[
                        "known_at_semantics"
                    ],
                    "publication_time_proven": False,
                    "provider_origin_attested": False,
                    "capture_runtime_isolation_verified": False,
                    "data_admission_eligible": False,
                    "downstream_eligible": False,
                    "downstream_ineligible": True,
                    "url": request.url,
                    "subject_security_codes": request.metadata[
                        "subject_security_codes"
                    ],
                    "evidence_question": request.metadata["evidence_question"],
                    "document_format": "pdf",
                    "document_sha256": hashlib.sha256(body).hexdigest(),
                    "document_size_bytes": len(body),
                    "content_length": response_headers.get("content-length"),
                    "content_type": response_headers.get("content-type"),
                    "source_request_id": request.request_id,
                    "source_raw_envelope_sha256": receipt.get(
                        "raw_envelope_sha256"
                    ),
                    "source_raw_payload_sha256": wrapper.get(
                        "raw_payload_sha256"
                    ),
                    "pit_timeline_adjudicated": False,
                    "security_identity_adjudicated": False,
                    "security_lifecycle_adjudicated": False,
                }
            )
        payload = b"".join(
            json.dumps(
                row,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            + b"\n"
            for row in rows
        )
        _atomic_bytes_at(output_fd, index_path.name, payload)
        manifest = {
            "schema_version": NORMALIZATION_SCHEMA,
            "profile_id": PROFILE_ID,
            "document_count": len(rows),
            "document_index_sha256": hashlib.sha256(payload).hexdigest(),
            "request_plan_hash": REQUEST_PLAN_HASH,
            "population_root": POPULATION_ROOT,
            "known_at_semantics": KNOWN_AT_SEMANTICS,
            "known_at_binds_announcement_publication_date_only": True,
            "publication_time_proven": False,
            "provider_origin_attested": False,
            "capture_runtime_isolation_verified": False,
            "data_admission_eligible": False,
            "downstream_eligible": False,
            "downstream_ineligible": True,
            "pit_timeline_adjudicated": False,
            "security_identity_adjudicated": False,
            "security_lifecycle_adjudicated": False,
            "formal_data_admission_ready": False,
            "blockers": list(_NON_ADMISSION_BLOCKERS),
            "safety": {name: False for name in SAFETY_FLAGS},
        }
        manifest["content_hash"] = canonical_hash(manifest)
        manifest_bytes = (
            json.dumps(
                manifest,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ).encode()
            + b"\n"
        )
        _atomic_bytes_at(output_fd, "normalized_manifest.json", manifest_bytes)
    finally:
        os.close(output_fd)
    return (
        NormalizedArtifact(
            "cninfo_security_identity_lifecycle_document_index",
            "normalized/document_index.jsonl",
            len(rows),
        ),
        NormalizedArtifact(
            "normalized_manifest",
            "normalized/normalized_manifest.json",
            1,
        ),
    )


def validate_cninfo_security_identity_lifecycle_capture(
    path: str | Path,
    *,
    require_current_replay_compatible: bool = True,
) -> dict[str, Any]:
    """Verify signed bytes, authorized identity, and optionally current replay.

    A historical capture can remain cryptographically intact after this
    normalizer changes.  Callers inspecting history may opt out of current
    replay compatibility; governed consumption must keep the default.
    """

    capture = validate_free_provider_backfill(path)
    root = Path(str(capture["manifest_path"])).parent
    contract = read_json(root / "activity_contract.json")
    plan = read_json(root / "request_plan.json")
    if (
        capture.get("status") != "succeeded"
        or capture.get("publication_signature_verified") is not True
    ):
        raise ValueError("cninfo_security_lifecycle_capture_identity_invalid")
    current_replay_compatible = _validate_authorized_contract_closure(
        contract,
        plan,
    )
    integrity = capture | {
        "profile_id": PROFILE_ID,
        "signed_integrity_verified": True,
        "operator_contract_and_capture_key_authorization_verified": True,
        "operator_capture_authorization_semantics": (
            OPERATOR_AUTHORIZATION_SEMANTICS
        ),
        "approved_capture_key_verified": True,
        "approved_capture_key_semantics": (
            "local_operator_capture_signature_not_provider_origin"
        ),
        "provider_origin_attested": False,
        "capture_runtime_isolation_verified": False,
        "current_replay_compatible": current_replay_compatible,
        "normalized_replay_verified": False,
        "formal_data_admission_ready": False,
        "data_admission_eligible": False,
        "downstream_eligible": False,
        "downstream_ineligible": True,
        "safety": {name: False for name in SAFETY_FLAGS},
        "known_at_semantics": KNOWN_AT_SEMANTICS,
        "publication_time_proven": False,
        "pit_timeline_adjudicated": False,
        "security_identity_adjudicated": False,
        "security_lifecycle_adjudicated": False,
    }
    if not current_replay_compatible:
        if require_current_replay_compatible:
            raise ValueError(
                "cninfo_security_lifecycle_current_replay_incompatible"
            )
        return integrity | {
            "blockers": [
                *_NON_ADMISSION_BLOCKERS,
                "current_lifecycle_implementation_root_mismatch",
            ]
        }
    replayed, replay_root = replay_normalized_artifacts(
        capture["manifest_path"],
        normalizer=normalize_cninfo_security_identity_lifecycle_documents,
        required_roles=(
            "cninfo_security_identity_lifecycle_document_index",
            "normalized_manifest",
        ),
    )
    published_index = (root / "normalized/document_index.jsonl").read_bytes()
    published_manifest = (
        root / "normalized/normalized_manifest.json"
    ).read_bytes()
    if (
        replayed["cninfo_security_identity_lifecycle_document_index"]
        != published_index
        or replayed["normalized_manifest"] != published_manifest
    ):
        raise ValueError("cninfo_security_lifecycle_normalized_replay_mismatch")
    normalized = json.loads(published_manifest)
    normalized_semantic = {
        key: value for key, value in normalized.items() if key != "content_hash"
    }
    if (
        normalized.get("schema_version") != NORMALIZATION_SCHEMA
        or normalized.get("content_hash") != canonical_hash(normalized_semantic)
        or normalized.get("profile_id") != PROFILE_ID
        or normalized.get("document_count") != len(_LOCKED_DOCUMENTS)
        or normalized.get("formal_data_admission_ready") is not False
        or normalized.get("known_at_binds_announcement_publication_date_only")
        is not True
        or normalized.get("publication_time_proven") is not False
        or normalized.get("provider_origin_attested") is not False
        or normalized.get("capture_runtime_isolation_verified") is not False
        or normalized.get("data_admission_eligible") is not False
        or normalized.get("downstream_eligible") is not False
        or normalized.get("downstream_ineligible") is not True
        or normalized.get("pit_timeline_adjudicated") is not False
        or normalized.get("security_identity_adjudicated") is not False
        or normalized.get("security_lifecycle_adjudicated") is not False
        or normalized.get("safety")
        != {name: False for name in SAFETY_FLAGS}
        or normalized.get("blockers") != list(_NON_ADMISSION_BLOCKERS)
    ):
        raise ValueError("cninfo_security_lifecycle_normalized_manifest_invalid")
    return integrity | {
        "normalized_replay_verified": True,
        "normalized_replay_root": replay_root,
        "blockers": list(normalized.get("blockers") or ()),
    }


def cninfo_security_identity_lifecycle_implementation_root() -> str:
    return canonical_hash(
        {
            "module_sha256": sha256_file(Path(__file__)),
            "capture_engine_module_sha256": sha256_file(
                Path(capture_module.__file__)
            ),
            "http_backfill_module_sha256": sha256_file(
                Path(http_module.__file__)
            ),
            "provider_probe_module_sha256": sha256_file(
                Path(probe_module.__file__)
            ),
            "capture_engine_root": capture_module._capture_engine_root(),
            "capture_engine": inspect.getsource(
                capture_module.run_free_provider_backfill
            ),
            "official_transport": inspect.getsource(
                probe_module.OfficialHttpProbeTransport
            ),
            "no_redirect_handler": inspect.getsource(
                probe_module._NoRedirectHandler
            ),
            "safe_response_headers": inspect.getsource(
                probe_module._safe_response_headers
            ),
            "profile_transport": inspect.getsource(
                CNINFOSecurityIdentityLifecycleDocumentTransport
            ),
            "profile_normalizer": inspect.getsource(
                normalize_cninfo_security_identity_lifecycle_documents
            ),
            "locked_plan": inspect.getsource(_locked_plan_without_validation),
            "locked_budget": inspect.getsource(_locked_budget),
            "locked_contract": inspect.getsource(_locked_contract),
            "authorized_contract_closure": inspect.getsource(
                _validate_authorized_contract_closure
            ),
            "official_envelope_decoder": inspect.getsource(
                http_module._decode_cninfo_official_http_envelope
            ),
            "document_format": inspect.getsource(http_module._document_format),
            "document_structure": inspect.getsource(
                http_module._document_structure_valid
            ),
            "document_block_reason": inspect.getsource(
                _document_block_reason
            ),
            "content_length_matches": inspect.getsource(
                _content_length_matches
            ),
            "content_type_compatible": inspect.getsource(
                _content_type_compatible
            ),
            "official_envelope_checks": inspect.getsource(
                _official_envelope_checks
            ),
            "exact_json_object_decoder": inspect.getsource(
                _decode_exact_json_object
            ),
            "normalized_output_guard": inspect.getsource(
                _open_normalized_output
            ),
            "no_symlink_ancestry_guard": inspect.getsource(
                _assert_no_symlink_ancestry
            ),
            "confined_input_reader": inspect.getsource(_read_confined_json),
            "no_follow_artifact_writer": inspect.getsource(_atomic_bytes_at),
            "cninfo_document_body_max_bytes": CNINFO_DOCUMENT_BODY_MAX_BYTES,
            "activity_name": ACTIVITY_NAME,
            "http_adapter_id": HTTP_ADAPTER_ID,
            "authorization_policy_id": AUTHORIZATION_POLICY_ID,
            "operator_authorization_semantics": (
                OPERATOR_AUTHORIZATION_SEMANTICS
            ),
            "non_admission_blockers": list(_NON_ADMISSION_BLOCKERS),
            "approved_capture_key_sha256": APPROVED_CAPTURE_KEY_SHA256,
            "permission_context_id": DEFAULT_PERMISSION_CONTEXT,
            "locked_scope": LOCKED_SCOPE,
            "locked_allowed_hosts": list(LOCKED_ALLOWED_HOSTS),
            "locked_budget_value": _locked_budget(
                len(_LOCKED_DOCUMENTS)
            ).to_dict(),
            "contract_keys": sorted(_CONTRACT_KEYS),
            "adapter_identity_keys": sorted(_ADAPTER_IDENTITY_KEYS),
            "plan_keys": sorted(_PLAN_KEYS),
            "official_http_envelope_keys": sorted(
                _OFFICIAL_HTTP_ENVELOPE_KEYS
            ),
            "profile_id": PROFILE_ID,
            "locked_documents": [list(row) for row in _LOCKED_DOCUMENTS],
            "required_checks": list(_REQUIRED_CHECKS),
            "population_root": POPULATION_ROOT,
            "request_plan_hash": REQUEST_PLAN_HASH,
        }
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Capture the five locked CNINFO identity/lifecycle source PDFs."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--plan-only", action="store_true")
    mode.add_argument("--validate")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    if args.validate:
        try:
            payload = validate_cninfo_security_identity_lifecycle_capture(
                args.validate
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(
                json.dumps(
                    {"status": "blocked", "reason": str(exc)},
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 1
        print(_render(payload, pretty=args.pretty))
        return 0
    preview = {
        "schema_version": (
            "cninfo_security_identity_lifecycle_plan_preview_v1"
        ),
        "profile_id": PROFILE_ID,
        "population_count": len(_LOCKED_DOCUMENTS),
        "population_root": POPULATION_ROOT,
        "request_count": len(_LOCKED_DOCUMENTS),
        "request_plan_hash": REQUEST_PLAN_HASH,
        "network_called": False,
        "formal_data_admission_ready": False,
        "provider_origin_attested": False,
        "capture_runtime_isolation_verified": False,
        "data_admission_eligible": False,
        "downstream_eligible": False,
        "downstream_ineligible": True,
        "blockers": list(_NON_ADMISSION_BLOCKERS),
        "safety": {name: False for name in SAFETY_FLAGS},
    }
    if args.plan_only:
        print(_render(preview, pretty=args.pretty))
        return 0
    if not args.allow_network:
        print(
            _render(
                preview
                | {
                    "status": "blocked",
                    "reason": (
                        "free_provider_backfill_network_authority_missing"
                    ),
                },
                pretty=args.pretty,
            )
        )
        return 2
    signer = PersistentReceiptSigner.load(DEFAULT_CAPTURE_KEY)
    delay = 2.0
    transport = CNINFOSecurityIdentityLifecycleDocumentTransport(
        minimum_delay_seconds=delay
    )
    result = capture_cninfo_security_identity_lifecycle_documents(
        output_root=DEFAULT_OUTPUT_ROOT,
        signer=signer,
        transport=transport,
        minimum_delay_seconds=delay,
        timeout_seconds=30.0,
        max_retries=2,
    )
    print(_render(result, pretty=args.pretty))
    return 0 if result.get("status") == "succeeded" else 1


def _render(value: Mapping[str, Any], *, pretty: bool) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2 if pretty else None,
        sort_keys=True,
    )


def _locked_plan_without_validation(
) -> tuple[list[dict[str, Any]], list[ProviderProbeRequest]]:
    population: list[dict[str, Any]] = []
    requests: list[ProviderProbeRequest] = []
    for announcement_id, date, url, subject_codes, question in _LOCKED_DOCUMENTS:
        row = {
            "profile_id": PROFILE_ID,
            "announcement_id": announcement_id,
            "announcement_date": date,
            "known_at_date": date,
            "known_at_semantics": KNOWN_AT_SEMANTICS,
            "publication_time_proven": False,
            "provider_origin_attested": False,
            "capture_runtime_isolation_verified": False,
            "data_admission_eligible": False,
            "downstream_eligible": False,
            "downstream_ineligible": True,
            "url": url,
            "subject_security_codes": list(subject_codes),
            "evidence_question": question,
            "pit_timeline_adjudicated": False,
            "security_identity_adjudicated": False,
            "security_lifecycle_adjudicated": False,
        }
        population.append(row)
        requests.append(
            ProviderProbeRequest(
                request_id=(
                    "cninfo_security_identity_lifecycle_document_"
                    f"{announcement_id}"
                ),
                provider="cninfo",
                endpoint="security_identity_lifecycle_official_document",
                method="GET",
                url=url,
                headers={
                    "Referer": "https://www.cninfo.com.cn/",
                    "User-Agent": USER_AGENT,
                },
                disposition="bounded_backfill",
                evidence_semantics="official_http_response_envelope",
                expected_terminal_states=("positive",),
                required_checks=_REQUIRED_CHECKS,
                metadata={
                    "case": "cninfo_security_identity_lifecycle_document",
                    "source_provider": "cninfo_official_archive",
                    "source_document_kind": "official_announcement_pdf",
                    **row,
                },
            )
        )
    return population, requests


def _locked_budget(request_count: int) -> BackfillResourceBudget:
    if request_count != len(_LOCKED_DOCUMENTS):
        raise ValueError("cninfo_security_lifecycle_request_count_invalid")
    maximum_attempts = request_count * (LOCKED_MAX_RETRIES + 1)
    return BackfillResourceBudget(
        max_requests=maximum_attempts,
        max_wire_exchanges=maximum_attempts,
        max_response_bytes=LOCKED_MAX_RESPONSE_BYTES,
        max_total_response_bytes=LOCKED_MAX_TOTAL_RESPONSE_BYTES,
        timeout_seconds=LOCKED_TIMEOUT_SECONDS,
        minimum_delay_seconds=LOCKED_MINIMUM_DELAY_SECONDS,
        max_retries=LOCKED_MAX_RETRIES,
    )


def _capture_public_key_hash(public_key_pem: bytes) -> str:
    try:
        decoded = public_key_pem.decode("ascii")
    except (AttributeError, UnicodeDecodeError) as exc:
        raise ValueError("cninfo_security_lifecycle_capture_key_invalid") from exc
    return canonical_hash(decoded)


def _locked_contract(
    *,
    output_root: str | Path,
    signer: CaptureSigner,
    implementation_root: str,
) -> FreeProviderBackfillContract:
    capture_public_key_sha256 = _capture_public_key_hash(signer.public_key_pem)
    if capture_public_key_sha256 != APPROVED_CAPTURE_KEY_SHA256:
        raise ValueError("cninfo_security_lifecycle_capture_key_not_approved")
    if _HEX_64.fullmatch(implementation_root) is None:
        raise ValueError("cninfo_security_lifecycle_implementation_root_invalid")
    return FreeProviderBackfillContract(
        activity_name=ACTIVITY_NAME,
        provider="cninfo",
        output_root=output_root,
        permission_context_id=DEFAULT_PERMISSION_CONTEXT,
        population_root=POPULATION_ROOT,
        capture_public_key_sha256=capture_public_key_sha256,
        capture_public_key_pem_b64=base64.b64encode(
            signer.public_key_pem
        ).decode("ascii"),
        scope_start=LOCKED_SCOPE["date_start"],
        scope_end=LOCKED_SCOPE["date_end"],
        request_start=LOCKED_SCOPE["request_start"],
        request_end=LOCKED_SCOPE["request_end"],
        allowed_hosts=LOCKED_ALLOWED_HOSTS,
        budget=_locked_budget(len(_LOCKED_DOCUMENTS)),
        adapter_identity={
            "adapter": ADAPTER_ID,
            "http": HTTP_ADAPTER_ID,
            "implementation_root": implementation_root,
            "profile_id": PROFILE_ID,
            "request_plan_hash": REQUEST_PLAN_HASH,
            "known_at_semantics": KNOWN_AT_SEMANTICS,
            "pit_timeline_adjudicated": "false",
            "authorization_policy": AUTHORIZATION_POLICY_ID,
        },
        source_profile_id=PROFILE_ID,
    )


def _validate_authorized_contract_closure(
    contract: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> bool:
    """Validate authorization and return current implementation compatibility."""

    population, expected_requests = _locked_plan_without_validation()
    expected_request_rows = [request.semantic() for request in expected_requests]
    adapter = contract.get("adapter_identity")
    encoded_key = contract.get("capture_public_key_pem_b64")
    try:
        public_key = base64.b64decode(encoded_key, validate=True)
        public_key_hash = _capture_public_key_hash(public_key)
    except (TypeError, ValueError):
        public_key_hash = ""
    authorized_identity = (
        type(contract) is dict
        and set(contract) == _CONTRACT_KEYS
        and type(plan) is dict
        and set(plan) == _PLAN_KEYS
        and contract.get("schema_version") == capture_module.CONTRACT_SCHEMA
        and contract.get("activity_name") == ACTIVITY_NAME
        and contract.get("provider") == "cninfo"
        and type(contract.get("output_namespace_id")) is str
        and _HEX_64.fullmatch(contract["output_namespace_id"]) is not None
        and contract.get("permission_context_id") == DEFAULT_PERMISSION_CONTEXT
        and contract.get("population_root") == POPULATION_ROOT
        and canonical_hash(population) == POPULATION_ROOT
        and contract.get("capture_public_key_sha256")
        == APPROVED_CAPTURE_KEY_SHA256
        and public_key_hash == APPROVED_CAPTURE_KEY_SHA256
        and contract.get("scope") == LOCKED_SCOPE
        and contract.get("allowed_hosts") == list(LOCKED_ALLOWED_HOSTS)
        and contract.get("budget")
        == _locked_budget(len(_LOCKED_DOCUMENTS)).to_dict()
        and type(adapter) is dict
        and set(adapter) == _ADAPTER_IDENTITY_KEYS
        and adapter.get("adapter") == ADAPTER_ID
        and adapter.get("http") == HTTP_ADAPTER_ID
        and adapter.get("profile_id") == PROFILE_ID
        and adapter.get("request_plan_hash") == REQUEST_PLAN_HASH
        and adapter.get("known_at_semantics") == KNOWN_AT_SEMANTICS
        and adapter.get("pit_timeline_adjudicated") == "false"
        and adapter.get("authorization_policy") == AUTHORIZATION_POLICY_ID
        and type(adapter.get("implementation_root")) is str
        and _HEX_64.fullmatch(adapter["implementation_root"]) is not None
        and contract.get("source_profile_id") == PROFILE_ID
        and contract.get("mode") == "signed_raw_provider_capture"
        and contract.get("capture_before_normalization") is True
        and contract.get("old_lake_mutated") is False
        and contract.get("safety") == {name: False for name in SAFETY_FLAGS}
        and plan.get("schema_version") == capture_module.PLAN_SCHEMA
        and plan.get("request_plan_hash") == REQUEST_PLAN_HASH
        and plan.get("requests") == expected_request_rows
        and canonical_hash(expected_request_rows) == REQUEST_PLAN_HASH
    )
    if not authorized_identity:
        raise ValueError("cninfo_security_lifecycle_capture_identity_invalid")
    return (
        adapter["implementation_root"]
        == cninfo_security_identity_lifecycle_implementation_root()
    )


def _official_envelope_checks(
    official: Mapping[str, Any],
    *,
    body: bytes,
    request: ProviderProbeRequest,
) -> tuple[dict[str, bool], dict[str, str]]:
    response_headers_raw = official.get("response_headers")
    response_headers_shape_exact = type(response_headers_raw) is dict and all(
        type(key) is str and type(value) is str
        for key, value in response_headers_raw.items()
    )
    if response_headers_shape_exact:
        response_headers_shape_exact = len(
            {key.lower() for key in response_headers_raw}
        ) == len(response_headers_raw)
    response_headers = (
        {key.lower(): value for key, value in response_headers_raw.items()}
        if response_headers_shape_exact
        else {}
    )
    elapsed_seconds = official.get("elapsed_seconds")
    elapsed_seconds_valid = (
        type(elapsed_seconds) in {int, float}
        and math.isfinite(float(elapsed_seconds))
        and float(elapsed_seconds) >= 0
    )
    document_format = _document_format(
        body,
        adjunct_url=str(request.metadata.get("url") or ""),
    )
    blocked = _document_block_reason(body)
    checks = {
        "http_envelope_schema_exact": (
            type(official) is dict
            and set(official) == _OFFICIAL_HTTP_ENVELOPE_KEYS
            and official.get("schema_version")
            == "official_http_probe_envelope_v1"
            and type(official.get("url")) is str
            and type(official.get("method")) is str
            and type(official.get("status_code")) is int
            and response_headers_shape_exact
            and type(official.get("body_base64")) is str
            and type(official.get("body_sha256")) is str
            and _HEX_64.fullmatch(official["body_sha256"]) is not None
            and elapsed_seconds_valid
            and type(official.get("redirect_followed")) is bool
        ),
        "response_headers_shape_exact": response_headers_shape_exact,
        "elapsed_seconds_valid": elapsed_seconds_valid,
        "body_not_truncated": "body_truncated" not in official,
        "redirect_chain_absent": "redirect_chain" not in official,
        "request_method_bound": official.get("method") == "GET",
        "http_status_success": (
            type(official.get("status_code")) is int
            and official.get("status_code") == 200
        ),
        "redirect_not_followed": official.get("redirect_followed") is False,
        "request_url_bound": str(official.get("url") or "") == request.url,
        "body_sha256_matches": (
            type(official.get("body_sha256")) is str
            and official["body_sha256"] == hashlib.sha256(body).hexdigest()
        ),
        "nonempty_document": bool(body),
        "pdf_magic_valid": document_format == "pdf" and blocked is None,
        "content_length_matches": _content_length_matches(
            response_headers.get("content-length"), len(body)
        ),
        "content_type_compatible": _content_type_compatible(
            "pdf", response_headers.get("content-type")
        ),
        "pdf_structure_valid": _document_structure_valid(
            body,
            document_format="pdf",
            announcement_id=str(request.metadata.get("announcement_id") or ""),
            announcement_time=None,
        ),
    }
    return checks, response_headers


def _decode_exact_json_object(payload: bytes) -> dict[str, Any]:
    def no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(
                    "cninfo_security_lifecycle_duplicate_json_key"
                )
            value[key] = item
        return value

    try:
        value = json.loads(payload, object_pairs_hook=no_duplicate_keys)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(
            "cninfo_security_lifecycle_json_object_invalid"
        ) from exc
    if type(value) is not dict:
        raise ValueError("cninfo_security_lifecycle_json_object_required")
    return value


def _assert_no_symlink_ancestry(path: Path) -> None:
    absolute = path if path.is_absolute() else Path.cwd() / path
    for candidate in (absolute, *absolute.parents):
        try:
            status = os.lstat(candidate)
        except OSError as exc:
            raise ValueError(
                "cninfo_security_lifecycle_path_ancestry_invalid"
            ) from exc
        if stat.S_ISLNK(status.st_mode):
            raise ValueError(
                "cninfo_security_lifecycle_path_symlink_forbidden"
            )


def _read_confined_json(run_root: Path, relative_path: str) -> dict[str, Any]:
    relative = Path(relative_path)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError("cninfo_security_lifecycle_input_path_invalid")
    _assert_no_symlink_ancestry(run_root)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY
    file_flags = os.O_RDONLY | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
        file_flags |= os.O_NOFOLLOW
    descriptors: list[int] = []
    file_descriptor: int | None = None
    try:
        descriptors.append(os.open(run_root, directory_flags))
        for component in relative.parts[:-1]:
            descriptors.append(
                os.open(component, directory_flags, dir_fd=descriptors[-1])
            )
        file_descriptor = os.open(
            relative.parts[-1],
            file_flags,
            dir_fd=descriptors[-1],
        )
        file_status = os.fstat(file_descriptor)
        if (
            not stat.S_ISREG(file_status.st_mode)
            or file_status.st_size > LOCKED_MAX_RESPONSE_BYTES * 2
        ):
            raise ValueError("cninfo_security_lifecycle_input_file_invalid")
        with os.fdopen(file_descriptor, "rb", closefd=True) as handle:
            file_descriptor = None
            payload = handle.read()
    except OSError as exc:
        raise ValueError("cninfo_security_lifecycle_input_path_invalid") from exc
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    return _decode_exact_json_object(payload)


def _open_normalized_output(run_root: Path) -> tuple[Path, int]:
    _assert_no_symlink_ancestry(run_root)
    try:
        root_status = os.lstat(run_root)
    except OSError as exc:
        raise ValueError("cninfo_security_lifecycle_run_root_invalid") from exc
    if not stat.S_ISDIR(root_status.st_mode) or stat.S_ISLNK(root_status.st_mode):
        raise ValueError("cninfo_security_lifecycle_run_root_invalid")
    output = run_root / "normalized"
    try:
        os.mkdir(output, mode=0o700)
    except FileExistsError:
        pass
    try:
        output_status = os.lstat(output)
    except OSError as exc:
        raise ValueError("cninfo_security_lifecycle_normalized_root_invalid") from exc
    if not stat.S_ISDIR(output_status.st_mode) or stat.S_ISLNK(
        output_status.st_mode
    ):
        raise ValueError("cninfo_security_lifecycle_normalized_root_invalid")
    allowed = {"document_index.jsonl", "normalized_manifest.json"}
    for child in output.iterdir():
        child_status = os.lstat(child)
        if child.name not in allowed or not stat.S_ISREG(child_status.st_mode):
            raise ValueError("cninfo_security_lifecycle_normalized_closure_invalid")
    flags = os.O_RDONLY | os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(output, flags)
    except OSError as exc:
        raise ValueError("cninfo_security_lifecycle_normalized_root_invalid") from exc
    descriptor_status = os.fstat(descriptor)
    current_status = os.stat(output, follow_symlinks=False)
    if (
        descriptor_status.st_dev != current_status.st_dev
        or descriptor_status.st_ino != current_status.st_ino
    ):
        os.close(descriptor)
        raise ValueError("cninfo_security_lifecycle_normalized_root_changed")
    return output, descriptor


def _atomic_bytes_at(directory_fd: int, name: str, payload: bytes) -> None:
    if Path(name).name != name or name in {"", ".", ".."}:
        raise ValueError("cninfo_security_lifecycle_artifact_name_invalid")
    temporary = f".{name}.{os.getpid()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, flags, 0o600, dir_fd=directory_fd)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = None
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(
            temporary,
            name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

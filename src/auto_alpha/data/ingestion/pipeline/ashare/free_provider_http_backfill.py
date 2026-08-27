"""CNINFO/CSI official-HTTP inventory and document acquisition plans.

The physical capture engine lives in :mod:`free_provider_backfill`.  This
module owns only official-site request geometry and deterministic replay.  It
uses month leaves so the CNINFO 100-page display boundary cannot silently
truncate an annual result.
"""

from __future__ import annotations

import argparse
import base64
import calendar
import hashlib
import inspect
import json
import math
import os
import re
import urllib.parse
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from auto_alpha.platform.artifacts.storage import canonical_hash, read_json, sha256_file
from auto_alpha.platform.governance.network.signing import PersistentReceiptSigner

from .free_provider_backfill import (
    BackfillResourceBudget,
    FreeProviderBackfillContract,
    NormalizedArtifact,
    _public_key_hash,
    _request_from_semantic,
    replay_normalized_artifacts,
    run_free_provider_backfill,
    validate_free_provider_backfill,
)
from .provider_probe import (
    ProviderProbeObservation,
    ProviderProbeRequest,
)
from . import run_provider_probe as run_provider_probe_module
from .run_provider_probe import (
    CNINFO_HEADERS,
    CNINFO_QUERY_URL,
    USER_AGENT,
    OfficialHttpProbeTransport,
)


LAKE_ROOT = Path("/home/lijunsi/data/auto-alpha/ashare_lake")
SCOPE_ROOT = (
    LAKE_ROOT
    / "staging/data_admission"
    / "dap_d785714ef1b912a20c0f19ca"
    / "research_20120101_20191231_asof_20191231"
)
DEFAULT_CAPTURE_KEY = (
    LAKE_ROOT / "governance/capture_keys/free_domestic_backfill_20260816.pem"
)
DEFAULT_PERMISSION_CONTEXT = (
    "human_authorization_20260816_free_domestic_missing_data_backfill_v1"
)
CNINFO_PAGE_SIZE = 30
CNINFO_MAX_PAGES_PER_LEAF = 100
CNINFO_DOCUMENT_BODY_MAX_BYTES = 96 * 1024 * 1024
CNINFO_SOURCE_ANCESTRY_SCHEMA = "cninfo_source_ancestry_v1"
CNINFO_SOURCE_BINDING_SCHEMA = "cninfo_source_binding_v1"
CNINFO_GOVERNANCE_QUALIFICATION_SCHEMA = (
    "cninfo_governance_qualification_v1"
)
CNINFO_HISTORICAL_IMPLEMENTATION_ROOTS = frozenset(
    {
        "35c27d2670d231ee07a6026a8e8d1d451b321f0837b047db26f3dcd87ae3c49e",
    }
)
CNINFO_SCOPE = {
    "date_start": "20120101",
    "date_end": "20191231",
    "request_start": "20110101",
    "request_end": "20191231",
}
# This is the already-running, ancestry-free 2011 document activity sealed
# before source ancestry became mandatory.  No other plan may use this escape.
CNINFO_LEGACY_2011_DOCUMENT_REQUEST_PLAN_HASH = (
    "b666f4b60a308e74f60747e560e3c28725a0e6f17b2d2d83d572033c03861172"
)
CNINFO_LEGACY_2011_DOCUMENT_ACTIVITY_ID = (
    "c8cf6651d00be2882877ff5f938000b351405b5fa0d31724b46d195de7ac89de"
)
CNINFO_LEGACY_2011_DOCUMENT_CONTRACT_ID = (
    "f958eab0b83d4045e746642d4792122893441196c8019b16f38324467dbd7cc4"
)
CNINFO_LEGACY_2011_DOCUMENT_INPUT_CAPTURE_HASH = (
    "69514a282f301d58508c0ae4a3b180a8148c6ec49e372c7457e3b6bff21604b8"
)
CNINFO_LEGACY_2011_DOCUMENT_IMPLEMENTATION_ROOT = (
    "85c0ff0718f21c5b0440f7e4c05d02b9dfc55964cd8690b8f039e710400baed9"
)
CNINFO_LEAF_KINDS = (
    (
        "st_delist",
        "category_tbclts_szsh;",
        "szse",
        "",
    ),
    (
        "corporate_actions",
        "category_qyfpxzcs_szsh;",
        "szse",
        "",
    ),
    (
        "suspensions_sh",
        "category_jgjg_tfp",
        "regulator",
        "jgjg_sh",
    ),
    (
        "suspensions_sz",
        "category_jgjg_tfp",
        "regulator",
        "jgjg_sz",
    ),
)
CNINFO_SUPPLEMENTAL_LEAF_KINDS = (
    ("corrections", "category_bcgz_szsh;", "szse", ""),
    ("rights_issues", "category_pg_szsh;", "szse", ""),
    ("initial_offerings", "category_sf_szsh;", "szse", ""),
    ("delisting_period", "category_tszlq_szsh;", "szse", ""),
    ("secondary_offerings", "category_zf_szsh;", "szse", ""),
    ("equity_changes", "category_gqbd_szsh;", "szse", ""),
    ("risk_warnings", "category_fxts_szsh;", "szse", ""),
)
CNINFO_LEAF_PROFILES = {
    "base": CNINFO_LEAF_KINDS,
    "supplemental": CNINFO_SUPPLEMENTAL_LEAF_KINDS,
}
# CNINFO caps list traversal at 100 pages and silently maps page 101 back to
# page 1.  These two observed month/category cells exceed that cap.  Replace
# each whole-month leaf with an exact, non-overlapping half-month partition;
# never increase the page limit or accept a truncated month.
CNINFO_STATIC_DATE_SPLITS = {
    "supplemental": {
        "secondary_offerings_201511": (
            ("d01_15", "2015-11-01", "2015-11-15"),
            ("d16_30", "2015-11-16", "2015-11-30"),
        ),
        "secondary_offerings_201512": (
            ("d01_15", "2015-12-01", "2015-12-15"),
            ("d16_31", "2015-12-16", "2015-12-31"),
        ),
    },
}
CNINFO_TRANSIENT_LIST_ERROR_MAP = {
    "http_status:404": (
        "transport_exception:RuntimeError:cninfo_list_transient_http_404"
    ),
}


class CNINFOArchiveTransport:
    """Scope reviewed transient status normalization to list requests."""

    def __init__(self, *, minimum_delay_seconds: float) -> None:
        self._transport = OfficialHttpProbeTransport(
            minimum_delay_seconds=minimum_delay_seconds
        )

    def __call__(
        self, request: ProviderProbeRequest, timeout_seconds: float
    ) -> ProviderProbeObservation:
        observation = self._transport(request, timeout_seconds)
        original_error = str(observation.error_code or "")
        normalized_error = CNINFO_TRANSIENT_LIST_ERROR_MAP.get(
            original_error
        )
        if (
            request.provider != "cninfo"
            or request.metadata.get("case") != "cninfo_list"
            or observation.terminal_state != "error"
            or observation.status_code != 404
            or normalized_error is None
        ):
            return observation
        return ProviderProbeObservation(
            terminal_state=observation.terminal_state,
            raw_payload=observation.raw_payload,
            row_count=observation.row_count,
            status_code=observation.status_code,
            error_code=normalized_error,
            diagnostics={
                **dict(observation.diagnostics),
                "transient_error_normalization": {
                    "adapter": type(self).__name__,
                    "original_error_code": original_error,
                    "normalized_error_code": normalized_error,
                    "retry_scope": "cninfo_list_only",
                },
            },
            checks=observation.checks,
            transport_exchange_count=observation.transport_exchange_count,
        )


class CNINFODocumentTransport:
    """Accept the official archive's PDF, HTML and JavaScript document forms."""

    def __init__(self, *, minimum_delay_seconds: float) -> None:
        self._transport = OfficialHttpProbeTransport(
            minimum_delay_seconds=minimum_delay_seconds,
            max_response_bytes=CNINFO_DOCUMENT_BODY_MAX_BYTES,
        )

    def __call__(
        self, request: ProviderProbeRequest, timeout_seconds: float
    ) -> ProviderProbeObservation:
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
            official = json.loads(observation.raw_payload)
            body = base64.b64decode(
                str(official.get("body_base64") or ""), validate=True
            )
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return ProviderProbeObservation(
                terminal_state="error",
                raw_payload=observation.raw_payload,
                row_count=None,
                status_code=observation.status_code,
                error_code="cninfo_document_http_envelope_invalid",
                diagnostics=dict(observation.diagnostics)
                | {"envelope_error_type": type(exc).__name__},
                checks={"http_envelope_decoded": False},
                transport_exchange_count=observation.transport_exchange_count,
            )
        document_format = _document_format(
            body,
            adjunct_url=str(request.metadata.get("adjunct_url") or ""),
        )
        blocked_document = _document_block_reason(body)
        response_headers = {
            str(key).lower(): str(value)
            for key, value in (official.get("response_headers") or {}).items()
        }
        content_length_matches = _content_length_matches(
            response_headers.get("content-length"), len(body)
        )
        content_type_compatible = _content_type_compatible(
            document_format, response_headers.get("content-type")
        )
        document_structure_valid = _document_structure_valid(
            body,
            document_format=document_format,
            announcement_id=str(request.metadata.get("announcement_id") or ""),
            announcement_time=request.metadata.get("announcement_time"),
        )
        adjunct_size_reasonable = _adjunct_size_reasonable(
            request.metadata.get("adjunct_size_kb"), len(body)
        )
        envelope_checks = {
            "http_envelope_schema_exact": official.get("schema_version")
            == "official_http_probe_envelope_v1",
            "request_method_bound": official.get("method") == "GET",
            "http_status_success": official.get("status_code") == 200,
            "redirect_not_followed": official.get("redirect_followed") is False,
            "request_url_bound": str(official.get("url") or "") == request.url,
            "body_sha256_matches": str(official.get("body_sha256") or "")
            == hashlib.sha256(body).hexdigest(),
        }
        accepted = bool(
            body
            and all(envelope_checks.values())
            and document_format
            and not blocked_document
            and content_length_matches
            and content_type_compatible
            and document_structure_valid
            and adjunct_size_reasonable
        )
        return ProviderProbeObservation(
            terminal_state="positive" if accepted else "error",
            raw_payload=observation.raw_payload,
            row_count=1 if accepted else None,
            status_code=observation.status_code,
            error_code=(
                None
                if accepted
                else "cninfo_document_waf_or_format_unknown"
            ),
            diagnostics=dict(observation.diagnostics)
            | {
                "document_format": document_format,
                "document_sha256": hashlib.sha256(body).hexdigest(),
                "document_block_reason": blocked_document,
                "waf_html_observed": blocked_document is not None,
                "content_length_matches": content_length_matches,
                "content_type_compatible": content_type_compatible,
                "document_structure_valid": document_structure_valid,
                "adjunct_size_reasonable": adjunct_size_reasonable,
                "actual_size_bytes": len(body),
                "declared_adjunct_size_kb": request.metadata.get("adjunct_size_kb"),
            },
            checks=envelope_checks
            | {
                "nonempty_document": bool(body),
                "recognized_document_format": document_format
                in {"pdf", "html", "javascript"}
                and blocked_document is None,
                "content_length_matches": content_length_matches,
                "content_type_compatible": content_type_compatible,
                "document_structure_valid": document_structure_valid,
                "adjunct_size_reasonable": adjunct_size_reasonable,
            },
            transport_exchange_count=observation.transport_exchange_count,
        )


def _decode_cninfo_official_http_envelope(
    wrapper: Mapping[str, Any],
    *,
    request: ProviderProbeRequest | Mapping[str, Any],
    terminal: Mapping[str, Any],
) -> tuple[dict[str, Any], bytes]:
    """Recompute the transport binding before any CNINFO bytes are consumed."""

    request_id = (
        request.request_id
        if isinstance(request, ProviderProbeRequest)
        else str(request.get("request_id") or "")
    )
    request_method = (
        request.method
        if isinstance(request, ProviderProbeRequest)
        else str(request.get("method") or "")
    ).upper()
    request_url = (
        request.url
        if isinstance(request, ProviderProbeRequest)
        else str(request.get("url") or "")
    )
    try:
        raw_payload = base64.b64decode(
            str(wrapper.get("raw_payload_base64") or ""),
            validate=True,
        )
        official = json.loads(raw_payload)
        if not isinstance(official, dict):
            raise ValueError("official_envelope_object_required")
        body = base64.b64decode(
            str(official.get("body_base64") or ""),
            validate=True,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("cninfo_official_http_envelope_invalid:decode") from exc
    official_status = official.get("status_code")
    terminal_status = terminal.get("status_code")
    if (
        wrapper.get("schema_version")
        != "free_provider_backfill_raw_envelope_v1"
        or str(wrapper.get("request_id") or "") != request_id
        or str(wrapper.get("raw_payload_sha256") or "")
        != hashlib.sha256(raw_payload).hexdigest()
        or official.get("schema_version")
        != "official_http_probe_envelope_v1"
        or str(official.get("method") or "") != request_method
        or str(official.get("url") or "") != request_url
        or isinstance(official_status, bool)
        or official_status != 200
        or isinstance(terminal_status, bool)
        or terminal_status != official_status
        or official.get("redirect_followed") is not False
        or str(official.get("body_sha256") or "")
        != hashlib.sha256(body).hexdigest()
    ):
        raise ValueError(
            f"cninfo_official_http_envelope_invalid:{request_id or 'missing'}"
        )
    return official, body


def _cninfo_direct_source(
    validated: Mapping[str, Any],
    *,
    expected_phase: str,
    leaf_profile: str,
) -> dict[str, Any]:
    root = Path(str(validated.get("manifest_path") or "")).parent
    source_contract = read_json(root / "activity_contract.json")
    adapter_identity = source_contract.get("adapter_identity") or {}
    source_adapter = str(adapter_identity.get("adapter") or "")
    implementation_root = str(adapter_identity.get("implementation_root") or "")
    source_scope = dict(source_contract.get("scope") or {})
    if (
        source_contract.get("provider") != "cninfo"
        or source_adapter != f"cninfo_{expected_phase}_signed_http_capture_v1"
        or source_scope != CNINFO_SCOPE
        or adapter_identity.get("leaf_profile") != leaf_profile
        or re.fullmatch(r"[0-9a-f]{64}", implementation_root) is None
    ):
        raise ValueError("cninfo_discovery_source_contract_invalid")
    signed = validated.get("publication_signature_verified") is True
    normalized_trusted = validated.get("normalized_artifacts_trusted") is True
    direct = {
        "source_capture_schema": validated.get("schema_version"),
        "source_generation_id": validated.get("generation_id"),
        "source_content_hash": validated.get("content_hash"),
        "source_contract_id": validated.get("contract_id"),
        "source_contract_content_hash": canonical_hash(source_contract),
        "source_provider": "cninfo",
        "source_phase": expected_phase,
        "source_adapter": source_adapter,
        "source_leaf_profile": leaf_profile,
        "source_scope": source_scope,
        "source_implementation_root": implementation_root,
        "source_publication_signature_verified": signed,
        "source_normalized_artifacts_trusted": normalized_trusted,
        "weak_source_ancestry": not (signed and normalized_trusted),
    }
    _validate_cninfo_direct_source(direct, leaf_profile=leaf_profile)
    return direct


def _cninfo_source_ancestry(
    *,
    source_stage: str,
    leaf_profile: str,
    direct_sources: Sequence[Mapping[str, Any]],
    upstream_ancestry: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    direct = sorted(
        (dict(row) for row in direct_sources),
        key=lambda row: (
            str(row.get("source_generation_id") or ""),
            str(row.get("source_content_hash") or ""),
        ),
    )
    upstream = sorted(
        (dict(row) for row in upstream_ancestry),
        key=lambda row: str(row.get("ancestry_root") or ""),
    )
    semantic = {
        "schema_version": CNINFO_SOURCE_ANCESTRY_SCHEMA,
        "source_stage": source_stage,
        "leaf_profile": leaf_profile,
        "direct_sources": direct,
        "upstream_ancestry": upstream,
        "weak_source_ancestry": any(
            row.get("weak_source_ancestry") is True for row in (*direct, *upstream)
        ),
    }
    ancestry = semantic | {"ancestry_root": canonical_hash(semantic)}
    _validate_cninfo_source_ancestry(
        ancestry,
        expected_stage=source_stage,
        expected_leaf_profile=leaf_profile,
    )
    return ancestry


def _validate_cninfo_direct_source(
    value: Mapping[str, Any],
    *,
    leaf_profile: str,
) -> None:
    phase = str(value.get("source_phase") or "")
    signed = value.get("source_publication_signature_verified")
    trusted = value.get("source_normalized_artifacts_trusted")
    content_hash = str(value.get("source_content_hash") or "")
    contract_id = str(value.get("source_contract_id") or "")
    if (
        set(value)
        != {
            "source_capture_schema",
            "source_generation_id",
            "source_content_hash",
            "source_contract_id",
            "source_contract_content_hash",
            "source_provider",
            "source_phase",
            "source_adapter",
            "source_leaf_profile",
            "source_scope",
            "source_implementation_root",
            "source_publication_signature_verified",
            "source_normalized_artifacts_trusted",
            "weak_source_ancestry",
        }
        or value.get("source_capture_schema")
        not in {
            "free_provider_backfill_capture_v1",
            "free_provider_backfill_capture_v2",
        }
        or value.get("source_provider") != "cninfo"
        or phase not in {"cninfo-discovery", "cninfo-inventory"}
        or value.get("source_adapter")
        != f"cninfo_{phase}_signed_http_capture_v1"
        or value.get("source_leaf_profile") != leaf_profile
        or value.get("source_scope") != CNINFO_SCOPE
        or not all(
            re.fullmatch(r"[0-9a-f]{64}", str(value.get(key) or ""))
            for key in (
                "source_content_hash",
                "source_contract_id",
                "source_contract_content_hash",
                "source_implementation_root",
            )
        )
        or value.get("source_generation_id")
        != f"free_provider_backfill_{content_hash[:24]}"
        or value.get("source_contract_content_hash") != contract_id
        or not isinstance(signed, bool)
        or not isinstance(trusted, bool)
        or value.get("weak_source_ancestry") is not (not (signed and trusted))
    ):
        raise ValueError("cninfo_source_ancestry_invalid")


def _validate_cninfo_source_ancestry(
    value: Any,
    *,
    expected_stage: str,
    expected_leaf_profile: str | None = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("cninfo_source_ancestry_invalid")
    leaf_profile = str(value.get("leaf_profile") or "")
    direct = value.get("direct_sources")
    upstream = value.get("upstream_ancestry")
    semantic = {key: item for key, item in value.items() if key != "ancestry_root"}
    if (
        set(value)
        != {
            "schema_version",
            "source_stage",
            "leaf_profile",
            "direct_sources",
            "upstream_ancestry",
            "weak_source_ancestry",
            "ancestry_root",
        }
        or value.get("schema_version") != CNINFO_SOURCE_ANCESTRY_SCHEMA
        or value.get("source_stage") != expected_stage
        or leaf_profile not in CNINFO_LEAF_PROFILES
        or (
            expected_leaf_profile is not None
            and leaf_profile != expected_leaf_profile
        )
        or not isinstance(direct, list)
        or not direct
        or not isinstance(upstream, list)
        or value.get("ancestry_root") != canonical_hash(semantic)
    ):
        raise ValueError("cninfo_source_ancestry_invalid")
    for row in direct:
        if not isinstance(row, Mapping):
            raise ValueError("cninfo_source_ancestry_invalid")
        _validate_cninfo_direct_source(row, leaf_profile=leaf_profile)
    if expected_stage == "discovery_capture_set":
        if upstream or any(
            row.get("source_phase") != "cninfo-discovery" for row in direct
        ):
            raise ValueError("cninfo_source_ancestry_invalid")
    elif expected_stage == "inventory_capture":
        if (
            len(direct) != 1
            or direct[0].get("source_phase") != "cninfo-inventory"
            or len(upstream) != 1
        ):
            raise ValueError("cninfo_source_ancestry_invalid")
        _validate_cninfo_source_ancestry(
            upstream[0],
            expected_stage="discovery_capture_set",
            expected_leaf_profile=leaf_profile,
        )
    else:
        raise ValueError("cninfo_source_ancestry_invalid")
    derived_weak = any(
        row.get("weak_source_ancestry") is True for row in [*direct, *upstream]
    )
    if value.get("weak_source_ancestry") is not derived_weak:
        raise ValueError("cninfo_source_ancestry_invalid")
    return dict(value)


def _cninfo_upstream_content_hashes(
    source_ancestry: Mapping[str, Any],
) -> list[str]:
    hashes: list[str] = []

    def visit(value: Mapping[str, Any]) -> None:
        for row in value.get("direct_sources") or ():
            content_hash = str(row.get("source_content_hash") or "")
            if re.fullmatch(r"[0-9a-f]{64}", content_hash) is None:
                raise ValueError("cninfo_source_ancestry_invalid")
            hashes.append(content_hash)
        for upstream in value.get("upstream_ancestry") or ():
            if not isinstance(upstream, Mapping):
                raise ValueError("cninfo_source_ancestry_invalid")
            visit(upstream)

    visit(source_ancestry)
    return sorted(hashes)


def _cninfo_source_binding(
    *,
    phase: str,
    input_capture_content_hash: str,
    source_ancestry: Mapping[str, Any],
    derivation: Mapping[str, Any],
) -> dict[str, Any]:
    expected_stage = {
        "cninfo-inventory": "discovery_capture_set",
        "cninfo-documents": "inventory_capture",
    }.get(phase)
    if expected_stage is None:
        raise ValueError("cninfo_source_binding_phase_invalid")
    ancestry = _validate_cninfo_source_ancestry(
        source_ancestry,
        expected_stage=expected_stage,
    )
    upstream_hashes = _cninfo_upstream_content_hashes(ancestry)
    semantic = {
        "schema_version": CNINFO_SOURCE_BINDING_SCHEMA,
        "phase": phase,
        "input_capture_content_hash": input_capture_content_hash,
        "source_ancestry_root": ancestry["ancestry_root"],
        "source_leaf_profile": ancestry["leaf_profile"],
        "upstream_content_hashes": upstream_hashes,
        "upstream_content_hashes_root": canonical_hash(upstream_hashes),
        "derivation": dict(derivation),
    }
    if re.fullmatch(r"[0-9a-f]{64}", input_capture_content_hash) is None:
        raise ValueError("cninfo_source_binding_invalid")
    return semantic | {"content_hash": canonical_hash(semantic)}


def _validate_cninfo_source_binding(
    value: Any,
    *,
    phase: str,
    source_ancestry: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("cninfo_source_binding_invalid")
    ancestry = _validate_cninfo_source_ancestry(
        source_ancestry,
        expected_stage=(
            "discovery_capture_set"
            if phase == "cninfo-inventory"
            else "inventory_capture"
        ),
    )
    semantic = {key: item for key, item in value.items() if key != "content_hash"}
    upstream_hashes = _cninfo_upstream_content_hashes(ancestry)
    if (
        set(value)
        != {
            "schema_version",
            "phase",
            "input_capture_content_hash",
            "source_ancestry_root",
            "source_leaf_profile",
            "upstream_content_hashes",
            "upstream_content_hashes_root",
            "derivation",
            "content_hash",
        }
        or value.get("schema_version") != CNINFO_SOURCE_BINDING_SCHEMA
        or value.get("phase") != phase
        or re.fullmatch(
            r"[0-9a-f]{64}",
            str(value.get("input_capture_content_hash") or ""),
        )
        is None
        or value.get("source_ancestry_root") != ancestry["ancestry_root"]
        or value.get("source_leaf_profile") != ancestry["leaf_profile"]
        or value.get("upstream_content_hashes") != upstream_hashes
        or value.get("upstream_content_hashes_root")
        != canonical_hash(upstream_hashes)
        or not isinstance(value.get("derivation"), Mapping)
        or value.get("content_hash") != canonical_hash(semantic)
    ):
        raise ValueError("cninfo_source_binding_invalid")
    return dict(value)


def _with_cninfo_source_binding(
    request: ProviderProbeRequest,
    source_ancestry: Mapping[str, Any],
    source_binding: Mapping[str, Any],
) -> ProviderProbeRequest:
    return ProviderProbeRequest(
        **{
            **request.__dict__,
            "metadata": dict(request.metadata)
            | {
                "source_ancestry": dict(source_ancestry),
                "source_binding": dict(source_binding),
            },
        }
    )


def _cninfo_request_source_evidence(
    requests: Sequence[ProviderProbeRequest],
    *,
    phase: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    ancestry_rows = [request.metadata.get("source_ancestry") for request in requests]
    binding_rows = [request.metadata.get("source_binding") for request in requests]
    if (
        not ancestry_rows
        or ancestry_rows[0] is None
        or any(row != ancestry_rows[0] for row in ancestry_rows)
        or not binding_rows
        or binding_rows[0] is None
        or any(row != binding_rows[0] for row in binding_rows)
    ):
        raise ValueError("cninfo_source_evidence_missing_or_mixed")
    expected_stage = (
        "discovery_capture_set"
        if phase == "cninfo-inventory"
        else "inventory_capture"
    )
    ancestry = _validate_cninfo_source_ancestry(
        ancestry_rows[0],
        expected_stage=expected_stage,
    )
    binding = _validate_cninfo_source_binding(
        binding_rows[0],
        phase=phase,
        source_ancestry=ancestry,
    )
    return ancestry, binding


def _cninfo_activity_context(
    run_root: Path,
    requests: Sequence[ProviderProbeRequest],
    *,
    expected_phase: str,
    expected_activity_id: str | None = None,
    require_activity_root: bool,
) -> dict[str, Any]:
    contract = read_json(run_root / "activity_contract.json")
    plan = read_json(run_root / "request_plan.json")
    request_rows = [request.semantic() for request in requests]
    request_plan_hash = canonical_hash(request_rows)
    contract_id = canonical_hash(contract)
    activity_id = canonical_hash(
        {"contract_id": contract_id, "request_plan_hash": request_plan_hash}
    )
    adapter_identity = contract.get("adapter_identity") or {}
    if (
        contract.get("schema_version") != "free_provider_backfill_contract_v2"
        or contract.get("provider") != "cninfo"
        or contract.get("scope") != CNINFO_SCOPE
        or adapter_identity.get("adapter")
        != f"cninfo_{expected_phase}_signed_http_capture_v1"
        or re.fullmatch(
            r"[0-9a-f]{64}",
            str(adapter_identity.get("implementation_root") or ""),
        )
        is None
        or plan.get("schema_version")
        != "free_provider_backfill_request_plan_v1"
        or plan.get("requests") != request_rows
        or plan.get("request_plan_hash") != request_plan_hash
        or (expected_activity_id is not None and activity_id != expected_activity_id)
        or (require_activity_root and run_root.name != activity_id)
    ):
        raise ValueError("cninfo_sealed_activity_context_invalid")
    return {
        "activity_id": activity_id,
        "contract_id": contract_id,
        "request_plan_hash": request_plan_hash,
        "contract": contract,
        "adapter_identity": dict(adapter_identity),
    }


def _validate_cninfo_context_source_binding(
    context: Mapping[str, Any],
    *,
    phase: str,
    source_ancestry: Mapping[str, Any],
    source_binding: Mapping[str, Any],
) -> None:
    adapter_identity = context.get("adapter_identity") or {}
    binding = _validate_cninfo_source_binding(
        source_binding,
        phase=phase,
        source_ancestry=source_ancestry,
    )
    if (
        adapter_identity.get("input_capture_content_hash")
        != binding["input_capture_content_hash"]
        or adapter_identity.get("source_binding_root") != binding["content_hash"]
        or adapter_identity.get("source_ancestry_root")
        != binding["source_ancestry_root"]
        or adapter_identity.get("source_upstream_content_hashes_root")
        != binding["upstream_content_hashes_root"]
        or adapter_identity.get("leaf_profile") != binding["source_leaf_profile"]
    ):
        raise ValueError("cninfo_contract_source_binding_invalid")


def _validate_cninfo_binding_derivation(
    source_ancestry: Mapping[str, Any],
    source_binding: Mapping[str, Any],
    *,
    phase: str,
    expected_implementation_root: str | None = None,
) -> None:
    derivation = dict(source_binding.get("derivation") or {})
    if phase == "cninfo-inventory":
        direct_hashes = sorted(
            str(row.get("source_content_hash") or "")
            for row in source_ancestry.get("direct_sources") or ()
        )
        expected_derivation = {
            "discovery_capture_content_hashes": direct_hashes,
        }
        expected_input = canonical_hash(
            {
                "leaf_profile": source_ancestry["leaf_profile"],
                **expected_derivation,
                "source_ancestry": dict(source_ancestry),
            }
        )
    elif phase == "cninfo-documents":
        direct = list(source_ancestry.get("direct_sources") or ())
        if len(direct) != 1:
            raise ValueError("cninfo_source_binding_derivation_invalid")
        expected_derivation = {
            "capture_content_hash": direct[0]["source_content_hash"],
            "normalized_replay_root": derivation.get("normalized_replay_root"),
            "implementation_root": expected_implementation_root,
        }
        if (
            re.fullmatch(
                r"[0-9a-f]{64}",
                str(expected_derivation["normalized_replay_root"] or ""),
            )
            is None
            or re.fullmatch(
                r"[0-9a-f]{64}",
                str(expected_implementation_root or ""),
            )
            is None
        ):
            raise ValueError("cninfo_source_binding_derivation_invalid")
        expected_input = canonical_hash(
            {
                **expected_derivation,
                "source_ancestry": dict(source_ancestry),
            }
        )
    else:
        raise ValueError("cninfo_source_binding_phase_invalid")
    if (
        derivation != expected_derivation
        or source_binding.get("input_capture_content_hash") != expected_input
    ):
        raise ValueError("cninfo_source_binding_derivation_invalid")


def build_cninfo_discovery_plan(
    include_leaf_ids: Sequence[str] | None = None,
    include_years: Sequence[int] | None = None,
    *,
    leaf_profile: str = "base",
) -> tuple[list[dict[str, str]], list[ProviderProbeRequest]]:
    selected = {str(value) for value in include_leaf_ids or ()}
    selected_years = {int(value) for value in include_years or ()}
    leaves = [
        leaf
        for leaf in _cninfo_month_leaves(leaf_profile)
        if (not selected or leaf["leaf_id"] in selected)
        and (
            not selected_years
            or int(str(leaf["date_start"])[:4]) in selected_years
        )
    ]
    if selected - {leaf["leaf_id"] for leaf in leaves}:
        raise ValueError("cninfo_discovery_leaf_filter_unknown")
    requests = [_cninfo_org_map_request()]
    requests.extend(_cninfo_leaf_request(leaf, page=1) for leaf in leaves)
    return leaves, requests


def build_cninfo_inventory_plan(
    discovery_captures: Sequence[str | Path],
    *,
    leaf_profile: str = "base",
) -> tuple[list[dict[str, Any]], list[ProviderProbeRequest], str]:
    if not discovery_captures:
        raise ValueError("cninfo_discovery_capture_missing")
    validated_rows = [
        validate_free_provider_backfill(path) for path in discovery_captures
    ]
    if any(row.get("status") != "succeeded" for row in validated_rows):
        raise ValueError("cninfo_discovery_capture_blocked")
    direct_sources = [
        _cninfo_direct_source(
            row,
            expected_phase="cninfo-discovery",
            leaf_profile=leaf_profile,
        )
        for row in validated_rows
    ]
    source_ancestry = _cninfo_source_ancestry(
        source_stage="discovery_capture_set",
        leaf_profile=leaf_profile,
        direct_sources=direct_sources,
    )
    captured: dict[str, dict[str, Any]] = {}
    for validated in validated_rows:
        for request_id, row in _captured_cninfo_pages(
            validated["manifest_path"]
        ).items():
            prior = captured.setdefault(request_id, row)
            if prior != row:
                raise ValueError(f"cninfo_discovery_duplicate_conflict:{request_id}")
    leaves = _cninfo_month_leaves(leaf_profile)
    expected_requests = {
        request.request_id: request
        for request in (
            _cninfo_org_map_request(),
            *(_cninfo_leaf_request(leaf, page=1) for leaf in leaves),
        )
    }
    captured_ids = set(captured)
    expected_ids = set(expected_requests)
    if "cninfo_security_org_map" not in captured_ids:
        raise ValueError("cninfo_discovery_org_map_missing")
    extra_ids = sorted(captured_ids - expected_ids)
    if extra_ids:
        raise ValueError(f"cninfo_discovery_leaf_extra:{extra_ids[0]}")
    missing_ids = sorted(expected_ids - captured_ids)
    if missing_ids:
        raise ValueError(f"cninfo_discovery_leaf_missing:{missing_ids[0]}")
    for request_id, expected in expected_requests.items():
        if captured[request_id]["request"] != expected.semantic():
            raise ValueError(f"cninfo_discovery_request_semantics_invalid:{request_id}")
    requests: list[ProviderProbeRequest] = [
        _with_source_ancestry(_cninfo_org_map_request(), source_ancestry)
    ]
    resolved: list[dict[str, Any]] = []
    for leaf in leaves:
        request_id = _cninfo_request_id(leaf, 1)
        capture = captured[request_id]["payload"]
        total = _nonnegative_int(capture.get("totalAnnouncement"))
        if total is None:
            raise ValueError(f"cninfo_discovery_total_invalid:{leaf['leaf_id']}")
        page_count = max(1, math.ceil(total / CNINFO_PAGE_SIZE))
        if page_count > CNINFO_MAX_PAGES_PER_LEAF:
            raise ValueError(
                f"cninfo_month_leaf_requires_finer_split:{leaf['leaf_id']}:{total}"
            )
        resolved_leaf = dict(leaf) | {
            "reported_total": total,
            "page_count": page_count,
        }
        resolved.append(resolved_leaf)
        requests.extend(
            _with_source_ancestry(
                _cninfo_leaf_request(resolved_leaf, page=page),
                source_ancestry,
            )
            for page in range(1, page_count + 1)
        )
    input_root = canonical_hash(
        {
            "leaf_profile": leaf_profile,
            "discovery_capture_content_hashes": sorted(
                str(row["content_hash"]) for row in validated_rows
            ),
            "source_ancestry": source_ancestry,
        }
    )
    source_binding = _cninfo_source_binding(
        phase="cninfo-inventory",
        input_capture_content_hash=input_root,
        source_ancestry=source_ancestry,
        derivation={
            "discovery_capture_content_hashes": sorted(
                str(row["content_hash"]) for row in validated_rows
            )
        },
    )
    requests = [
        _with_cninfo_source_binding(request, source_ancestry, source_binding)
        for request in requests
    ]
    return resolved, requests, input_root


def build_cninfo_document_plan(
    inventory_capture: str | Path,
    include_years: Sequence[int] | None = None,
) -> tuple[list[dict[str, Any]], list[ProviderProbeRequest], str]:
    validated = validate_free_provider_backfill(inventory_capture)
    if validated.get("status") != "succeeded":
        raise ValueError("cninfo_inventory_capture_blocked")
    source_root = Path(str(validated["manifest_path"])).parent
    source_contract = read_json(source_root / "activity_contract.json")
    source_adapter_identity = source_contract.get("adapter_identity") or {}
    leaf_profile = str(source_adapter_identity.get("leaf_profile") or "")
    if leaf_profile not in CNINFO_LEAF_PROFILES:
        raise ValueError("cninfo_document_source_contract_invalid")
    try:
        direct_source = _cninfo_direct_source(
            validated,
            expected_phase="cninfo-inventory",
            leaf_profile=leaf_profile,
        )
    except ValueError as exc:
        raise ValueError("cninfo_document_source_contract_invalid") from exc
    replayed, replay_root = _replay_cninfo_inventory_artifacts(
        validated["manifest_path"],
        required_roles=(
            "cninfo_announcement_inventory",
            "cninfo_page_coverage",
            "normalized_manifest",
        ),
    )
    normalized_manifest = json.loads(replayed["normalized_manifest"])
    normalized_semantic = {
        key: value
        for key, value in normalized_manifest.items()
        if key != "content_hash"
    }
    if (
        normalized_manifest.get("schema_version")
        != "cninfo_announcement_inventory_normalization_v2"
        or normalized_manifest.get("content_hash")
        != canonical_hash(normalized_semantic)
        or normalized_manifest.get("source_ancestry") is None
        or normalized_manifest.get("source_binding") is None
    ):
        raise ValueError("cninfo_document_source_ancestry_missing")
    upstream_ancestry = _validate_cninfo_source_ancestry(
        normalized_manifest["source_ancestry"],
        expected_stage="discovery_capture_set",
        expected_leaf_profile=leaf_profile,
    )
    inventory_binding = _validate_cninfo_source_binding(
        normalized_manifest["source_binding"],
        phase="cninfo-inventory",
        source_ancestry=upstream_ancestry,
    )
    _validate_cninfo_context_source_binding(
        _cninfo_activity_context(
            source_root,
            [
                _request_from_semantic(row)
                for row in read_json(source_root / "request_plan.json")["requests"]
            ],
            expected_phase="cninfo-inventory",
            expected_activity_id=str(validated.get("activity_id") or ""),
            require_activity_root=False,
        ),
        phase="cninfo-inventory",
        source_ancestry=upstream_ancestry,
        source_binding=inventory_binding,
    )
    _validate_cninfo_inventory_normalized_closure(
        normalized_manifest,
        replayed["cninfo_page_coverage"],
        leaf_profile=leaf_profile,
    )
    source_ancestry = _cninfo_source_ancestry(
        source_stage="inventory_capture",
        leaf_profile=leaf_profile,
        direct_sources=(direct_source,),
        upstream_ancestry=(upstream_ancestry,),
    )
    rows = [
        json.loads(line)
        for line in replayed["cninfo_announcement_inventory"].decode("utf-8").splitlines()
        if line.strip()
    ]
    _validate_inventory_announcement_dates(rows)
    selected_years = {int(value) for value in include_years or ()}
    if selected_years:
        rows = [
            row
            for row in rows
            if int(str(_announcement_date(row.get("announcement_time")))[:4])
            in selected_years
        ]
        if not rows:
            raise ValueError("cninfo_document_year_filter_empty")
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        announcement_id = str(row.get("announcement_id") or "")
        adjunct_url = str(row.get("adjunct_url") or "")
        if not announcement_id or not adjunct_url:
            raise ValueError("cninfo_announcement_document_identity_missing")
        prior = unique.setdefault(announcement_id, row)
        if str(prior.get("adjunct_url")) != adjunct_url:
            raise ValueError(f"cninfo_announcement_document_url_conflict:{announcement_id}")
    input_root = canonical_hash(
        {
            "capture_content_hash": validated["content_hash"],
            "normalized_replay_root": replay_root,
            "source_ancestry": source_ancestry,
            "implementation_root": _implementation_root(),
        }
    )
    source_binding = _cninfo_source_binding(
        phase="cninfo-documents",
        input_capture_content_hash=input_root,
        source_ancestry=source_ancestry,
        derivation={
            "capture_content_hash": validated["content_hash"],
            "normalized_replay_root": replay_root,
            "implementation_root": _implementation_root(),
        },
    )
    requests = [
        ProviderProbeRequest(
            request_id=f"cninfo_document_{announcement_id}",
            provider="cninfo",
            endpoint="announcement_document",
            method="GET",
            url=_cninfo_document_url(str(row["adjunct_url"])),
            headers={
                "Referer": "https://www.cninfo.com.cn/",
                "User-Agent": USER_AGENT,
            },
            disposition="bounded_backfill",
            evidence_semantics="official_http_response_envelope",
            expected_terminal_states=("positive",),
            required_checks=(
                "http_envelope_schema_exact",
                "request_method_bound",
                "http_status_success",
                "redirect_not_followed",
                "request_url_bound",
                "body_sha256_matches",
                "nonempty_document",
                "recognized_document_format",
                "content_length_matches",
                "content_type_compatible",
                "document_structure_valid",
                "adjunct_size_reasonable",
            ),
            metadata={
                "case": "cninfo_document",
                "announcement_id": announcement_id,
                "announcement_time": row.get("announcement_time"),
                "adjunct_url": row["adjunct_url"],
                "adjunct_size_kb": row.get("adjunct_size_kb"),
                "source_ancestry": source_ancestry,
                "source_binding": source_binding,
            },
        )
        for announcement_id, row in sorted(unique.items())
    ]
    return [unique[key] for key in sorted(unique)], requests, input_root


def normalize_cninfo_discovery(
    run_root: Path,
    requests: Sequence[ProviderProbeRequest],
    terminal: Mapping[str, Mapping[str, Any]],
) -> Sequence[NormalizedArtifact]:
    return _normalize_cninfo_pages(
        run_root,
        requests,
        terminal,
        require_full_page_chains=False,
    )


def normalize_cninfo_inventory(
    run_root: Path,
    requests: Sequence[ProviderProbeRequest],
    terminal: Mapping[str, Mapping[str, Any]],
) -> Sequence[NormalizedArtifact]:
    sealed_context = _cninfo_activity_context(
        run_root,
        requests,
        expected_phase="cninfo-inventory",
        require_activity_root=True,
    )
    return _normalize_cninfo_pages(
        run_root,
        requests,
        terminal,
        require_full_page_chains=True,
        sealed_context=sealed_context,
    )


def _cninfo_document_source_ancestry(
    requests: Sequence[ProviderProbeRequest],
    *,
    sealed_context: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    ancestry_rows = [request.metadata.get("source_ancestry") for request in requests]
    if ancestry_rows and all(row is None for row in ancestry_rows):
        adapter_identity = sealed_context.get("adapter_identity") or {}
        if (
            sealed_context.get("activity_id")
            == CNINFO_LEGACY_2011_DOCUMENT_ACTIVITY_ID
            and sealed_context.get("contract_id")
            == CNINFO_LEGACY_2011_DOCUMENT_CONTRACT_ID
            and sealed_context.get("request_plan_hash")
            == CNINFO_LEGACY_2011_DOCUMENT_REQUEST_PLAN_HASH
            and adapter_identity.get("input_capture_content_hash")
            == CNINFO_LEGACY_2011_DOCUMENT_INPUT_CAPTURE_HASH
            and adapter_identity.get("implementation_root")
            == CNINFO_LEGACY_2011_DOCUMENT_IMPLEMENTATION_ROOT
        ):
            return None, None
        raise ValueError("cninfo_document_source_ancestry_missing")
    source_ancestry, source_binding = _cninfo_request_source_evidence(
        requests,
        phase="cninfo-documents",
    )
    _validate_cninfo_context_source_binding(
        sealed_context,
        phase="cninfo-documents",
        source_ancestry=source_ancestry,
        source_binding=source_binding,
    )
    _validate_cninfo_binding_derivation(
        source_ancestry,
        source_binding,
        phase="cninfo-documents",
        expected_implementation_root=str(
            (sealed_context.get("adapter_identity") or {}).get(
                "implementation_root"
            )
            or ""
        ),
    )
    return source_ancestry, source_binding


def normalize_cninfo_documents(
    run_root: Path,
    requests: Sequence[ProviderProbeRequest],
    terminal: Mapping[str, Mapping[str, Any]],
    *,
    _sealed_context: Mapping[str, Any] | None = None,
) -> Sequence[NormalizedArtifact]:
    output = run_root / "normalized"
    output.mkdir(exist_ok=True)
    index_path = output / "document_index.jsonl"
    sealed_context = _sealed_context or _cninfo_activity_context(
        run_root,
        requests,
        expected_phase="cninfo-documents",
        require_activity_root=True,
    )
    source_ancestry, source_binding = _cninfo_document_source_ancestry(
        requests,
        sealed_context=sealed_context,
    )
    count = 0
    with index_path.open("wb") as handle:
        for request in requests:
            receipt = terminal[request.request_id]
            wrapper = read_json(run_root / str(receipt["raw_envelope_relative_path"]))
            official, body = _decode_cninfo_official_http_envelope(
                wrapper,
                request=request,
                terminal=receipt,
            )
            document_format = _document_format(
                body,
                adjunct_url=str(request.metadata["adjunct_url"]),
            )
            block_reason = _document_block_reason(body)
            response_headers = {
                str(key).lower(): str(value)
                for key, value in (official.get("response_headers") or {}).items()
            }
            if (
                document_format is None
                or block_reason is not None
                or not _content_length_matches(
                    response_headers.get("content-length"), len(body)
                )
                or not _content_type_compatible(
                    document_format, response_headers.get("content-type")
                )
                or not _document_structure_valid(
                    body,
                    document_format=document_format,
                    announcement_id=str(request.metadata.get("announcement_id") or ""),
                    announcement_time=request.metadata.get("announcement_time"),
                )
                or not _adjunct_size_reasonable(
                    request.metadata.get("adjunct_size_kb"), len(body)
                )
            ):
                raise ValueError(
                    "cninfo_document_format_or_block_page_invalid:"
                    f"{request.metadata.get('announcement_id')}"
                )
            row = {
                "announcement_id": request.metadata["announcement_id"],
                "announcement_time": request.metadata.get("announcement_time"),
                "adjunct_url": request.metadata["adjunct_url"],
                "document_format": document_format,
                "document_sha256": hashlib.sha256(body).hexdigest(),
                "document_size_bytes": len(body),
                "declared_adjunct_size_kb": request.metadata.get("adjunct_size_kb"),
                "source_request_id": request.request_id,
                "source_payload_sha256": wrapper["raw_payload_sha256"],
                "content_length": response_headers.get("content-length"),
                "content_type": response_headers.get("content-type"),
            }
            _write_jsonl_row(handle, row)
            count += 1
        handle.flush()
        os.fsync(handle.fileno())
    manifest_path = output / "normalized_manifest.json"
    manifest: dict[str, Any] = {
        "schema_version": (
            "cninfo_document_normalization_v2"
            if source_ancestry is not None
            else "cninfo_document_normalization_v1"
        ),
        "document_count": count,
        "document_index_sha256": sha256_file(index_path),
        "documents_extracted": False,
        "raw_capture_contains_exact_document_bytes": True,
        "pit_field_parsing_complete": False,
        "blockers": ["corporate_action_pdf_field_parser_not_run"],
    }
    if source_ancestry is not None:
        manifest["source_ancestry"] = dict(source_ancestry)
        manifest["source_binding"] = dict(source_binding or {})
        if source_ancestry["weak_source_ancestry"]:
            manifest["blockers"].append("weak_source_acquisition_ancestry")
    manifest["content_hash"] = canonical_hash(manifest)
    _atomic_json(manifest_path, manifest)
    return (
        NormalizedArtifact("cninfo_document_index", "normalized/document_index.jsonl", count),
        NormalizedArtifact("normalized_manifest", "normalized/normalized_manifest.json", 1),
    )


def _normalize_cninfo_pages(
    run_root: Path,
    requests: Sequence[ProviderProbeRequest],
    terminal: Mapping[str, Mapping[str, Any]],
    *,
    require_full_page_chains: bool,
    sealed_context: Mapping[str, Any] | None = None,
) -> Sequence[NormalizedArtifact]:
    output = run_root / "normalized"
    output.mkdir(exist_ok=True)
    source_ancestry: dict[str, Any] | None = None
    source_binding: dict[str, Any] | None = None
    if require_full_page_chains:
        if sealed_context is None:
            raise ValueError("cninfo_inventory_sealed_context_missing")
        source_ancestry, source_binding = _cninfo_request_source_evidence(
            requests,
            phase="cninfo-inventory",
        )
        _validate_cninfo_context_source_binding(
            sealed_context,
            phase="cninfo-inventory",
            source_ancestry=source_ancestry,
            source_binding=source_binding,
        )
        _validate_cninfo_binding_derivation(
            source_ancestry,
            source_binding,
            phase="cninfo-inventory",
        )
    elif any(
        request.metadata.get(key) is not None
        for request in requests
        for key in ("source_ancestry", "source_binding")
    ):
        raise ValueError("cninfo_discovery_source_ancestry_unexpected")
    inventory_path = output / "announcement_inventory.jsonl"
    coverage_path = output / "page_coverage.jsonl"
    conflicts_path = output / "conflicts.jsonl"
    pages_by_leaf: dict[str, list[tuple[ProviderProbeRequest, dict[str, Any], str]]] = defaultdict(list)
    org_map_count = 0
    org_map_requests: list[ProviderProbeRequest] = []
    for request in requests:
        wrapper = read_json(
            run_root / str(terminal[request.request_id]["raw_envelope_relative_path"])
        )
        _official, body_bytes = _decode_cninfo_official_http_envelope(
            wrapper,
            request=request,
            terminal=terminal[request.request_id],
        )
        body = json.loads(body_bytes)
        if request.metadata.get("case") == "cninfo_org_map":
            org_map_requests.append(request)
            rows = body.get("stockList") if isinstance(body, Mapping) else None
            if not isinstance(rows, list):
                rows = body if isinstance(body, list) else []
            org_map_count = len(rows)
            continue
        leaf_id = str(request.metadata.get("leaf_id") or "")
        pages_by_leaf[leaf_id].append((request, body, wrapper["raw_payload_sha256"]))

    if require_full_page_chains:
        _validate_cninfo_inventory_request_closure(
            requests=requests,
            org_map_requests=org_map_requests,
            pages_by_leaf=pages_by_leaf,
            source_ancestry=source_ancestry or {},
            source_binding=source_binding or {},
        )

    conflicts: list[dict[str, Any]] = []
    coverage_rows: list[dict[str, Any]] = []
    inventory: dict[str, dict[str, Any]] = {}
    for leaf_id, captured_pages in sorted(pages_by_leaf.items()):
        captured_pages.sort(key=lambda item: int(item[0].metadata["page"]))
        totals = {_nonnegative_int(item[1].get("totalAnnouncement")) for item in captured_pages}
        total = next(iter(totals)) if len(totals) == 1 else None
        expected_pages = max(1, math.ceil((total or 0) / CNINFO_PAGE_SIZE))
        ordinals = [int(item[0].metadata["page"]) for item in captured_pages]
        ids: set[str] = set()
        duplicate_ids: set[str] = set()
        for request, body, source_hash in captured_pages:
            rows = body.get("announcements") if isinstance(body, Mapping) else None
            reported_total = _nonnegative_int(body.get("totalAnnouncement"))
            if rows is None and reported_total == 0:
                rows = []
            elif not isinstance(rows, list):
                conflicts.append({"leaf_id": leaf_id, "reason": "announcement_list_shape"})
                rows = []
            for provider_row in rows:
                if not isinstance(provider_row, Mapping):
                    conflicts.append({"leaf_id": leaf_id, "reason": "announcement_row_shape"})
                    continue
                announcement_id = str(provider_row.get("announcementId") or "")
                if not announcement_id:
                    conflicts.append({"leaf_id": leaf_id, "reason": "announcement_id_missing"})
                    continue
                announcement_date = _announcement_date(
                    provider_row.get("announcementTime")
                )
                if (
                    announcement_date is None
                    or not str(request.metadata["date_start"])
                    <= announcement_date
                    <= str(request.metadata["date_end"])
                ):
                    conflicts.append(
                        {
                            "leaf_id": leaf_id,
                            "announcement_id": announcement_id,
                            "announcement_date": announcement_date,
                            "reason": "announcement_time_outside_leaf",
                        }
                    )
                    continue
                if announcement_id in ids:
                    duplicate_ids.add(announcement_id)
                ids.add(announcement_id)
                existing = inventory.get(announcement_id)
                normalized = {
                    "announcement_id": announcement_id,
                    "sec_code": provider_row.get("secCode"),
                    "sec_name": provider_row.get("secName"),
                    "org_id": provider_row.get("orgId"),
                    "announcement_title": provider_row.get("announcementTitle"),
                    "announcement_time": provider_row.get("announcementTime"),
                    "announcement_date": announcement_date,
                    "adjunct_url": provider_row.get("adjunctUrl"),
                    "adjunct_size_kb": provider_row.get("adjunctSize"),
                    "announcement_type": provider_row.get("announcementType"),
                    "column_id": provider_row.get("columnId"),
                    "matched_leaves": [leaf_id],
                    "source_request_ids": [request.request_id],
                    "source_payload_sha256": [source_hash],
                }
                if existing is None:
                    inventory[announcement_id] = normalized
                else:
                    identity_fields = (
                        "sec_code",
                        "org_id",
                        "announcement_title",
                        "announcement_time",
                        "adjunct_url",
                    )
                    if any(existing.get(key) != normalized.get(key) for key in identity_fields):
                        conflicts.append(
                            {
                                "leaf_id": leaf_id,
                                "announcement_id": announcement_id,
                                "reason": "cross_leaf_identity_conflict",
                            }
                        )
                    existing["matched_leaves"] = sorted(
                        set(existing["matched_leaves"]) | {leaf_id}
                    )
                    existing["source_request_ids"].append(request.request_id)
                    existing["source_payload_sha256"].append(source_hash)
        final_has_more = bool(captured_pages[-1][1].get("hasMore"))
        full_valid = (
            total is not None
            and not duplicate_ids
            and ordinals == list(range(1, expected_pages + 1))
            and len(ids) == total
            and final_has_more is False
        )
        if require_full_page_chains and not full_valid:
            conflicts.append(
                {
                    "leaf_id": leaf_id,
                    "reason": "page_chain_incomplete",
                    "reported_totals": sorted(value for value in totals if value is not None),
                    "expected_pages": expected_pages,
                    "captured_pages": ordinals,
                    "unique_ids": len(ids),
                    "duplicate_ids": sorted(duplicate_ids)[:20],
                    "final_has_more": final_has_more,
                }
            )
        coverage_rows.append(
            {
                "leaf_id": leaf_id,
                "reported_total": total,
                "expected_page_count": expected_pages,
                "captured_pages": ordinals,
                "unique_announcement_count": len(ids),
                "full_page_chain_valid": full_valid if require_full_page_chains else False,
            }
        )
    if require_full_page_chains and conflicts:
        raise ValueError(f"cninfo_inventory_page_chain_invalid:{conflicts[0]['reason']}")
    _atomic_jsonl(inventory_path, [inventory[key] for key in sorted(inventory)])
    _atomic_jsonl(coverage_path, coverage_rows)
    _atomic_jsonl(conflicts_path, conflicts)
    manifest_path = output / "normalized_manifest.json"
    manifest = {
        "schema_version": (
            "cninfo_announcement_inventory_normalization_v2"
            if source_ancestry is not None
            else "cninfo_announcement_inventory_normalization_v1"
        ),
        "require_full_page_chains": require_full_page_chains,
        "leaf_count": len(pages_by_leaf),
        "org_map_count": org_map_count,
        "org_map_request_count": len(org_map_requests),
        "announcement_count": len(inventory),
        "conflict_count": len(conflicts),
        "all_page_chains_valid": require_full_page_chains and not conflicts,
        "announcement_inventory_sha256": sha256_file(inventory_path),
        "page_coverage_sha256": sha256_file(coverage_path),
        "conflicts_sha256": sha256_file(conflicts_path),
        "pit_field_parsing_complete": False,
    }
    if source_ancestry is not None:
        manifest["source_ancestry"] = source_ancestry
        manifest["source_binding"] = source_binding
        if source_ancestry["weak_source_ancestry"]:
            manifest["blockers"] = ["weak_source_acquisition_ancestry"]
    manifest["content_hash"] = canonical_hash(manifest)
    _atomic_json(manifest_path, manifest)
    return (
        NormalizedArtifact("cninfo_announcement_inventory", "normalized/announcement_inventory.jsonl", len(inventory)),
        NormalizedArtifact("cninfo_page_coverage", "normalized/page_coverage.jsonl", len(coverage_rows)),
        NormalizedArtifact("conflicts", "normalized/conflicts.jsonl", len(conflicts)),
        NormalizedArtifact("normalized_manifest", "normalized/normalized_manifest.json", 1),
    )


def _validate_cninfo_inventory_request_closure(
    *,
    requests: Sequence[ProviderProbeRequest],
    org_map_requests: Sequence[ProviderProbeRequest],
    pages_by_leaf: Mapping[
        str,
        Sequence[tuple[ProviderProbeRequest, Mapping[str, Any], str]],
    ],
    source_ancestry: Mapping[str, Any],
    source_binding: Mapping[str, Any],
) -> None:
    leaf_profile = str(source_ancestry.get("leaf_profile") or "")
    leaves = _cninfo_month_leaves(leaf_profile)
    expected_by_id = {leaf["leaf_id"]: leaf for leaf in leaves}
    if (
        len(org_map_requests) != 1
        or set(pages_by_leaf) != set(expected_by_id)
        or len({request.request_id for request in requests}) != len(requests)
    ):
        raise ValueError("cninfo_inventory_request_closure_invalid")
    expected_org = _with_cninfo_source_binding(
        _cninfo_org_map_request(),
        source_ancestry,
        source_binding,
    )
    if org_map_requests[0].semantic() != expected_org.semantic():
        raise ValueError("cninfo_inventory_org_map_semantics_invalid")
    expected_request_count = 1
    for leaf_id, leaf in expected_by_id.items():
        captured = list(pages_by_leaf[leaf_id])
        totals = {
            _nonnegative_int(body.get("totalAnnouncement"))
            for _request, body, _source_hash in captured
        }
        if len(totals) != 1 or None in totals:
            raise ValueError(f"cninfo_inventory_total_invalid:{leaf_id}")
        total = next(iter(totals))
        page_count = max(1, math.ceil(int(total) / CNINFO_PAGE_SIZE))
        if page_count > CNINFO_MAX_PAGES_PER_LEAF:
            raise ValueError(f"cninfo_inventory_page_budget_invalid:{leaf_id}")
        actual_by_page: dict[int, ProviderProbeRequest] = {}
        for request, _body, _source_hash in captured:
            page = _nonnegative_int(request.metadata.get("page"))
            if page is None or page <= 0 or page in actual_by_page:
                raise ValueError(f"cninfo_inventory_page_identity_invalid:{leaf_id}")
            actual_by_page[page] = request
        if set(actual_by_page) != set(range(1, page_count + 1)):
            raise ValueError(f"cninfo_inventory_page_closure_invalid:{leaf_id}")
        resolved_leaf = dict(leaf) | {
            "reported_total": total,
            "page_count": page_count,
        }
        for page, actual in actual_by_page.items():
            expected = _with_cninfo_source_binding(
                _cninfo_leaf_request(resolved_leaf, page=page),
                source_ancestry,
                source_binding,
            )
            if actual.semantic() != expected.semantic():
                raise ValueError(
                    f"cninfo_inventory_request_semantics_invalid:{actual.request_id}"
                )
        expected_request_count += page_count
    if len(requests) != expected_request_count:
        raise ValueError("cninfo_inventory_request_count_invalid")


def _validate_cninfo_inventory_normalized_closure(
    normalized_manifest: Mapping[str, Any],
    coverage_payload: bytes,
    *,
    leaf_profile: str,
) -> None:
    expected_leaf_ids = {
        leaf["leaf_id"] for leaf in _cninfo_month_leaves(leaf_profile)
    }
    try:
        coverage = [
            json.loads(line)
            for line in coverage_payload.decode("utf-8").splitlines()
            if line.strip()
        ]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("cninfo_inventory_coverage_invalid") from exc
    by_leaf = {
        str(row.get("leaf_id") or ""): row
        for row in coverage
        if isinstance(row, Mapping)
    }
    if (
        len(by_leaf) != len(coverage)
        or set(by_leaf) != expected_leaf_ids
        or normalized_manifest.get("leaf_count") != len(expected_leaf_ids)
        or normalized_manifest.get("org_map_request_count") != 1
        or not isinstance(normalized_manifest.get("org_map_count"), int)
        or int(normalized_manifest.get("org_map_count") or 0) <= 0
        or normalized_manifest.get("conflict_count") != 0
        or normalized_manifest.get("all_page_chains_valid") is not True
    ):
        raise ValueError("cninfo_inventory_normalized_closure_invalid")
    for leaf_id, row in by_leaf.items():
        expected_page_count = _nonnegative_int(row.get("expected_page_count"))
        captured_pages = row.get("captured_pages")
        if (
            expected_page_count is None
            or expected_page_count <= 0
            or captured_pages != list(range(1, expected_page_count + 1))
            or row.get("full_page_chain_valid") is not True
        ):
            raise ValueError(
                f"cninfo_inventory_normalized_leaf_invalid:{leaf_id}"
            )


def _replay_cninfo_inventory_artifacts(
    path: str | Path,
    *,
    required_roles: Sequence[str],
) -> tuple[dict[str, bytes], str]:
    validated = validate_free_provider_backfill(path)
    source_root = Path(str(validated["manifest_path"])).parent
    plan = read_json(source_root / "request_plan.json")
    requests = [
        _request_from_semantic(row) for row in plan.get("requests") or ()
    ]
    sealed_context = _cninfo_activity_context(
        source_root,
        requests,
        expected_phase="cninfo-inventory",
        expected_activity_id=str(validated.get("activity_id") or ""),
        require_activity_root=False,
    )

    def replay_normalizer(
        run_root: Path,
        replay_requests: Sequence[ProviderProbeRequest],
        terminal: Mapping[str, Mapping[str, Any]],
    ) -> Sequence[NormalizedArtifact]:
        if [request.semantic() for request in replay_requests] != [
            request.semantic() for request in requests
        ]:
            raise ValueError("cninfo_inventory_replay_plan_invalid")
        return _normalize_cninfo_pages(
            run_root,
            replay_requests,
            terminal,
            require_full_page_chains=True,
            sealed_context=sealed_context,
        )

    return replay_normalized_artifacts(
        validated["manifest_path"],
        normalizer=replay_normalizer,
        required_roles=required_roles,
    )


def _replay_cninfo_document_artifacts(
    path: str | Path,
    *,
    required_roles: Sequence[str],
) -> tuple[dict[str, bytes], str]:
    validated = validate_free_provider_backfill(path)
    source_root = Path(str(validated["manifest_path"])).parent
    plan = read_json(source_root / "request_plan.json")
    requests = [
        _request_from_semantic(row) for row in plan.get("requests") or ()
    ]
    sealed_context = _cninfo_activity_context(
        source_root,
        requests,
        expected_phase="cninfo-documents",
        expected_activity_id=str(validated.get("activity_id") or ""),
        require_activity_root=False,
    )

    def replay_normalizer(
        run_root: Path,
        replay_requests: Sequence[ProviderProbeRequest],
        terminal: Mapping[str, Mapping[str, Any]],
    ) -> Sequence[NormalizedArtifact]:
        if [request.semantic() for request in replay_requests] != [
            request.semantic() for request in requests
        ]:
            raise ValueError("cninfo_document_replay_plan_invalid")
        return normalize_cninfo_documents(
            run_root,
            replay_requests,
            terminal,
            _sealed_context=sealed_context,
        )

    return replay_normalized_artifacts(
        validated["manifest_path"],
        normalizer=replay_normalizer,
        required_roles=required_roles,
    )


def _cninfo_month_leaves(leaf_profile: str = "base") -> list[dict[str, str]]:
    kinds = CNINFO_LEAF_PROFILES.get(leaf_profile)
    if kinds is None:
        raise ValueError("cninfo_leaf_profile_unknown")
    leaves: list[dict[str, str]] = []
    for year in range(2011, 2020):
        for month in range(1, 13):
            last_day = calendar.monthrange(year, month)[1]
            start = f"{year:04d}-{month:02d}-01"
            end = f"{year:04d}-{month:02d}-{last_day:02d}"
            for kind, category, column, plate in kinds:
                leaf_id = f"{kind}_{year:04d}{month:02d}"
                base = {
                    "leaf_id": leaf_id,
                    "kind": kind,
                    "category": category,
                    "column": column,
                    "plate": plate,
                    "date_start": start,
                    "date_end": end,
                }
                splits = CNINFO_STATIC_DATE_SPLITS.get(
                    leaf_profile, {}
                ).get(leaf_id)
                if splits is None:
                    leaves.append(base)
                    continue
                expected_start = start
                suffixes: set[str] = set()
                for suffix, date_start, date_end in splits:
                    try:
                        parsed_start = datetime.strptime(
                            date_start, "%Y-%m-%d"
                        )
                        parsed_end = datetime.strptime(date_end, "%Y-%m-%d")
                    except ValueError as exc:
                        raise ValueError(
                            "cninfo_static_date_split_invalid"
                        ) from exc
                    if (
                        re.fullmatch(r"d[0-9]{2}_[0-9]{2}", suffix) is None
                        or suffix in suffixes
                        or date_start != expected_start
                        or parsed_end < parsed_start
                        or date_end > end
                    ):
                        raise ValueError("cninfo_static_date_split_invalid")
                    suffixes.add(suffix)
                    expected_start = (
                        parsed_end + timedelta(days=1)
                    ).strftime("%Y-%m-%d")
                if expected_start != (
                    datetime.strptime(end, "%Y-%m-%d")
                    + timedelta(days=1)
                ).strftime("%Y-%m-%d"):
                    raise ValueError("cninfo_static_date_split_invalid")
                leaves.extend(
                    base
                    | {
                        "leaf_id": f"{leaf_id}_{suffix}",
                        "date_start": date_start,
                        "date_end": date_end,
                    }
                    for suffix, date_start, date_end in splits
                )
    return leaves


def _with_source_ancestry(
    request: ProviderProbeRequest,
    source_ancestry: Mapping[str, Any],
) -> ProviderProbeRequest:
    return ProviderProbeRequest(
        **{
            **request.__dict__,
            "metadata": dict(request.metadata)
            | {"source_ancestry": dict(source_ancestry)},
        }
    )


def _cninfo_leaf_request(leaf: Mapping[str, Any], *, page: int) -> ProviderProbeRequest:
    body = urllib.parse.urlencode(
        {
            "pageNum": page,
            "pageSize": CNINFO_PAGE_SIZE,
            "column": leaf["column"],
            "tabName": "fulltext",
            "plate": leaf["plate"],
            "stock": "",
            "searchkey": "",
            "secid": "",
            "category": leaf["category"],
            "trade": "",
            "seDate": f"{leaf['date_start']}~{leaf['date_end']}",
            "sortName": "",
            "sortType": "",
            "isHLtitle": "true",
        }
    ).encode()
    return ProviderProbeRequest(
        request_id=_cninfo_request_id(leaf, page),
        provider="cninfo",
        endpoint=f"{leaf['kind']}_announcement_list",
        method="POST",
        url=CNINFO_QUERY_URL,
        headers=CNINFO_HEADERS,
        body=body,
        disposition="bounded_backfill",
        evidence_semantics="official_http_response_envelope",
        expected_terminal_states=("positive", "empty"),
        required_checks=(
            "announcement_list_shape",
            "unique_ids_within_page",
            "page_size_server_cap",
            "total_semantics",
        ),
        metadata={
            "case": "cninfo_list",
            "leaf_id": leaf["leaf_id"],
            "kind": leaf["kind"],
            "page": page,
            "date_start": leaf["date_start"],
            "date_end": leaf["date_end"],
        },
    )


def _cninfo_request_id(leaf: Mapping[str, Any], page: int) -> str:
    return f"cninfo_{leaf['leaf_id']}_page_{page:03d}"


def _cninfo_org_map_request() -> ProviderProbeRequest:
    return ProviderProbeRequest(
        request_id="cninfo_security_org_map",
        provider="cninfo",
        endpoint="security_org_map",
        method="GET",
        url="https://www.cninfo.com.cn/new/data/szse_stock.json",
        headers={"Referer": "https://www.cninfo.com.cn/", "User-Agent": USER_AGENT},
        disposition="bounded_backfill",
        evidence_semantics="official_http_response_envelope",
        expected_terminal_states=("positive",),
        required_checks=("known_code_mapped", "list_shape"),
        metadata={"case": "cninfo_org_map"},
    )


def _cninfo_document_url(adjunct_url: str) -> str:
    lexical = adjunct_url.strip()
    if "\\" in lexical:
        raise ValueError("cninfo_document_url_path_invalid")
    parsed = urllib.parse.urlsplit(lexical)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.path.startswith("/")
    ):
        raise ValueError("cninfo_document_url_not_relative")
    raw_parts = parsed.path.split("/")
    if not raw_parts or any(not part for part in raw_parts):
        raise ValueError("cninfo_document_url_path_invalid")
    decoded_parts: list[str] = []
    for raw_part in raw_parts:
        decoded = raw_part
        for _ in range(4):
            next_value = urllib.parse.unquote(decoded, errors="strict")
            if next_value == decoded:
                break
            decoded = next_value
        if (
            decoded in {"", ".", ".."}
            or "/" in decoded
            or "\\" in decoded
            or any(ord(character) < 32 or ord(character) == 127 for character in decoded)
        ):
            raise ValueError("cninfo_document_url_path_invalid")
        decoded_parts.append(decoded)
    encoded = "/".join(
        urllib.parse.quote(part, safe="-_.~") for part in decoded_parts
    )
    return f"https://static.cninfo.com.cn/{encoded}"


def _document_format(body: bytes, *, adjunct_url: str) -> str | None:
    stripped = body.lstrip()
    suffix = Path(adjunct_url.strip()).suffix.lower()
    if _document_block_reason(body) is not None:
        return None
    if stripped.startswith(b"%PDF"):
        return "pdf"
    if suffix in {".html", ".htm"} and stripped.startswith(b"<"):
        return "html"
    if suffix == ".js" and stripped and not stripped.startswith(b"<"):
        return "javascript"
    return None


def _document_block_reason(body: bytes) -> str | None:
    prefix = body[:256 * 1024].lstrip().lower()
    if not prefix.startswith((b"<", b"<!doctype")):
        return None
    tokens = (
        b"captcha",
        b"access denied",
        b"request blocked",
        b"too many requests",
        b"waf",
        "访问被阻断".encode("utf-8"),
        "访问频繁".encode("utf-8"),
        "安全验证".encode("utf-8"),
    )
    return "official_archive_html_block_page" if any(token in prefix for token in tokens) else None


def _content_length_matches(header_value: str | None, actual_size: int) -> bool:
    if not header_value:
        return True
    try:
        declared = int(header_value)
    except (TypeError, ValueError):
        return False
    return declared >= 0 and declared == actual_size


def _content_type_compatible(
    document_format: str | None, content_type: str | None
) -> bool:
    if document_format is None:
        return False
    normalized = str(content_type or "").split(";", 1)[0].strip().lower()
    if not normalized:
        return True
    allowed = {
        "pdf": {"application/pdf", "application/octet-stream"},
        "html": {"text/html", "application/xhtml+xml"},
        "javascript": {
            "application/javascript",
            "text/javascript",
            "application/x-javascript",
            "text/plain",
        },
    }
    return normalized in allowed[document_format]


def _adjunct_size_reasonable(declared_kb: Any, actual_size: int) -> bool:
    declared = _nonnegative_int(declared_kb)
    if declared is None or declared <= 0 or actual_size <= 0:
        return False
    declared_bytes = declared * 1024
    return max(1, declared_bytes // 16) <= actual_size <= max(
        64 * 1024, declared_bytes * 8
    )


def _document_structure_valid(
    body: bytes,
    *,
    document_format: str | None,
    announcement_id: str,
    announcement_time: Any,
) -> bool:
    stripped = body.lstrip()
    if document_format == "pdf":
        if not stripped.startswith(b"%PDF-") or len(stripped) <= 32:
            return False
        trailer = stripped[-64 * 1024 :]
        matches = tuple(
            re.finditer(
                rb"startxref[ \t\r\n]+(\d+)[ \t\r\n]+%%EOF",
                trailer,
            )
        )
        match = matches[-1] if matches else None
        startxref = int(match.group(1)) if match is not None else -1
        trailing = trailer[match.end() :] if match is not None else b""
        return bool(
            match is not None
            and startxref < len(stripped)
            and (
                _pdf_trailing_comments_valid(trailing)
                or _pdf_bounded_legacy_binary_trailer_valid(
                    trailing,
                    body=stripped,
                    startxref=startxref,
                )
            )
        )
    if document_format == "html":
        prefix = stripped[: 2 * 1024 * 1024].lower()
        tail = stripped[-64 * 1024 :].lower()
        announcement_date = _announcement_date(announcement_time)
        return (
            prefix.startswith((b"<!doctype html", b"<html"))
            and b"<body" in prefix
            and b"</html" in tail
            and (b'class="zbt"' in prefix or b'class="zw"' in prefix or b"<pre" in prefix)
            and (
                announcement_date is not None
                and announcement_date.encode("ascii") in stripped[: 4 * 1024 * 1024]
            )
        )
    if document_format == "javascript":
        text = stripped[: 4 * 1024 * 1024].decode("gb18030", errors="replace")
        return (
            text.lstrip().startswith("var affiches=")
            and bool(announcement_id)
            and f'"webTxtID":"{announcement_id}"' in text
        )
    return False


def _pdf_trailing_comments_valid(payload: bytes) -> bool:
    if not payload:
        return True
    if (
        len(payload) > 4 * 1024
        or payload[:1] not in b" \t\r\n"
        or b"\x00" in payload
    ):
        return False
    lines = re.split(rb"\r\n|\r|\n", payload)
    if len(lines) > 32:
        return False
    for line in lines:
        if len(line) > 1024:
            return False
        content = line.lstrip(b" \t")
        if content and not content.startswith(b"%"):
            return False
    return True


def _pdf_bounded_legacy_binary_trailer_valid(
    payload: bytes,
    *,
    body: bytes,
    startxref: int,
) -> bool:
    """Accept the observed fixed-width legacy record after a valid xref."""

    return bool(
        re.fullmatch(rb"\r\n\x00[^\x00\r\n]{15}\x00\x00", payload)
        and 0 <= startxref < len(body)
        and re.match(rb"xref(?:[ \t]|\r\n|\r|\n)", body[startxref:])
    )


def _announcement_date(value: Any) -> str | None:
    try:
        milliseconds = int(value)
    except (TypeError, ValueError):
        return None
    try:
        observed = datetime.fromtimestamp(
            milliseconds / 1000,
            tz=UTC,
        ) + timedelta(hours=8)
    except (OSError, OverflowError, ValueError):
        return None
    return observed.date().isoformat()


def _validate_inventory_announcement_dates(
    rows: Sequence[Mapping[str, Any]],
) -> None:
    leaf_intervals: dict[str, tuple[str, str]] = {}
    for profile in sorted(CNINFO_LEAF_PROFILES):
        for leaf in _cninfo_month_leaves(profile):
            leaf_id = leaf["leaf_id"]
            interval = (leaf["date_start"], leaf["date_end"])
            prior = leaf_intervals.setdefault(leaf_id, interval)
            if prior != interval:
                raise ValueError("cninfo_leaf_interval_identity_conflict")
    for row in rows:
        announcement_id = str(row.get("announcement_id") or "")
        observed_date = _announcement_date(row.get("announcement_time"))
        matched = row.get("matched_leaves")
        if (
            not announcement_id
            or observed_date is None
            or not isinstance(matched, list)
            or not matched
            or any(not isinstance(value, str) for value in matched)
            or len(set(matched)) != len(matched)
            or any(
                value not in leaf_intervals
                or not (
                    leaf_intervals[value][0]
                    <= observed_date
                    <= leaf_intervals[value][1]
                )
                for value in matched
            )
        ):
            raise ValueError(
                f"cninfo_inventory_announcement_date_invalid:{announcement_id or 'missing'}"
            )


def _captured_cninfo_pages(manifest_path: str | Path) -> dict[str, dict[str, Any]]:
    root = Path(manifest_path).parent
    plan = read_json(root / "request_plan.json")
    request_rows = {str(row["request_id"]): row for row in plan["requests"]}
    latest_terminal: dict[str, dict[str, Any]] = {}
    for event in _read_jsonl(root / "capture_journal.jsonl"):
        if event.get("event_type") == "capture_attempt_terminal":
            latest_terminal[str(event["request_id"])] = event
    pages: dict[str, dict[str, Any]] = {}
    for request_id, terminal in sorted(latest_terminal.items()):
        wrapper_path = root / str(terminal["raw_envelope_relative_path"])
        wrapper = read_json(wrapper_path)
        request = request_rows.get(request_id)
        if not request:
            raise ValueError(f"cninfo_discovery_request_missing:{request_id}")
        _official, body = _decode_cninfo_official_http_envelope(
            wrapper,
            request=request,
            terminal=terminal,
        )
        payload = json.loads(body)
        if not isinstance(payload, (dict, list)):
            raise ValueError(f"cninfo_discovery_payload_shape_invalid:{request_id}")
        pages[request_id] = {
            "request": request,
            "payload": payload,
        }
    if set(pages) != set(request_rows):
        raise ValueError("cninfo_discovery_terminal_request_closure_invalid")
    return pages


def _contract(
    *,
    phase: str,
    output_root: Path,
    signer: PersistentReceiptSigner,
    population_root: str,
    request_count: int,
    input_capture_hash: str | None,
    delay: float,
    timeout: float,
    max_retries: int,
    max_total_bytes: int,
    permission_context_id: str,
    leaf_profile: str,
    source_binding: Mapping[str, Any] | None = None,
) -> FreeProviderBackfillContract:
    adapter_identity = {
        "adapter": f"cninfo_{phase}_signed_http_capture_v1",
        "http": "python_urllib_no_redirect_v1",
        "implementation_root": _implementation_root(),
        "leaf_profile": leaf_profile,
    }
    if input_capture_hash:
        adapter_identity["input_capture_content_hash"] = input_capture_hash
    if source_binding is not None:
        binding_semantic = {
            key: value
            for key, value in source_binding.items()
            if key != "content_hash"
        }
        if (
            phase not in {"cninfo-inventory", "cninfo-documents"}
            or source_binding.get("content_hash")
            != canonical_hash(binding_semantic)
            or source_binding.get("phase") != phase
            or source_binding.get("input_capture_content_hash")
            != input_capture_hash
            or source_binding.get("source_leaf_profile") != leaf_profile
        ):
            raise ValueError("cninfo_contract_source_binding_invalid")
        adapter_identity.update(
            {
                "source_binding_root": str(source_binding["content_hash"]),
                "source_ancestry_root": str(
                    source_binding["source_ancestry_root"]
                ),
                "source_upstream_content_hashes_root": str(
                    source_binding["upstream_content_hashes_root"]
                ),
            }
        )
    elif phase in {"cninfo-inventory", "cninfo-documents"}:
        raise ValueError("cninfo_contract_source_binding_missing")
    if phase == "cninfo-documents":
        adapter_identity["document_body_max_bytes"] = (
            CNINFO_DOCUMENT_BODY_MAX_BYTES
        )
    return FreeProviderBackfillContract(
        activity_name=f"free_domestic_cninfo_{phase}_2011_2019_v1",
        provider="cninfo",
        output_root=output_root,
        permission_context_id=permission_context_id,
        population_root=population_root,
        capture_public_key_sha256=_public_key_hash(signer.public_key_pem),
        capture_public_key_pem_b64=base64.b64encode(signer.public_key_pem).decode(),
        scope_start="20120101",
        scope_end="20191231",
        request_start="20110101",
        request_end="20191231",
        allowed_hosts=("www.cninfo.com.cn", "static.cninfo.com.cn"),
        budget=BackfillResourceBudget(
            max_requests=request_count * (max_retries + 1),
            max_wire_exchanges=request_count * (max_retries + 1),
            max_response_bytes=(
                132 * 1024 * 1024
                if phase == "cninfo-documents"
                else 64 * 1024 * 1024
            ),
            max_total_response_bytes=max_total_bytes,
            timeout_seconds=timeout,
            minimum_delay_seconds=delay,
            max_retries=max_retries,
        ),
        adapter_identity=adapter_identity,
    )


def _implementation_root() -> str:
    return canonical_hash(
        {
            "discovery_plan": inspect.getsource(build_cninfo_discovery_plan),
            "inventory_plan": inspect.getsource(build_cninfo_inventory_plan),
            "captured_pages": inspect.getsource(_captured_cninfo_pages),
            "nonnegative_int": inspect.getsource(_nonnegative_int),
            "month_leaf_plan": inspect.getsource(_cninfo_month_leaves)
            + inspect.getsource(_cninfo_leaf_request)
            + inspect.getsource(_with_source_ancestry),
            "document_plan": inspect.getsource(build_cninfo_document_plan),
            "official_envelope_decoder": inspect.getsource(
                _decode_cninfo_official_http_envelope
            ),
            "source_ancestry": inspect.getsource(_cninfo_direct_source)
            + inspect.getsource(_cninfo_source_ancestry)
            + inspect.getsource(_validate_cninfo_direct_source)
            + inspect.getsource(_validate_cninfo_source_ancestry)
            + inspect.getsource(_cninfo_document_source_ancestry),
            "source_binding": inspect.getsource(_cninfo_upstream_content_hashes)
            + inspect.getsource(_cninfo_source_binding)
            + inspect.getsource(_validate_cninfo_source_binding)
            + inspect.getsource(_with_cninfo_source_binding)
            + inspect.getsource(_cninfo_request_source_evidence)
            + inspect.getsource(_cninfo_activity_context)
            + inspect.getsource(_validate_cninfo_context_source_binding)
            + inspect.getsource(_validate_cninfo_binding_derivation),
            "page_normalizer": inspect.getsource(_normalize_cninfo_pages),
            "inventory_closure": inspect.getsource(
                _validate_cninfo_inventory_request_closure
            )
            + inspect.getsource(_validate_cninfo_inventory_normalized_closure)
            + inspect.getsource(_replay_cninfo_inventory_artifacts)
            + inspect.getsource(_replay_cninfo_document_artifacts),
            "document_normalizer": inspect.getsource(normalize_cninfo_documents),
            "governance_qualification": inspect.getsource(
                _cninfo_governance_qualification
            )
            + inspect.getsource(validate_cninfo_governance),
            "document_transport": inspect.getsource(CNINFODocumentTransport),
            "archive_transport": inspect.getsource(CNINFOArchiveTransport),
            "transient_list_error_map": CNINFO_TRANSIENT_LIST_ERROR_MAP,
            "document_url": inspect.getsource(_cninfo_document_url),
            "document_format": inspect.getsource(_document_format),
            "document_block_detection": inspect.getsource(_document_block_reason),
            "document_content_length": inspect.getsource(_content_length_matches),
            "document_content_type": inspect.getsource(_content_type_compatible),
            "document_size": inspect.getsource(_adjunct_size_reasonable),
            "document_structure": inspect.getsource(_document_structure_valid),
            "pdf_trailing_comments": inspect.getsource(
                _pdf_trailing_comments_valid
            ),
            "announcement_date": inspect.getsource(_announcement_date),
            "inventory_date_validator": inspect.getsource(
                _validate_inventory_announcement_dates
            ),
            "official_http_transport": inspect.getsource(OfficialHttpProbeTransport),
            "official_http_transport_dependencies": inspect.getsource(
                run_provider_probe_module._NoRedirectHandler
            )
            + inspect.getsource(
                run_provider_probe_module._safe_response_headers
            )
            + inspect.getsource(run_provider_probe_module._json_observation),
            "cninfo_leaf_profiles": {
                name: [list(row) for row in rows]
                for name, rows in sorted(CNINFO_LEAF_PROFILES.items())
            },
            "cninfo_static_date_splits": CNINFO_STATIC_DATE_SPLITS,
            "cninfo_document_body_max_bytes": CNINFO_DOCUMENT_BODY_MAX_BYTES,
            "cninfo_page_size": CNINFO_PAGE_SIZE,
            "cninfo_max_pages_per_leaf": CNINFO_MAX_PAGES_PER_LEAF,
            "cninfo_scope": CNINFO_SCOPE,
            "cninfo_source_ancestry_schema": CNINFO_SOURCE_ANCESTRY_SCHEMA,
            "cninfo_source_binding_schema": CNINFO_SOURCE_BINDING_SCHEMA,
            "legacy_2011_document_identity": {
                "activity_id": CNINFO_LEGACY_2011_DOCUMENT_ACTIVITY_ID,
                "contract_id": CNINFO_LEGACY_2011_DOCUMENT_CONTRACT_ID,
                "request_plan_hash": (
                    CNINFO_LEGACY_2011_DOCUMENT_REQUEST_PLAN_HASH
                ),
                "input_capture_content_hash": (
                    CNINFO_LEGACY_2011_DOCUMENT_INPUT_CAPTURE_HASH
                ),
                "implementation_root": (
                    CNINFO_LEGACY_2011_DOCUMENT_IMPLEMENTATION_ROOT
                ),
            },
        }
    )


def _implementation_root_compatible(value: object) -> bool:
    observed = str(value or "")
    return bool(
        observed == _implementation_root()
        or observed in CNINFO_HISTORICAL_IMPLEMENTATION_ROOTS
    )


def _cninfo_governance_qualification(
    *,
    context: Mapping[str, Any],
    phase: str,
    normalized_manifest: Mapping[str, Any],
    publication_signature_verified: bool,
) -> dict[str, Any]:
    adapter_identity = context["adapter_identity"]
    implementation_root = str(
        adapter_identity.get("implementation_root") or ""
    )
    implementation_compatible = _implementation_root_compatible(
        implementation_root
    )
    historical_implementation_allowlisted = bool(
        implementation_root in CNINFO_HISTORICAL_IMPLEMENTATION_ROOTS
    )
    exact_legacy = (
        context["activity_id"] == CNINFO_LEGACY_2011_DOCUMENT_ACTIVITY_ID
        and context["contract_id"] == CNINFO_LEGACY_2011_DOCUMENT_CONTRACT_ID
        and context["request_plan_hash"]
        == CNINFO_LEGACY_2011_DOCUMENT_REQUEST_PLAN_HASH
        and adapter_identity.get("input_capture_content_hash")
        == CNINFO_LEGACY_2011_DOCUMENT_INPUT_CAPTURE_HASH
        and adapter_identity.get("implementation_root")
        == CNINFO_LEGACY_2011_DOCUMENT_IMPLEMENTATION_ROOT
    )
    blockers: list[str] = []
    weak_source_ancestry = False
    source_lineage_complete = False
    if exact_legacy:
        if normalized_manifest.get("schema_version") != "cninfo_document_normalization_v1":
            raise ValueError("cninfo_legacy_normalized_schema_invalid")
        blockers.append("legacy_2011_document_source_ancestry_incomplete")
        weak_source_ancestry = True
    elif phase == "cninfo-discovery":
        source_lineage_complete = publication_signature_verified
        weak_source_ancestry = not source_lineage_complete
    else:
        source_ancestry = normalized_manifest.get("source_ancestry")
        source_binding = normalized_manifest.get("source_binding")
        try:
            ancestry = _validate_cninfo_source_ancestry(
                source_ancestry,
                expected_stage=(
                    "discovery_capture_set"
                    if phase == "cninfo-inventory"
                    else "inventory_capture"
                ),
            )
            binding = _validate_cninfo_source_binding(
                source_binding,
                phase=phase,
                source_ancestry=ancestry,
            )
            _validate_cninfo_context_source_binding(
                context,
                phase=phase,
                source_ancestry=ancestry,
                source_binding=binding,
            )
            _validate_cninfo_binding_derivation(
                ancestry,
                binding,
                phase=phase,
                expected_implementation_root=(
                    str(
                        (context.get("adapter_identity") or {}).get(
                            "implementation_root"
                        )
                        or ""
                    )
                    if phase == "cninfo-documents"
                    else None
                ),
            )
            source_lineage_complete = True
            weak_source_ancestry = ancestry["weak_source_ancestry"] is True
        except ValueError:
            blockers.append("cninfo_source_lineage_binding_invalid")
            weak_source_ancestry = True
    if weak_source_ancestry:
        blockers.append("weak_source_acquisition_ancestry")
    if not implementation_compatible:
        blockers.append("cninfo_implementation_identity_incompatible")
    governed_evidence_eligible = bool(
        publication_signature_verified
        and source_lineage_complete
        and not weak_source_ancestry
        and implementation_compatible
    )
    if not governed_evidence_eligible:
        blockers.append("cninfo_governed_evidence_ineligible")
    qualification_semantic = {
        "schema_version": CNINFO_GOVERNANCE_QUALIFICATION_SCHEMA,
        "activity_id": context["activity_id"],
        "contract_id": context["contract_id"],
        "request_plan_hash": context["request_plan_hash"],
        "phase": phase,
        "implementation_compatible": implementation_compatible,
        "historical_implementation_allowlisted": (
            historical_implementation_allowlisted
        ),
        "source_lineage_complete": source_lineage_complete,
        "weak_source_ancestry": weak_source_ancestry,
        "quarantined": not governed_evidence_eligible,
        "governed_evidence_eligible": governed_evidence_eligible,
        "blockers": sorted(set(blockers)),
    }
    return qualification_semantic | {
        "content_hash": canonical_hash(qualification_semantic)
    }


def validate_cninfo_governance(path: str | Path) -> dict[str, Any]:
    """Validate capture integrity and derive a fail-closed CNINFO lineage verdict."""

    validated = validate_free_provider_backfill(path)
    source_root = Path(str(validated["manifest_path"])).parent
    contract = read_json(source_root / "activity_contract.json")
    adapter = str((contract.get("adapter_identity") or {}).get("adapter") or "")
    phase = next(
        (
            candidate
            for candidate in (
                "cninfo-discovery",
                "cninfo-inventory",
                "cninfo-documents",
            )
            if adapter == f"cninfo_{candidate}_signed_http_capture_v1"
        ),
        "",
    )
    if not phase:
        raise ValueError("cninfo_governance_phase_invalid")
    plan = read_json(source_root / "request_plan.json")
    requests = [
        _request_from_semantic(row) for row in plan.get("requests") or ()
    ]
    context = _cninfo_activity_context(
        source_root,
        requests,
        expected_phase=phase,
        expected_activity_id=str(validated.get("activity_id") or ""),
        require_activity_root=False,
    )
    normalized_manifest = read_json(source_root / "normalized/normalized_manifest.json")
    qualification = _cninfo_governance_qualification(
        context=context,
        phase=phase,
        normalized_manifest=normalized_manifest,
        publication_signature_verified=(
            validated.get("publication_signature_verified") is True
        ),
    )
    governed_evidence_eligible = qualification["governed_evidence_eligible"] is True
    return validated | {
        "normalized_artifacts_integrity_verified": validated.get(
            "normalized_artifacts_trusted"
        )
        is True,
        "normalized_artifacts_trusted": governed_evidence_eligible,
        "cninfo_governance_qualification": qualification,
    }


def _default_output(phase: str) -> Path:
    return SCOPE_ROOT / "cninfo" / phase.replace("cninfo-", "").replace("-", "_")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run CNINFO official archive backfill.")
    parser.add_argument(
        "--phase",
        choices=("cninfo-discovery", "cninfo-inventory", "cninfo-documents"),
        required=True,
    )
    parser.add_argument("--input-capture", action="append")
    parser.add_argument(
        "--leaf-profile",
        choices=tuple(CNINFO_LEAF_PROFILES),
        default="base",
        help="Lock the CNINFO category family for discovery/inventory.",
    )
    parser.add_argument(
        "--leaf-id",
        action="append",
        help="Limit discovery to an exact month-leaf ID; repeatable for a canary.",
    )
    parser.add_argument("--year", action="append", type=int)
    parser.add_argument("--permission-context-id", default=DEFAULT_PERMISSION_CONTEXT)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--validate")
    parser.add_argument("--minimum-delay-seconds", type=float, default=2.0)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--max-documents", type=int, default=130_000)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.validate:
        try:
            payload = validate_cninfo_governance(args.validate)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(json.dumps({"status": "blocked", "reason": str(exc)}, sort_keys=True))
            return 1
        print(_render(payload, pretty=args.pretty))
        return 0
    input_hash: str | None = None
    if args.phase == "cninfo-discovery":
        population, requests = build_cninfo_discovery_plan(
            args.leaf_id,
            args.year,
            leaf_profile=args.leaf_profile,
        )
        normalizer = normalize_cninfo_discovery
        max_total_bytes = 2 * 1024 * 1024 * 1024
    elif args.phase == "cninfo-inventory":
        if not args.input_capture:
            raise SystemExit("--input-capture is required for cninfo-inventory")
        population, requests, input_hash = build_cninfo_inventory_plan(
            args.input_capture,
            leaf_profile=args.leaf_profile,
        )
        normalizer = normalize_cninfo_inventory
        max_total_bytes = 8 * 1024 * 1024 * 1024
    else:
        if args.leaf_profile != "base":
            raise SystemExit("--leaf-profile is only valid for discovery/inventory")
        if not args.input_capture or len(args.input_capture) != 1:
            raise SystemExit("--input-capture is required for cninfo-documents")
        population, requests, input_hash = build_cninfo_document_plan(
            args.input_capture[0], args.year
        )
        if len(requests) > args.max_documents:
            raise SystemExit(
                f"document plan {len(requests)} exceeds --max-documents {args.max_documents}"
            )
        normalizer = normalize_cninfo_documents
        max_total_bytes = 256 * 1024 * 1024 * 1024
    population_root = canonical_hash(
        {"population": population, "input_capture_content_hash": input_hash}
    )
    source_binding = (
        None
        if args.phase == "cninfo-discovery"
        else _cninfo_request_source_evidence(
            requests,
            phase=args.phase,
        )[1]
    )
    preview = {
        "schema_version": "free_provider_http_backfill_plan_preview_v1",
        "phase": args.phase,
        "population_count": len(population),
        "population_root": population_root,
        "request_count": len(requests),
        "request_plan_hash": canonical_hash([row.semantic() for row in requests]),
        "input_capture_content_hash": input_hash,
        "network_called": False,
    }
    if args.plan_only and not DEFAULT_CAPTURE_KEY.is_file():
        print(_render(preview | {"capture_key_status": "not_initialized"}, pretty=args.pretty))
        return 0
    if not args.plan_only and not args.allow_network:
        print(
            _render(
                preview
                | {
                    "status": "blocked",
                    "reason": "free_provider_backfill_network_authority_missing",
                },
                pretty=args.pretty,
            )
        )
        return 2
    signer = PersistentReceiptSigner.load(DEFAULT_CAPTURE_KEY)
    output_root = _default_output(args.phase)
    contract = _contract(
        phase=args.phase,
        output_root=output_root,
        signer=signer,
        population_root=population_root,
        request_count=len(requests),
        input_capture_hash=input_hash,
        delay=args.minimum_delay_seconds,
        timeout=args.timeout_seconds,
        max_retries=args.max_retries,
        max_total_bytes=max_total_bytes,
        permission_context_id=args.permission_context_id,
        leaf_profile=(
            args.leaf_profile
            if args.phase in {"cninfo-discovery", "cninfo-inventory"}
            else str(source_binding["source_leaf_profile"])
        ),
        source_binding=source_binding,
    )
    if args.plan_only:
        print(
            _render(
                preview
                | {
                    "contract_id": canonical_hash(contract.semantic()),
                    "capture_public_key_sha256": contract.capture_public_key_sha256,
                },
                pretty=args.pretty,
            )
        )
        return 0
    transport = (
        CNINFODocumentTransport(
            minimum_delay_seconds=args.minimum_delay_seconds
        )
        if args.phase == "cninfo-documents"
        else CNINFOArchiveTransport(
            minimum_delay_seconds=args.minimum_delay_seconds
        )
    )
    result = run_free_provider_backfill(
        contract,
        requests,
        transport=transport,
        signer=signer,
        normalizer=normalizer,
        runtime_implementation_root=_implementation_root(),
    )
    print(_render(result, pretty=args.pretty))
    return 0 if result.get("status") == "succeeded" else 1


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"jsonl_object_required:{path}")
                rows.append(value)
    return rows


def _write_jsonl_row(handle: Any, row: Mapping[str, Any]) -> None:
    handle.write(
        json.dumps(
            dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        + b"\n"
    )


def _atomic_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("wb") as handle:
            for row in rows:
                _write_jsonl_row(handle, row)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _render(payload: Mapping[str, Any], *, pretty: bool) -> str:
    return json.dumps(
        dict(payload), ensure_ascii=False, indent=2 if pretty else None, sort_keys=True
    )


if __name__ == "__main__":
    raise SystemExit(main())

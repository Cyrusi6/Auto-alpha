"""Signed CSI official announcement inventory and detail acquisition."""

from __future__ import annotations

import argparse
import base64
import calendar
import hashlib
import html as html_lib
import io
import inspect
import json
import math
import os
import re
import urllib.parse
import zipfile
from collections import defaultdict
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Mapping, Sequence

from auto_alpha.platform.artifacts.storage import canonical_hash, read_json, sha256_file
from auto_alpha.platform.governance.network.signing import PersistentReceiptSigner

from . import free_provider_backfill as free_provider_backfill_module
from .free_provider_backfill import (
    BackfillResourceBudget,
    FreeProviderBackfillContract,
    MANIFEST_NAME,
    NormalizedArtifact,
    _public_key_hash,
    _request_from_semantic,
    replay_normalized_artifacts,
    run_free_provider_backfill,
    validate_free_provider_backfill,
)
from .provider_probe import ProviderProbeObservation, ProviderProbeRequest
from . import run_provider_probe as run_provider_probe_module
from .run_provider_probe import (
    CSINDEX_HEADERS,
    CSINDEX_LIST_URL,
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
CSINDEX_PAGE_SIZE = 1000
CSINDEX_MAX_PAGES_PER_MONTH = 100
CSINDEX_LIST_MONTH_START = "2011-01-01"
CSINDEX_LIST_MONTH_END = "2019-12-31"
CSINDEX_ATTACHMENT_HOSTS = (
    "oss-ch.csindex.com.cn",
    "www.csindex.com.cn",
)
CSINDEX_ATTACHMENT_EXTENSIONS = frozenset(
    {
        "csv",
        "doc",
        "docx",
        "gif",
        "jpeg",
        "jpg",
        "pdf",
        "png",
        "txt",
        "xls",
        "xlsx",
        "zip",
    }
)
CSINDEX_ATTACHMENT_TEMPORAL_BLOCKER = (
    "current_attachment_retrieval_does_not_prove_historical_known_at_or_vintage"
)
CSINDEX_ATTACHMENT_BODY_MAX_BYTES = 128 * 1024 * 1024
CSINDEX_JSON_BODY_MAX_BYTES = 64 * 1024 * 1024
CSINDEX_ATTACHMENT_PATH_DATE_START = "20110101"
CSINDEX_ATTACHMENT_PATH_DATE_END = "20191231"
CSINDEX_SCOPE = {
    "date_start": "20120101",
    "date_end": "20191231",
    "request_start": "20110101",
    "request_end": "20191231",
}
CSINDEX_SOURCE_ANCESTRY_SCHEMA = "csindex_source_ancestry_v1"
CSINDEX_SOURCE_BINDING_SCHEMA = "csindex_source_binding_v1"
CSINDEX_FULL_PROFILE = "csindex_rebalance_archive_2011_2019_full_v2"
CSINDEX_DISCOVERY_SLICE_PROFILE = "csindex_rebalance_archive_leaf_slice_v2"
CSINDEX_ATTACHMENT_FULL_PROFILE = "csindex_attachment_archive_full_v3"
CSINDEX_ATTACHMENT_HOST_SLICE_PROFILE = "csindex_attachment_host_slice_v3"
CSINDEX_LEGACY_CONS_REPAIR_PROFILE = "csindex_legacy_cons_exact_repair_v1"
CSINDEX_PHASE_ADAPTERS = {
    "csindex-discovery": "csindex_csindex-discovery_signed_http_capture_v2",
    "csindex-inventory": "csindex_csindex-inventory_signed_http_capture_v2",
    "csindex-details": "csindex_csindex-details_signed_http_capture_v2",
    "csindex-attachments": "csindex_attachments_signed_binary_capture_v3",
    "csindex-legacy-cons-repair": (
        "csindex_legacy_cons_exact_repair_signed_binary_capture_v1"
    ),
}
CSINDEX_SOURCE_PROFILE_ID = "dap_d785714ef1b912a20c0f19ca"
CSINDEX_APPROVED_CAPTURE_KEY_SHA256 = (
    "0afef940a253b9ef0f3702af5eb099c4ed48209975bc4f1991a471e4c50f446f"
)
CSINDEX_HTTP_IDENTITY = "python_urllib_no_redirect_v1"
CSINDEX_PHASE_RUNTIME_POLICY = {
    "csindex-discovery": {
        "activity_name": "free_domestic_csindex_csindex-discovery_2011_2019_v2",
        "minimum_delay_seconds": 5.0,
        "timeout_seconds": 30.0,
        "max_retries": 2,
        "max_response_bytes": 64 * 1024 * 1024,
        "max_total_response_bytes": 8 * 1024 * 1024 * 1024,
    },
    "csindex-inventory": {
        "activity_name": "free_domestic_csindex_csindex-inventory_2011_2019_v2",
        "minimum_delay_seconds": 5.0,
        "timeout_seconds": 30.0,
        "max_retries": 2,
        "max_response_bytes": 64 * 1024 * 1024,
        "max_total_response_bytes": 8 * 1024 * 1024 * 1024,
    },
    "csindex-details": {
        "activity_name": "free_domestic_csindex_csindex-details_2011_2019_v2",
        "minimum_delay_seconds": 7.5,
        "timeout_seconds": 30.0,
        "max_retries": 2,
        "max_response_bytes": 64 * 1024 * 1024,
        "max_total_response_bytes": 8 * 1024 * 1024 * 1024,
    },
    "csindex-attachments": {
        "activity_name": "free_domestic_csindex_attachments_2011_2019_v3",
        "minimum_delay_seconds": 2.0,
        "timeout_seconds": 30.0,
        "max_retries": 2,
        "max_response_bytes": 176 * 1024 * 1024,
        "max_total_response_bytes": 16 * 1024 * 1024 * 1024,
    },
    "csindex-legacy-cons-repair": {
        "activity_name": "free_domestic_csindex_legacy_cons_exact_repair_v1",
        "minimum_delay_seconds": 2.0,
        "timeout_seconds": 30.0,
        "max_retries": 2,
        "max_response_bytes": 176 * 1024 * 1024,
        "max_total_response_bytes": 16 * 1024 * 1024 * 1024,
    },
}
CSINDEX_LEGACY_CONS_REPAIR_ROWS = (
    {
        "attachment_url": (
            "https://oss-ch.csindex.com.cn/static/html/csindex/public/"
            "sseportal/upload/files/upload/201511302cons.xls"
        ),
        "announcement_ids": ("4271", "4272"),
        "announcement_publish_date": "2015-11-30",
        "csi300_announcement_id": "4272",
    },
    {
        "attachment_url": (
            "https://oss-ch.csindex.com.cn/static/html/csindex/public/"
            "sseportal/upload/files/upload/201605302cons.xls"
        ),
        "announcement_ids": ("3855", "4423"),
        "announcement_publish_date": "2016-05-30",
        "csi300_announcement_id": "3855",
    },
)
_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_PATH_DATE_TOKEN = re.compile(r"(?<!\d)(\d{8})(?!\d)")
_ATTACHMENT_REFERENCE_HINT = re.compile(
    r"\.(?:csv|docx?|gif|jpe?g|pdf|png|txt|xlsx?|zip)(?:$|[?#])",
    re.IGNORECASE,
)


class _AttachmentReferenceParser(HTMLParser):
    """Collect only literal href/src values; URL policy is applied separately."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: list[tuple[str, str]] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        del tag
        for name, value in attrs:
            lowered = str(name).lower()
            if lowered in {"href", "src"} and value is not None:
                self.references.append((lowered, value))

    handle_startendtag = handle_starttag


def _decode_csindex_official_payload(
    raw_payload: bytes,
    *,
    request: ProviderProbeRequest,
    status_code: Any,
    max_body_bytes: int,
) -> tuple[dict[str, Any], bytes]:
    """Decode one official HTTP envelope and bind it to the sealed request."""

    try:
        official = json.loads(raw_payload)
        if not isinstance(official, dict):
            raise ValueError("official_envelope_object_required")
        body = base64.b64decode(
            str(official.get("body_base64") or ""), validate=True
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("csindex_official_http_envelope_invalid:decode") from exc
    official_status = official.get("status_code")
    if (
        official.get("schema_version") != "official_http_probe_envelope_v1"
        or official.get("method") != request.method.upper()
        or official.get("url") != request.url
        or isinstance(official_status, bool)
        or official_status != 200
        or isinstance(status_code, bool)
        or status_code != official_status
        or official.get("redirect_followed") is not False
        or official.get("body_sha256") != hashlib.sha256(body).hexdigest()
        or len(body) > max_body_bytes
    ):
        raise ValueError(
            f"csindex_official_http_envelope_invalid:{request.request_id}"
        )
    return official, body


def _decode_csindex_official_http_envelope(
    wrapper: Mapping[str, Any],
    *,
    request: ProviderProbeRequest,
    terminal: Mapping[str, Any],
    max_body_bytes: int = CSINDEX_JSON_BODY_MAX_BYTES,
) -> tuple[dict[str, Any], bytes]:
    """Verify the immutable outer wrapper before decoding its HTTP envelope."""

    try:
        raw_payload = base64.b64decode(
            str(wrapper.get("raw_payload_base64") or ""), validate=True
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("csindex_official_http_envelope_invalid:decode") from exc
    if (
        wrapper.get("schema_version") != "free_provider_backfill_raw_envelope_v1"
        or wrapper.get("request_id") != request.request_id
        or wrapper.get("raw_payload_sha256")
        != hashlib.sha256(raw_payload).hexdigest()
        or wrapper.get("raw_payload_size_bytes") != len(raw_payload)
    ):
        raise ValueError(
            f"csindex_official_http_envelope_invalid:{request.request_id}"
        )
    return _decode_csindex_official_payload(
        raw_payload,
        request=request,
        status_code=terminal.get("status_code"),
        max_body_bytes=max_body_bytes,
    )


def _csindex_direct_source(
    validated: Mapping[str, Any],
    *,
    expected_phase: str,
    expected_profiles: Sequence[str],
) -> dict[str, Any]:
    root = Path(str(validated.get("manifest_path") or "")).parent
    contract = read_json(root / "activity_contract.json")
    plan = read_json(root / "request_plan.json")
    adapter_identity = contract.get("adapter_identity") or {}
    phase_adapter = CSINDEX_PHASE_ADAPTERS.get(expected_phase)
    capture_profile = str(adapter_identity.get("capture_profile") or "")
    implementation_root = str(adapter_identity.get("implementation_root") or "")
    request_count = len(plan.get("requests") or ())
    _validate_csindex_authorized_contract(
        contract,
        phase=expected_phase,
        capture_profile=capture_profile,
        request_count=request_count,
    )
    if (
        phase_adapter is None
        or contract.get("provider") != "csindex"
        or adapter_identity.get("adapter") != phase_adapter
        or contract.get("scope") != CSINDEX_SCOPE
        or capture_profile not in set(expected_profiles)
        or re.fullmatch(r"[0-9a-f]{64}", implementation_root) is None
    ):
        raise ValueError(f"csindex_{expected_phase}_source_contract_invalid")
    signed = validated.get("publication_signature_verified") is True
    trusted = validated.get("normalized_artifacts_trusted") is True
    direct = {
        "source_capture_schema": validated.get("schema_version"),
        "source_generation_id": validated.get("generation_id"),
        "source_content_hash": validated.get("content_hash"),
        "source_contract_id": validated.get("contract_id"),
        "source_contract_content_hash": canonical_hash(contract),
        "source_provider": "csindex",
        "source_phase": expected_phase,
        "source_adapter": phase_adapter,
        "source_capture_profile": capture_profile,
        "source_scope": dict(contract.get("scope") or {}),
        "source_implementation_root": implementation_root,
        "source_activity_id": validated.get("activity_id"),
        "source_request_plan_hash": validated.get("request_plan_hash"),
        "source_request_count": request_count,
        "source_population_root": contract.get("population_root"),
        "source_permission_context_id": contract.get("permission_context_id"),
        "source_activity_name": contract.get("activity_name"),
        "source_allowed_hosts": contract.get("allowed_hosts"),
        "source_budget": contract.get("budget"),
        "source_profile_id": contract.get("source_profile_id"),
        "source_http_identity": adapter_identity.get("http"),
        "source_capture_public_key_sha256": contract.get(
            "capture_public_key_sha256"
        ),
        "source_publication_signature_verified": signed,
        "source_normalized_artifacts_trusted": trusted,
        "weak_source_ancestry": not (signed and trusted),
    }
    _validate_csindex_direct_source(direct)
    return direct


def _validate_csindex_direct_source(value: Mapping[str, Any]) -> None:
    phase = str(value.get("source_phase") or "")
    signed = value.get("source_publication_signature_verified")
    trusted = value.get("source_normalized_artifacts_trusted")
    source_content_hash = str(value.get("source_content_hash") or "")
    source_contract_id = str(value.get("source_contract_id") or "")
    phase_profiles = {
        "csindex-discovery": CSINDEX_FULL_PROFILE,
        "csindex-inventory": CSINDEX_FULL_PROFILE,
        "csindex-details": CSINDEX_FULL_PROFILE,
    }
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
            "source_capture_profile",
            "source_scope",
            "source_implementation_root",
            "source_activity_id",
            "source_request_plan_hash",
            "source_request_count",
            "source_population_root",
            "source_permission_context_id",
            "source_activity_name",
            "source_allowed_hosts",
            "source_budget",
            "source_profile_id",
            "source_http_identity",
            "source_capture_public_key_sha256",
            "source_publication_signature_verified",
            "source_normalized_artifacts_trusted",
            "weak_source_ancestry",
        }
        or value.get("source_capture_schema")
        not in {
            "free_provider_backfill_capture_v1",
            "free_provider_backfill_capture_v2",
        }
        or value.get("source_provider") != "csindex"
        or phase not in phase_profiles
        or value.get("source_adapter") != CSINDEX_PHASE_ADAPTERS[phase]
        or value.get("source_capture_profile") != phase_profiles[phase]
        or value.get("source_scope") != CSINDEX_SCOPE
        or re.fullmatch(r"[0-9a-f]{64}", source_content_hash) is None
        or value.get("source_generation_id")
        != f"free_provider_backfill_{source_content_hash[:24]}"
        or re.fullmatch(r"[0-9a-f]{64}", source_contract_id) is None
        or value.get("source_contract_content_hash")
        != source_contract_id
        or re.fullmatch(
            r"[0-9a-f]{64}", str(value.get("source_implementation_root") or "")
        )
        is None
        or any(
            re.fullmatch(r"[0-9a-f]{64}", str(value.get(key) or "")) is None
            for key in (
                "source_activity_id",
                "source_request_plan_hash",
                "source_population_root",
            )
        )
        or not isinstance(value.get("source_request_count"), int)
        or isinstance(value.get("source_request_count"), bool)
        or int(value.get("source_request_count") or 0) <= 0
        or value.get("source_permission_context_id") != DEFAULT_PERMISSION_CONTEXT
        or value.get("source_activity_name")
        != CSINDEX_PHASE_RUNTIME_POLICY[phase]["activity_name"]
        or value.get("source_allowed_hosts") != ["www.csindex.com.cn"]
        or value.get("source_profile_id") != CSINDEX_SOURCE_PROFILE_ID
        or value.get("source_http_identity") != CSINDEX_HTTP_IDENTITY
        or value.get("source_capture_public_key_sha256")
        != CSINDEX_APPROVED_CAPTURE_KEY_SHA256
        or value.get("source_budget")
        != {
            "max_requests": int(value.get("source_request_count"))
            * (int(CSINDEX_PHASE_RUNTIME_POLICY[phase]["max_retries"]) + 1),
            "max_wire_exchanges": int(value.get("source_request_count"))
            * (int(CSINDEX_PHASE_RUNTIME_POLICY[phase]["max_retries"]) + 1),
            "max_response_bytes": CSINDEX_PHASE_RUNTIME_POLICY[phase][
                "max_response_bytes"
            ],
            "max_total_response_bytes": CSINDEX_PHASE_RUNTIME_POLICY[phase][
                "max_total_response_bytes"
            ],
            "timeout_seconds": CSINDEX_PHASE_RUNTIME_POLICY[phase][
                "timeout_seconds"
            ],
            "minimum_delay_seconds": CSINDEX_PHASE_RUNTIME_POLICY[phase][
                "minimum_delay_seconds"
            ],
            "max_retries": CSINDEX_PHASE_RUNTIME_POLICY[phase]["max_retries"],
        }
        or not isinstance(signed, bool)
        or not isinstance(trusted, bool)
        or value.get("weak_source_ancestry") is not (not (signed and trusted))
    ):
        raise ValueError("csindex_source_ancestry_invalid")


def _csindex_source_ancestry(
    *,
    source_stage: str,
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
        "schema_version": CSINDEX_SOURCE_ANCESTRY_SCHEMA,
        "source_stage": source_stage,
        "direct_sources": direct,
        "upstream_ancestry": upstream,
        "weak_source_ancestry": any(
            row.get("weak_source_ancestry") is True for row in (*direct, *upstream)
        ),
    }
    ancestry = semantic | {"ancestry_root": canonical_hash(semantic)}
    return _validate_csindex_source_ancestry(
        ancestry, expected_stage=source_stage
    )


def _validate_csindex_source_ancestry(
    value: Any,
    *,
    expected_stage: str | None = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("csindex_source_ancestry_invalid")
    direct = value.get("direct_sources")
    upstream = value.get("upstream_ancestry")
    semantic = {key: item for key, item in value.items() if key != "ancestry_root"}
    if (
        set(value)
        != {
            "schema_version",
            "source_stage",
            "direct_sources",
            "upstream_ancestry",
            "weak_source_ancestry",
            "ancestry_root",
        }
        or value.get("schema_version") != CSINDEX_SOURCE_ANCESTRY_SCHEMA
        or not isinstance(value.get("source_stage"), str)
        or (expected_stage is not None and value.get("source_stage") != expected_stage)
        or not isinstance(direct, list)
        or not direct
        or not isinstance(upstream, list)
        or value.get("ancestry_root") != canonical_hash(semantic)
    ):
        raise ValueError("csindex_source_ancestry_invalid")
    for row in direct:
        if not isinstance(row, Mapping):
            raise ValueError("csindex_source_ancestry_invalid")
        _validate_csindex_direct_source(row)
    if list(direct) != sorted(
        direct,
        key=lambda row: (
            str(row.get("source_generation_id") or ""),
            str(row.get("source_content_hash") or ""),
        ),
    ):
        raise ValueError("csindex_source_ancestry_invalid")
    for row in upstream:
        _validate_csindex_source_ancestry(row)
    if list(upstream) != sorted(
        upstream, key=lambda row: str(row.get("ancestry_root") or "")
    ):
        raise ValueError("csindex_source_ancestry_invalid")
    derived_weak = any(
        row.get("weak_source_ancestry") is True for row in [*direct, *upstream]
    )
    if value.get("weak_source_ancestry") is not derived_weak:
        raise ValueError("csindex_source_ancestry_invalid")
    geometry = {
        "discovery_capture": ("csindex-discovery", None),
        "inventory_capture": ("csindex-inventory", "discovery_capture"),
        "details_capture": ("csindex-details", "inventory_capture"),
    }
    expected_geometry = geometry.get(str(value.get("source_stage") or ""))
    if (
        expected_geometry is None
        or len(direct) != 1
        or direct[0].get("source_phase") != expected_geometry[0]
        or (
            expected_geometry[1] is None
            and upstream != []
        )
        or (
            expected_geometry[1] is not None
            and (
                len(upstream) != 1
                or upstream[0].get("source_stage") != expected_geometry[1]
            )
        )
    ):
        raise ValueError("csindex_source_ancestry_phase_geometry_invalid")
    return dict(value)


def _csindex_upstream_content_hashes(
    source_ancestry: Mapping[str, Any],
) -> list[str]:
    hashes: set[str] = set()

    def visit(value: Mapping[str, Any]) -> None:
        for row in value.get("direct_sources") or ():
            content_hash = str(row.get("source_content_hash") or "")
            if re.fullmatch(r"[0-9a-f]{64}", content_hash) is None:
                raise ValueError("csindex_source_ancestry_invalid")
            hashes.add(content_hash)
        for row in value.get("upstream_ancestry") or ():
            if not isinstance(row, Mapping):
                raise ValueError("csindex_source_ancestry_invalid")
            visit(row)

    visit(source_ancestry)
    return sorted(hashes)


def _csindex_source_binding(
    *,
    phase: str,
    capture_profile: str,
    input_capture_content_hash: str,
    source_ancestry: Mapping[str, Any],
    derivation: Mapping[str, Any],
) -> dict[str, Any]:
    expected_stage = {
        "csindex-inventory": "discovery_capture",
        "csindex-details": "inventory_capture",
        "csindex-attachments": "details_capture",
        "csindex-legacy-cons-repair": "details_capture",
    }.get(phase)
    if expected_stage is None:
        raise ValueError("csindex_source_binding_phase_invalid")
    ancestry = _validate_csindex_source_ancestry(
        source_ancestry, expected_stage=expected_stage
    )
    upstream_hashes = _csindex_upstream_content_hashes(ancestry)
    semantic = {
        "schema_version": CSINDEX_SOURCE_BINDING_SCHEMA,
        "phase": phase,
        "capture_profile": capture_profile,
        "input_capture_content_hash": input_capture_content_hash,
        "source_ancestry_root": ancestry["ancestry_root"],
        "upstream_content_hashes": upstream_hashes,
        "upstream_content_hashes_root": canonical_hash(upstream_hashes),
        "derivation": dict(derivation),
    }
    binding = semantic | {"content_hash": canonical_hash(semantic)}
    return _validate_csindex_source_binding(
        binding,
        phase=phase,
        source_ancestry=ancestry,
    )


def _validate_csindex_source_binding(
    value: Any,
    *,
    phase: str,
    source_ancestry: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("csindex_source_binding_invalid")
    expected_stage = {
        "csindex-inventory": "discovery_capture",
        "csindex-details": "inventory_capture",
        "csindex-attachments": "details_capture",
        "csindex-legacy-cons-repair": "details_capture",
    }.get(phase)
    expected_profiles = {
        "csindex-inventory": {CSINDEX_FULL_PROFILE},
        "csindex-details": {CSINDEX_FULL_PROFILE},
        "csindex-attachments": {
            CSINDEX_ATTACHMENT_FULL_PROFILE,
            CSINDEX_ATTACHMENT_HOST_SLICE_PROFILE,
        },
        "csindex-legacy-cons-repair": {CSINDEX_LEGACY_CONS_REPAIR_PROFILE},
    }.get(phase)
    if expected_stage is None or expected_profiles is None:
        raise ValueError("csindex_source_binding_phase_invalid")
    ancestry = _validate_csindex_source_ancestry(
        source_ancestry, expected_stage=expected_stage
    )
    upstream_hashes = _csindex_upstream_content_hashes(ancestry)
    semantic = {key: item for key, item in value.items() if key != "content_hash"}
    if (
        set(value)
        != {
            "schema_version",
            "phase",
            "capture_profile",
            "input_capture_content_hash",
            "source_ancestry_root",
            "upstream_content_hashes",
            "upstream_content_hashes_root",
            "derivation",
            "content_hash",
        }
        or value.get("schema_version") != CSINDEX_SOURCE_BINDING_SCHEMA
        or value.get("phase") != phase
        or value.get("capture_profile") not in expected_profiles
        or re.fullmatch(
            r"[0-9a-f]{64}", str(value.get("input_capture_content_hash") or "")
        )
        is None
        or value.get("source_ancestry_root") != ancestry["ancestry_root"]
        or value.get("upstream_content_hashes") != upstream_hashes
        or value.get("upstream_content_hashes_root")
        != canonical_hash(upstream_hashes)
        or not isinstance(value.get("derivation"), Mapping)
        or value.get("content_hash") != canonical_hash(semantic)
    ):
        raise ValueError("csindex_source_binding_invalid")
    return dict(value)


def _with_csindex_source_evidence(
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


def _csindex_request_source_evidence(
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
        raise ValueError("csindex_source_evidence_missing_or_mixed")
    ancestry = _validate_csindex_source_ancestry(ancestry_rows[0])
    binding = _validate_csindex_source_binding(
        binding_rows[0], phase=phase, source_ancestry=ancestry
    )
    return ancestry, binding


def _validate_csindex_binding_derivation(
    source_ancestry: Mapping[str, Any],
    source_binding: Mapping[str, Any],
    *,
    phase: str,
    expected_derivation: Mapping[str, Any],
) -> None:
    binding = _validate_csindex_source_binding(
        source_binding,
        phase=phase,
        source_ancestry=source_ancestry,
    )
    derivation = dict(binding.get("derivation") or {})
    if (
        derivation != dict(expected_derivation)
        or binding.get("input_capture_content_hash")
        != canonical_hash({**derivation, "source_ancestry": dict(source_ancestry)})
    ):
        raise ValueError("csindex_source_binding_derivation_invalid")


class CSIndexBackfillTransport:
    """Bind HTTP success to CSI's application status and leaf date geometry."""

    def __init__(self, *, minimum_delay_seconds: float) -> None:
        self._transport = OfficialHttpProbeTransport(
            minimum_delay_seconds=minimum_delay_seconds
        )

    def __call__(
        self, request: ProviderProbeRequest, timeout_seconds: float
    ) -> ProviderProbeObservation:
        observation = self._transport(request, timeout_seconds)
        if observation.status_code != 200 or observation.terminal_state == "error":
            return observation
        try:
            _official, body = _decode_csindex_official_payload(
                observation.raw_payload,
                request=request,
                status_code=observation.status_code,
                max_body_bytes=CSINDEX_JSON_BODY_MAX_BYTES,
            )
            payload = json.loads(body)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return ProviderProbeObservation(
                terminal_state="error",
                raw_payload=observation.raw_payload,
                row_count=None,
                status_code=observation.status_code,
                error_code="csindex_official_http_envelope_invalid",
                diagnostics=dict(observation.diagnostics)
                | {"envelope_error_type": type(exc).__name__},
                checks=dict(observation.checks)
                | {"official_http_envelope_valid": False},
                transport_exchange_count=observation.transport_exchange_count,
            )
        provider_success = (
            isinstance(payload, Mapping)
            and payload.get("success") is True
            and str(payload.get("code") or "") == "200"
        )
        checks = dict(observation.checks) | {"provider_success": provider_success}
        error_code = None
        if request.metadata.get("case") == "csindex_list":
            rows = payload.get("data") if isinstance(payload, Mapping) else None
            date_start = _strict_iso_date(request.metadata.get("date_start"))
            date_end = _strict_iso_date(request.metadata.get("date_end"))
            publication_dates_valid = isinstance(rows, list) and all(
                isinstance(row, Mapping)
                and date_start is not None
                and date_end is not None
                and (publish_date := _strict_iso_date(row.get("publishDate")))
                is not None
                and date_start <= publish_date <= date_end
                for row in (rows if isinstance(rows, list) else ())
            )
            current_page_valid = (
                _nonnegative_int(payload.get("currentPage"))
                == int(request.metadata.get("page") or -1)
                if isinstance(payload, Mapping)
                else False
            )
            page_size_valid = (
                _nonnegative_int(payload.get("pageSize"))
                == int(request.metadata.get("page_size") or -1)
                or (
                    _nonnegative_int(payload.get("total")) == 0
                    and rows == []
                    and _nonnegative_int(payload.get("pageSize")) == 0
                )
                if isinstance(payload, Mapping)
                else False
            )
            checks |= {
                "publication_dates_within_leaf": publication_dates_valid,
                "current_page_matches_request": current_page_valid,
                "page_size_matches_request": page_size_valid,
            }
            if not publication_dates_valid:
                error_code = "csindex_publication_date_outside_leaf"
            elif not current_page_valid:
                error_code = "csindex_page_clamped_or_mismatched"
            elif not page_size_valid:
                error_code = "csindex_page_size_not_honored"
        elif request.metadata.get("case") == "csindex_detail":
            detail = payload.get("data") if isinstance(payload, Mapping) else None
            detail_identity_valid = (
                isinstance(detail, Mapping)
                and str(detail.get("id") or "")
                == str(request.metadata.get("announcement_id") or "")
            )
            detail_publish_date_valid = (
                isinstance(detail, Mapping)
                and _strict_iso_date(detail.get("publishDate")) is not None
                and _strict_iso_date(detail.get("publishDate"))
                == _strict_iso_date(request.metadata.get("publish_date"))
            )
            checks |= {
                "detail_identity_matches_inventory": detail_identity_valid,
                "detail_publish_date_matches_inventory": detail_publish_date_valid,
            }
            if not detail_identity_valid:
                error_code = "csindex_detail_identity_mismatch"
            elif not detail_publish_date_valid:
                error_code = "csindex_detail_publish_date_mismatch"
        if not provider_success:
            error_code = "csindex_application_status_invalid"
        valid = error_code is None and all(checks.values())
        return ProviderProbeObservation(
            terminal_state=observation.terminal_state if valid else "error",
            raw_payload=observation.raw_payload,
            row_count=observation.row_count if valid else None,
            status_code=observation.status_code,
            error_code=error_code,
            diagnostics=dict(observation.diagnostics)
            | {
                "provider_success": provider_success,
                "provider_code": payload.get("code") if isinstance(payload, Mapping) else None,
            },
            checks=checks,
            transport_exchange_count=observation.transport_exchange_count,
        )

    def restore(
        self, request: ProviderProbeRequest, record: Mapping[str, Any]
    ) -> None:
        self._transport.restore(request, record)


class CSIndexAttachmentTransport:
    """Capture CSI binaries while independently validating their wire evidence."""

    def __init__(self, *, minimum_delay_seconds: float) -> None:
        self._transport = OfficialHttpProbeTransport(
            minimum_delay_seconds=minimum_delay_seconds,
            max_response_bytes=CSINDEX_ATTACHMENT_BODY_MAX_BYTES,
        )

    def __call__(
        self, request: ProviderProbeRequest, timeout_seconds: float
    ) -> ProviderProbeObservation:
        # The shared transport needs a binary case to preserve arbitrary bytes.  Its
        # PDF checks are deliberately ignored; every attachment check below is
        # independently derived from the archived HTTP envelope.
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
            official, body = _decode_csindex_official_payload(
                observation.raw_payload,
                request=request,
                status_code=observation.status_code,
                max_body_bytes=CSINDEX_ATTACHMENT_BODY_MAX_BYTES,
            )
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return ProviderProbeObservation(
                terminal_state="error",
                raw_payload=observation.raw_payload,
                row_count=None,
                status_code=observation.status_code,
                error_code="csindex_attachment_http_envelope_invalid",
                diagnostics=dict(observation.diagnostics)
                | {"envelope_error_type": type(exc).__name__},
                checks={"http_envelope_decoded": False},
                transport_exchange_count=observation.transport_exchange_count,
            )
        response_headers = {
            str(key).lower(): str(value)
            for key, value in (official.get("response_headers") or {}).items()
        }
        extension = str(request.metadata.get("extension") or "").lower()
        block_reason = _attachment_block_reason(body)
        checks = {
            "http_envelope_schema_exact": official.get("schema_version")
            == "official_http_probe_envelope_v1",
            "request_method_bound": official.get("method") == "GET",
            "redirect_not_followed": official.get("redirect_followed") is False,
            "http_status_exact": official.get("status_code") == 200,
            "request_url_bound": str(official.get("url") or "") == request.url,
            "nonempty_attachment": bool(body),
            "content_length_matches": _attachment_content_length_matches(
                response_headers.get("content-length"), len(body)
            ),
            "content_type_compatible": _attachment_content_type_compatible(
                extension, response_headers.get("content-type")
            ),
            "attachment_magic_matches": _attachment_magic_valid(body, extension),
            "not_html_or_waf": block_reason is None,
            "body_sha256_matches": str(official.get("body_sha256") or "")
            == hashlib.sha256(body).hexdigest(),
        }
        accepted = all(checks.values())
        return ProviderProbeObservation(
            terminal_state="positive" if accepted else "error",
            raw_payload=observation.raw_payload,
            row_count=1 if accepted else None,
            status_code=observation.status_code,
            error_code=None if accepted else "csindex_attachment_wire_evidence_invalid",
            diagnostics={
                "attachment_sha256": hashlib.sha256(body).hexdigest(),
                "attachment_size_bytes": len(body),
                "attachment_extension": extension,
                "content_type": response_headers.get("content-type"),
                "content_length": response_headers.get("content-length"),
                "attachment_block_reason": block_reason,
                "waf_html_observed": block_reason is not None,
            },
            checks=checks,
            transport_exchange_count=observation.transport_exchange_count,
        )

    def restore(
        self, request: ProviderProbeRequest, record: Mapping[str, Any]
    ) -> None:
        self._transport.restore(request, record)


def _replay_validated_csindex_source(
    capture: str | Path,
    *,
    expected_phase: str,
    expected_profiles: Sequence[str],
    normalizer: Any,
    required_roles: Sequence[str],
    require_complete_profile: bool,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, bytes],
    str,
    dict[str, Any],
]:
    validated = validate_csindex_governance(capture)
    if validated.get("status") != "succeeded":
        raise ValueError(f"csindex_{expected_phase}_capture_blocked")
    direct = _csindex_direct_source(
        validated,
        expected_phase=expected_phase,
        expected_profiles=expected_profiles,
    )
    replayed, replay_root = replay_normalized_artifacts(
        validated["manifest_path"],
        normalizer=normalizer,
        required_roles=tuple(required_roles) + ("normalized_manifest",),
    )
    try:
        normalized_manifest = json.loads(replayed["normalized_manifest"])
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("csindex_source_normalized_manifest_invalid") from exc
    capture_profile = direct["source_capture_profile"]
    if (
        normalized_manifest.get("capture_profile") != capture_profile
        or not isinstance(normalized_manifest.get("profile_complete"), bool)
        or (
            require_complete_profile
            and normalized_manifest.get("profile_complete") is not True
        )
    ):
        raise ValueError("csindex_source_profile_closure_invalid")
    if expected_phase in {"csindex-inventory", "csindex-details"}:
        ancestry = _validate_csindex_source_ancestry(
            normalized_manifest.get("source_ancestry")
        )
        binding = _validate_csindex_source_binding(
            normalized_manifest.get("source_binding"),
            phase=expected_phase,
            source_ancestry=ancestry,
        )
        root = Path(str(validated["manifest_path"])).parent
        contract = read_json(root / "activity_contract.json")
        adapter = contract.get("adapter_identity") or {}
        if (
            adapter.get("source_binding_root") != binding["content_hash"]
            or adapter.get("source_ancestry_root") != binding["source_ancestry_root"]
            or adapter.get("source_upstream_content_hashes_root")
            != binding["upstream_content_hashes_root"]
            or adapter.get("input_capture_content_hash")
            != binding["input_capture_content_hash"]
            or normalized_manifest.get("weak_source_ancestry")
            is not ancestry["weak_source_ancestry"]
        ):
            raise ValueError("csindex_source_contract_binding_invalid")
    return validated, direct, replayed, replay_root, normalized_manifest


def build_csindex_discovery_plan(
    include_leaf_ids: Sequence[str] | None = None,
) -> tuple[list[dict[str, str]], list[ProviderProbeRequest]]:
    selected = {str(value) for value in include_leaf_ids or ()}
    leaves = [
        leaf
        for leaf in _month_leaves()
        if not selected or leaf["leaf_id"] in selected
    ]
    if selected - {leaf["leaf_id"] for leaf in leaves}:
        raise ValueError("csindex_discovery_leaf_filter_unknown")
    capture_profile = (
        CSINDEX_DISCOVERY_SLICE_PROFILE if selected else CSINDEX_FULL_PROFILE
    )
    profile_complete = not selected
    requests = [
        _with_csindex_capture_profile(
            _filter_request(),
            capture_profile=capture_profile,
            profile_complete=profile_complete,
        )
    ]
    requests.extend(
        _with_csindex_capture_profile(
            _list_request(leaf, page=1),
            capture_profile=capture_profile,
            profile_complete=profile_complete,
        )
        for leaf in leaves
    )
    return leaves, requests


def build_csindex_inventory_plan(
    discovery_capture: str | Path,
) -> tuple[list[dict[str, Any]], list[ProviderProbeRequest], str]:
    validated, direct, _replayed, replay_root, normalized_manifest = (
        _replay_validated_csindex_source(
            discovery_capture,
            expected_phase="csindex-discovery",
            expected_profiles=(CSINDEX_FULL_PROFILE,),
            normalizer=normalize_csindex_discovery,
            required_roles=(
                "csindex_announcement_inventory",
                "csindex_page_coverage",
            ),
            require_complete_profile=True,
        )
    )
    if (
        normalized_manifest.get("leaf_count") != len(_month_leaves())
        or normalized_manifest.get("filter_topics_captured") is not True
    ):
        raise ValueError("csindex_discovery_full_profile_invalid")
    pages = _captured_list_pages(validated["manifest_path"])
    leaves = _month_leaves()
    resolved: list[dict[str, Any]] = []
    base_requests = [
        _with_csindex_capture_profile(
            _filter_request(),
            capture_profile=CSINDEX_FULL_PROFILE,
            profile_complete=True,
        )
    ]
    for leaf in leaves:
        page_one = pages.get(_request_id(leaf, 1))
        if page_one is None:
            raise ValueError(f"csindex_discovery_leaf_missing:{leaf['leaf_id']}")
        total = _nonnegative_int(page_one.get("total"))
        if total is None:
            raise ValueError(f"csindex_discovery_total_invalid:{leaf['leaf_id']}")
        page_count = max(1, math.ceil(total / CSINDEX_PAGE_SIZE))
        if page_count > CSINDEX_MAX_PAGES_PER_MONTH:
            raise ValueError(f"csindex_month_leaf_requires_finer_split:{leaf['leaf_id']}")
        resolved_leaf = dict(leaf) | {
            "reported_total": total,
            "page_count": page_count,
        }
        resolved.append(resolved_leaf)
        base_requests.extend(
            _with_csindex_capture_profile(
                _list_request(resolved_leaf, page=page),
                capture_profile=CSINDEX_FULL_PROFILE,
                profile_complete=True,
            )
            for page in range(1, page_count + 1)
        )
    ancestry = _csindex_source_ancestry(
        source_stage="discovery_capture",
        direct_sources=(direct,),
    )
    derivation = {
        "capture_content_hash": validated["content_hash"],
        "normalized_replay_root": replay_root,
        "resolved_population_root": canonical_hash(resolved),
        "request_semantics_root": canonical_hash(
            [request.semantic() for request in base_requests]
        ),
        "implementation_root": _implementation_root(),
    }
    input_root = canonical_hash({**derivation, "source_ancestry": ancestry})
    binding = _csindex_source_binding(
        phase="csindex-inventory",
        capture_profile=CSINDEX_FULL_PROFILE,
        input_capture_content_hash=input_root,
        source_ancestry=ancestry,
        derivation=derivation,
    )
    requests = [
        _with_csindex_source_evidence(request, ancestry, binding)
        for request in base_requests
    ]
    return resolved, requests, input_root


def build_csindex_detail_plan(
    inventory_capture: str | Path,
) -> tuple[list[dict[str, Any]], list[ProviderProbeRequest], str]:
    validated, direct, replayed, replay_root, normalized_manifest = (
        _replay_validated_csindex_source(
            inventory_capture,
            expected_phase="csindex-inventory",
            expected_profiles=(CSINDEX_FULL_PROFILE,),
            normalizer=normalize_csindex_inventory,
            required_roles=(
                "csindex_announcement_inventory",
                "csindex_page_coverage",
            ),
            require_complete_profile=True,
        )
    )
    if (
        normalized_manifest.get("leaf_count") != len(_month_leaves())
        or normalized_manifest.get("all_page_chains_valid") is not True
        or normalized_manifest.get("conflict_count") != 0
    ):
        raise ValueError("csindex_inventory_full_profile_invalid")
    rows = [
        json.loads(line)
        for line in replayed["csindex_announcement_inventory"].decode("utf-8").splitlines()
        if line.strip()
    ]
    base_requests = [
        _csindex_detail_request(row)
        for row in rows
    ]
    upstream = _validate_csindex_source_ancestry(
        normalized_manifest.get("source_ancestry"),
        expected_stage="discovery_capture",
    )
    ancestry = _csindex_source_ancestry(
        source_stage="inventory_capture",
        direct_sources=(direct,),
        upstream_ancestry=(upstream,),
    )
    population_root = canonical_hash(rows)
    request_root = canonical_hash(
        [request.semantic() for request in base_requests]
    )
    derivation = {
        "capture_content_hash": validated["content_hash"],
        "normalized_replay_root": replay_root,
        "resolved_population_root": population_root,
        "request_semantics_root": request_root,
        "implementation_root": _implementation_root(),
    }
    input_root = canonical_hash(
        {**derivation, "source_ancestry": ancestry}
    )
    binding = _csindex_source_binding(
        phase="csindex-details",
        capture_profile=CSINDEX_FULL_PROFILE,
        input_capture_content_hash=input_root,
        source_ancestry=ancestry,
        derivation=derivation,
    )
    requests = [
        _with_csindex_source_evidence(request, ancestry, binding)
        for request in base_requests
    ]
    return rows, requests, input_root


def _csindex_detail_request(row: Mapping[str, Any]) -> ProviderProbeRequest:
    return _with_csindex_capture_profile(
        ProviderProbeRequest(
            request_id=f"csindex_detail_{row['announcement_id']}",
            provider="csindex",
            endpoint="index_rebalance_announcement_detail",
            method="GET",
            url=(
                "https://www.csindex.com.cn/csindex-home/announcement/"
                f"queryAnnouncementById?id={row['announcement_id']}"
            ),
            headers={
                "Referer": "https://www.csindex.com.cn/",
                "User-Agent": USER_AGENT,
            },
            disposition="provider_cannot_prove",
            evidence_semantics="official_http_response_envelope",
            expected_terminal_states=("positive",),
            required_checks=(
                "detail_object",
                "publication_metadata",
                "required_tokens",
                "detail_or_waf_evidence_captured",
                "detail_identity_matches_inventory",
                "detail_publish_date_matches_inventory",
            ),
            metadata={
                "case": "csindex_detail",
                "announcement_id": row["announcement_id"],
                "publish_date": row.get("publish_date"),
                "list_file_url": row.get("file_url"),
                "list_file_name": row.get("file_name"),
                "tokens": [],
                "source_inventory_row": dict(row),
            },
        ),
        capture_profile=CSINDEX_FULL_PROFILE,
        profile_complete=True,
    )


def build_csindex_attachment_plan(
    details_capture: str | Path,
    include_hosts: Sequence[str] | None = None,
) -> tuple[list[dict[str, Any]], list[ProviderProbeRequest], str]:
    """Derive a bounded attachment plan only from replayed, signed detail bytes."""

    validated, direct, replayed, replay_root, normalized_manifest = (
        _replay_validated_csindex_source(
            details_capture,
            expected_phase="csindex-details",
            expected_profiles=(CSINDEX_FULL_PROFILE,),
            normalizer=normalize_csindex_details,
            required_roles=("csindex_announcement_details",),
            require_complete_profile=True,
        )
    )
    details = [
        json.loads(line)
        for line in replayed["csindex_announcement_details"]
        .decode("utf-8")
        .splitlines()
        if line.strip()
    ]
    hosts = _selected_attachment_hosts(include_hosts)
    full_population = _attachment_population_from_details(details)
    population = _attachment_population_for_hosts(full_population, hosts=hosts)
    if not population:
        raise ValueError("csindex_attachment_plan_empty")
    upstream = _validate_csindex_source_ancestry(
        normalized_manifest.get("source_ancestry"),
        expected_stage="inventory_capture",
    )
    ancestry = _csindex_source_ancestry(
        source_stage="details_capture",
        direct_sources=(direct,),
        upstream_ancestry=(upstream,),
    )
    capture_profile = (
        CSINDEX_ATTACHMENT_FULL_PROFILE
        if set(hosts) == set(CSINDEX_ATTACHMENT_HOSTS)
        else CSINDEX_ATTACHMENT_HOST_SLICE_PROFILE
    )
    capture_population = [
        row for row in population if row.get("reference_disposition") == "capture_eligible"
    ]
    derivation = {
        "capture_content_hash": validated["content_hash"],
        "normalized_replay_root": replay_root,
        "full_population_root": canonical_hash(full_population),
        "selected_population_root": canonical_hash(population),
        "request_semantics_root": canonical_hash(
            [_attachment_request_identity(row) for row in capture_population]
        ),
        "selected_hosts": list(hosts),
        "implementation_root": _implementation_root(),
    }
    input_root = canonical_hash({**derivation, "source_ancestry": ancestry})
    binding = _csindex_source_binding(
        phase="csindex-attachments",
        capture_profile=capture_profile,
        input_capture_content_hash=input_root,
        source_ancestry=ancestry,
        derivation=derivation,
    )
    requests = _attachment_requests(
        population,
        source_ancestry=ancestry,
        source_binding=binding,
        capture_profile=capture_profile,
    )
    return population, requests, input_root


def build_csindex_legacy_cons_repair_plan(
    details_capture: str | Path,
) -> tuple[list[dict[str, Any]], list[ProviderProbeRequest], str]:
    """Build the exact two-request legacy constituent attachment repair."""

    validated, direct, replayed, replay_root, normalized_manifest = (
        _replay_validated_csindex_source(
            details_capture,
            expected_phase="csindex-details",
            expected_profiles=(CSINDEX_FULL_PROFILE,),
            normalizer=normalize_csindex_details,
            required_roles=("csindex_announcement_details",),
            require_complete_profile=True,
        )
    )
    details = [
        json.loads(line)
        for line in replayed["csindex_announcement_details"]
        .decode("utf-8")
        .splitlines()
        if line.strip()
    ]
    population = _legacy_cons_repair_population(details)
    upstream = _validate_csindex_source_ancestry(
        normalized_manifest.get("source_ancestry"),
        expected_stage="inventory_capture",
    )
    ancestry = _csindex_source_ancestry(
        source_stage="details_capture",
        direct_sources=(direct,),
        upstream_ancestry=(upstream,),
    )
    if ancestry["weak_source_ancestry"]:
        raise ValueError("csindex_legacy_cons_repair_weak_source_blocked")
    capture_population = [
        row
        for row in population
        if row.get("reference_disposition") == "capture_eligible"
    ]
    derivation = {
        "capture_content_hash": validated["content_hash"],
        "normalized_replay_root": replay_root,
        "request_semantics_root": canonical_hash(
            [_attachment_request_identity(row) for row in capture_population]
        ),
        "selected_hosts": ["oss-ch.csindex.com.cn"],
        "implementation_root": _implementation_root(),
        "repair_population_root": canonical_hash(population),
        "repair_profile_root": canonical_hash(CSINDEX_LEGACY_CONS_REPAIR_ROWS),
    }
    input_root = canonical_hash({**derivation, "source_ancestry": ancestry})
    binding = _csindex_source_binding(
        phase="csindex-legacy-cons-repair",
        capture_profile=CSINDEX_LEGACY_CONS_REPAIR_PROFILE,
        input_capture_content_hash=input_root,
        source_ancestry=ancestry,
        derivation=derivation,
    )
    requests = _attachment_requests(
        population,
        source_ancestry=ancestry,
        source_binding=binding,
        capture_profile=CSINDEX_LEGACY_CONS_REPAIR_PROFILE,
    )
    if len(requests) != 2:
        raise ValueError("csindex_legacy_cons_repair_request_count_invalid")
    return population, requests, input_root


def _attachment_population_from_details(
    details: Sequence[Mapping[str, Any]],
    *,
    include_hosts: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Extract, confine and deduplicate href/src attachment references."""

    selected_hosts = set(_selected_attachment_hosts(include_hosts))
    attachments: dict[str, dict[str, Any]] = {}
    sources: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    rejected: dict[str, dict[str, Any]] = {}
    rejected_sources: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for detail in details:
        announcement_id = str(detail.get("announcement_id") or "")
        if not announcement_id:
            raise ValueError("csindex_attachment_source_identity_missing")
        parser = _AttachmentReferenceParser()
        parser.feed(str(detail.get("content_html") or ""))
        parser.close()
        by_reference: dict[str, set[str]] = defaultdict(set)
        for attribute, raw_reference in parser.references:
            if not _looks_like_csindex_attachment_reference(raw_reference):
                continue
            literal_reference = html_lib.unescape(str(raw_reference)).strip()
            try:
                attachment_url = _canonical_csindex_attachment_url(
                    literal_reference
                )
            except ValueError as exc:
                rejection_reason = str(exc)
                rejection_id = canonical_hash(
                    {
                        "raw_reference": literal_reference,
                        "rejection_reason": rejection_reason,
                    }
                )
                rejected.setdefault(
                    rejection_id,
                    {
                        "attachment_url": None,
                        "raw_reference": literal_reference,
                        "host": None,
                        "extension": None,
                        "path_dates": [],
                        "reference_disposition": "blocked_rejected_reference",
                        "rejection_reason": rejection_reason,
                        "temporal_blocker": CSINDEX_ATTACHMENT_TEMPORAL_BLOCKER,
                    },
                )
                source = rejected_sources[rejection_id].setdefault(
                    announcement_id,
                    _attachment_source_edge(
                        detail,
                        announcement_id=announcement_id,
                        edge_disposition="blocked_rejected_reference",
                    ),
                )
                source["reference_attributes"] = sorted(
                    set(source.get("reference_attributes") or ()) | {attribute}
                )
                continue
            by_reference[attachment_url].add(attribute)
        for attachment_url, attributes in sorted(by_reference.items()):
            parsed = urllib.parse.urlsplit(attachment_url)
            host = str(parsed.hostname or "").lower()
            if host not in selected_hosts:
                continue
            extension = _attachment_extension(parsed.path)
            path_dates = _attachment_path_dates(parsed.path)
            if not path_dates:
                reference_disposition = "blocked_attachment_path_date_unproven"
            elif any(
                value < CSINDEX_ATTACHMENT_PATH_DATE_START
                or value > CSINDEX_ATTACHMENT_PATH_DATE_END
                for value in path_dates
            ):
                reference_disposition = "blocked_out_of_scope_reference"
            else:
                reference_disposition = "capture_eligible"
            attachments.setdefault(
                attachment_url,
                {
                    "attachment_url": attachment_url,
                    "host": host,
                    "extension": extension,
                    "path_dates": path_dates,
                    "reference_disposition": reference_disposition,
                    "temporal_blocker": CSINDEX_ATTACHMENT_TEMPORAL_BLOCKER,
                },
            )
            publish_date = _strict_iso_date(detail.get("publish_date"))
            publish_token = publish_date.strftime("%Y%m%d") if publish_date else None
            source_in_scope = bool(
                publish_token
                and CSINDEX_ATTACHMENT_PATH_DATE_START
                <= publish_token
                <= CSINDEX_ATTACHMENT_PATH_DATE_END
            )
            if reference_disposition != "capture_eligible" or not source_in_scope:
                edge_disposition = reference_disposition
            elif any(value > str(publish_token) for value in path_dates):
                edge_disposition = "value_only_migrated_reference"
            else:
                edge_disposition = "historical_edge_candidate"
            source = _attachment_source_edge(
                detail,
                announcement_id=announcement_id,
                edge_disposition=edge_disposition,
            )
            source["reference_attributes"] = sorted(attributes)
            sources[attachment_url][announcement_id] = source
    ordered = sorted(
        attachments.values(),
        key=lambda row: (
            CSINDEX_ATTACHMENT_HOSTS.index(str(row["host"])),
            str(row["attachment_url"]),
        ),
    )
    accepted_rows = [
        row
        | {
            "source_announcements": [
                sources[str(row["attachment_url"])][key]
                for key in sorted(sources[str(row["attachment_url"])])
            ]
        }
        for row in ordered
    ]
    rejected_rows = [
        row
        | {
            "rejection_id": rejection_id,
            "source_announcements": [
                rejected_sources[rejection_id][key]
                for key in sorted(rejected_sources[rejection_id])
            ],
        }
        for rejection_id, row in sorted(rejected.items())
    ]
    return accepted_rows + rejected_rows


def _attachment_source_edge(
    detail: Mapping[str, Any],
    *,
    announcement_id: str,
    edge_disposition: str,
) -> dict[str, Any]:
    return {
        "announcement_id": announcement_id,
        "announcement_publish_date": detail.get("publish_date"),
        "detail_source_request_id": detail.get("source_request_id"),
        "detail_source_payload_sha256": detail.get("source_payload_sha256"),
        "contains_csi300": detail.get("contains_csi300") is True,
        "reference_attributes": [],
        "edge_disposition": edge_disposition,
        "historical_known_at_proven": False,
    }


def _attachment_population_for_hosts(
    population: Sequence[Mapping[str, Any]],
    *,
    hosts: Sequence[str],
) -> list[dict[str, Any]]:
    selected = set(hosts)
    rows: list[dict[str, Any]] = []
    for value in population:
        row = dict(value)
        if (
            row.get("reference_disposition") == "capture_eligible"
            and row.get("host") not in selected
        ):
            row["reference_disposition"] = "blocked_capture_profile_host_omission"
            row["capture_profile_omission"] = True
        rows.append(row)
    return rows


def _legacy_cons_repair_population(
    details: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Promote only two reviewed legacy `*cons.xls` edges into capture requests."""

    population = _attachment_population_from_details(details)
    expected = {
        str(row["attachment_url"]): row for row in CSINDEX_LEGACY_CONS_REPAIR_ROWS
    }
    observed: set[str] = set()
    repaired: list[dict[str, Any]] = []
    for value in population:
        row = dict(value)
        url = str(row.get("attachment_url") or "")
        spec = expected.get(url)
        if spec is None:
            row["pre_repair_reference_disposition"] = row.get(
                "reference_disposition"
            )
            row["reference_disposition"] = (
                "blocked_outside_legacy_cons_exact_repair_profile"
            )
            repaired.append(row)
            continue
        sources = row.get("source_announcements")
        source_ids = (
            sorted(str(source.get("announcement_id") or "") for source in sources)
            if isinstance(sources, list)
            and all(isinstance(source, Mapping) for source in sources)
            else []
        )
        source_dates = {
            str(source.get("announcement_publish_date") or "")
            for source in (sources if isinstance(sources, list) else ())
            if isinstance(source, Mapping)
        }
        csi300_ids = {
            str(source.get("announcement_id") or "")
            for source in (sources if isinstance(sources, list) else ())
            if isinstance(source, Mapping)
            and source.get("contains_csi300") is True
        }
        publish_date = str(spec["announcement_publish_date"])
        if (
            row.get("host") != "oss-ch.csindex.com.cn"
            or row.get("extension") != "xls"
            or row.get("path_dates") != []
            or row.get("reference_disposition")
            != "blocked_attachment_path_date_unproven"
            or source_ids != sorted(str(value) for value in spec["announcement_ids"])
            or source_dates != {publish_date}
            or csi300_ids != {str(spec["csi300_announcement_id"])}
        ):
            raise ValueError(f"csindex_legacy_cons_repair_source_invalid:{url}")
        observed.add(url)
        row |= {
            "pre_repair_reference_disposition": row["reference_disposition"],
            "reference_disposition": "capture_eligible",
            "path_dates": [publish_date.replace("-", "")],
            "legacy_cons_repair_profile": CSINDEX_LEGACY_CONS_REPAIR_PROFILE,
            "reviewed_source_announcement_ids": list(spec["announcement_ids"]),
            "reviewed_csi300_announcement_id": spec[
                "csi300_announcement_id"
            ],
            "historical_known_at_proven": False,
            "pit_membership_authorized": False,
        }
        repaired.append(row)
    if observed != set(expected):
        raise ValueError("csindex_legacy_cons_repair_exact_population_missing")
    _validate_legacy_cons_repair_population(repaired)
    return repaired


def _validate_legacy_cons_repair_population(
    population: Sequence[Mapping[str, Any]],
) -> None:
    expected = {
        str(row["attachment_url"]): row for row in CSINDEX_LEGACY_CONS_REPAIR_ROWS
    }
    eligible = [
        row
        for row in population
        if row.get("reference_disposition") == "capture_eligible"
    ]
    if len(eligible) != 2 or {str(row.get("attachment_url") or "") for row in eligible} != set(expected):
        raise ValueError("csindex_legacy_cons_repair_exact_population_invalid")
    for row in eligible:
        url = str(row["attachment_url"])
        spec = expected[url]
        sources = row.get("source_announcements")
        source_ids = (
            sorted(str(source.get("announcement_id") or "") for source in sources)
            if isinstance(sources, list)
            and all(isinstance(source, Mapping) for source in sources)
            else []
        )
        source_dates = {
            str(source.get("announcement_publish_date") or "")
            for source in (sources if isinstance(sources, list) else ())
            if isinstance(source, Mapping)
        }
        csi300_ids = {
            str(source.get("announcement_id") or "")
            for source in (sources if isinstance(sources, list) else ())
            if isinstance(source, Mapping)
            and source.get("contains_csi300") is True
        }
        if (
            row.get("host") != "oss-ch.csindex.com.cn"
            or row.get("extension") != "xls"
            or row.get("path_dates")
            != [str(spec["announcement_publish_date"]).replace("-", "")]
            or row.get("legacy_cons_repair_profile")
            != CSINDEX_LEGACY_CONS_REPAIR_PROFILE
            or row.get("reviewed_source_announcement_ids")
            != list(spec["announcement_ids"])
            or row.get("reviewed_csi300_announcement_id")
            != spec["csi300_announcement_id"]
            or source_ids
            != sorted(str(value) for value in spec["announcement_ids"])
            or source_dates != {str(spec["announcement_publish_date"])}
            or csi300_ids != {str(spec["csi300_announcement_id"])}
            or row.get("historical_known_at_proven") is not False
            or row.get("pit_membership_authorized") is not False
        ):
            raise ValueError("csindex_legacy_cons_repair_exact_population_invalid")


def _legacy_cons_repair_path_dates(url: str) -> list[str]:
    for row in CSINDEX_LEGACY_CONS_REPAIR_ROWS:
        if row["attachment_url"] == url:
            return [str(row["announcement_publish_date"]).replace("-", "")]
    raise ValueError("csindex_legacy_cons_repair_url_invalid")


def _restore_full_attachment_population(
    population: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for value in population:
        row = dict(value)
        if row.pop("capture_profile_omission", False) is True:
            if row.get("reference_disposition") != (
                "blocked_capture_profile_host_omission"
            ):
                raise ValueError("csindex_attachment_host_slice_invalid")
            row["reference_disposition"] = "capture_eligible"
        rows.append(row)
    return rows


def _attachment_request_identity(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "attachment_url": row.get("attachment_url"),
        "host": row.get("host"),
        "extension": row.get("extension"),
        "path_dates": row.get("path_dates"),
        "reference_disposition": row.get("reference_disposition"),
        "source_announcements": row.get("source_announcements"),
    }


def _attachment_requests(
    population: Sequence[Mapping[str, Any]],
    *,
    source_ancestry: Mapping[str, Any],
    source_binding: Mapping[str, Any],
    capture_profile: str,
) -> list[ProviderProbeRequest]:
    required_checks = (
        "http_envelope_schema_exact",
        "request_method_bound",
        "redirect_not_followed",
        "http_status_exact",
        "request_url_bound",
        "nonempty_attachment",
        "content_length_matches",
        "content_type_compatible",
        "attachment_magic_matches",
        "not_html_or_waf",
        "body_sha256_matches",
    )
    capture_population = [
        row
        for row in population
        if row.get("reference_disposition") == "capture_eligible"
    ]
    blocked_references = [
        dict(row)
        for row in population
        if row.get("reference_disposition") != "capture_eligible"
    ]
    if not capture_population:
        raise ValueError("csindex_attachment_capture_plan_empty")
    ancestry = _validate_csindex_source_ancestry(
        source_ancestry, expected_stage="details_capture"
    )
    if capture_profile == CSINDEX_LEGACY_CONS_REPAIR_PROFILE:
        if ancestry["weak_source_ancestry"]:
            raise ValueError("csindex_legacy_cons_repair_weak_source_blocked")
        _validate_legacy_cons_repair_population(population)
    binding = _validate_csindex_source_binding(
        source_binding,
        phase=(
            "csindex-legacy-cons-repair"
            if capture_profile == CSINDEX_LEGACY_CONS_REPAIR_PROFILE
            else "csindex-attachments"
        ),
        source_ancestry=ancestry,
    )
    blocked_reference_root = canonical_hash(blocked_references)
    requests = [
        ProviderProbeRequest(
            request_id=(
                "csindex_attachment_"
                + hashlib.sha256(str(row["attachment_url"]).encode()).hexdigest()[:24]
            ),
            provider="csindex",
            endpoint="index_rebalance_announcement_attachment",
            method="GET",
            url=str(row["attachment_url"]),
            headers={
                "Referer": "https://www.csindex.com.cn/",
                "User-Agent": USER_AGENT,
            },
            disposition="bounded_backfill",
            evidence_semantics="official_http_binary_response_envelope",
            expected_terminal_states=("positive",),
            required_checks=required_checks,
            metadata={
                "case": "csindex_attachment",
                "extension": row["extension"],
                "attachment_host": row["host"],
                "source_announcements": row["source_announcements"],
                "path_dates": row["path_dates"],
                "reference_disposition": row["reference_disposition"],
                "blocked_reference_count": len(blocked_references),
                "blocked_reference_root": blocked_reference_root,
                "population_count": len(population),
                "population_root": canonical_hash(population),
                "source_ancestry": ancestry,
                "source_binding": binding,
                "capture_profile": capture_profile,
                "profile_complete": capture_profile
                in {
                    CSINDEX_ATTACHMENT_FULL_PROFILE,
                    CSINDEX_LEGACY_CONS_REPAIR_PROFILE,
                },
                "temporal_blocker": CSINDEX_ATTACHMENT_TEMPORAL_BLOCKER,
                "historical_known_at_proven": False,
            },
        )
        for row in capture_population
    ]
    requests[0] = ProviderProbeRequest(
        **{
            **requests[0].__dict__,
            "metadata": dict(requests[0].metadata)
            | {
                "blocked_references": blocked_references,
                "attachment_population": [dict(row) for row in population],
            },
        }
    )
    return requests


def normalize_csindex_discovery(
    run_root: Path,
    requests: Sequence[ProviderProbeRequest],
    terminal: Mapping[str, Mapping[str, Any]],
) -> Sequence[NormalizedArtifact]:
    return _normalize_list_pages(
        run_root,
        requests,
        terminal,
        require_full_page_chains=False,
    )


def normalize_csindex_inventory(
    run_root: Path,
    requests: Sequence[ProviderProbeRequest],
    terminal: Mapping[str, Mapping[str, Any]],
) -> Sequence[NormalizedArtifact]:
    return _normalize_list_pages(
        run_root,
        requests,
        terminal,
        require_full_page_chains=True,
    )


def normalize_csindex_details(
    run_root: Path,
    requests: Sequence[ProviderProbeRequest],
    terminal: Mapping[str, Mapping[str, Any]],
) -> Sequence[NormalizedArtifact]:
    if not requests:
        raise ValueError("csindex_detail_requests_empty")
    if any(
        request.metadata.get("capture_profile") != CSINDEX_FULL_PROFILE
        or request.metadata.get("profile_complete") is not True
        for request in requests
    ):
        raise ValueError("csindex_detail_profile_incomplete")
    source_ancestry, source_binding = _csindex_request_source_evidence(
        requests, phase="csindex-details"
    )
    _validate_csindex_source_ancestry(
        source_ancestry, expected_stage="inventory_capture"
    )
    source_rows = [request.metadata.get("source_inventory_row") for request in requests]
    if (
        any(not isinstance(row, Mapping) for row in source_rows)
        or len(
            {
                str(row.get("announcement_id") or "")
                for row in source_rows
                if isinstance(row, Mapping)
            }
        )
        != len(source_rows)
    ):
        raise ValueError("csindex_detail_population_invalid")
    resolved_rows = [dict(row) for row in source_rows if isinstance(row, Mapping)]
    base_requests = [_csindex_detail_request(row) for row in resolved_rows]
    expected_requests = [
        _with_csindex_source_evidence(request, source_ancestry, source_binding)
        for request in base_requests
    ]
    if [request.semantic() for request in requests] != [
        request.semantic() for request in expected_requests
    ]:
        raise ValueError("csindex_detail_request_closure_invalid")
    derivation = dict(source_binding.get("derivation") or {})
    expected_derivation = {
        "capture_content_hash": source_ancestry["direct_sources"][0][
            "source_content_hash"
        ],
        "normalized_replay_root": derivation.get("normalized_replay_root"),
        "resolved_population_root": canonical_hash(resolved_rows),
        "request_semantics_root": canonical_hash(
            [request.semantic() for request in base_requests]
        ),
        "implementation_root": derivation.get("implementation_root"),
    }
    if any(
        re.fullmatch(r"[0-9a-f]{64}", str(expected_derivation[key] or ""))
        is None
        for key in ("normalized_replay_root", "implementation_root")
    ):
        raise ValueError("csindex_source_binding_derivation_invalid")
    _validate_csindex_binding_derivation(
        source_ancestry,
        source_binding,
        phase="csindex-details",
        expected_derivation=expected_derivation,
    )
    output = run_root / "normalized"
    output.mkdir(exist_ok=True)
    details_path = output / "announcement_details.jsonl"
    candidates_path = output / "csi300_candidate_announcements.jsonl"
    conflicts_path = output / "conflicts.jsonl"
    detail_count = 0
    candidate_count = 0
    conflicts: list[dict[str, Any]] = []
    with details_path.open("wb") as details, candidates_path.open("wb") as candidates:
        for request in requests:
            receipt = terminal[request.request_id]
            wrapper = read_json(run_root / str(receipt["raw_envelope_relative_path"]))
            _official, body = _decode_csindex_official_http_envelope(
                wrapper,
                request=request,
                terminal=receipt,
            )
            provider = json.loads(body)
            if (
                not isinstance(provider, Mapping)
                or provider.get("success") is not True
                or str(provider.get("code") or "") != "200"
            ):
                raise ValueError(
                    "csindex_detail_provider_status_invalid:"
                    f"{request.request_id}"
                )
            detail = provider.get("data") if isinstance(provider, Mapping) else None
            if not isinstance(detail, Mapping):
                conflicts.append(
                    {
                        "announcement_id": request.metadata["announcement_id"],
                        "reason": "detail_shape_invalid",
                    }
                )
                continue
            observed_id = str(detail.get("id") or "")
            expected_id = str(request.metadata["announcement_id"])
            if observed_id != expected_id:
                conflicts.append(
                    {
                        "announcement_id": expected_id,
                        "observed_id": observed_id,
                        "reason": "detail_identity_mismatch",
                    }
                )
                continue
            observed_publish_date = _strict_iso_date(detail.get("publishDate"))
            expected_publish_date = _strict_iso_date(
                request.metadata.get("publish_date")
            )
            if observed_publish_date is None or observed_publish_date != expected_publish_date:
                conflicts.append(
                    {
                        "announcement_id": expected_id,
                        "observed_publish_date": (
                            observed_publish_date.isoformat()
                            if observed_publish_date is not None
                            else None
                        ),
                        "expected_publish_date": (
                            expected_publish_date.isoformat()
                            if expected_publish_date is not None
                            else None
                        ),
                        "reason": "detail_publish_date_mismatch",
                    }
                )
                continue
            content = str(detail.get("content") or "")
            row = {
                "announcement_id": expected_id,
                "publish_date": detail.get("publishDate"),
                "title": detail.get("title"),
                "content_html": content,
                "enclosure_list": detail.get("enclosureList") or [],
                "image_list": detail.get("imgList") or [],
                "list_file_url": request.metadata.get("list_file_url"),
                "list_file_name": request.metadata.get("list_file_name"),
                "contains_csi300": "沪深300" in content or "沪深 300" in content or "沪深300" in str(detail.get("title") or ""),
                "source_request_id": request.request_id,
                "source_payload_sha256": wrapper["raw_payload_sha256"],
            }
            _write_row(details, row)
            detail_count += 1
            if row["contains_csi300"]:
                _write_row(candidates, row)
                candidate_count += 1
        for handle in (details, candidates):
            handle.flush()
            os.fsync(handle.fileno())
    _atomic_jsonl(conflicts_path, conflicts)
    if conflicts:
        raise ValueError(f"csindex_detail_normalization_invalid:{conflicts[0]['reason']}")
    manifest_path = output / "normalized_manifest.json"
    manifest = {
        "schema_version": "csindex_detail_normalization_v2",
        "capture_profile": CSINDEX_FULL_PROFILE,
        "profile_complete": True,
        "resolved_population_root": canonical_hash(resolved_rows),
        "detail_count": detail_count,
        "csi300_candidate_count": candidate_count,
        "conflict_count": len(conflicts),
        "details_sha256": sha256_file(details_path),
        "candidates_sha256": sha256_file(candidates_path),
        "pit_event_parser_complete": False,
        "csi300_membership_chain_complete": False,
        "blockers": [
            "csi300_effective_date_and_member_table_parser_not_run",
            "csi300_seed_membership_not_proven",
            "historical_daily_weight_unavailable",
        ],
        "source_ancestry": source_ancestry,
        "source_binding": source_binding,
        "weak_source_ancestry": source_ancestry["weak_source_ancestry"],
    }
    if source_ancestry["weak_source_ancestry"]:
        manifest["blockers"].append("weak_source_acquisition_ancestry")
    manifest["content_hash"] = canonical_hash(manifest)
    _atomic_json(manifest_path, manifest)
    return (
        NormalizedArtifact("csindex_announcement_details", "normalized/announcement_details.jsonl", detail_count),
        NormalizedArtifact("csi300_candidate_announcements", "normalized/csi300_candidate_announcements.jsonl", candidate_count),
        NormalizedArtifact("conflicts", "normalized/conflicts.jsonl", 0),
        NormalizedArtifact("normalized_manifest", "normalized/normalized_manifest.json", 1),
    )


def normalize_csindex_attachments(
    run_root: Path,
    requests: Sequence[ProviderProbeRequest],
    terminal: Mapping[str, Mapping[str, Any]],
) -> Sequence[NormalizedArtifact]:
    """Write only a binary index; exact attachment bytes remain in signed raw."""

    output = run_root / "normalized"
    output.mkdir(exist_ok=True)
    index_path = output / "attachment_index.jsonl"
    blocked_path = output / "blocked_reference_index.jsonl"
    if not requests:
        raise ValueError("csindex_attachment_normalization_requests_empty")
    capture_profiles = {request.metadata.get("capture_profile") for request in requests}
    if len(capture_profiles) != 1:
        raise ValueError("csindex_attachment_capture_profile_mixed")
    capture_profile = str(next(iter(capture_profiles)))
    phase = (
        "csindex-legacy-cons-repair"
        if capture_profile == CSINDEX_LEGACY_CONS_REPAIR_PROFILE
        else "csindex-attachments"
    )
    if capture_profile not in {
        CSINDEX_ATTACHMENT_FULL_PROFILE,
        CSINDEX_ATTACHMENT_HOST_SLICE_PROFILE,
        CSINDEX_LEGACY_CONS_REPAIR_PROFILE,
    }:
        raise ValueError("csindex_attachment_capture_profile_invalid")
    source_ancestry, source_binding = _csindex_request_source_evidence(
        requests, phase=phase
    )
    _validate_csindex_source_ancestry(
        source_ancestry, expected_stage="details_capture"
    )
    population = requests[0].metadata.get("attachment_population")
    blocked_references = requests[0].metadata.get("blocked_references")
    if not isinstance(population, list) or not isinstance(blocked_references, list):
        raise ValueError("csindex_attachment_population_audit_missing")
    expected_requests = _attachment_requests(
        population,
        source_ancestry=source_ancestry,
        source_binding=source_binding,
        capture_profile=capture_profile,
    )
    if [request.semantic() for request in requests] != [
        request.semantic() for request in expected_requests
    ]:
        raise ValueError("csindex_attachment_request_closure_invalid")
    blocked_reference_root = canonical_hash(blocked_references)
    if any(
        request.metadata.get("blocked_reference_count") != len(blocked_references)
        or request.metadata.get("blocked_reference_root") != blocked_reference_root
        or request.metadata.get("population_count") != len(population)
        or request.metadata.get("population_root") != canonical_hash(population)
        or request.metadata.get("source_ancestry") != source_ancestry
        or request.metadata.get("source_binding") != source_binding
        or request.metadata.get("reference_disposition") != "capture_eligible"
        or request.metadata.get("path_dates")
        != (
            _legacy_cons_repair_path_dates(request.url)
            if phase == "csindex-legacy-cons-repair"
            else _attachment_path_dates(urllib.parse.urlsplit(request.url).path)
        )
        or not request.metadata.get("path_dates")
        or any(
            value < CSINDEX_ATTACHMENT_PATH_DATE_START
            or value > CSINDEX_ATTACHMENT_PATH_DATE_END
            for value in request.metadata.get("path_dates") or ()
        )
        or (
            index > 0
            and any(
                key in request.metadata
                for key in ("blocked_references", "attachment_population")
            )
        )
        for index, request in enumerate(requests)
    ):
        raise ValueError("csindex_attachment_reference_audit_invalid")
    if any(
        row.get("reference_disposition") == "capture_eligible"
        for row in blocked_references
        if isinstance(row, Mapping)
    ) or any(not isinstance(row, Mapping) for row in blocked_references):
        raise ValueError("csindex_attachment_blocked_reference_audit_invalid")
    derivation = dict(source_binding.get("derivation") or {})
    full_population = _restore_full_attachment_population(population)
    capture_population = [
        row
        for row in population
        if isinstance(row, Mapping)
        and row.get("reference_disposition") == "capture_eligible"
    ]
    common_derivation = {
        "capture_content_hash": source_ancestry["direct_sources"][0][
            "source_content_hash"
        ],
        "normalized_replay_root": derivation.get("normalized_replay_root"),
        "request_semantics_root": canonical_hash(
            [_attachment_request_identity(row) for row in capture_population]
        ),
        "selected_hosts": derivation.get("selected_hosts"),
        "implementation_root": derivation.get("implementation_root"),
    }
    expected_derivation = (
        common_derivation
        | {
            "repair_population_root": canonical_hash(population),
            "repair_profile_root": canonical_hash(
                CSINDEX_LEGACY_CONS_REPAIR_ROWS
            ),
        }
        if phase == "csindex-legacy-cons-repair"
        else common_derivation
        | {
            "full_population_root": canonical_hash(full_population),
            "selected_population_root": canonical_hash(population),
        }
    )
    if (
        any(
            re.fullmatch(r"[0-9a-f]{64}", str(expected_derivation[key] or ""))
            is None
            for key in ("normalized_replay_root", "implementation_root")
        )
        or not isinstance(expected_derivation["selected_hosts"], list)
        or not set(expected_derivation["selected_hosts"])
        <= set(CSINDEX_ATTACHMENT_HOSTS)
        or (
            capture_profile == CSINDEX_ATTACHMENT_FULL_PROFILE
            and expected_derivation["selected_hosts"]
            != list(CSINDEX_ATTACHMENT_HOSTS)
        )
        or (
            capture_profile == CSINDEX_ATTACHMENT_HOST_SLICE_PROFILE
            and (
                not expected_derivation["selected_hosts"]
                or set(expected_derivation["selected_hosts"])
                >= set(CSINDEX_ATTACHMENT_HOSTS)
            )
        )
        or (
            capture_profile == CSINDEX_LEGACY_CONS_REPAIR_PROFILE
            and expected_derivation["selected_hosts"]
            != ["oss-ch.csindex.com.cn"]
        )
    ):
        raise ValueError("csindex_source_binding_derivation_invalid")
    _validate_csindex_binding_derivation(
        source_ancestry,
        source_binding,
        phase=phase,
        expected_derivation=expected_derivation,
    )
    _atomic_jsonl(blocked_path, blocked_references)
    count = 0
    with index_path.open("wb") as handle:
        for request in requests:
            receipt = terminal[request.request_id]
            wrapper = read_json(
                run_root / str(receipt["raw_envelope_relative_path"])
            )
            official, body = _decode_csindex_official_http_envelope(
                wrapper,
                request=request,
                terminal=receipt,
                max_body_bytes=CSINDEX_ATTACHMENT_BODY_MAX_BYTES,
            )
            response_headers = {
                str(key).lower(): str(value)
                for key, value in (official.get("response_headers") or {}).items()
            }
            extension = str(request.metadata.get("extension") or "").lower()
            block_reason = _attachment_block_reason(body)
            if (
                official.get("schema_version")
                != "official_http_probe_envelope_v1"
                or official.get("method") != "GET"
                or official.get("redirect_followed") is not False
                or official.get("status_code") != 200
                or str(official.get("url") or "") != request.url
                or not body
                or not _attachment_content_length_matches(
                    response_headers.get("content-length"), len(body)
                )
                or not _attachment_content_type_compatible(
                    extension, response_headers.get("content-type")
                )
                or not _attachment_magic_valid(body, extension)
                or block_reason is not None
                or str(official.get("body_sha256") or "")
                != hashlib.sha256(body).hexdigest()
            ):
                raise ValueError(
                    "csindex_attachment_normalization_wire_evidence_invalid:"
                    f"{request.request_id}"
                )
            row = {
                "attachment_url": request.url,
                "attachment_host": request.metadata.get("attachment_host"),
                "attachment_extension": extension,
                "path_dates": request.metadata.get("path_dates"),
                "reference_disposition": request.metadata.get(
                    "reference_disposition"
                ),
                "attachment_sha256": hashlib.sha256(body).hexdigest(),
                "attachment_size_bytes": len(body),
                "source_announcements": request.metadata.get(
                    "source_announcements"
                ),
                "source_request_id": request.request_id,
                "source_payload_sha256": wrapper["raw_payload_sha256"],
                "historical_known_at": None,
                "historical_known_at_proven": False,
                "temporal_blocker": CSINDEX_ATTACHMENT_TEMPORAL_BLOCKER,
            }
            _write_row(handle, row)
            count += 1
        handle.flush()
        os.fsync(handle.fileno())
    manifest_path = output / "normalized_manifest.json"
    manifest = {
        "schema_version": (
            "csindex_legacy_cons_repair_normalization_v1"
            if phase == "csindex-legacy-cons-repair"
            else "csindex_attachment_normalization_v3"
        ),
        "capture_profile": capture_profile,
        "profile_complete": capture_profile
        in {CSINDEX_ATTACHMENT_FULL_PROFILE, CSINDEX_LEGACY_CONS_REPAIR_PROFILE},
        "attachment_count": count,
        "attachment_index_sha256": sha256_file(index_path),
        "blocked_reference_count": len(blocked_references),
        "blocked_reference_root": blocked_reference_root,
        "blocked_reference_index_sha256": sha256_file(blocked_path),
        "source_ancestry": dict(source_ancestry),
        "source_binding": dict(source_binding),
        "weak_source_ancestry": source_ancestry["weak_source_ancestry"],
        "raw_capture_contains_exact_attachment_bytes": True,
        "binary_payloads_extracted_from_raw": False,
        "historical_known_at_proven": False,
        "pit_membership_authorized": False,
        "blockers": [
            CSINDEX_ATTACHMENT_TEMPORAL_BLOCKER,
            "csi300_attachment_semantic_parser_not_run",
        ]
        + (
            ["weak_source_acquisition_ancestry"]
            if source_ancestry["weak_source_ancestry"]
            else []
        ),
    }
    manifest["content_hash"] = canonical_hash(manifest)
    _atomic_json(manifest_path, manifest)
    return (
        NormalizedArtifact(
            "csindex_attachment_index",
            "normalized/attachment_index.jsonl",
            count,
        ),
        NormalizedArtifact(
            "csindex_blocked_reference_index",
            "normalized/blocked_reference_index.jsonl",
            len(blocked_references),
        ),
        NormalizedArtifact(
            "normalized_manifest", "normalized/normalized_manifest.json", 1
        ),
    )


def normalize_csindex_legacy_cons_repair(
    run_root: Path,
    requests: Sequence[ProviderProbeRequest],
    terminal: Mapping[str, Mapping[str, Any]],
) -> Sequence[NormalizedArtifact]:
    if len(requests) != 2:
        raise ValueError("csindex_legacy_cons_repair_request_count_invalid")
    population = requests[0].metadata.get("attachment_population")
    if not isinstance(population, list):
        raise ValueError("csindex_legacy_cons_repair_population_missing")
    _validate_legacy_cons_repair_population(population)
    ancestry, _binding = _csindex_request_source_evidence(
        requests, phase="csindex-legacy-cons-repair"
    )
    if ancestry["weak_source_ancestry"]:
        raise ValueError("csindex_legacy_cons_repair_weak_source_blocked")
    return normalize_csindex_attachments(run_root, requests, terminal)


def _normalize_list_pages(
    run_root: Path,
    requests: Sequence[ProviderProbeRequest],
    terminal: Mapping[str, Mapping[str, Any]],
    *,
    require_full_page_chains: bool,
) -> Sequence[NormalizedArtifact]:
    output = run_root / "normalized"
    output.mkdir(exist_ok=True)
    profiles = {request.metadata.get("capture_profile") for request in requests}
    completeness = {request.metadata.get("profile_complete") for request in requests}
    expected_profile = CSINDEX_FULL_PROFILE
    if (
        len(profiles) != 1
        or len(completeness) != 1
        or next(iter(profiles), None)
        not in {CSINDEX_FULL_PROFILE, CSINDEX_DISCOVERY_SLICE_PROFILE}
        or next(iter(completeness), None) not in {True, False}
    ):
        raise ValueError("csindex_capture_profile_mixed_or_missing")
    capture_profile = str(next(iter(profiles)))
    declared_complete = next(iter(completeness)) is True
    source_ancestry: dict[str, Any] | None = None
    source_binding: dict[str, Any] | None = None
    if require_full_page_chains:
        if capture_profile != expected_profile or not declared_complete:
            raise ValueError("csindex_inventory_profile_incomplete")
        source_ancestry, source_binding = _csindex_request_source_evidence(
            requests, phase="csindex-inventory"
        )
        _validate_csindex_source_ancestry(
            source_ancestry, expected_stage="discovery_capture"
        )
    elif any(
        request.metadata.get(key) is not None
        for request in requests
        for key in ("source_ancestry", "source_binding")
    ):
        raise ValueError("csindex_discovery_source_evidence_unexpected")
    pages_by_leaf: dict[str, list[tuple[ProviderProbeRequest, dict[str, Any], str]]] = defaultdict(list)
    filter_captured = False
    for request in requests:
        receipt = terminal[request.request_id]
        wrapper = read_json(run_root / str(receipt["raw_envelope_relative_path"]))
        _official, body_bytes = _decode_csindex_official_http_envelope(
            wrapper,
            request=request,
            terminal=receipt,
        )
        body = json.loads(body_bytes)
        if request.metadata.get("case") == "csindex_filter":
            filter_captured = _csindex_filter_topic_present(body)
            continue
        pages_by_leaf[str(request.metadata["leaf_id"])].append(
            (request, body, wrapper["raw_payload_sha256"])
        )
    resolved_population, base_requests, profile_complete = (
        _validate_csindex_list_request_closure(
            requests=requests,
            pages_by_leaf=pages_by_leaf,
            filter_captured=filter_captured,
            require_full_page_chains=require_full_page_chains,
            capture_profile=capture_profile,
            source_ancestry=source_ancestry,
            source_binding=source_binding,
        )
    )
    inventory: dict[str, dict[str, Any]] = {}
    coverage: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for leaf_id, captured in sorted(pages_by_leaf.items()):
        captured.sort(key=lambda value: int(value[0].metadata["page"]))
        totals = {_nonnegative_int(body.get("total")) for _request, body, _hash in captured}
        total = next(iter(totals)) if len(totals) == 1 else None
        expected_pages = max(1, math.ceil((total or 0) / CSINDEX_PAGE_SIZE))
        ordinals = [int(request.metadata["page"]) for request, _body, _hash in captured]
        ids: set[str] = set()
        duplicates: set[str] = set()
        current_pages: list[int | None] = []
        for request, body, source_hash in captured:
            current_pages.append(_nonnegative_int(body.get("currentPage")))
            if body.get("success") is not True or str(body.get("code") or "") != "200":
                conflicts.append({"leaf_id": leaf_id, "reason": "provider_status_invalid"})
            rows = body.get("data") if isinstance(body, Mapping) else None
            if not isinstance(rows, list):
                conflicts.append({"leaf_id": leaf_id, "reason": "list_shape_invalid"})
                rows = []
            for provider_row in rows:
                if not isinstance(provider_row, Mapping):
                    conflicts.append({"leaf_id": leaf_id, "reason": "list_row_shape_invalid"})
                    continue
                announcement_id = str(provider_row.get("id") or "")
                publish_date = _strict_iso_date(provider_row.get("publishDate"))
                date_start = _strict_iso_date(request.metadata.get("date_start"))
                date_end = _strict_iso_date(request.metadata.get("date_end"))
                if not (
                    publish_date is not None
                    and date_start is not None
                    and date_end is not None
                    and date_start <= publish_date <= date_end
                ):
                    conflicts.append(
                        {
                            "leaf_id": leaf_id,
                            "announcement_id": announcement_id,
                            "reason": "publication_date_outside_leaf",
                        }
                    )
                    continue
                if not announcement_id:
                    conflicts.append({"leaf_id": leaf_id, "reason": "announcement_id_missing"})
                    continue
                if announcement_id in ids:
                    duplicates.add(announcement_id)
                ids.add(announcement_id)
                normalized = {
                    "announcement_id": announcement_id,
                    "title": provider_row.get("title"),
                    "theme": provider_row.get("theme"),
                    "publish_date": provider_row.get("publishDate"),
                    "notice_type": provider_row.get("noticeType"),
                    "file_url": provider_row.get("fileUrl"),
                    "file_name": provider_row.get("fileName"),
                    "source_leaf_id": leaf_id,
                    "source_request_id": request.request_id,
                    "source_payload_sha256": source_hash,
                }
                prior = inventory.setdefault(announcement_id, normalized)
                if any(
                    prior.get(key) != normalized.get(key)
                    for key in ("title", "publish_date", "notice_type")
                ):
                    conflicts.append(
                        {
                            "leaf_id": leaf_id,
                            "announcement_id": announcement_id,
                            "reason": "announcement_identity_conflict",
                        }
                    )
        full_valid = (
            total is not None
            and ordinals == list(range(1, expected_pages + 1))
            and current_pages == ordinals
            and len(ids) == total
            and not duplicates
        )
        if require_full_page_chains and not full_valid:
            conflicts.append(
                {
                    "leaf_id": leaf_id,
                    "reason": "page_chain_incomplete_or_clamped",
                    "reported_totals": sorted(value for value in totals if value is not None),
                    "expected_pages": expected_pages,
                    "requested_pages": ordinals,
                    "provider_current_pages": current_pages,
                    "unique_ids": len(ids),
                    "duplicate_ids": sorted(duplicates)[:20],
                }
            )
        coverage.append(
            {
                "leaf_id": leaf_id,
                "reported_total": total,
                "expected_page_count": expected_pages,
                "captured_pages": ordinals,
                "provider_current_pages": current_pages,
                "unique_announcement_count": len(ids),
                "full_page_chain_valid": full_valid if require_full_page_chains else False,
            }
        )
    if require_full_page_chains and conflicts:
        raise ValueError(f"csindex_inventory_page_chain_invalid:{conflicts[0]['reason']}")
    inventory_path = output / "announcement_inventory.jsonl"
    coverage_path = output / "page_coverage.jsonl"
    conflicts_path = output / "conflicts.jsonl"
    _atomic_jsonl(inventory_path, [inventory[key] for key in sorted(inventory, key=int)])
    _atomic_jsonl(coverage_path, coverage)
    _atomic_jsonl(conflicts_path, conflicts)
    manifest_path = output / "normalized_manifest.json"
    manifest = {
        "schema_version": (
            "csindex_announcement_inventory_normalization_v2"
            if require_full_page_chains
            else "csindex_announcement_discovery_normalization_v2"
        ),
        "capture_profile": capture_profile,
        "profile_complete": profile_complete,
        "require_full_page_chains": require_full_page_chains,
        "filter_topics_captured": filter_captured,
        "leaf_count": len(pages_by_leaf),
        "announcement_count": len(inventory),
        "conflict_count": len(conflicts),
        "all_page_chains_valid": require_full_page_chains and not conflicts,
        "inventory_sha256": sha256_file(inventory_path),
        "coverage_sha256": sha256_file(coverage_path),
    }
    if source_ancestry is not None and source_binding is not None:
        derivation = dict(source_binding.get("derivation") or {})
        expected_derivation = {
            "capture_content_hash": (
                source_ancestry["direct_sources"][0]["source_content_hash"]
            ),
            "normalized_replay_root": derivation.get("normalized_replay_root"),
            "resolved_population_root": canonical_hash(resolved_population),
            "request_semantics_root": canonical_hash(
                [request.semantic() for request in base_requests]
            ),
            "implementation_root": derivation.get("implementation_root"),
        }
        if any(
            re.fullmatch(r"[0-9a-f]{64}", str(expected_derivation[key] or ""))
            is None
            for key in ("normalized_replay_root", "implementation_root")
        ):
            raise ValueError("csindex_source_binding_derivation_invalid")
        _validate_csindex_binding_derivation(
            source_ancestry,
            source_binding,
            phase="csindex-inventory",
            expected_derivation=expected_derivation,
        )
        manifest["source_ancestry"] = source_ancestry
        manifest["source_binding"] = source_binding
        manifest["weak_source_ancestry"] = source_ancestry[
            "weak_source_ancestry"
        ]
        if source_ancestry["weak_source_ancestry"]:
            manifest["blockers"] = ["weak_source_acquisition_ancestry"]
    manifest["content_hash"] = canonical_hash(manifest)
    _atomic_json(manifest_path, manifest)
    return (
        NormalizedArtifact("csindex_announcement_inventory", "normalized/announcement_inventory.jsonl", len(inventory)),
        NormalizedArtifact("csindex_page_coverage", "normalized/page_coverage.jsonl", len(coverage)),
        NormalizedArtifact("conflicts", "normalized/conflicts.jsonl", len(conflicts)),
        NormalizedArtifact("normalized_manifest", "normalized/normalized_manifest.json", 1),
    )


def _validate_csindex_list_request_closure(
    *,
    requests: Sequence[ProviderProbeRequest],
    pages_by_leaf: Mapping[
        str,
        Sequence[tuple[ProviderProbeRequest, Mapping[str, Any], str]],
    ],
    filter_captured: bool,
    require_full_page_chains: bool,
    capture_profile: str,
    source_ancestry: Mapping[str, Any] | None,
    source_binding: Mapping[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[ProviderProbeRequest], bool]:
    all_leaves = _month_leaves()
    by_leaf = {leaf["leaf_id"]: leaf for leaf in all_leaves}
    actual_leaf_ids = set(pages_by_leaf)
    filter_requests = [
        request
        for request in requests
        if request.metadata.get("case") == "csindex_filter"
    ]
    if (
        not filter_captured
        or len(filter_requests) != 1
        or not actual_leaf_ids
        or not actual_leaf_ids <= set(by_leaf)
        or len({request.request_id for request in requests}) != len(requests)
        or (
            capture_profile == CSINDEX_FULL_PROFILE
            and actual_leaf_ids != set(by_leaf)
        )
    ):
        raise ValueError("csindex_list_request_population_closure_invalid")
    base_requests = [
        _with_csindex_capture_profile(
            _filter_request(),
            capture_profile=capture_profile,
            profile_complete=capture_profile == CSINDEX_FULL_PROFILE,
        )
    ]
    resolved: list[dict[str, Any]] = []
    for leaf in all_leaves:
        leaf_id = leaf["leaf_id"]
        if leaf_id not in actual_leaf_ids:
            continue
        captured = list(pages_by_leaf[leaf_id])
        for request, body, _source_hash in captured:
            rows = body.get("data") if isinstance(body, Mapping) else None
            page_size = _nonnegative_int(
                body.get("pageSize") if isinstance(body, Mapping) else None
            )
            total_value = _nonnegative_int(
                body.get("total") if isinstance(body, Mapping) else None
            )
            if (
                not isinstance(rows, list)
                or body.get("success") is not True
                or str(body.get("code") or "") != "200"
                or _nonnegative_int(body.get("currentPage"))
                != int(request.metadata.get("page") or -1)
                or not (
                    page_size == CSINDEX_PAGE_SIZE
                    or (total_value == 0 and rows == [] and page_size == 0)
                )
                or any(
                    not isinstance(row, Mapping)
                    or (publish_date := _strict_iso_date(row.get("publishDate")))
                    is None
                    or (date_start := _strict_iso_date(
                        request.metadata.get("date_start")
                    ))
                    is None
                    or (date_end := _strict_iso_date(
                        request.metadata.get("date_end")
                    ))
                    is None
                    or not (date_start <= publish_date <= date_end)
                    for row in rows
                )
            ):
                raise ValueError(
                    f"csindex_list_response_semantics_invalid:{request.request_id}"
                )
        totals = {
            _nonnegative_int(body.get("total")) for _request, body, _hash in captured
        }
        if len(totals) != 1 or None in totals:
            raise ValueError(f"csindex_list_total_invalid:{leaf_id}")
        total = int(next(iter(totals)))
        page_count = max(1, math.ceil(total / CSINDEX_PAGE_SIZE))
        if page_count > CSINDEX_MAX_PAGES_PER_MONTH:
            raise ValueError(f"csindex_list_page_budget_invalid:{leaf_id}")
        expected_pages = (
            range(1, page_count + 1) if require_full_page_chains else (1,)
        )
        resolved_leaf = dict(leaf) | {
            "reported_total": total,
            "page_count": page_count,
        }
        resolved.append(resolved_leaf)
        base_requests.extend(
            _with_csindex_capture_profile(
                _list_request(resolved_leaf, page=page),
                capture_profile=capture_profile,
                profile_complete=capture_profile == CSINDEX_FULL_PROFILE,
            )
            for page in expected_pages
        )
    expected_requests = base_requests
    if source_ancestry is not None and source_binding is not None:
        expected_requests = [
            _with_csindex_source_evidence(
                request, source_ancestry, source_binding
            )
            for request in base_requests
        ]
    actual_by_id = {request.request_id: request.semantic() for request in requests}
    expected_by_id = {
        request.request_id: request.semantic() for request in expected_requests
    }
    if actual_by_id != expected_by_id:
        raise ValueError("csindex_list_request_semantics_invalid")
    return resolved, base_requests, capture_profile == CSINDEX_FULL_PROFILE


def _csindex_filter_topic_present(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    rows = value.get("data")
    if not isinstance(rows, list):
        return False
    return any(
        isinstance(row, Mapping)
        and "index_rebalance"
        in {
            str(item)
            for item in (
                row.get("key"),
                row.get("value"),
                row.get("name"),
                row.get("code"),
            )
            if item is not None
        }
        for row in rows
    )


def _month_leaves() -> list[dict[str, str]]:
    leaves: list[dict[str, str]] = []
    for year in range(2011, 2020):
        for month in range(1, 13):
            last = calendar.monthrange(year, month)[1]
            leaves.append(
                {
                    "leaf_id": f"index_rebalance_{year:04d}{month:02d}",
                    "date_start": f"{year:04d}-{month:02d}-01",
                    "date_end": f"{year:04d}-{month:02d}-{last:02d}",
                }
            )
    return leaves


def _list_request(leaf: Mapping[str, Any], *, page: int) -> ProviderProbeRequest:
    body = {
        "startDate": leaf["date_start"],
        "endDate": leaf["date_end"],
        "classList": [],
        "typeList": [],
        "relatedTopics": ["index_rebalance"],
        "indexList": [],
        "page": {"key": "", "page": page, "rows": CSINDEX_PAGE_SIZE, "sortBy": ""},
    }
    return ProviderProbeRequest(
        request_id=_request_id(leaf, page),
        provider="csindex",
        endpoint="index_rebalance_announcement_list",
        method="POST",
        url=CSINDEX_LIST_URL,
        headers=CSINDEX_HEADERS,
        body=json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(),
        disposition="bounded_backfill",
        evidence_semantics="official_http_response_envelope",
        expected_terminal_states=("positive", "empty"),
        required_checks=(
            "list_shape",
            "total_semantics",
            "current_page_present",
            "provider_success",
            "publication_dates_within_leaf",
            "current_page_matches_request",
            "page_size_matches_request",
        ),
        metadata={
            "case": "csindex_list",
            "leaf_id": leaf["leaf_id"],
            "page": page,
            "page_size": CSINDEX_PAGE_SIZE,
            "date_start": leaf["date_start"],
            "date_end": leaf["date_end"],
            "expected_current_page": page,
        },
    )


def _filter_request() -> ProviderProbeRequest:
    return ProviderProbeRequest(
        request_id="csindex_filter_topics",
        provider="csindex",
        endpoint="announcement_filters",
        method="GET",
        url="https://www.csindex.com.cn/csindex-home/announcement/announcement-filter-list",
        headers={"Referer": "https://www.csindex.com.cn/", "User-Agent": USER_AGENT},
        disposition="bounded_backfill",
        evidence_semantics="official_http_response_envelope",
        expected_terminal_states=("positive",),
        required_checks=("index_rebalance_topic_present",),
        metadata={"case": "csindex_filter"},
    )


def _request_id(leaf: Mapping[str, Any], page: int) -> str:
    return f"csindex_{leaf['leaf_id']}_page_{page:03d}"


def _with_csindex_capture_profile(
    request: ProviderProbeRequest,
    *,
    capture_profile: str,
    profile_complete: bool,
) -> ProviderProbeRequest:
    return ProviderProbeRequest(
        **{
            **request.__dict__,
            "metadata": dict(request.metadata)
            | {
                "capture_profile": capture_profile,
                "profile_complete": profile_complete,
            },
        }
    )


def _captured_list_pages(manifest_path: str | Path) -> dict[str, dict[str, Any]]:
    root = Path(manifest_path).parent
    plan = read_json(root / "request_plan.json")
    requests = {str(row["request_id"]): row for row in plan["requests"]}
    latest_terminal: dict[str, dict[str, Any]] = {}
    for event in _read_jsonl(root / "capture_journal.jsonl"):
        if event.get("event_type") == "capture_attempt_terminal":
            latest_terminal[str(event["request_id"])] = event
    pages: dict[str, dict[str, Any]] = {}
    for request_id, terminal in sorted(latest_terminal.items()):
        wrapper_path = root / str(terminal["raw_envelope_relative_path"])
        wrapper = read_json(wrapper_path)
        request = requests.get(request_id)
        if not request or (request.get("metadata") or {}).get("case") != "csindex_list":
            continue
        _official, body = _decode_csindex_official_http_envelope(
            wrapper,
            request=_request_from_semantic(request),
            terminal=terminal,
        )
        pages[request_id] = json.loads(body)
    return pages


def _validate_csindex_authorized_contract(
    contract: Mapping[str, Any],
    *,
    phase: str,
    capture_profile: str,
    request_count: int,
) -> None:
    policy = CSINDEX_PHASE_RUNTIME_POLICY.get(phase)
    adapter = contract.get("adapter_identity") or {}
    attachment_phase = phase in {
        "csindex-attachments",
        "csindex-legacy-cons-repair",
    }
    expected_hosts = (
        ["oss-ch.csindex.com.cn"]
        if phase == "csindex-legacy-cons-repair"
        else (
            list(CSINDEX_ATTACHMENT_HOSTS)
            if capture_profile == CSINDEX_ATTACHMENT_FULL_PROFILE
            else list(contract.get("allowed_hosts") or ())
            if capture_profile == CSINDEX_ATTACHMENT_HOST_SLICE_PROFILE
            else ["www.csindex.com.cn"]
        )
    )
    retries = int((policy or {}).get("max_retries") or -1)
    expected_budget = {
        "max_requests": request_count * (retries + 1),
        "max_wire_exchanges": request_count * (retries + 1),
        "max_response_bytes": (policy or {}).get("max_response_bytes"),
        "max_total_response_bytes": (policy or {}).get(
            "max_total_response_bytes"
        ),
        "timeout_seconds": (policy or {}).get("timeout_seconds"),
        "minimum_delay_seconds": (policy or {}).get(
            "minimum_delay_seconds"
        ),
        "max_retries": retries,
    }
    expected_identity_keys = {
        "adapter",
        "implementation_root",
        "http",
        "capture_profile",
        "profile_complete",
    }
    if attachment_phase:
        expected_identity_keys |= {
            "attachment_contract",
            "attachment_body_max_bytes",
        }
    if phase != "csindex-discovery":
        expected_identity_keys |= {
            "input_capture_content_hash",
            "source_binding_root",
            "source_ancestry_root",
            "source_upstream_content_hashes_root",
        }
    if (
        policy is None
        or contract.get("schema_version") != "free_provider_backfill_contract_v2"
        or contract.get("provider") != "csindex"
        or contract.get("permission_context_id") != DEFAULT_PERMISSION_CONTEXT
        or contract.get("capture_public_key_sha256")
        != CSINDEX_APPROVED_CAPTURE_KEY_SHA256
        or contract.get("activity_name") != policy["activity_name"]
        or contract.get("scope") != CSINDEX_SCOPE
        or contract.get("allowed_hosts") != expected_hosts
        or contract.get("budget") != expected_budget
        or contract.get("source_profile_id") != CSINDEX_SOURCE_PROFILE_ID
        or contract.get("mode") != "signed_raw_provider_capture"
        or contract.get("capture_before_normalization") is not True
        or contract.get("old_lake_mutated") is not False
        or re.fullmatch(
            r"[0-9a-f]{64}", str(contract.get("population_root") or "")
        )
        is None
        or set(adapter) != expected_identity_keys
        or adapter.get("adapter") != CSINDEX_PHASE_ADAPTERS[phase]
        or adapter.get("http") != CSINDEX_HTTP_IDENTITY
        or adapter.get("capture_profile") != capture_profile
        or adapter.get("profile_complete")
        != str(
            capture_profile
            in {
                CSINDEX_FULL_PROFILE,
                CSINDEX_ATTACHMENT_FULL_PROFILE,
                CSINDEX_LEGACY_CONS_REPAIR_PROFILE,
            }
        ).lower()
        or re.fullmatch(
            r"[0-9a-f]{64}", str(adapter.get("implementation_root") or "")
        )
        is None
        or (
            phase != "csindex-discovery"
            and any(
                re.fullmatch(r"[0-9a-f]{64}", str(adapter.get(key) or ""))
                is None
                for key in (
                    "input_capture_content_hash",
                    "source_binding_root",
                    "source_ancestry_root",
                    "source_upstream_content_hashes_root",
                )
            )
        )
        or (
            attachment_phase
            and (
                adapter.get("attachment_body_max_bytes")
                != CSINDEX_ATTACHMENT_BODY_MAX_BYTES
                or adapter.get("attachment_contract")
                != (
                    "csindex_legacy_cons_exact_repair_contract_v1"
                    if phase == "csindex-legacy-cons-repair"
                    else "csindex_attachment_capture_contract_v3"
                )
            )
        )
    ):
        raise ValueError("csindex_authorized_contract_closure_invalid")


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
    retries: int,
    permission_context_id: str,
    allowed_hosts: Sequence[str] = ("www.csindex.com.cn",),
    capture_profile: str | None = None,
    source_binding: Mapping[str, Any] | None = None,
) -> FreeProviderBackfillContract:
    attachment_phase = phase in {
        "csindex-attachments",
        "csindex-legacy-cons-repair",
    }
    resolved_profile = capture_profile or (
        CSINDEX_LEGACY_CONS_REPAIR_PROFILE
        if phase == "csindex-legacy-cons-repair"
        else (
            CSINDEX_ATTACHMENT_FULL_PROFILE
            if phase == "csindex-attachments"
            else CSINDEX_FULL_PROFILE
        )
    )
    expected_adapter = CSINDEX_PHASE_ADAPTERS.get(phase)
    if expected_adapter is None:
        raise ValueError("csindex_contract_phase_invalid")
    allowed_profile_by_phase = {
        "csindex-discovery": {
            CSINDEX_FULL_PROFILE,
            CSINDEX_DISCOVERY_SLICE_PROFILE,
        },
        "csindex-inventory": {CSINDEX_FULL_PROFILE},
        "csindex-details": {CSINDEX_FULL_PROFILE},
        "csindex-attachments": {
            CSINDEX_ATTACHMENT_FULL_PROFILE,
            CSINDEX_ATTACHMENT_HOST_SLICE_PROFILE,
        },
        "csindex-legacy-cons-repair": {CSINDEX_LEGACY_CONS_REPAIR_PROFILE},
    }
    host_set = set(allowed_hosts)
    if (
        resolved_profile not in allowed_profile_by_phase[phase]
        or (
            resolved_profile == CSINDEX_ATTACHMENT_FULL_PROFILE
            and host_set != set(CSINDEX_ATTACHMENT_HOSTS)
        )
        or (
            resolved_profile == CSINDEX_ATTACHMENT_HOST_SLICE_PROFILE
            and (not host_set or not host_set < set(CSINDEX_ATTACHMENT_HOSTS))
        )
        or (
            resolved_profile == CSINDEX_LEGACY_CONS_REPAIR_PROFILE
            and host_set != {"oss-ch.csindex.com.cn"}
        )
        or (
            phase in {"csindex-discovery", "csindex-inventory", "csindex-details"}
            and host_set != {"www.csindex.com.cn"}
        )
        or (phase == "csindex-legacy-cons-repair" and request_count != 2)
    ):
        raise ValueError("csindex_contract_profile_or_host_scope_invalid")
    identity = {
        "adapter": expected_adapter,
        "implementation_root": _implementation_root(),
        "http": CSINDEX_HTTP_IDENTITY,
        "capture_profile": resolved_profile,
        "profile_complete": str(
            resolved_profile
            in {
                CSINDEX_FULL_PROFILE,
                CSINDEX_ATTACHMENT_FULL_PROFILE,
                CSINDEX_LEGACY_CONS_REPAIR_PROFILE,
            }
        ).lower(),
    }
    if attachment_phase:
        identity["attachment_contract"] = (
            "csindex_legacy_cons_exact_repair_contract_v1"
            if phase == "csindex-legacy-cons-repair"
            else "csindex_attachment_capture_contract_v3"
        )
        identity["attachment_body_max_bytes"] = CSINDEX_ATTACHMENT_BODY_MAX_BYTES
    if input_capture_hash:
        identity["input_capture_content_hash"] = input_capture_hash
    if phase != "csindex-discovery":
        if source_binding is None:
            raise ValueError("csindex_contract_source_binding_missing")
        ancestry_root = str(source_binding.get("source_ancestry_root") or "")
        binding_root = str(source_binding.get("content_hash") or "")
        upstream_root = str(source_binding.get("upstream_content_hashes_root") or "")
        if (
            source_binding.get("phase") != phase
            or source_binding.get("capture_profile") != resolved_profile
            or source_binding.get("input_capture_content_hash")
            != input_capture_hash
            or any(
                re.fullmatch(r"[0-9a-f]{64}", value) is None
                for value in (ancestry_root, binding_root, upstream_root)
            )
            or (
                attachment_phase
                and (source_binding.get("derivation") or {}).get(
                    "selected_hosts"
                )
                != [
                    host
                    for host in CSINDEX_ATTACHMENT_HOSTS
                    if host in host_set
                ]
            )
        ):
            raise ValueError("csindex_contract_source_binding_invalid")
        identity |= {
            "source_binding_root": binding_root,
            "source_ancestry_root": ancestry_root,
            "source_upstream_content_hashes_root": upstream_root,
        }
    contract = FreeProviderBackfillContract(
        activity_name=str(CSINDEX_PHASE_RUNTIME_POLICY[phase]["activity_name"]),
        provider="csindex",
        output_root=output_root,
        permission_context_id=permission_context_id,
        population_root=population_root,
        capture_public_key_sha256=_public_key_hash(signer.public_key_pem),
        capture_public_key_pem_b64=base64.b64encode(signer.public_key_pem).decode(),
        scope_start="20120101",
        scope_end="20191231",
        request_start="20110101",
        request_end="20191231",
        allowed_hosts=tuple(allowed_hosts),
        budget=BackfillResourceBudget(
            max_requests=request_count * (retries + 1),
            max_wire_exchanges=request_count * (retries + 1),
            max_response_bytes=(
                176 * 1024 * 1024 if attachment_phase else 64 * 1024 * 1024
            ),
            max_total_response_bytes=(
                16 * 1024 * 1024 * 1024
                if attachment_phase
                else 8 * 1024 * 1024 * 1024
            ),
            timeout_seconds=timeout,
            minimum_delay_seconds=delay,
            max_retries=retries,
        ),
        adapter_identity=identity,
    )
    _validate_csindex_authorized_contract(
        contract.semantic(),
        phase=phase,
        capture_profile=resolved_profile,
        request_count=request_count,
    )
    return contract


def _csindex_phase_from_contract(
    contract: Mapping[str, Any],
    *,
    request_count: int,
) -> tuple[str, str]:
    adapter = str((contract.get("adapter_identity") or {}).get("adapter") or "")
    phases = [
        phase
        for phase, expected_adapter in CSINDEX_PHASE_ADAPTERS.items()
        if adapter == expected_adapter
    ]
    if len(phases) != 1:
        raise ValueError("csindex_governance_phase_invalid")
    phase = phases[0]
    capture_profile = str(
        (contract.get("adapter_identity") or {}).get("capture_profile") or ""
    )
    _validate_csindex_authorized_contract(
        contract,
        phase=phase,
        capture_profile=capture_profile,
        request_count=request_count,
    )
    return phase, capture_profile


def _csindex_generation_manifest(
    provider_root: Path,
    *,
    phase: str,
    generation_id: str,
) -> Path:
    phase_root = provider_root / phase.replace("csindex-", "").replace("-", "_")
    candidate = phase_root / "generations" / generation_id / MANIFEST_NAME
    if (
        not candidate.is_file()
        or candidate.is_symlink()
        or candidate.parent.is_symlink()
        or candidate.resolve() != candidate.absolute()
    ):
        raise ValueError("csindex_upstream_generation_missing_or_unconfined")
    return candidate


def validate_csindex_governance(
    path: str | Path,
    *,
    _provider_root: Path | None = None,
    _visited: frozenset[tuple[str, str]] = frozenset(),
) -> dict[str, Any]:
    """Replay and recursively authenticate one CSI generation and all ancestors."""

    validated = validate_free_provider_backfill(path)
    root = Path(str(validated["manifest_path"])).parent
    contract = read_json(root / "activity_contract.json")
    plan = read_json(root / "request_plan.json")
    requests = [_request_from_semantic(row) for row in plan.get("requests") or ()]
    phase, capture_profile = _csindex_phase_from_contract(
        contract, request_count=len(requests)
    )
    output_root = root.parent.parent
    provider_root = (_provider_root or output_root.parent).resolve()
    expected_output = (
        provider_root / phase.replace("csindex-", "").replace("-", "_")
    ).resolve()
    if output_root.resolve() != expected_output:
        raise ValueError("csindex_governance_output_geometry_invalid")
    visit = (phase, str(validated.get("generation_id") or ""))
    if visit in _visited:
        raise ValueError("csindex_upstream_generation_cycle")
    visited = _visited | {visit}
    normalizers = {
        "csindex-discovery": normalize_csindex_discovery,
        "csindex-inventory": normalize_csindex_inventory,
        "csindex-details": normalize_csindex_details,
        "csindex-attachments": normalize_csindex_attachments,
        "csindex-legacy-cons-repair": normalize_csindex_legacy_cons_repair,
    }
    roles = {
        "csindex-discovery": (
            "csindex_announcement_inventory",
            "csindex_page_coverage",
        ),
        "csindex-inventory": (
            "csindex_announcement_inventory",
            "csindex_page_coverage",
        ),
        "csindex-details": ("csindex_announcement_details",),
        "csindex-attachments": ("csindex_attachment_index",),
        "csindex-legacy-cons-repair": ("csindex_attachment_index",),
    }
    replayed, replay_root = replay_normalized_artifacts(
        validated["manifest_path"],
        normalizer=normalizers[phase],
        required_roles=roles[phase] + ("normalized_manifest",),
    )
    normalized_manifest = json.loads(replayed["normalized_manifest"])
    if (
        not isinstance(normalized_manifest, Mapping)
        or normalized_manifest.get("capture_profile") != capture_profile
        or not isinstance(normalized_manifest.get("profile_complete"), bool)
    ):
        raise ValueError("csindex_governance_normalized_profile_invalid")
    signature_integrity = validated.get("publication_signature_verified") is True
    approved_key = (
        signature_integrity
        and contract.get("capture_public_key_sha256")
        == CSINDEX_APPROVED_CAPTURE_KEY_SHA256
    )
    source_ancestry: dict[str, Any] | None = None
    source_binding: dict[str, Any] | None = None
    upstream_result: dict[str, Any] | None = None
    if phase != "csindex-discovery":
        source_ancestry, source_binding = _csindex_request_source_evidence(
            requests, phase=phase
        )
        if (
            normalized_manifest.get("source_ancestry") != source_ancestry
            or normalized_manifest.get("source_binding") != source_binding
        ):
            raise ValueError("csindex_governance_normalized_source_binding_invalid")
        direct_sources = list(source_ancestry.get("direct_sources") or ())
        if len(direct_sources) != 1:
            raise ValueError("csindex_governance_direct_source_closure_invalid")
        declared_direct = direct_sources[0]
        source_phase = str(declared_direct.get("source_phase") or "")
        source_manifest = _csindex_generation_manifest(
            provider_root,
            phase=source_phase,
            generation_id=str(declared_direct.get("source_generation_id") or ""),
        )
        upstream_result = validate_csindex_governance(
            source_manifest,
            _provider_root=provider_root,
            _visited=visited,
        )
        if upstream_result.get("csindex_phase") != source_phase:
            raise ValueError("csindex_governance_upstream_phase_invalid")
        expected_ancestry = upstream_result.get("csindex_downstream_ancestry")
        if not isinstance(expected_ancestry, Mapping):
            raise ValueError("csindex_governance_upstream_ineligible")
        if source_ancestry != expected_ancestry:
            raise ValueError("csindex_governance_real_ancestry_mismatch")
        recomputed_binding = _csindex_source_binding(
            phase=phase,
            capture_profile=capture_profile,
            input_capture_content_hash=str(
                source_binding["input_capture_content_hash"]
            ),
            source_ancestry=expected_ancestry,
            derivation=dict(source_binding["derivation"]),
        )
        adapter = contract.get("adapter_identity") or {}
        if (
            source_binding != recomputed_binding
            or adapter.get("input_capture_content_hash")
            != recomputed_binding["input_capture_content_hash"]
            or adapter.get("source_binding_root")
            != recomputed_binding["content_hash"]
            or adapter.get("source_ancestry_root")
            != recomputed_binding["source_ancestry_root"]
            or adapter.get("source_upstream_content_hashes_root")
            != recomputed_binding["upstream_content_hashes_root"]
        ):
            raise ValueError("csindex_governance_real_binding_mismatch")
    profile_complete = normalized_manifest.get("profile_complete") is True
    upstream_eligible = (
        upstream_result is None
        or upstream_result.get("csindex_downstream_eligible") is True
    )
    weak_source = bool(
        source_ancestry is not None
        and source_ancestry.get("weak_source_ancestry") is True
    )
    downstream_eligible = bool(
        approved_key and profile_complete and upstream_eligible and not weak_source
    )
    blockers: list[str] = []
    if not approved_key:
        blockers.append("capture_key_not_approved")
    if not profile_complete:
        blockers.append("capture_profile_incomplete")
    if not upstream_eligible or weak_source:
        blockers.append("upstream_lineage_ineligible")
    result = validated | {
        "signature_integrity_verified": signature_integrity,
        "approved_capture_key_verified": approved_key,
        "normalized_artifacts_integrity_verified": validated.get(
            "normalized_artifacts_trusted"
        )
        is True,
        "normalized_artifacts_trusted": downstream_eligible,
        "csindex_phase": phase,
        "csindex_downstream_eligible": downstream_eligible,
        "pit_membership_authorized": False,
        "historical_known_at_proven": False,
        "csindex_governance_qualification": {
            "schema_version": "csindex_governance_qualification_v1",
            "signature_integrity_verified": signature_integrity,
            "approved_capture_key_verified": approved_key,
            "downstream_eligible": downstream_eligible,
            "hardened_replay_root": replay_root,
            "pit_membership_authorized": False,
            "historical_known_at_proven": False,
            "blockers": blockers,
        },
    }
    downstream_ancestry: dict[str, Any] | None = None
    if downstream_eligible and phase in {
        "csindex-discovery",
        "csindex-inventory",
        "csindex-details",
    }:
        direct = _csindex_direct_source(
            result,
            expected_phase=phase,
            expected_profiles=(CSINDEX_FULL_PROFILE,),
        )
        stage = {
            "csindex-discovery": "discovery_capture",
            "csindex-inventory": "inventory_capture",
            "csindex-details": "details_capture",
        }[phase]
        downstream_ancestry = _csindex_source_ancestry(
            source_stage=stage,
            direct_sources=(direct,),
            upstream_ancestry=(source_ancestry,) if source_ancestry else (),
        )
    result["csindex_downstream_ancestry"] = downstream_ancestry
    return result


def _implementation_root() -> str:
    return canonical_hash(
        {
            "official_http_decoder": inspect.getsource(
                _decode_csindex_official_payload
            )
            + inspect.getsource(_decode_csindex_official_http_envelope),
            "source_capture_replay": inspect.getsource(
                _replay_validated_csindex_source
            ),
            "governance_validation": inspect.getsource(
                _validate_csindex_authorized_contract
            )
            + inspect.getsource(_csindex_phase_from_contract)
            + inspect.getsource(_csindex_generation_manifest)
            + inspect.getsource(validate_csindex_governance),
            "source_ancestry": inspect.getsource(_csindex_direct_source)
            + inspect.getsource(_validate_csindex_direct_source)
            + inspect.getsource(_csindex_source_ancestry)
            + inspect.getsource(_validate_csindex_source_ancestry)
            + inspect.getsource(_csindex_upstream_content_hashes),
            "source_binding": inspect.getsource(_csindex_source_binding)
            + inspect.getsource(_validate_csindex_source_binding)
            + inspect.getsource(_with_csindex_source_evidence)
            + inspect.getsource(_csindex_request_source_evidence)
            + inspect.getsource(_validate_csindex_binding_derivation),
            "discovery_plan": inspect.getsource(build_csindex_discovery_plan),
            "inventory_plan": inspect.getsource(build_csindex_inventory_plan),
            "detail_plan": inspect.getsource(build_csindex_detail_plan),
            "detail_request": inspect.getsource(_csindex_detail_request),
            "attachment_plan": inspect.getsource(build_csindex_attachment_plan),
            "legacy_cons_repair_plan": inspect.getsource(
                build_csindex_legacy_cons_repair_plan
            ),
            "legacy_cons_repair_population": inspect.getsource(
                _legacy_cons_repair_population
            )
            + inspect.getsource(_validate_legacy_cons_repair_population)
            + inspect.getsource(_legacy_cons_repair_path_dates),
            "attachment_population": inspect.getsource(
                _attachment_population_from_details
            )
            + inspect.getsource(_attachment_source_edge),
            "attachment_reference_parser": inspect.getsource(
                _AttachmentReferenceParser
            ),
            "attachment_requests": inspect.getsource(_attachment_requests),
            "attachment_population_profiles": inspect.getsource(
                _attachment_population_for_hosts
            )
            + inspect.getsource(_restore_full_attachment_population)
            + inspect.getsource(_attachment_request_identity),
            "attachment_host_policy": inspect.getsource(
                _selected_attachment_hosts
            )
            + inspect.getsource(_looks_like_csindex_attachment_reference),
            "attachment_url_policy": inspect.getsource(
                _canonical_csindex_attachment_url
            )
            + inspect.getsource(_canonical_attachment_path)
            + inspect.getsource(_attachment_extension)
            + inspect.getsource(_attachment_path_dates),
            "list_normalizer": inspect.getsource(_normalize_list_pages),
            "list_request_closure": inspect.getsource(
                _validate_csindex_list_request_closure
            )
            + inspect.getsource(_csindex_filter_topic_present),
            "detail_normalizer": inspect.getsource(normalize_csindex_details),
            "attachment_normalizer": inspect.getsource(
                normalize_csindex_attachments
            )
            + inspect.getsource(normalize_csindex_legacy_cons_repair),
            "transport": inspect.getsource(CSIndexBackfillTransport),
            "attachment_transport": inspect.getsource(
                CSIndexAttachmentTransport
            ),
            "attachment_wire_checks": inspect.getsource(
                _attachment_content_length_matches
            )
            + inspect.getsource(
                _attachment_content_type_compatible
            )
            + inspect.getsource(_attachment_magic_valid)
            + inspect.getsource(_text_attachment_valid)
            + inspect.getsource(_attachment_block_reason),
            "attachment_policy_constants": {
                "allowed_hosts": list(CSINDEX_ATTACHMENT_HOSTS),
                "allowed_extensions": sorted(CSINDEX_ATTACHMENT_EXTENSIONS),
                "path_date_start": CSINDEX_ATTACHMENT_PATH_DATE_START,
                "path_date_end": CSINDEX_ATTACHMENT_PATH_DATE_END,
                "temporal_blocker": CSINDEX_ATTACHMENT_TEMPORAL_BLOCKER,
                "body_max_bytes": CSINDEX_ATTACHMENT_BODY_MAX_BYTES,
            },
            "list_request_helpers": inspect.getsource(_captured_list_pages)
            + inspect.getsource(_month_leaves)
            + inspect.getsource(_list_request)
            + inspect.getsource(_filter_request)
            + inspect.getsource(_request_id)
            + inspect.getsource(_with_csindex_capture_profile)
            + inspect.getsource(_strict_iso_date)
            + inspect.getsource(_nonnegative_int),
            "phase_profile_constants": {
                "scope": CSINDEX_SCOPE,
                "source_ancestry_schema": CSINDEX_SOURCE_ANCESTRY_SCHEMA,
                "source_binding_schema": CSINDEX_SOURCE_BINDING_SCHEMA,
                "full_profile": CSINDEX_FULL_PROFILE,
                "discovery_slice_profile": CSINDEX_DISCOVERY_SLICE_PROFILE,
                "attachment_full_profile": CSINDEX_ATTACHMENT_FULL_PROFILE,
                "attachment_host_slice_profile": (
                    CSINDEX_ATTACHMENT_HOST_SLICE_PROFILE
                ),
                "legacy_cons_repair_profile": (
                    CSINDEX_LEGACY_CONS_REPAIR_PROFILE
                ),
                "phase_adapters": CSINDEX_PHASE_ADAPTERS,
                "source_profile_id": CSINDEX_SOURCE_PROFILE_ID,
                "approved_capture_key_sha256": (
                    CSINDEX_APPROVED_CAPTURE_KEY_SHA256
                ),
                "http_identity": CSINDEX_HTTP_IDENTITY,
                "phase_runtime_policy": CSINDEX_PHASE_RUNTIME_POLICY,
                "legacy_cons_repair_rows": CSINDEX_LEGACY_CONS_REPAIR_ROWS,
                "page_size": CSINDEX_PAGE_SIZE,
                "max_pages_per_month": CSINDEX_MAX_PAGES_PER_MONTH,
                "json_body_max_bytes": CSINDEX_JSON_BODY_MAX_BYTES,
            },
            "contract": inspect.getsource(_contract),
            "official_http_transport": inspect.getsource(OfficialHttpProbeTransport),
            "official_http_transport_module_sha256": sha256_file(
                Path(run_provider_probe_module.__file__)
            ),
            "shared_capture_engine_module_sha256": sha256_file(
                Path(free_provider_backfill_module.__file__)
            ),
        }
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run CSI official archive backfill.")
    parser.add_argument(
        "--phase",
        choices=(
            "csindex-discovery",
            "csindex-inventory",
            "csindex-details",
            "csindex-attachments",
            "csindex-legacy-cons-repair",
        ),
        required=True,
    )
    parser.add_argument("--leaf-id", action="append")
    parser.add_argument("--input-capture")
    parser.add_argument(
        "--attachment-host",
        action="append",
        choices=CSINDEX_ATTACHMENT_HOSTS,
        help="Restrict attachment capture to one approved host; may be repeated.",
    )
    parser.add_argument("--permission-context-id", default=DEFAULT_PERMISSION_CONTEXT)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--validate")
    parser.add_argument("--minimum-delay-seconds", type=float)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.validate:
        try:
            payload = validate_csindex_governance(args.validate)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(json.dumps({"status": "blocked", "reason": str(exc)}, sort_keys=True))
            return 1
        print(_render(payload, pretty=args.pretty))
        return 0
    input_hash: str | None = None
    attachment_hosts: tuple[str, ...] = ("www.csindex.com.cn",)
    transport_type: type[CSIndexBackfillTransport | CSIndexAttachmentTransport]
    transport_type = CSIndexBackfillTransport
    if args.phase == "csindex-discovery":
        if args.attachment_host:
            raise SystemExit("--attachment-host is only valid for csindex-attachments")
        population, requests = build_csindex_discovery_plan(args.leaf_id)
        normalizer = normalize_csindex_discovery
        default_delay = 5.0
    elif args.phase == "csindex-inventory":
        if args.attachment_host:
            raise SystemExit("--attachment-host is only valid for csindex-attachments")
        if not args.input_capture:
            raise SystemExit("--input-capture is required for csindex-inventory")
        population, requests, input_hash = build_csindex_inventory_plan(args.input_capture)
        normalizer = normalize_csindex_inventory
        default_delay = 5.0
    elif args.phase == "csindex-details":
        if args.attachment_host:
            raise SystemExit("--attachment-host is only valid for csindex-attachments")
        if not args.input_capture:
            raise SystemExit("--input-capture is required for csindex-details")
        population, requests, input_hash = build_csindex_detail_plan(args.input_capture)
        normalizer = normalize_csindex_details
        default_delay = 7.5
    elif args.phase == "csindex-attachments":
        if args.leaf_id:
            raise SystemExit("--leaf-id is not valid for csindex-attachments")
        if not args.input_capture:
            raise SystemExit("--input-capture is required for csindex-attachments")
        attachment_hosts = _selected_attachment_hosts(args.attachment_host)
        population, requests, input_hash = build_csindex_attachment_plan(
            args.input_capture,
            include_hosts=attachment_hosts,
        )
        normalizer = normalize_csindex_attachments
        transport_type = CSIndexAttachmentTransport
        default_delay = 2.0
    else:
        if args.leaf_id or args.attachment_host:
            raise SystemExit(
                "--leaf-id/--attachment-host are not valid for "
                "csindex-legacy-cons-repair"
            )
        if not args.input_capture:
            raise SystemExit(
                "--input-capture is required for csindex-legacy-cons-repair"
            )
        population, requests, input_hash = build_csindex_legacy_cons_repair_plan(
            args.input_capture
        )
        normalizer = normalize_csindex_legacy_cons_repair
        transport_type = CSIndexAttachmentTransport
        attachment_hosts = ("oss-ch.csindex.com.cn",)
        default_delay = 2.0
    delay = args.minimum_delay_seconds if args.minimum_delay_seconds is not None else default_delay
    population_root = canonical_hash(
        {"population": population, "input_capture_content_hash": input_hash}
    )
    preview = {
        "schema_version": "free_provider_csindex_backfill_plan_preview_v1",
        "phase": args.phase,
        "population_count": len(population),
        "population_root": population_root,
        "request_count": len(requests),
        "request_plan_hash": canonical_hash([request.semantic() for request in requests]),
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
    output = SCOPE_ROOT / "csindex" / args.phase.replace("csindex-", "").replace("-", "_")
    capture_profile = str(requests[0].metadata.get("capture_profile") or "")
    source_binding = requests[0].metadata.get("source_binding")
    contract = _contract(
        phase=args.phase,
        output_root=output,
        signer=signer,
        population_root=population_root,
        request_count=len(requests),
        input_capture_hash=input_hash,
        delay=delay,
        timeout=args.timeout_seconds,
        retries=args.max_retries,
        permission_context_id=args.permission_context_id,
        allowed_hosts=attachment_hosts,
        capture_profile=capture_profile,
        source_binding=(
            source_binding if isinstance(source_binding, Mapping) else None
        ),
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
    result = run_free_provider_backfill(
        contract,
        requests,
        transport=transport_type(minimum_delay_seconds=delay),
        signer=signer,
        normalizer=normalizer,
        runtime_implementation_root=_implementation_root(),
    )
    print(_render(result, pretty=args.pretty))
    return 0 if result.get("status") == "succeeded" else 1


def _selected_attachment_hosts(
    include_hosts: Sequence[str] | None,
) -> tuple[str, ...]:
    selected = {
        str(host).strip().lower().rstrip(".")
        for host in (include_hosts or CSINDEX_ATTACHMENT_HOSTS)
    }
    if not selected or not selected <= set(CSINDEX_ATTACHMENT_HOSTS):
        raise ValueError("csindex_attachment_host_filter_invalid")
    return tuple(host for host in CSINDEX_ATTACHMENT_HOSTS if host in selected)


def _looks_like_csindex_attachment_reference(value: Any) -> bool:
    candidate = html_lib.unescape(str(value or "")).strip().lower()
    return (
        candidate.startswith("file/")
        or any(host in candidate for host in CSINDEX_ATTACHMENT_HOSTS)
        or _ATTACHMENT_REFERENCE_HINT.search(candidate) is not None
    )


def _canonical_csindex_attachment_url(value: Any) -> str:
    """Return one confined attachment URL or reject the reference."""

    if not isinstance(value, str):
        raise ValueError("csindex_attachment_url_invalid")
    candidate = html_lib.unescape(value).strip()
    if (
        not candidate
        or any(ord(character) < 32 or ord(character) == 127 for character in candidate)
        or "\\" in candidate
    ):
        raise ValueError("csindex_attachment_url_invalid")
    if candidate.startswith("file/"):
        parsed = urllib.parse.urlsplit(candidate)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("csindex_attachment_relative_url_invalid")
        path = _canonical_attachment_path(parsed.path, leading_slash=False)
        if not path.startswith("file/"):
            raise ValueError("csindex_attachment_relative_url_invalid")
        _attachment_extension(path)
        return f"https://www.csindex.com.cn/{path}"

    parsed = urllib.parse.urlsplit(candidate)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("csindex_attachment_absolute_url_invalid") from exc
    host = str(parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme.lower() != "https"
        or host != "oss-ch.csindex.com.cn"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
        or parsed.netloc.lower().rstrip(".") != host
    ):
        raise ValueError("csindex_attachment_absolute_url_invalid")
    path = _canonical_attachment_path(parsed.path, leading_slash=True)
    _attachment_extension(path)
    return f"https://oss-ch.csindex.com.cn{path}"


def _canonical_attachment_path(path: str, *, leading_slash: bool) -> str:
    if leading_slash:
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("csindex_attachment_path_invalid")
        raw = path[1:]
    else:
        if path.startswith("/"):
            raise ValueError("csindex_attachment_path_invalid")
        raw = path
    if not raw or raw.endswith("/") or "//" in raw:
        raise ValueError("csindex_attachment_path_invalid")
    encoded: list[str] = []
    for segment in raw.split("/"):
        if not segment or _INVALID_PERCENT_ESCAPE.search(segment):
            raise ValueError("csindex_attachment_path_invalid")
        decoded = urllib.parse.unquote(segment, errors="strict")
        decoded_twice = urllib.parse.unquote(decoded, errors="strict")
        if (
            decoded_twice != decoded
            or decoded in {".", ".."}
            or any(
                character in decoded
                for character in ("/", "\\", "?", "#")
            )
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in decoded
            )
        ):
            raise ValueError("csindex_attachment_path_invalid")
        encoded.append(urllib.parse.quote(decoded, safe="-._~"))
    canonical = "/".join(encoded)
    return f"/{canonical}" if leading_slash else canonical


def _attachment_extension(path: str) -> str:
    decoded_name = urllib.parse.unquote(path.rsplit("/", 1)[-1]).lower()
    if "." not in decoded_name:
        raise ValueError("csindex_attachment_extension_unsupported")
    extension = decoded_name.rsplit(".", 1)[-1]
    if extension not in CSINDEX_ATTACHMENT_EXTENSIONS:
        raise ValueError("csindex_attachment_extension_unsupported")
    return extension


def _attachment_path_dates(path: str) -> list[str]:
    dates: list[str] = []
    decoded_path = urllib.parse.unquote(path, errors="strict")
    for token in _PATH_DATE_TOKEN.findall(decoded_path):
        try:
            parsed = date(
                int(token[:4]),
                int(token[4:6]),
                int(token[6:]),
            )
        except ValueError:
            continue
        if parsed.strftime("%Y%m%d") == token:
            dates.append(token)
    return sorted(set(dates))


def _attachment_content_length_matches(value: Any, actual_size: int) -> bool:
    if not isinstance(value, str) or not value.strip().isdigit():
        return False
    return int(value.strip()) == actual_size


def _attachment_content_type_compatible(extension: str, value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    content_type = value.split(";", 1)[0].strip().lower()
    generic = {"application/octet-stream", "application/x-download"}
    accepted = {
        "xls": generic
        | {"application/vnd.ms-excel", "application/msexcel", "application/xls"},
        "xlsx": generic
        | {
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/zip",
        },
        "pdf": generic | {"application/pdf"},
        "txt": generic | {"text/plain"},
        "csv": generic | {"text/csv", "text/plain", "application/vnd.ms-excel"},
        "zip": generic | {"application/zip", "application/x-zip-compressed"},
        "doc": generic | {"application/msword"},
        "docx": generic
        | {
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/zip",
        },
        "jpg": {"image/jpeg", "application/octet-stream"},
        "jpeg": {"image/jpeg", "application/octet-stream"},
        "png": {"image/png", "application/octet-stream"},
        "gif": {"image/gif", "application/octet-stream"},
    }
    return content_type in accepted.get(extension, set())


def _attachment_magic_valid(body: bytes, extension: str) -> bool:
    if not body or _attachment_block_reason(body) is not None:
        return False
    if extension == "pdf":
        return body.startswith(b"%PDF-") and b"%%EOF" in body[-65536:]
    if extension in {"xls", "doc"}:
        return body.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    if extension in {"xlsx", "docx", "zip"}:
        if not body.startswith(b"PK"):
            return False
        try:
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                names = set(archive.namelist())
        except (OSError, ValueError, zipfile.BadZipFile):
            return False
        if not names:
            return False
        if extension == "xlsx":
            return "[Content_Types].xml" in names and any(
                name.startswith("xl/") for name in names
            )
        if extension == "docx":
            return "[Content_Types].xml" in names and any(
                name.startswith("word/") for name in names
            )
        return True
    if extension in {"txt", "csv"}:
        return _text_attachment_valid(body)
    if extension in {"jpg", "jpeg"}:
        return body.startswith(b"\xff\xd8\xff")
    if extension == "png":
        return body.startswith(b"\x89PNG\r\n\x1a\n")
    if extension == "gif":
        return body.startswith((b"GIF87a", b"GIF89a"))
    return False


def _text_attachment_valid(body: bytes) -> bool:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = body.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "\x00" in text:
            return False
        visible = sum(
            character.isprintable() or character in "\r\n\t" for character in text
        )
        return bool(text.strip()) and visible / max(1, len(text)) >= 0.95
    return False


def _attachment_block_reason(body: bytes) -> str | None:
    prefix_bytes = body[:16384]
    if prefix_bytes.startswith(b"\xef\xbb\xbf"):
        prefix_bytes = prefix_bytes[3:]
    prefix = prefix_bytes.lstrip().lower()
    if prefix.startswith((b"<!doctype html", b"<html", b"<head", b"<body")):
        return "html_response"
    waf_tokens = (
        b"access denied",
        b"request blocked",
        b"web application firewall",
        b"captcha",
    )
    if any(token in prefix for token in waf_tokens):
        return "waf_or_block_page"
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = prefix_bytes.decode(encoding).strip().lower()
        except UnicodeDecodeError:
            continue
        if text.startswith(("<!doctype html", "<html", "<head", "<body")):
            return "html_response"
        if any(
            token in text
            for token in (
                "访问被阻断",
                "请求被拒绝",
                "拒绝访问",
                "安全验证",
                "验证码",
            )
        ):
            return "waf_or_block_page"
    return None


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _strict_iso_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _write_row(handle: Any, row: Mapping[str, Any]) -> None:
    handle.write(
        json.dumps(dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        + b"\n"
    )


def _atomic_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("wb") as handle:
            for row in rows:
                _write_row(handle, row)
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

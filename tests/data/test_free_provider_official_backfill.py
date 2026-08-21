from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import re
import urllib.request
import zipfile
import zlib
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest

from auto_alpha.data.ingestion.pipeline.ashare import (
    free_provider_backfill as capture_backfill,
    free_provider_baostock_reconciliation as baostock_reconciliation,
    free_provider_csindex_backfill as csindex_backfill,
    free_provider_http_backfill as cninfo_backfill,
)
from auto_alpha.data.ingestion.pipeline.ashare.free_provider_baostock_reconciliation import (
    _implementation_root as baostock_reconciliation_implementation_root,
    build_adjustment_plan,
    build_dividend_plan,
    build_index_daily_plan,
    build_security_basic_plan,
    build_security_snapshot_plan,
    build_turnover_plan,
    normalize_index_daily,
    normalize_security_snapshots,
    normalize_turnover,
)
from auto_alpha.data.ingestion.pipeline.ashare.free_provider_backfill import (
    replay_normalized_artifacts,
    run_free_provider_backfill,
    validate_free_provider_backfill,
    _validate_baostock_wire_envelope,
)
from auto_alpha.data.ingestion.pipeline.ashare.free_provider_csindex_backfill import (
    CSIndexAttachmentTransport,
    CSIndexBackfillTransport,
    _attachment_content_length_matches,
    _attachment_content_type_compatible,
    _attachment_magic_valid,
    _attachment_population_from_details,
    _attachment_requests,
    _canonical_csindex_attachment_url,
    _contract as csindex_contract,
    _implementation_root as csindex_implementation_root,
    _strict_iso_date,
    build_csindex_discovery_plan,
    build_csindex_inventory_plan,
    normalize_csindex_attachments,
    normalize_csindex_discovery,
)
from auto_alpha.data.ingestion.pipeline.ashare.free_provider_http_backfill import (
    CNINFODocumentTransport,
    _contract as cninfo_contract,
    _cninfo_document_url,
    _implementation_root as cninfo_implementation_root,
    _adjunct_size_reasonable,
    _content_length_matches,
    _content_type_compatible,
    _document_block_reason,
    _document_format,
    _document_structure_valid,
    build_cninfo_document_plan,
    build_cninfo_discovery_plan,
    build_cninfo_inventory_plan,
    normalize_cninfo_documents,
    normalize_cninfo_discovery,
    normalize_cninfo_inventory,
)
from auto_alpha.data.ingestion.pipeline.ashare.provider_probe import (
    ProviderProbeObservation,
    ProviderProbeRequest,
)
from auto_alpha.data.ingestion.pipeline.ashare.run_provider_probe import (
    BAOSTOCK_FIELDS,
    BaostockProbeTransport,
    OfficialHttpProbeTransport,
)
from auto_alpha.platform.artifacts.storage import canonical_hash
from auto_alpha.platform.governance.network.signing import EphemeralReceiptSigner


@pytest.fixture
def approved_csindex_signer(
    monkeypatch: pytest.MonkeyPatch,
) -> EphemeralReceiptSigner:
    signer = EphemeralReceiptSigner.generate()
    monkeypatch.setattr(
        csindex_backfill,
        "CSINDEX_APPROVED_CAPTURE_KEY_SHA256",
        capture_backfill._public_key_hash(signer.public_key_pem),
    )
    return signer


def _synthetic_csindex_direct_source(
    phase: str,
    *,
    content_digit: str,
    contract_digit: str,
    request_count: int,
    weak: bool,
) -> dict[str, object]:
    policy = csindex_backfill.CSINDEX_PHASE_RUNTIME_POLICY[phase]
    content_hash = content_digit * 64
    retries = int(policy["max_retries"])
    return {
        "source_capture_schema": "free_provider_backfill_capture_v2",
        "source_generation_id": f"free_provider_backfill_{content_hash[:24]}",
        "source_content_hash": content_hash,
        "source_contract_id": contract_digit * 64,
        "source_contract_content_hash": contract_digit * 64,
        "source_provider": "csindex",
        "source_phase": phase,
        "source_adapter": csindex_backfill.CSINDEX_PHASE_ADAPTERS[phase],
        "source_capture_profile": csindex_backfill.CSINDEX_FULL_PROFILE,
        "source_scope": dict(csindex_backfill.CSINDEX_SCOPE),
        "source_implementation_root": "c" * 64,
        "source_activity_id": "4" * 64,
        "source_request_plan_hash": "5" * 64,
        "source_request_count": request_count,
        "source_population_root": "6" * 64,
        "source_permission_context_id": csindex_backfill.DEFAULT_PERMISSION_CONTEXT,
        "source_activity_name": policy["activity_name"],
        "source_allowed_hosts": ["www.csindex.com.cn"],
        "source_budget": {
            "max_requests": request_count * (retries + 1),
            "max_wire_exchanges": request_count * (retries + 1),
            "max_response_bytes": policy["max_response_bytes"],
            "max_total_response_bytes": policy["max_total_response_bytes"],
            "timeout_seconds": policy["timeout_seconds"],
            "minimum_delay_seconds": policy["minimum_delay_seconds"],
            "max_retries": retries,
        },
        "source_profile_id": csindex_backfill.CSINDEX_SOURCE_PROFILE_ID,
        "source_http_identity": csindex_backfill.CSINDEX_HTTP_IDENTITY,
        "source_capture_public_key_sha256": (
            csindex_backfill.CSINDEX_APPROVED_CAPTURE_KEY_SHA256
        ),
        "source_publication_signature_verified": not weak,
        "source_normalized_artifacts_trusted": not weak,
        "weak_source_ancestry": weak,
    }


def _attachment_source_ancestry(*, weak: bool = False) -> dict[str, object]:
    detail_direct = _synthetic_csindex_direct_source(
        "csindex-details",
        content_digit="a",
        contract_digit="b",
        request_count=4,
        weak=weak,
    )
    discovery_direct = _synthetic_csindex_direct_source(
        "csindex-discovery",
        content_digit="d",
        contract_digit="e",
        request_count=109,
        weak=False,
    )
    inventory_direct = _synthetic_csindex_direct_source(
        "csindex-inventory",
        content_digit="1",
        contract_digit="2",
        request_count=109,
        weak=False,
    )
    discovery = csindex_backfill._csindex_source_ancestry(
        source_stage="discovery_capture",
        direct_sources=(discovery_direct,),
    )
    inventory = csindex_backfill._csindex_source_ancestry(
        source_stage="inventory_capture",
        direct_sources=(inventory_direct,),
        upstream_ancestry=(discovery,),
    )
    return csindex_backfill._csindex_source_ancestry(
        source_stage="details_capture",
        direct_sources=(detail_direct,),
        upstream_ancestry=(inventory,),
    )


def _attachment_source_binding(
    ancestry: dict[str, object],
    *,
    population: list[dict[str, object]],
    phase: str = "csindex-attachments",
    capture_profile: str | None = None,
) -> dict[str, object]:
    capture_population = [
        row
        for row in population
        if row.get("reference_disposition") == "capture_eligible"
    ]
    derivation = {
        "capture_content_hash": ancestry["direct_sources"][0][
            "source_content_hash"
        ],
        "normalized_replay_root": "e" * 64,
        "full_population_root": canonical_hash(population),
        "selected_population_root": canonical_hash(population),
        "request_semantics_root": canonical_hash(
            [
                csindex_backfill._attachment_request_identity(row)
                for row in capture_population
            ]
        ),
        "selected_hosts": list(csindex_backfill.CSINDEX_ATTACHMENT_HOSTS),
        "implementation_root": csindex_implementation_root(),
    }
    input_root = canonical_hash({**derivation, "source_ancestry": ancestry})
    return csindex_backfill._csindex_source_binding(
        phase=phase,
        capture_profile=(
            capture_profile or csindex_backfill.CSINDEX_ATTACHMENT_FULL_PROFILE
        ),
        input_capture_content_hash=input_root,
        source_ancestry=ancestry,
        derivation=derivation,
    )


def _fixture_attachment_requests(
    population: list[dict[str, object]],
    *,
    weak: bool = False,
) -> list[ProviderProbeRequest]:
    ancestry = _attachment_source_ancestry(weak=weak)
    return _attachment_requests(
        population,
        source_ancestry=ancestry,
        source_binding=_attachment_source_binding(ancestry, population=population),
        capture_profile=csindex_backfill.CSINDEX_ATTACHMENT_FULL_PROFILE,
    )


def _legacy_cons_repair_fixture_population() -> list[dict[str, object]]:
    details: list[dict[str, object]] = []
    for spec in csindex_backfill.CSINDEX_LEGACY_CONS_REPAIR_ROWS:
        for announcement_id in spec["announcement_ids"]:
            details.append(
                {
                    "announcement_id": announcement_id,
                    "publish_date": spec["announcement_publish_date"],
                    "source_request_id": f"detail_{announcement_id}",
                    "source_payload_sha256": str(announcement_id).zfill(64),
                    "contains_csi300": (
                        announcement_id == spec["csi300_announcement_id"]
                    ),
                    "content_html": f'<a href="{spec["attachment_url"]}">xls</a>',
                }
            )
    details[0]["content_html"] = str(details[0]["content_html"]) + (
        '<a href="https://oss-ch.csindex.com.cn/static/generic.xls">other</a>'
    )
    return csindex_backfill._legacy_cons_repair_population(details)


def _legacy_cons_repair_source_binding(
    ancestry: dict[str, object],
    population: list[dict[str, object]],
) -> dict[str, object]:
    capture_population = [
        row
        for row in population
        if row.get("reference_disposition") == "capture_eligible"
    ]
    derivation = {
        "capture_content_hash": ancestry["direct_sources"][0][
            "source_content_hash"
        ],
        "normalized_replay_root": "e" * 64,
        "request_semantics_root": canonical_hash(
            [
                csindex_backfill._attachment_request_identity(row)
                for row in capture_population
            ]
        ),
        "selected_hosts": ["oss-ch.csindex.com.cn"],
        "implementation_root": csindex_implementation_root(),
        "repair_population_root": canonical_hash(population),
        "repair_profile_root": canonical_hash(
            csindex_backfill.CSINDEX_LEGACY_CONS_REPAIR_ROWS
        ),
    }
    input_root = canonical_hash({**derivation, "source_ancestry": ancestry})
    return csindex_backfill._csindex_source_binding(
        phase="csindex-legacy-cons-repair",
        capture_profile=csindex_backfill.CSINDEX_LEGACY_CONS_REPAIR_PROFILE,
        input_capture_content_hash=input_root,
        source_ancestry=ancestry,
        derivation=derivation,
    )


def _cninfo_discovery_source_ancestry(
    *,
    weak: bool = True,
) -> dict[str, object]:
    discovery_source = {
        "source_capture_schema": "free_provider_backfill_capture_v2",
        "source_generation_id": "free_provider_backfill_" + "a" * 24,
        "source_content_hash": "a" * 64,
        "source_contract_id": "b" * 64,
        "source_contract_content_hash": "b" * 64,
        "source_provider": "cninfo",
        "source_phase": "cninfo-discovery",
        "source_adapter": "cninfo_cninfo-discovery_signed_http_capture_v1",
        "source_leaf_profile": "supplemental",
        "source_scope": {
            "date_start": "20120101",
            "date_end": "20191231",
            "request_start": "20110101",
            "request_end": "20191231",
        },
        "source_implementation_root": "d" * 64,
        "source_publication_signature_verified": not weak,
        "source_normalized_artifacts_trusted": not weak,
        "weak_source_ancestry": weak,
    }
    discovery_semantic: dict[str, object] = {
        "schema_version": "cninfo_source_ancestry_v1",
        "source_stage": "discovery_capture_set",
        "leaf_profile": "supplemental",
        "direct_sources": [discovery_source],
        "upstream_ancestry": [],
        "weak_source_ancestry": weak,
    }
    discovery_ancestry = discovery_semantic | {
        "ancestry_root": canonical_hash(discovery_semantic)
    }
    return discovery_ancestry


def _cninfo_source_ancestry(*, weak: bool = True) -> dict[str, object]:
    discovery_ancestry = _cninfo_discovery_source_ancestry(weak=weak)
    inventory_source = {
        "source_capture_schema": "free_provider_backfill_capture_v2",
        "source_generation_id": "free_provider_backfill_" + "a" * 24,
        "source_content_hash": "a" * 64,
        "source_contract_id": "b" * 64,
        "source_contract_content_hash": "b" * 64,
        "source_provider": "cninfo",
        "source_phase": "cninfo-inventory",
        "source_adapter": "cninfo_cninfo-inventory_signed_http_capture_v1",
        "source_leaf_profile": "supplemental",
        "source_scope": {
            "date_start": "20120101",
            "date_end": "20191231",
            "request_start": "20110101",
            "request_end": "20191231",
        },
        "source_implementation_root": "e" * 64,
        "source_publication_signature_verified": True,
        "source_normalized_artifacts_trusted": True,
        "weak_source_ancestry": False,
    }
    inventory_semantic: dict[str, object] = {
        "schema_version": "cninfo_source_ancestry_v1",
        "source_stage": "inventory_capture",
        "leaf_profile": "supplemental",
        "direct_sources": [inventory_source],
        "upstream_ancestry": [discovery_ancestry],
        "weak_source_ancestry": weak,
    }
    return inventory_semantic | {
        "ancestry_root": canonical_hash(inventory_semantic)
    }


def _official_wrapper(
    body: dict,
    request: ProviderProbeRequest,
    *,
    envelope_overrides: dict[str, object] | None = None,
) -> tuple[dict, dict]:
    provider_body = json.dumps(body, ensure_ascii=False, sort_keys=True).encode()
    official_payload = {
        "schema_version": "official_http_probe_envelope_v1",
        "url": request.url,
        "method": request.method.upper(),
        "status_code": 200,
        "response_headers": {},
        "body_base64": base64.b64encode(provider_body).decode(),
        "body_sha256": hashlib.sha256(provider_body).hexdigest(),
        "redirect_followed": False,
    }
    official_payload.update(envelope_overrides or {})
    official = json.dumps(
        official_payload,
        sort_keys=True,
    ).encode()
    wrapper = {
        "schema_version": "free_provider_backfill_raw_envelope_v1",
        "request_id": request.request_id,
        "raw_payload_base64": base64.b64encode(official).decode(),
        "raw_payload_sha256": __import__("hashlib").sha256(official).hexdigest(),
        "raw_payload_size_bytes": len(official),
    }
    terminal = {
        "raw_envelope_relative_path": f"raw_envelopes/{request.request_id}.json",
        "terminal_state": "positive",
        "status_code": 200,
    }
    return wrapper, terminal


def _cninfo_zero_or_one_body(
    request: ProviderProbeRequest,
    *,
    announcement_leaf_id: str | None = None,
    announcement_time: int = 1294093800000,
    adjunct_url: str = "finalpage/2011-01-04/58854747.PDF",
) -> dict[str, object]:
    if request.metadata.get("case") == "cninfo_org_map":
        return {"stockList": [{"code": "600000"}]}
    if request.metadata.get("leaf_id") == announcement_leaf_id:
        return {
            "totalAnnouncement": 1,
            "announcements": [
                {
                    "announcementId": "58854747",
                    "secCode": "600000",
                    "secName": "浦发银行",
                    "orgId": "gssh0600000",
                    "announcementTitle": "更正公告",
                    "announcementTime": announcement_time,
                    "adjunctUrl": adjunct_url,
                    "adjunctSize": 1,
                    "announcementType": "x",
                    "columnId": "y",
                }
            ],
            "hasMore": False,
        }
    return {"totalAnnouncement": 0, "announcements": None, "hasMore": False}


class _FastFixtureSigner:
    public_key_pem = b"-----BEGIN PUBLIC KEY-----\nfixture\n-----END PUBLIC KEY-----\n"

    def sign(self, payload: bytes) -> str:
        return base64.b64encode(hashlib.sha256(payload).digest()).decode()


def _publish_cninfo_capture(
    output_root: Path,
    *,
    phase: str,
    requests: list[ProviderProbeRequest],
    normalizer: object,
    leaf_profile: str,
    input_capture_hash: str | None = None,
    announcement_leaf_id: str | None = None,
    announcement_time: int = 1294093800000,
    adjunct_url: str = "finalpage/2011-01-04/58854747.PDF",
    contract_provider: str = "cninfo",
    contract_adapter: str | None = None,
    contract_leaf_profile: str | None = None,
    omit_contract_leaf_profile: bool = False,
    scope_start: str = "20120101",
    request_start: str = "20110101",
    signer: object | None = None,
) -> str:
    signer = signer or EphemeralReceiptSigner.generate()
    source_binding = (
        requests[0].metadata.get("source_binding")
        if phase in {"cninfo-inventory", "cninfo-documents"} and requests
        else None
    )
    contract = cninfo_contract(
        phase=phase,
        output_root=output_root,
        signer=signer,
        population_root=canonical_hash([row.semantic() for row in requests]),
        request_count=len(requests),
        input_capture_hash=input_capture_hash,
        delay=0,
        timeout=3,
        max_retries=0,
        max_total_bytes=128 * 1024 * 1024,
        permission_context_id="human-approved-fixture",
        leaf_profile=leaf_profile,
        source_binding=source_binding,
    )
    adapter_identity = dict(contract.adapter_identity)
    if contract_adapter is not None:
        adapter_identity["adapter"] = contract_adapter
    if contract_leaf_profile is not None:
        adapter_identity["leaf_profile"] = contract_leaf_profile
    if omit_contract_leaf_profile:
        adapter_identity.pop("leaf_profile", None)
    contract = replace(
        contract,
        provider=contract_provider,
        scope_start=scope_start,
        request_start=request_start,
        adapter_identity=adapter_identity,
    )
    bound_requests = [
        replace(request, provider=contract_provider) for request in requests
    ]

    def transport(
        request: ProviderProbeRequest,
        _timeout: float,
    ) -> ProviderProbeObservation:
        body = _cninfo_zero_or_one_body(
            request,
            announcement_leaf_id=announcement_leaf_id,
            announcement_time=announcement_time,
            adjunct_url=adjunct_url,
        )
        provider_body = json.dumps(body, ensure_ascii=False, sort_keys=True).encode()
        raw = json.dumps(
            {
                "schema_version": "official_http_probe_envelope_v1",
                "url": request.url,
                "method": request.method.upper(),
                "status_code": 200,
                "response_headers": {},
                "body_base64": base64.b64encode(provider_body).decode(),
                "body_sha256": hashlib.sha256(provider_body).hexdigest(),
                "redirect_followed": False,
            },
            sort_keys=True,
        ).encode()
        is_empty = body.get("totalAnnouncement") == 0
        return ProviderProbeObservation(
            terminal_state="empty" if is_empty else "positive",
            raw_payload=raw,
            row_count=0 if is_empty else 1,
            status_code=200,
            checks={name: True for name in request.required_checks},
            transport_exchange_count=1,
        )

    result = run_free_provider_backfill(
        contract,
        bound_requests,
        transport=transport,
        signer=signer,
        normalizer=normalizer,  # type: ignore[arg-type]
        runtime_implementation_root=cninfo_implementation_root(),
    )
    assert result["status"] == "succeeded"
    return str(result["manifest_path"])


def _publish_csindex_discovery_capture(
    output_root: Path,
    *,
    signer: EphemeralReceiptSigner,
    contract_adapter: str | None = None,
    rows_by_leaf: dict[str, list[dict[str, object]]] | None = None,
) -> str:
    population, requests = build_csindex_discovery_plan()
    policy = csindex_backfill.CSINDEX_PHASE_RUNTIME_POLICY["csindex-discovery"]
    contract = csindex_contract(
        phase="csindex-discovery",
        output_root=output_root,
        signer=signer,
        population_root=canonical_hash(population),
        request_count=len(requests),
        input_capture_hash=None,
        delay=float(policy["minimum_delay_seconds"]),
        timeout=float(policy["timeout_seconds"]),
        retries=int(policy["max_retries"]),
        permission_context_id=csindex_backfill.DEFAULT_PERMISSION_CONTEXT,
        allowed_hosts=("www.csindex.com.cn",),
        capture_profile=csindex_backfill.CSINDEX_FULL_PROFILE,
    )
    if contract_adapter is not None:
        contract = replace(
            contract,
            adapter_identity=dict(contract.adapter_identity)
            | {"adapter": contract_adapter},
        )

    def transport(
        request: ProviderProbeRequest,
        _timeout: float,
    ) -> ProviderProbeObservation:
        if request.metadata.get("case") == "csindex_filter":
            body = {
                "success": True,
                "code": "200",
                "data": {
                    "classlist": [],
                    "indexlist": [],
                    "related_topics": [
                        {
                            "filterKey": "index_rebalance",
                            "filterName": "指数调样",
                            "filterNameEn": "Index Rebalance",
                        }
                    ],
                    "typelist": [],
                },
            }
            terminal_state = "positive"
            row_count = 1
        else:
            rows = (rows_by_leaf or {}).get(
                str(request.metadata.get("leaf_id") or ""), []
            )
            body = {
                "data": rows,
                "total": len(rows),
                "currentPage": 1,
                "pageSize": 1000 if rows else 0,
                "success": True,
                "code": "200",
            }
            terminal_state = "positive" if rows else "empty"
            row_count = len(rows)
        provider_body = json.dumps(body, sort_keys=True).encode()
        raw = json.dumps(
            {
                "schema_version": "official_http_probe_envelope_v1",
                "url": request.url,
                "method": request.method,
                "status_code": 200,
                "response_headers": {},
                "body_base64": base64.b64encode(provider_body).decode(),
                "body_sha256": hashlib.sha256(provider_body).hexdigest(),
                "redirect_followed": False,
            },
            sort_keys=True,
        ).encode()
        return ProviderProbeObservation(
            terminal_state=terminal_state,
            raw_payload=raw,
            row_count=row_count,
            status_code=200,
            checks={name: True for name in request.required_checks},
            transport_exchange_count=1,
        )

    with patch.object(capture_backfill.time, "sleep", return_value=None):
        result = run_free_provider_backfill(
            contract,
            requests,
            transport=transport,
            signer=signer,
            normalizer=normalize_csindex_discovery,
            runtime_implementation_root=csindex_implementation_root(),
        )
    assert result["status"] == "succeeded"
    return str(result["manifest_path"])


def _csindex_repair_announcement_rows() -> dict[str, list[dict[str, object]]]:
    by_leaf: dict[str, list[dict[str, object]]] = {}
    for spec in csindex_backfill.CSINDEX_LEGACY_CONS_REPAIR_ROWS:
        publish_date = str(spec["announcement_publish_date"])
        leaf_id = "index_rebalance_" + publish_date[:7].replace("-", "")
        by_leaf[leaf_id] = [
            {
                "id": announcement_id,
                "title": (
                    "沪深300指数调样"
                    if announcement_id == spec["csi300_announcement_id"]
                    else "中证指数调样"
                ),
                "theme": "指数调样",
                "publishDate": publish_date,
                "noticeType": "announcement",
                "fileUrl": None,
                "fileName": None,
            }
            for announcement_id in spec["announcement_ids"]
        ]
    return by_leaf


def _publish_csindex_json_capture(
    output_root: Path,
    *,
    phase: str,
    population: list[dict[str, object]],
    requests: list[ProviderProbeRequest],
    input_capture_hash: str,
    normalizer: object,
    signer: EphemeralReceiptSigner,
    rows_by_leaf: dict[str, list[dict[str, object]]],
) -> str:
    policy = csindex_backfill.CSINDEX_PHASE_RUNTIME_POLICY[phase]
    source_binding = requests[0].metadata["source_binding"]
    contract = csindex_contract(
        phase=phase,
        output_root=output_root,
        signer=signer,
        population_root=canonical_hash(
            {
                "population": population,
                "input_capture_content_hash": input_capture_hash,
            }
        ),
        request_count=len(requests),
        input_capture_hash=input_capture_hash,
        delay=float(policy["minimum_delay_seconds"]),
        timeout=float(policy["timeout_seconds"]),
        retries=int(policy["max_retries"]),
        permission_context_id=csindex_backfill.DEFAULT_PERMISSION_CONTEXT,
        allowed_hosts=("www.csindex.com.cn",),
        capture_profile=csindex_backfill.CSINDEX_FULL_PROFILE,
        source_binding=source_binding,
    )

    def transport(
        request: ProviderProbeRequest,
        _timeout: float,
    ) -> ProviderProbeObservation:
        case = request.metadata.get("case")
        if case == "csindex_filter":
            body: dict[str, object] = {
                "success": True,
                "code": "200",
                "data": {
                    "classlist": [],
                    "indexlist": [],
                    "related_topics": [
                        {
                            "filterKey": "index_rebalance",
                            "filterName": "指数调样",
                            "filterNameEn": "Index Rebalance",
                        }
                    ],
                    "typelist": [],
                },
            }
            terminal_state, row_count = "positive", 1
        elif case == "csindex_list":
            rows = rows_by_leaf.get(str(request.metadata["leaf_id"]), [])
            body = {
                "data": rows,
                "total": len(rows),
                "currentPage": request.metadata["page"],
                "pageSize": 1000 if rows else 0,
                "success": True,
                "code": "200",
            }
            terminal_state = "positive" if rows else "empty"
            row_count = len(rows)
        else:
            announcement_id = str(request.metadata["announcement_id"])
            source_row = next(
                row
                for rows in rows_by_leaf.values()
                for row in rows
                if str(row["id"]) == announcement_id
            )
            spec = next(
                row
                for row in csindex_backfill.CSINDEX_LEGACY_CONS_REPAIR_ROWS
                if announcement_id in row["announcement_ids"]
            )
            body = {
                "success": True,
                "code": "200",
                "data": {
                    "id": announcement_id,
                    "publishDate": source_row["publishDate"],
                    "title": source_row["title"],
                    "content": (
                        f'<a href="{spec["attachment_url"]}">constituents</a>'
                    ),
                    "enclosureList": [],
                    "imgList": [],
                },
            }
            terminal_state, row_count = "positive", 1
        provider_body = json.dumps(body, ensure_ascii=False, sort_keys=True).encode()
        raw = json.dumps(
            {
                "schema_version": "official_http_probe_envelope_v1",
                "url": request.url,
                "method": request.method,
                "status_code": 200,
                "response_headers": {},
                "body_base64": base64.b64encode(provider_body).decode(),
                "body_sha256": hashlib.sha256(provider_body).hexdigest(),
                "redirect_followed": False,
            },
            sort_keys=True,
        ).encode()
        return ProviderProbeObservation(
            terminal_state=terminal_state,
            raw_payload=raw,
            row_count=row_count,
            status_code=200,
            checks={name: True for name in request.required_checks},
            transport_exchange_count=1,
        )

    with patch.object(capture_backfill.time, "sleep", return_value=None):
        result = run_free_provider_backfill(
            contract,
            requests,
            transport=transport,
            signer=signer,
            normalizer=normalizer,  # type: ignore[arg-type]
            runtime_implementation_root=csindex_implementation_root(),
        )
    assert result["status"] == "succeeded"
    return str(result["manifest_path"])


def _cninfo_document_binding(
    source_ancestry: dict[str, object],
) -> tuple[str, dict[str, object]]:
    derivation = {
        "capture_content_hash": source_ancestry["direct_sources"][0][
            "source_content_hash"
        ],
        "normalized_replay_root": "9" * 64,
        "implementation_root": cninfo_implementation_root(),
    }
    input_root = canonical_hash(
        {**derivation, "source_ancestry": source_ancestry}
    )
    binding = cninfo_backfill._cninfo_source_binding(
        phase="cninfo-documents",
        input_capture_content_hash=input_root,
        source_ancestry=source_ancestry,
        derivation=derivation,
    )
    return input_root, binding


def _write_cninfo_activity_context(
    base: Path,
    requests: list[ProviderProbeRequest],
    *,
    phase: str,
    input_capture_hash: str,
    source_binding: dict[str, object],
    legacy: bool = False,
) -> tuple[Path, dict[str, str]]:
    signer = EphemeralReceiptSigner.generate()
    contract = cninfo_contract(
        phase=phase,
        output_root=base / "published",
        signer=signer,
        population_root=canonical_hash([request.semantic() for request in requests]),
        request_count=len(requests),
        input_capture_hash=input_capture_hash,
        delay=0,
        timeout=3,
        max_retries=0,
        max_total_bytes=128 * 1024 * 1024,
        permission_context_id="human-approved-fixture",
        leaf_profile=str(source_binding["source_leaf_profile"]),
        source_binding=source_binding,
    )
    contract_row = contract.semantic()
    if legacy:
        adapter = dict(contract_row["adapter_identity"])
        for key in (
            "leaf_profile",
            "source_binding_root",
            "source_ancestry_root",
            "source_upstream_content_hashes_root",
            "document_body_max_bytes",
        ):
            adapter.pop(key, None)
        contract_row["adapter_identity"] = adapter
    request_rows = [request.semantic() for request in requests]
    request_plan_hash = canonical_hash(request_rows)
    contract_id = canonical_hash(contract_row)
    activity_id = canonical_hash(
        {"contract_id": contract_id, "request_plan_hash": request_plan_hash}
    )
    run_root = base / ".documents.activities" / activity_id
    run_root.mkdir(parents=True)
    (run_root / "activity_contract.json").write_text(
        json.dumps(contract_row, sort_keys=True),
        encoding="utf-8",
    )
    (run_root / "request_plan.json").write_text(
        json.dumps(
            {
                "schema_version": "free_provider_backfill_request_plan_v1",
                "request_plan_hash": request_plan_hash,
                "requests": request_rows,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return run_root, {
        "activity_id": activity_id,
        "contract_id": contract_id,
        "request_plan_hash": request_plan_hash,
        "input_capture_content_hash": str(
            contract_row["adapter_identity"]["input_capture_content_hash"]
        ),
        "implementation_root": str(
            contract_row["adapter_identity"]["implementation_root"]
        ),
    }


def _baostock_request_frame(
    operation: str, arguments: list[str], *, message_type: str
) -> bytes:
    body = "\x01".join([operation, *arguments])
    header = f"00.9.30\x01{message_type}\x01{len(body):010d}"
    head_body = (header + body).encode()
    return head_body + f"\x01{zlib.crc32(head_body)}\n".encode()


def _baostock_response_frame(
    operation: str,
    *,
    rows: list[list[str]],
    response_suffix: list[str],
    message_type: str,
    user: str = "anonymous",
    page: int = 1,
    page_size: int = 2000,
) -> bytes:
    record = json.dumps(
        {"record": rows}, ensure_ascii=False, separators=(",", ":")
    )
    body = "\x01".join(
        [
            "0",
            "success",
            operation,
            user,
            str(page),
            str(page_size),
            record,
            *response_suffix,
        ]
    )
    if message_type in {"96", "99", "9B", "9D"}:
        encoded_body = zlib.compress(body.encode())
        declared = len(encoded_body)
    else:
        encoded_body = body.encode()
        declared = len(body)
    header = f"00.9.00\x01{message_type}\x01{declared:010d}".encode()
    crc = zlib.crc32(header + encoded_body)
    separator = b"\n" if message_type in {"96", "99", "9B", "9D"} else b""
    return (
        header
        + encoded_body
        + f"\x01{crc}".encode()
        + separator
        + b"<![CDATA[]]>\n"
    )


def _baostock_uncompressed_response_frame(
    tokens: list[str], *, message_type: str
) -> bytes:
    body = "\x01".join(tokens)
    header = f"00.9.00\x01{message_type}\x01{len(body):010d}".encode()
    head_body = header + body.encode()
    return (
        head_body
        + f"\x01{zlib.crc32(head_body)}".encode()
        + b"<![CDATA[]]>\n"
    )


def _baostock_error_payload(
    request: ProviderProbeRequest,
    *,
    operation: str,
    request_arguments: list[str],
    request_type: str,
) -> bytes:
    wire_request = _baostock_request_frame(
        operation, request_arguments, message_type=request_type
    )
    wire_response = _baostock_uncompressed_response_frame(
        ["10001001", "session expired"], message_type="04"
    )
    exchange = {
        "wire_request_base64": base64.b64encode(wire_request).decode(),
        "request_sha256": hashlib.sha256(wire_request).hexdigest(),
        "request_size_bytes": len(wire_request),
        "socket_peer": ["1.2.3.4", 10030],
        "wire_response_base64": base64.b64encode(wire_response).decode(),
        "wire_response_sha256": hashlib.sha256(wire_response).hexdigest(),
        "wire_size_bytes": len(wire_response),
        "terminal_marker_present": True,
    }
    return json.dumps(
        {
            "schema_version": "baostock_wire_probe_envelope_v1",
            "package_distribution_version": "0.9.3",
            "client_protocol_version": "00.9.30",
            "request_id": request.request_id,
            "wire_exchanges": [exchange],
            "parsed": {
                "fields": [],
                "row_count": 0,
                "pages": [],
                "first_rows": [],
                "last_rows": [],
                "canonical_logical_payload_sha256": canonical_hash(
                    {"fields": [], "rows": []}
                ),
            },
            "provider_error": {
                "code": "10001001",
                "message": "session expired",
            },
        },
        sort_keys=True,
    ).encode()


def _replace_first_baostock_request(
    raw_payload: bytes,
    mutate: object,
) -> bytes:
    envelope = json.loads(raw_payload)
    exchange = envelope["wire_exchanges"][0]
    original = base64.b64decode(exchange["wire_request_base64"])
    tokens, frame = capture_backfill._parse_baostock_request_frame(original)
    changed = list(tokens)
    mutate(changed)  # type: ignore[operator]
    wire_request = _baostock_request_frame(
        changed[0], changed[1:], message_type=frame["message_type"]
    )
    exchange["wire_request_base64"] = base64.b64encode(wire_request).decode()
    exchange["request_sha256"] = hashlib.sha256(wire_request).hexdigest()
    exchange["request_size_bytes"] = len(wire_request)
    return json.dumps(envelope, sort_keys=True).encode()


def _security_or_history_payload(
    request: ProviderProbeRequest,
    *,
    fields: list[str],
    rows: list[list[str]],
    parsed_rows: list[list[str]] | None = None,
    parsed_items: list[list[str]] | None = None,
    operation_override: str | None = None,
    response_override: bytes | None = None,
    query_overrides: dict[str, str] | None = None,
) -> bytes:
    query = {
        key: values[-1]
        for key, values in parse_qs(urlsplit(request.url).query).items()
    }
    query.update(query_overrides or {})
    if request.metadata.get("case") == "all_stock":
        operation = operation_override or "query_all_stock"
        request_arguments = ["anonymous", "1", "2000", query["date"]]
        response_suffix = [query["date"], ",".join(fields)]
        request_type, response_type = "35", "36"
    else:
        operation = operation_override or "query_history_k_data_plus"
        request_arguments = [
            "anonymous",
            "1",
            "2000",
            query["code"],
            query["fields"],
            query["start"],
            query["end"],
            "d",
            "3",
        ]
        response_suffix = [
            query["code"],
            ",".join(fields),
            query["start"],
            query["end"],
            "d",
            "3",
        ]
        request_type, response_type = "95", "96"
    wire_request = _baostock_request_frame(
        operation, request_arguments, message_type=request_type
    )
    wire_response = response_override or _baostock_response_frame(
        operation,
        rows=rows,
        response_suffix=response_suffix,
        message_type=response_type,
    )
    exchange = {
        "wire_request_base64": base64.b64encode(wire_request).decode(),
        "request_sha256": hashlib.sha256(wire_request).hexdigest(),
        "request_size_bytes": len(wire_request),
        "socket_peer": ["1.2.3.4", 10030],
        "wire_response_base64": base64.b64encode(wire_response).decode(),
        "wire_response_sha256": hashlib.sha256(wire_response).hexdigest(),
        "wire_size_bytes": len(wire_response),
        "terminal_marker_present": True,
    }
    package_rows = rows if parsed_rows is None else parsed_rows
    parsed: dict[str, object] = {
        "fields": fields,
        "row_count": len(package_rows),
        "pages": [
            {
                "page": 1,
                "row_count": len(package_rows),
                "provider_page_size": 2000,
            }
        ],
        "first_rows": package_rows[:3],
        "last_rows": package_rows[-3:],
        "canonical_logical_payload_sha256": canonical_hash(
            {"fields": fields, "rows": package_rows}
        ),
    }
    if parsed_items is not None:
        parsed["items"] = parsed_items
    return json.dumps(
        {
            "schema_version": "baostock_wire_probe_envelope_v1",
            "package_distribution_version": "0.9.3",
            "client_protocol_version": "00.9.30",
            "request_id": request.request_id,
            "wire_exchanges": [exchange],
            "parsed": parsed,
            "provider_error": {"code": "0", "message": "success"},
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode()


def _history_pages_payload(
    request: ProviderProbeRequest,
    *,
    page_rows: list[list[list[str]]],
    request_pages: list[int] | None = None,
    response_pages: list[int] | None = None,
    response_users: list[str] | None = None,
    request_type: str = "95",
    response_type: str = "96",
) -> bytes:
    query = {
        key: values[-1]
        for key, values in parse_qs(urlsplit(request.url).query).items()
    }
    pages = request_pages or list(range(1, len(page_rows) + 1))
    echoed_pages = response_pages or pages
    users = response_users or ["anonymous"] * len(page_rows)
    fields = BAOSTOCK_FIELDS.split(",")
    exchanges: list[dict[str, object]] = []
    for rows, request_page, response_page, response_user in zip(
        page_rows, pages, echoed_pages, users, strict=True
    ):
        request_bytes = _baostock_request_frame(
            "query_history_k_data_plus",
            [
                "anonymous",
                str(request_page),
                "2000",
                query["code"],
                query["fields"],
                query["start"],
                query["end"],
                "d",
                "3",
            ],
            message_type=request_type,
        )
        response_bytes = _baostock_response_frame(
            "query_history_k_data_plus",
            rows=rows,
            response_suffix=[
                query["code"],
                query["fields"],
                query["start"],
                query["end"],
                "d",
                "3",
            ],
            message_type=response_type,
            user=response_user,
            page=response_page,
        )
        exchanges.append(
            {
                "wire_request_base64": base64.b64encode(request_bytes).decode(),
                "request_sha256": hashlib.sha256(request_bytes).hexdigest(),
                "request_size_bytes": len(request_bytes),
                "socket_peer": ["1.2.3.4", 10030],
                "wire_response_base64": base64.b64encode(response_bytes).decode(),
                "wire_response_sha256": hashlib.sha256(response_bytes).hexdigest(),
                "wire_size_bytes": len(response_bytes),
                "terminal_marker_present": True,
            }
        )
    rows = [row for page in page_rows for row in page]
    return json.dumps(
        {
            "schema_version": "baostock_wire_probe_envelope_v1",
            "package_distribution_version": "0.9.3",
            "client_protocol_version": "00.9.30",
            "request_id": request.request_id,
            "wire_exchanges": exchanges,
            "parsed": {
                "fields": fields,
                "row_count": len(rows),
                "pages": [
                    {
                        "page": response_page,
                        "row_count": len(page),
                        "provider_page_size": 2000,
                    }
                    for response_page, page in zip(
                        echoed_pages, page_rows, strict=True
                    )
                ],
                "first_rows": rows[:3],
                "last_rows": rows[-3:],
                "canonical_logical_payload_sha256": canonical_hash(
                    {"fields": fields, "rows": rows}
                ),
            },
            "provider_error": {"code": "0", "message": "success"},
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode()


def _baostock_wrapper(
    *, request: ProviderProbeRequest, fields: list[str], rows: list[list[str]]
) -> tuple[dict, dict]:
    payload = _security_or_history_payload(request, fields=fields, rows=rows)
    wrapper = {
        "schema_version": "free_provider_backfill_raw_envelope_v1",
        "request_id": request.request_id,
        "raw_payload_base64": base64.b64encode(payload).decode(),
        "raw_payload_sha256": hashlib.sha256(payload).hexdigest(),
    }
    terminal = {
        "raw_envelope_relative_path": f"raw_envelopes/{request.request_id}.json",
        "terminal_state": "positive",
    }
    return wrapper, terminal


def test_cninfo_discovery_uses_four_monthly_leaf_families_without_annual_cap() -> None:
    leaves, requests = build_cninfo_discovery_plan()

    assert len(leaves) == 9 * 12 * 4
    assert len(requests) == len(leaves) + 1
    assert len({row["leaf_id"] for row in leaves}) == len(leaves)
    assert {row["kind"] for row in leaves} == {
        "st_delist",
        "corporate_actions",
        "suspensions_sh",
        "suspensions_sz",
    }
    sample = next(row for row in requests if row.request_id == "cninfo_corporate_actions_201202_page_001")
    params = parse_qs(sample.body.decode())
    assert params["seDate"] == ["2012-02-01~2012-02-29"]
    assert params["pageSize"] == ["30"]


def test_cninfo_supplemental_profile_locks_seven_monthly_category_families() -> None:
    leaves, requests = build_cninfo_discovery_plan(
        leaf_profile="supplemental"
    )

    assert len(leaves) == (9 * 12 * 7) + 2
    assert len(requests) == len(leaves) + 1
    assert {row["kind"] for row in leaves} == {
        "corrections",
        "rights_issues",
        "initial_offerings",
        "delisting_period",
        "secondary_offerings",
        "equity_changes",
        "risk_warnings",
    }
    sample = next(
        row
        for row in requests
        if row.request_id == "cninfo_rights_issues_201202_page_001"
    )
    params = parse_qs(sample.body.decode())
    assert params["category"] == ["category_pg_szsh;"]
    by_id = {row["leaf_id"]: row for row in leaves}
    assert "secondary_offerings_201511" not in by_id
    assert "secondary_offerings_201512" not in by_id
    assert {
        key: (by_id[key]["date_start"], by_id[key]["date_end"])
        for key in (
            "secondary_offerings_201511_d01_15",
            "secondary_offerings_201511_d16_30",
            "secondary_offerings_201512_d01_15",
            "secondary_offerings_201512_d16_31",
        )
    } == {
        "secondary_offerings_201511_d01_15": (
            "2015-11-01",
            "2015-11-15",
        ),
        "secondary_offerings_201511_d16_30": (
            "2015-11-16",
            "2015-11-30",
        ),
        "secondary_offerings_201512_d01_15": (
            "2015-12-01",
            "2015-12-15",
        ),
        "secondary_offerings_201512_d16_31": (
            "2015-12-16",
            "2015-12-31",
        ),
    }
    with pytest.raises(ValueError, match="leaf_filter_unknown"):
        build_cninfo_discovery_plan(["rights_issues_201202"])


def test_cninfo_supplemental_static_date_split_rejects_a_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cninfo_backfill,
        "CNINFO_STATIC_DATE_SPLITS",
        {
            "supplemental": {
                "secondary_offerings_201511": (
                    ("d01_15", "2015-11-01", "2015-11-15"),
                    ("d17_30", "2015-11-17", "2015-11-30"),
                )
            }
        },
    )

    with pytest.raises(ValueError, match="cninfo_static_date_split_invalid"):
        build_cninfo_discovery_plan(leaf_profile="supplemental")


def test_cninfo_split_leaf_announcement_date_uses_exact_interval() -> None:
    cninfo_backfill._validate_inventory_announcement_dates(
        [
            {
                "announcement_id": "1201790000",
                "announcement_time": 1447430400000,
                "matched_leaves": [
                    "secondary_offerings_201511_d01_15"
                ],
            }
        ]
    )

    with pytest.raises(
        ValueError,
        match="cninfo_inventory_announcement_date_invalid:1201790000",
    ):
        cninfo_backfill._validate_inventory_announcement_dates(
            [
                {
                    "announcement_id": "1201790000",
                    "announcement_time": 1447430400000,
                    "matched_leaves": [
                        "secondary_offerings_201511_d16_30"
                    ],
                }
            ]
        )


def test_cninfo_split_leaf_flows_from_discovery_to_document_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(capture_backfill.os, "fsync", lambda _fd: None)
    monkeypatch.setattr(
        capture_backfill,
        "verify_signature",
        lambda **_kwargs: None,
    )
    signer = _FastFixtureSigner()
    leaf_id = "secondary_offerings_201511_d01_15"
    announcement_time = 1447430400000
    adjunct_url = "finalpage/2015-11-14/58854747.PDF"
    _leaves, discovery_requests = build_cninfo_discovery_plan(
        leaf_profile="supplemental"
    )
    discovery_manifest = _publish_cninfo_capture(
        tmp_path / "discovery",
        phase="cninfo-discovery",
        requests=discovery_requests,
        normalizer=normalize_cninfo_discovery,
        leaf_profile="supplemental",
        announcement_leaf_id=leaf_id,
        announcement_time=announcement_time,
        adjunct_url=adjunct_url,
        signer=signer,
    )
    _population, inventory_requests, input_root = (
        build_cninfo_inventory_plan(
            [discovery_manifest],
            leaf_profile="supplemental",
        )
    )
    inventory_manifest = _publish_cninfo_capture(
        tmp_path / "inventory",
        phase="cninfo-inventory",
        requests=inventory_requests,
        normalizer=normalize_cninfo_inventory,
        leaf_profile="supplemental",
        input_capture_hash=input_root,
        announcement_leaf_id=leaf_id,
        announcement_time=announcement_time,
        adjunct_url=adjunct_url,
        signer=signer,
    )

    rows, document_requests, _document_root = build_cninfo_document_plan(
        inventory_manifest,
        include_years=[2015],
    )

    assert len(rows) == 1
    assert len(document_requests) == 1
    assert document_requests[0].metadata["adjunct_url"] == adjunct_url


@pytest.mark.parametrize(
    "contract_change",
    (
        {"contract_provider": "csindex"},
        {"contract_adapter": "cninfo_cninfo-inventory_signed_http_capture_v1"},
        {"scope_start": "20130101"},
        {"contract_leaf_profile": "base"},
    ),
)
def test_cninfo_inventory_plan_rejects_discovery_contract_outside_profile_scope(
    tmp_path: Path,
    contract_change: dict[str, object],
) -> None:
    _leaves, requests = build_cninfo_discovery_plan(
        ["corrections_201101"],
        leaf_profile="supplemental",
    )
    manifest_path = _publish_cninfo_capture(
        tmp_path / "discovery",
        phase="cninfo-discovery",
        requests=requests,
        normalizer=normalize_cninfo_discovery,
        leaf_profile="supplemental",
        **contract_change,  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match="cninfo_discovery_source_contract_invalid"):
        build_cninfo_inventory_plan(
            [manifest_path],
            leaf_profile="supplemental",
        )


@pytest.mark.parametrize(
    ("captured_profile", "leaf_id", "drop_org_map", "reason"),
    (
        ("supplemental", "corrections_201101", False, "leaf_missing"),
        ("base", "st_delist_201101", False, "leaf_extra"),
        ("supplemental", "corrections_201101", True, "org_map_missing"),
    ),
)
def test_cninfo_inventory_plan_requires_exact_profile_leaf_and_org_closure(
    tmp_path: Path,
    captured_profile: str,
    leaf_id: str,
    drop_org_map: bool,
    reason: str,
) -> None:
    _leaves, requests = build_cninfo_discovery_plan(
        [leaf_id],
        leaf_profile=captured_profile,
    )
    if drop_org_map:
        requests = requests[1:]
    manifest_path = _publish_cninfo_capture(
        tmp_path / "discovery",
        phase="cninfo-discovery",
        requests=requests,
        normalizer=normalize_cninfo_discovery,
        leaf_profile="supplemental",
    )

    with pytest.raises(ValueError, match=f"cninfo_discovery_{reason}"):
        build_cninfo_inventory_plan(
            [manifest_path],
            leaf_profile="supplemental",
        )


def test_cninfo_signed_inventory_preserves_weak_discovery_ancestry_in_documents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Durability/fsync behavior belongs to the shared capture-engine tests.  This
    # test exercises CNINFO closure and ancestry over 1,500 local fixture writes.
    monkeypatch.setattr(capture_backfill.os, "fsync", lambda _fd: None)
    monkeypatch.setattr(
        capture_backfill,
        "verify_signature",
        lambda **_kwargs: None,
    )
    signer = _FastFixtureSigner()
    _leaves, discovery_requests = build_cninfo_discovery_plan(
        leaf_profile="supplemental"
    )
    discovery_manifest = _publish_cninfo_capture(
        tmp_path / "discovery",
        phase="cninfo-discovery",
        requests=discovery_requests,
        normalizer=normalize_cninfo_discovery,
        leaf_profile="supplemental",
        announcement_leaf_id="corrections_201101",
        signer=signer,
    )
    _population, inventory_requests, input_root = build_cninfo_inventory_plan(
        [discovery_manifest],
        leaf_profile="supplemental",
    )
    strong_ancestry = inventory_requests[0].metadata["source_ancestry"]
    assert all(
        request.metadata["source_ancestry"] == strong_ancestry
        for request in inventory_requests
    )
    assert strong_ancestry["source_stage"] == "discovery_capture_set"
    assert len(strong_ancestry["direct_sources"]) == 1
    assert strong_ancestry["weak_source_ancestry"] is False

    weak_ancestry = json.loads(json.dumps(strong_ancestry))
    weak_ancestry["direct_sources"][0][
        "source_publication_signature_verified"
    ] = False
    weak_ancestry["direct_sources"][0][
        "source_normalized_artifacts_trusted"
    ] = False
    weak_ancestry["direct_sources"][0]["weak_source_ancestry"] = True
    weak_ancestry["weak_source_ancestry"] = True
    weak_semantic = {
        key: value
        for key, value in weak_ancestry.items()
        if key != "ancestry_root"
    }
    weak_ancestry["ancestry_root"] = canonical_hash(weak_semantic)
    weak_derivation = {
        "discovery_capture_content_hashes": sorted(
            row["source_content_hash"]
            for row in weak_ancestry["direct_sources"]
        )
    }
    weak_input_root = canonical_hash(
        {
            "leaf_profile": "supplemental",
            **weak_derivation,
            "source_ancestry": weak_ancestry,
        }
    )
    weak_binding = cninfo_backfill._cninfo_source_binding(
        phase="cninfo-inventory",
        input_capture_content_hash=weak_input_root,
        source_ancestry=weak_ancestry,
        derivation=weak_derivation,
    )
    inventory_requests = [
        replace(
            request,
            metadata=dict(request.metadata)
            | {
                "source_ancestry": weak_ancestry,
                "source_binding": weak_binding,
            },
        )
        for request in inventory_requests
    ]
    inventory_manifest = _publish_cninfo_capture(
        tmp_path / "inventory",
        phase="cninfo-inventory",
        requests=inventory_requests,
        normalizer=normalize_cninfo_inventory,
        leaf_profile="supplemental",
        input_capture_hash=weak_input_root,
        announcement_leaf_id="corrections_201101",
        signer=signer,
    )
    inventory_verdict = cninfo_backfill.validate_cninfo_governance(
        inventory_manifest
    )["cninfo_governance_qualification"]
    assert inventory_verdict["source_lineage_complete"] is True
    assert inventory_verdict["weak_source_ancestry"] is True
    assert inventory_verdict["governed_evidence_eligible"] is False

    _rows, document_requests, _document_input_root = build_cninfo_document_plan(
        inventory_manifest,
        include_years=[2011],
    )
    document_ancestry = document_requests[0].metadata["source_ancestry"]
    assert document_ancestry["source_stage"] == "inventory_capture"
    assert document_ancestry["direct_sources"][0][
        "source_publication_signature_verified"
    ] is True
    assert document_ancestry["upstream_ancestry"][0][
        "weak_source_ancestry"
    ] is True
    assert document_ancestry["weak_source_ancestry"] is True


def test_cninfo_inventory_rejects_weak_ancestry_rewritten_as_strong(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(capture_backfill.os, "fsync", lambda _fd: None)
    monkeypatch.setattr(capture_backfill, "verify_signature", lambda **_kwargs: None)
    signer = _FastFixtureSigner()
    _leaves, discovery_requests = build_cninfo_discovery_plan(
        leaf_profile="supplemental"
    )
    discovery_manifest = _publish_cninfo_capture(
        tmp_path / "discovery",
        phase="cninfo-discovery",
        requests=discovery_requests,
        normalizer=normalize_cninfo_discovery,
        leaf_profile="supplemental",
        signer=signer,
    )
    _population, inventory_requests, input_root = build_cninfo_inventory_plan(
        [discovery_manifest],
        leaf_profile="supplemental",
    )
    original_ancestry = inventory_requests[0].metadata["source_ancestry"]
    weak_ancestry = json.loads(json.dumps(original_ancestry))
    weak_ancestry["direct_sources"][0][
        "source_publication_signature_verified"
    ] = False
    weak_ancestry["direct_sources"][0][
        "source_normalized_artifacts_trusted"
    ] = False
    weak_ancestry["direct_sources"][0]["weak_source_ancestry"] = True
    weak_ancestry["weak_source_ancestry"] = True
    weak_ancestry["ancestry_root"] = canonical_hash(
        {
            key: value
            for key, value in weak_ancestry.items()
            if key != "ancestry_root"
        }
    )
    weak_derivation = {
        "discovery_capture_content_hashes": sorted(
            row["source_content_hash"]
            for row in weak_ancestry["direct_sources"]
        )
    }
    weak_input_root = canonical_hash(
        {
            "leaf_profile": "supplemental",
            **weak_derivation,
            "source_ancestry": weak_ancestry,
        }
    )
    weak_binding = cninfo_backfill._cninfo_source_binding(
        phase="cninfo-inventory",
        input_capture_content_hash=weak_input_root,
        source_ancestry=weak_ancestry,
        derivation=weak_derivation,
    )
    weak_requests = [
        replace(
            request,
            metadata=dict(request.metadata)
            | {
                "source_ancestry": weak_ancestry,
                "source_binding": weak_binding,
            },
        )
        for request in inventory_requests
    ]
    forged_strong = json.loads(json.dumps(weak_ancestry))
    forged_strong["direct_sources"][0][
        "source_publication_signature_verified"
    ] = True
    forged_strong["direct_sources"][0][
        "source_normalized_artifacts_trusted"
    ] = True
    forged_strong["direct_sources"][0]["weak_source_ancestry"] = False
    forged_strong["weak_source_ancestry"] = False
    forged_strong["ancestry_root"] = canonical_hash(
        {
            key: value
            for key, value in forged_strong.items()
            if key != "ancestry_root"
        }
    )
    forged_requests = [
        replace(
            request,
            metadata=dict(request.metadata)
            | {"source_ancestry": forged_strong},
        )
        for request in weak_requests
    ]

    with pytest.raises(ValueError, match="source_binding"):
        _publish_cninfo_capture(
            tmp_path / "forged_inventory",
            phase="cninfo-inventory",
            requests=forged_requests,
            normalizer=normalize_cninfo_inventory,
            leaf_profile="supplemental",
            input_capture_hash=weak_input_root,
            signer=signer,
        )

    assert input_root != weak_input_root


def test_cninfo_inventory_normalizer_rejects_partial_profile_closure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(capture_backfill.os, "fsync", lambda _fd: None)
    monkeypatch.setattr(capture_backfill, "verify_signature", lambda **_kwargs: None)
    signer = _FastFixtureSigner()
    _leaves, discovery_requests = build_cninfo_discovery_plan(
        leaf_profile="supplemental",
    )
    discovery_manifest = _publish_cninfo_capture(
        tmp_path / "discovery",
        phase="cninfo-discovery",
        requests=discovery_requests,
        normalizer=normalize_cninfo_discovery,
        leaf_profile="supplemental",
        signer=signer,
    )
    _population, inventory_requests, input_root = build_cninfo_inventory_plan(
        [discovery_manifest],
        leaf_profile="supplemental",
    )
    partial_requests = [
        request
        for request in inventory_requests
        if request.metadata.get("leaf_id") != "risk_warnings_201912"
    ]
    assert len(partial_requests) == len(inventory_requests) - 1

    with pytest.raises(ValueError, match="request_closure"):
        _publish_cninfo_capture(
            tmp_path / "partial_inventory",
            phase="cninfo-inventory",
            requests=partial_requests,
            normalizer=normalize_cninfo_inventory,
            leaf_profile="supplemental",
            input_capture_hash=input_root,
            signer=signer,
        )


def test_csindex_discovery_uses_all_rebalance_topics_by_month() -> None:
    leaves, requests = build_csindex_discovery_plan()

    assert len(leaves) == 9 * 12
    assert len(requests) == len(leaves) + 1
    sample = next(row for row in requests if row.request_id == "csindex_index_rebalance_201202_page_001")
    body = json.loads(sample.body)
    assert body["relatedTopics"] == ["index_rebalance"]
    assert body["startDate"] == "2012-02-01"
    assert body["endDate"] == "2012-02-29"
    assert body["page"]["rows"] == 1000
    assert "searchInput" not in body


@pytest.mark.parametrize(
    "payload",
    (
        {"success": True, "code": "200", "data": [{"key": "index_rebalance"}]},
        {
            "success": True,
            "code": "200",
            "data": {
                "classlist": [
                    {
                        "filterKey": "index_rebalance",
                        "filterName": "指数调样",
                        "filterNameEn": "Index Rebalance",
                    }
                ],
                "indexlist": [],
                "related_topics": [],
                "typelist": [],
            },
        },
        {
            "success": True,
            "code": "200",
            "data": {
                "classlist": [],
                "indexlist": [],
                "related_topics": [
                    {
                        "filterKey": "index_rebalance",
                        "filterName": "指数调样",
                        "filterNameEn": "Index Rebalance",
                    },
                    {
                        "filterKey": "index_rebalance",
                        "filterName": "重复",
                        "filterNameEn": "Duplicate",
                    },
                ],
                "typelist": [],
            },
        },
    ),
)
def test_csindex_filter_topic_parser_rejects_flat_or_ambiguous_schema(
    payload: dict[str, object],
) -> None:
    assert csindex_backfill._csindex_filter_topic_present(payload) is False


def test_csindex_inventory_plan_binds_exact_full_discovery_ancestry(
    tmp_path: Path,
    approved_csindex_signer: EphemeralReceiptSigner,
) -> None:
    manifest_path = _publish_csindex_discovery_capture(
        tmp_path / "csindex/discovery",
        signer=approved_csindex_signer,
    )

    population, requests, input_root = build_csindex_inventory_plan(manifest_path)

    assert len(population) == 108
    assert len(requests) == 109
    assert len(input_root) == 64
    ancestry = requests[0].metadata["source_ancestry"]
    binding = requests[0].metadata["source_binding"]
    assert ancestry["source_stage"] == "discovery_capture"
    assert ancestry["weak_source_ancestry"] is False
    assert binding["phase"] == "csindex-inventory"
    assert all(
        request.metadata["source_ancestry"] == ancestry
        and request.metadata["source_binding"] == binding
        for request in requests
    )


def test_csindex_inventory_plan_rejects_discovery_phase_confusion(
    tmp_path: Path,
    approved_csindex_signer: EphemeralReceiptSigner,
) -> None:
    manifest_path = _publish_csindex_discovery_capture(
        tmp_path / "csindex/discovery",
        signer=approved_csindex_signer,
        contract_adapter=csindex_backfill.CSINDEX_PHASE_ADAPTERS[
            "csindex-inventory"
        ],
    )

    with pytest.raises(ValueError, match="authorized_contract_closure"):
        build_csindex_inventory_plan(manifest_path)


@pytest.mark.parametrize(
    "override",
    (
        {"permission_context_id": "bogus-authorization"},
        {"signer": EphemeralReceiptSigner.generate()},
        {"delay": 0.0},
        {"timeout": 3.0},
        {"retries": 0},
    ),
)
def test_csindex_contract_rejects_authorization_or_budget_drift_before_capture(
    tmp_path: Path,
    approved_csindex_signer: EphemeralReceiptSigner,
    override: dict[str, object],
) -> None:
    policy = csindex_backfill.CSINDEX_PHASE_RUNTIME_POLICY["csindex-discovery"]
    arguments: dict[str, object] = {
        "phase": "csindex-discovery",
        "output_root": tmp_path / "csindex/discovery",
        "signer": approved_csindex_signer,
        "population_root": "a" * 64,
        "request_count": 109,
        "input_capture_hash": None,
        "delay": policy["minimum_delay_seconds"],
        "timeout": policy["timeout_seconds"],
        "retries": policy["max_retries"],
        "permission_context_id": csindex_backfill.DEFAULT_PERMISSION_CONTEXT,
        "allowed_hosts": ("www.csindex.com.cn",),
        "capture_profile": csindex_backfill.CSINDEX_FULL_PROFILE,
    }
    arguments.update(override)

    with pytest.raises(ValueError, match="authorized_contract_closure"):
        csindex_contract(**arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("source_phase", "csindex-inventory"),
        ("source_content_hash", "7" * 64),
        ("source_contract_id", "not-a-contract-id"),
        ("source_capture_profile", csindex_backfill.CSINDEX_DISCOVERY_SLICE_PROFILE),
    ),
)
def test_csindex_direct_source_rejects_phase_hash_contract_or_slice_drift(
    field: str,
    value: object,
) -> None:
    direct = _synthetic_csindex_direct_source(
        "csindex-discovery",
        content_digit="d",
        contract_digit="e",
        request_count=109,
        weak=False,
    )
    direct[field] = value

    with pytest.raises(ValueError, match="source_ancestry_invalid"):
        csindex_backfill._validate_csindex_direct_source(direct)


def test_csindex_transport_accepts_zero_page_size_only_for_structured_empty() -> None:
    _leaves, requests = build_csindex_discovery_plan(["index_rebalance_201104"])
    request = requests[1]
    provider_body = json.dumps(
        {
            "data": [],
            "total": 0,
            "currentPage": 1,
            "pageSize": 0,
            "code": "200",
            "success": True,
        }
    ).encode()
    official = json.dumps(
        {
            "schema_version": "official_http_probe_envelope_v1",
            "url": request.url,
            "method": request.method,
            "status_code": 200,
            "response_headers": {},
            "body_base64": base64.b64encode(provider_body).decode(),
            "body_sha256": hashlib.sha256(provider_body).hexdigest(),
            "redirect_followed": False,
        }
    ).encode()
    base_observation = ProviderProbeObservation(
        terminal_state="empty",
        raw_payload=official,
        row_count=0,
        status_code=200,
        checks={
            "list_shape": True,
            "total_semantics": True,
            "current_page_present": True,
            "current_page_semantics": True,
        },
        transport_exchange_count=1,
    )
    transport = CSIndexBackfillTransport(minimum_delay_seconds=0)
    transport._transport = lambda _request, _timeout: base_observation

    observed = transport(request, 3)

    assert observed.terminal_state == "empty"
    assert observed.error_code is None
    assert observed.checks["page_size_matches_request"] is True


def test_official_http_transport_disables_environment_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")

    transport = OfficialHttpProbeTransport(minimum_delay_seconds=0)
    proxy_handlers = [
        handler
        for handler in transport._opener.handlers
        if isinstance(handler, urllib.request.ProxyHandler)
    ]

    assert proxy_handlers == []


def test_official_http_over_budget_response_retains_exchange_evidence() -> None:
    class Response:
        status = 200
        headers = {
            "Content-Length": "9",
            "Content-Type": "application/octet-stream",
        }

        def read(self, _size: int) -> bytes:
            return b"123456789"

    class Opener:
        def open(self, _request: object, *, timeout: float) -> Response:
            assert timeout == 3
            return Response()

    transport = OfficialHttpProbeTransport(
        minimum_delay_seconds=0,
        max_response_bytes=8,
    )
    transport._opener = Opener()
    request = ProviderProbeRequest(
        request_id="over-budget",
        provider="csindex",
        endpoint="attachment",
        method="GET",
        url="https://oss-ch.csindex.com.cn/20120102/a.xlsx",
        disposition="bounded_backfill",
        evidence_semantics="official_http_binary_response_envelope",
        expected_terminal_states=("positive",),
        required_checks=("response_within_byte_budget",),
    )

    observed = transport(request, 3)
    envelope = json.loads(observed.raw_payload)

    assert observed.terminal_state == "error"
    assert observed.error_code == "official_http_response_budget_exceeded"
    assert observed.transport_exchange_count == 1
    assert envelope["body_truncated"] is True
    assert envelope["observed_prefix_size_bytes"] == 9
    assert envelope["observed_prefix_sha256"] == hashlib.sha256(
        b"123456789"
    ).hexdigest()


def test_cninfo_discovery_normalizer_archives_announcement_identity(tmp_path: Path) -> None:
    _leaves, requests = build_cninfo_discovery_plan(["st_delist_201201"])
    list_request = requests[1]
    org_body = {"stockList": [{"code": "600000"}]}
    list_body = {
        "totalAnnouncement": 1,
        "announcements": [
            {
                "announcementId": "123",
                "secCode": "600000",
                "secName": "浦发银行",
                "orgId": "gssh0600000",
                "announcementTitle": "公告",
                "announcementTime": 1325376000000,
                "adjunctUrl": "finalpage/2012-01-01/123.PDF",
                "adjunctSize": 10,
                "announcementType": "x",
                "columnId": "y",
            }
        ],
        "hasMore": False,
    }
    terminal = {}
    for request, body in zip(requests, (org_body, list_body)):
        wrapper, receipt = _official_wrapper(body, request)
        path = tmp_path / receipt["raw_envelope_relative_path"]
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(wrapper), encoding="utf-8")
        terminal[request.request_id] = receipt

    artifacts = normalize_cninfo_discovery(tmp_path, requests, terminal)
    inventory = [
        json.loads(line)
        for line in (tmp_path / "normalized/announcement_inventory.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]

    assert inventory[0]["announcement_id"] == "123"
    assert inventory[0]["adjunct_url"].endswith("123.PDF")
    assert {row.role for row in artifacts} >= {
        "cninfo_announcement_inventory",
        "cninfo_page_coverage",
    }


@pytest.mark.parametrize(
    "envelope_overrides",
    (
        {"schema_version": "official_http_probe_envelope_v0"},
        {"method": "GET"},
        {"method": "post"},
        {"url": "https://www.cninfo.com.cn/wrong"},
        {"status_code": 201},
        {"redirect_followed": True},
        {"body_sha256": "0" * 64},
    ),
)
def test_cninfo_page_replay_rejects_unbound_official_http_envelope(
    tmp_path: Path,
    envelope_overrides: dict[str, object],
) -> None:
    _leaves, requests = build_cninfo_discovery_plan(["st_delist_201201"])
    terminal = {}
    for request, body in zip(
        requests,
        (
            {"stockList": [{"code": "600000"}]},
            {"totalAnnouncement": 0, "announcements": None, "hasMore": False},
        ),
    ):
        overrides = (
            envelope_overrides
            if request.metadata.get("case") == "cninfo_list"
            else None
        )
        wrapper, receipt = _official_wrapper(
            body,
            request,
            envelope_overrides=overrides,
        )
        path = tmp_path / receipt["raw_envelope_relative_path"]
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(wrapper), encoding="utf-8")
        terminal[request.request_id] = receipt

    with pytest.raises(ValueError, match="cninfo_official_http_envelope_invalid"):
        normalize_cninfo_discovery(tmp_path, requests, terminal)


def test_csindex_discovery_normalizer_detects_canonical_list_identity(tmp_path: Path) -> None:
    _leaves, requests = build_csindex_discovery_plan(["index_rebalance_201201"])
    filter_body = {
        "success": True,
        "code": "200",
        "data": {
            "classlist": [],
            "indexlist": [],
            "related_topics": [
                {
                    "filterKey": "index_rebalance",
                    "filterName": "指数调样",
                    "filterNameEn": "Index Rebalance",
                }
            ],
            "typelist": [],
        },
    }
    list_body = {
        "data": [
            {
                "id": 42,
                "title": "指数调样",
                "theme": "指数调样",
                "publishDate": "2012-01-02",
                "noticeType": "announcement",
                "fileUrl": None,
                "fileName": None,
            }
        ],
        "total": 1,
        "currentPage": 1,
        "pageSize": 1000,
        "success": True,
        "code": "200",
    }
    terminal = {}
    for request, body in zip(requests, (filter_body, list_body)):
        wrapper, receipt = _official_wrapper(body, request)
        path = tmp_path / receipt["raw_envelope_relative_path"]
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(wrapper), encoding="utf-8")
        terminal[request.request_id] = receipt

    artifacts = normalize_csindex_discovery(tmp_path, requests, terminal)
    inventory = json.loads(
        (tmp_path / "normalized/announcement_inventory.jsonl")
        .read_text(encoding="utf-8")
        .strip()
    )

    assert inventory["announcement_id"] == "42"
    assert inventory["publish_date"] == "2012-01-02"
    assert canonical_hash(inventory)
    assert {row.role for row in artifacts} >= {
        "csindex_announcement_inventory",
        "csindex_page_coverage",
    }


@pytest.mark.parametrize(
    "envelope_overrides",
    (
        {"schema_version": "official_http_probe_envelope_v0"},
        {"method": "GET"},
        {"method": "post"},
        {"url": "https://www.csindex.com.cn/wrong"},
        {"status_code": 201},
        {"redirect_followed": True},
        {"body_sha256": "0" * 64},
    ),
)
def test_csindex_page_replay_rejects_unbound_official_http_envelope(
    tmp_path: Path,
    envelope_overrides: dict[str, object],
) -> None:
    _leaves, requests = build_csindex_discovery_plan(["index_rebalance_201201"])
    terminal = {}
    for request, body in zip(
        requests,
        (
            {"data": [{"key": "index_rebalance"}]},
            {
                "data": [],
                "total": 0,
                "currentPage": 1,
                "pageSize": 0,
                "success": True,
                "code": "200",
            },
        ),
    ):
        overrides = (
            envelope_overrides
            if request.metadata.get("case") == "csindex_list"
            else None
        )
        wrapper, receipt = _official_wrapper(
            body,
            request,
            envelope_overrides=overrides,
        )
        path = tmp_path / receipt["raw_envelope_relative_path"]
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(wrapper), encoding="utf-8")
        terminal[request.request_id] = receipt

    with pytest.raises(ValueError, match="csindex_official_http_envelope_invalid"):
        normalize_csindex_discovery(tmp_path, requests, terminal)


@pytest.mark.parametrize(
    "status_fields",
    (
        {"success": False, "code": "200"},
        {"success": True, "code": "500"},
        {"code": "200"},
    ),
)
def test_csindex_detail_replay_rejects_non_success_provider_status(
    tmp_path: Path,
    status_fields: dict[str, object],
) -> None:
    source_row = {
        "announcement_id": "42",
        "title": "指数调样",
        "theme": "指数调样",
        "publish_date": "2012-01-02",
        "notice_type": "announcement",
        "file_url": None,
        "file_name": None,
        "source_leaf_id": "index_rebalance_201201",
        "source_request_id": "csindex_index_rebalance_201201_page_001",
        "source_payload_sha256": "4" * 64,
    }
    base_request = csindex_backfill._csindex_detail_request(source_row)
    details_ancestry = _attachment_source_ancestry()
    inventory_ancestry = details_ancestry["upstream_ancestry"][0]
    derivation = {
        "capture_content_hash": inventory_ancestry["direct_sources"][0][
            "source_content_hash"
        ],
        "normalized_replay_root": "5" * 64,
        "resolved_population_root": canonical_hash([source_row]),
        "request_semantics_root": canonical_hash([base_request.semantic()]),
        "implementation_root": csindex_implementation_root(),
    }
    input_root = canonical_hash(
        {**derivation, "source_ancestry": inventory_ancestry}
    )
    binding = csindex_backfill._csindex_source_binding(
        phase="csindex-details",
        capture_profile=csindex_backfill.CSINDEX_FULL_PROFILE,
        input_capture_content_hash=input_root,
        source_ancestry=inventory_ancestry,
        derivation=derivation,
    )
    request = csindex_backfill._with_csindex_source_evidence(
        base_request, inventory_ancestry, binding
    )
    body = dict(status_fields) | {
        "data": {
            "id": "42",
            "publishDate": "2012-01-02",
            "title": "指数调样",
            "content": "沪深300",
        }
    }
    wrapper, receipt = _official_wrapper(body, request)
    raw_path = tmp_path / receipt["raw_envelope_relative_path"]
    raw_path.parent.mkdir(parents=True)
    raw_path.write_text(json.dumps(wrapper), encoding="utf-8")

    with pytest.raises(ValueError, match="csindex_detail_provider_status_invalid"):
        csindex_backfill.normalize_csindex_details(
            tmp_path,
            [request],
            {request.request_id: receipt},
        )


def test_cninfo_document_formats_reject_html_block_pages_and_js_masquerade() -> None:
    blocked = b"<html><title>Access Denied</title><body>request blocked</body></html>"

    assert _document_block_reason(blocked) == "official_archive_html_block_page"
    assert _document_format(blocked, adjunct_url="finalpage/a.js") is None
    assert _document_format(b"window.data = {};", adjunct_url="finalpage/a.js") == "javascript"
    assert _document_format(b"%PDF-1.7\n", adjunct_url="finalpage/a.PDF ") == "pdf"


def test_cninfo_document_evidence_checks_size_headers_and_structure() -> None:
    html = (
        b'<!DOCTYPE html><html><body><div class="zbt">2012-01-01</div>'
        b'<div class="zw"><pre>announcement</pre></div></body></html>'
    )
    javascript = (
        b'var affiches=[{"webTxtID":"123","Time":"2012-01-01 09:00:00"}];'
    )
    pdf = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\nstartxref\n9\n%%EOF\n"

    assert _content_length_matches(str(len(html)), len(html)) is True
    assert _content_length_matches(str(len(html) + 1), len(html)) is False
    assert _content_type_compatible("html", "text/html; charset=gb2312") is True
    assert _content_type_compatible("pdf", "text/html") is False
    assert _adjunct_size_reasonable(1, len(html)) is True
    assert _adjunct_size_reasonable(1000, len(html)) is False
    assert _document_structure_valid(
        html,
        document_format="html",
        announcement_id="123",
        announcement_time=1325347200000,
    ) is True
    assert _document_structure_valid(
        javascript,
        document_format="javascript",
        announcement_id="123",
        announcement_time=1325347200000,
    ) is True
    assert _document_structure_valid(
        pdf,
        document_format="pdf",
        announcement_id="123",
        announcement_time=1325347200000,
    ) is True
    assert _document_structure_valid(
        b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n",
        document_format="pdf",
        announcement_id="123",
        announcement_time=1325347200000,
    ) is False


def test_cninfo_document_transport_and_normalizer_bind_envelope_and_weak_ancestry(
    tmp_path: Path,
) -> None:
    body = b"%PDF-1.7\n" + b"x" * 64 + b"\nstartxref\n0\n%%EOF\n"
    request = ProviderProbeRequest(
        request_id="cninfo_document_42",
        provider="cninfo",
        endpoint="announcement_document",
        method="GET",
        url="https://static.cninfo.com.cn/finalpage/2012-01-02/42.PDF",
        disposition="bounded_backfill",
        evidence_semantics="official_http_response_envelope",
        expected_terminal_states=("positive",),
        required_checks=(
            "http_envelope_schema_exact",
            "request_method_bound",
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
            "announcement_id": "42",
            "announcement_time": 1325462400000,
            "adjunct_url": "finalpage/2012-01-02/42.PDF",
            "adjunct_size_kb": 1,
            "source_ancestry": _cninfo_source_ancestry(),
        },
    )
    source_ancestry = request.metadata["source_ancestry"]
    input_root, source_binding = _cninfo_document_binding(source_ancestry)
    request = replace(
        request,
        metadata=dict(request.metadata) | {"source_binding": source_binding},
    )
    run_root, _identity = _write_cninfo_activity_context(
        tmp_path,
        [request],
        phase="cninfo-documents",
        input_capture_hash=input_root,
        source_binding=source_binding,
    )
    official_payload = {
        "schema_version": "official_http_probe_envelope_v1",
        "url": request.url,
        "method": "GET",
        "status_code": 200,
        "response_headers": {
            "Content-Length": str(len(body)),
            "Content-Type": "application/pdf",
        },
        "body_base64": base64.b64encode(body).decode(),
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "redirect_followed": False,
    }
    official = json.dumps(official_payload, sort_keys=True).encode()
    base_observation = ProviderProbeObservation(
        terminal_state="positive",
        raw_payload=official,
        row_count=1,
        status_code=200,
        transport_exchange_count=1,
    )
    transport = CNINFODocumentTransport(minimum_delay_seconds=0)
    transport._transport = lambda _request, _timeout: base_observation

    observed = transport(request, 3)
    assert observed.terminal_state == "positive"
    assert all(observed.checks.values())

    redirected_payload = dict(official_payload) | {"redirect_followed": True}
    transport._transport = lambda _request, _timeout: ProviderProbeObservation(
        terminal_state="positive",
        raw_payload=json.dumps(redirected_payload, sort_keys=True).encode(),
        row_count=1,
        status_code=200,
        transport_exchange_count=1,
    )
    redirected = transport(request, 3)
    assert redirected.terminal_state == "error"
    assert redirected.checks["redirect_not_followed"] is False

    wrapper = {
        "schema_version": "free_provider_backfill_raw_envelope_v1",
        "request_id": request.request_id,
        "raw_payload_base64": base64.b64encode(official).decode(),
        "raw_payload_sha256": hashlib.sha256(official).hexdigest(),
    }
    relative = f"raw_envelopes/{request.request_id}.json"
    path = run_root / relative
    path.parent.mkdir()
    path.write_text(json.dumps(wrapper), encoding="utf-8")
    normalize_cninfo_documents(
        run_root,
        [request],
        {
            request.request_id: {
                "raw_envelope_relative_path": relative,
                "status_code": 200,
            }
        },
    )
    manifest = json.loads(
        (run_root / "normalized/normalized_manifest.json").read_text()
    )

    assert manifest["schema_version"] == "cninfo_document_normalization_v2"
    assert manifest["source_ancestry"]["weak_source_ancestry"] is True
    assert "weak_source_acquisition_ancestry" in manifest["blockers"]

    inconsistent_status = dict(official_payload) | {"status_code": 201}
    inconsistent_raw = json.dumps(inconsistent_status, sort_keys=True).encode()
    wrapper["raw_payload_base64"] = base64.b64encode(inconsistent_raw).decode()
    wrapper["raw_payload_sha256"] = hashlib.sha256(inconsistent_raw).hexdigest()
    path.write_text(json.dumps(wrapper), encoding="utf-8")
    with pytest.raises(ValueError, match="cninfo_official_http_envelope_invalid"):
        normalize_cninfo_documents(
            run_root,
            [request],
            {
                request.request_id: {
                    "raw_envelope_relative_path": relative,
                    "status_code": 200,
                }
            },
        )


def test_cninfo_archive_adapter_retries_only_list_endpoint_transient_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NotFoundTransport:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def __call__(
            self,
            _request: ProviderProbeRequest,
            _timeout_seconds: float,
        ) -> ProviderProbeObservation:
            return ProviderProbeObservation(
                terminal_state="error",
                raw_payload=b"official-http-404-fixture",
                row_count=None,
                status_code=404,
                error_code="http_status:404",
                diagnostics={},
                checks={"http_success": False},
                transport_exchange_count=1,
            )

    monkeypatch.setattr(
        cninfo_backfill,
        "OfficialHttpProbeTransport",
        NotFoundTransport,
    )
    _population, requests = build_cninfo_discovery_plan(
        ["corporate_actions_201404"]
    )
    list_request = next(
        request
        for request in requests
        if request.metadata.get("case") == "cninfo_list"
    )
    transport = cninfo_backfill.CNINFOArchiveTransport(
        minimum_delay_seconds=0
    )

    retryable = transport(list_request, 3.0)
    document_request = ProviderProbeRequest(
        **{
            **list_request.__dict__,
            "metadata": dict(list_request.metadata) | {"case": "cninfo_pdf"},
        }
    )
    nonretryable = transport(document_request, 3.0)

    assert retryable.error_code == (
        "transport_exception:RuntimeError:cninfo_list_transient_http_404"
    )
    assert retryable.diagnostics["transient_error_normalization"] == {
        "adapter": "CNINFOArchiveTransport",
        "original_error_code": "http_status:404",
        "normalized_error_code": (
            "transport_exception:RuntimeError:"
            "cninfo_list_transient_http_404"
        ),
        "retry_scope": "cninfo_list_only",
    }
    assert capture_backfill._retryable(
        retryable.error_code,
        provider="cninfo",
    ) is True
    assert nonretryable.error_code == "http_status:404"


def test_cninfo_new_document_normalization_rejects_missing_source_ancestry(
    tmp_path: Path,
) -> None:
    body = b"%PDF-1.7\n" + b"x" * 64 + b"\nstartxref\n9\n%%EOF\n"
    request = ProviderProbeRequest(
        request_id="cninfo_document_new_without_ancestry",
        provider="cninfo",
        endpoint="announcement_document",
        method="GET",
        url="https://static.cninfo.com.cn/finalpage/2012-01-02/new.PDF",
        disposition="bounded_backfill",
        evidence_semantics="official_http_response_envelope",
        expected_terminal_states=("positive",),
        metadata={
            "case": "cninfo_document",
            "announcement_id": "new",
            "announcement_time": 1325462400000,
            "adjunct_url": "finalpage/2012-01-02/new.PDF",
            "adjunct_size_kb": 1,
        },
    )
    fixture_ancestry = _cninfo_source_ancestry()
    fixture_input_root, fixture_binding = _cninfo_document_binding(
        fixture_ancestry
    )
    run_root, _identity = _write_cninfo_activity_context(
        tmp_path,
        [request],
        phase="cninfo-documents",
        input_capture_hash=fixture_input_root,
        source_binding=fixture_binding,
    )
    raw = json.dumps(
        {
            "schema_version": "official_http_probe_envelope_v1",
            "url": request.url,
            "method": "GET",
            "status_code": 200,
            "response_headers": {
                "Content-Length": str(len(body)),
                "Content-Type": "application/pdf",
            },
            "body_base64": base64.b64encode(body).decode(),
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "redirect_followed": False,
        },
        sort_keys=True,
    ).encode()
    wrapper = {
        "schema_version": "free_provider_backfill_raw_envelope_v1",
        "request_id": request.request_id,
        "raw_payload_base64": base64.b64encode(raw).decode(),
        "raw_payload_sha256": hashlib.sha256(raw).hexdigest(),
    }
    relative = f"raw_envelopes/{request.request_id}.json"
    path = run_root / relative
    path.parent.mkdir()
    path.write_text(json.dumps(wrapper), encoding="utf-8")

    with pytest.raises(ValueError, match="cninfo_document_source_ancestry_missing"):
        normalize_cninfo_documents(
            run_root,
            [request],
            {
                request.request_id: {
                    "raw_envelope_relative_path": relative,
                    "status_code": 200,
                }
            },
        )


def test_cninfo_exact_legacy_2011_plan_keeps_v1_normalized_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert cninfo_backfill.CNINFO_LEGACY_2011_DOCUMENT_REQUEST_PLAN_HASH == (
        "b666f4b60a308e74f60747e560e3c28725a0e6f17b2d2d83d572033c03861172"
    )
    assert cninfo_backfill.CNINFO_LEGACY_2011_DOCUMENT_ACTIVITY_ID.startswith(
        "c8cf6651"
    )
    assert cninfo_backfill.CNINFO_LEGACY_2011_DOCUMENT_CONTRACT_ID.startswith(
        "f958eab0"
    )
    assert (
        cninfo_backfill.CNINFO_LEGACY_2011_DOCUMENT_INPUT_CAPTURE_HASH
    ).startswith("69514a28")
    assert (
        cninfo_backfill.CNINFO_LEGACY_2011_DOCUMENT_IMPLEMENTATION_ROOT
    ).startswith("85c0ff07")
    body = b"%PDF-1.7\n" + b"x" * 64 + b"\nstartxref\n9\n%%EOF\n"
    request = ProviderProbeRequest(
        request_id="cninfo_document_legacy_fixture",
        provider="cninfo",
        endpoint="announcement_document",
        method="GET",
        url="https://static.cninfo.com.cn/finalpage/2011-01-04/legacy.PDF",
        disposition="bounded_backfill",
        evidence_semantics="official_http_response_envelope",
        expected_terminal_states=("positive",),
        metadata={
            "case": "cninfo_document",
            "announcement_id": "legacy",
            "announcement_time": 1294093800000,
            "adjunct_url": "finalpage/2011-01-04/legacy.PDF",
            "adjunct_size_kb": 1,
        },
    )
    fixture_ancestry = _cninfo_source_ancestry()
    fixture_input_root, fixture_binding = _cninfo_document_binding(
        fixture_ancestry
    )
    run_root, identity = _write_cninfo_activity_context(
        tmp_path,
        [request],
        phase="cninfo-documents",
        input_capture_hash=fixture_input_root,
        source_binding=fixture_binding,
        legacy=True,
    )
    for name, value in (
        ("CNINFO_LEGACY_2011_DOCUMENT_ACTIVITY_ID", identity["activity_id"]),
        ("CNINFO_LEGACY_2011_DOCUMENT_CONTRACT_ID", identity["contract_id"]),
        (
            "CNINFO_LEGACY_2011_DOCUMENT_REQUEST_PLAN_HASH",
            identity["request_plan_hash"],
        ),
        (
            "CNINFO_LEGACY_2011_DOCUMENT_INPUT_CAPTURE_HASH",
            identity["input_capture_content_hash"],
        ),
        (
            "CNINFO_LEGACY_2011_DOCUMENT_IMPLEMENTATION_ROOT",
            identity["implementation_root"],
        ),
    ):
        monkeypatch.setattr(cninfo_backfill, name, value)
    raw = json.dumps(
        {
            "schema_version": "official_http_probe_envelope_v1",
            "url": request.url,
            "method": "GET",
            "status_code": 200,
            "response_headers": {
                "Content-Length": str(len(body)),
                "Content-Type": "application/pdf",
            },
            "body_base64": base64.b64encode(body).decode(),
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "redirect_followed": False,
        },
        sort_keys=True,
    ).encode()
    wrapper = {
        "schema_version": "free_provider_backfill_raw_envelope_v1",
        "request_id": request.request_id,
        "raw_payload_base64": base64.b64encode(raw).decode(),
        "raw_payload_sha256": hashlib.sha256(raw).hexdigest(),
    }
    relative = f"raw_envelopes/{request.request_id}.json"
    path = run_root / relative
    path.parent.mkdir()
    path.write_text(json.dumps(wrapper), encoding="utf-8")

    normalize_cninfo_documents(
        run_root,
        [request],
        {
            request.request_id: {
                "raw_envelope_relative_path": relative,
                "status_code": 200,
            }
        },
    )

    expected_row = {
        "announcement_id": "legacy",
        "announcement_time": 1294093800000,
        "adjunct_url": "finalpage/2011-01-04/legacy.PDF",
        "document_format": "pdf",
        "document_sha256": hashlib.sha256(body).hexdigest(),
        "document_size_bytes": len(body),
        "declared_adjunct_size_kb": 1,
        "source_request_id": request.request_id,
        "source_payload_sha256": wrapper["raw_payload_sha256"],
        "content_length": str(len(body)),
        "content_type": "application/pdf",
    }
    expected_index = (
        json.dumps(
            expected_row,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        + b"\n"
    )
    expected_manifest = {
        "schema_version": "cninfo_document_normalization_v1",
        "document_count": 1,
        "document_index_sha256": hashlib.sha256(expected_index).hexdigest(),
        "documents_extracted": False,
        "raw_capture_contains_exact_document_bytes": True,
        "pit_field_parsing_complete": False,
        "blockers": ["corporate_action_pdf_field_parser_not_run"],
    }
    expected_manifest["content_hash"] = canonical_hash(expected_manifest)
    expected_manifest_bytes = (
        json.dumps(
            expected_manifest,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()

    assert (run_root / "normalized/document_index.jsonl").read_bytes() == expected_index
    assert (
        run_root / "normalized/normalized_manifest.json"
    ).read_bytes() == expected_manifest_bytes
    sealed_context = cninfo_backfill._cninfo_activity_context(
        run_root,
        [request],
        expected_phase="cninfo-documents",
        require_activity_root=True,
    )
    qualification = cninfo_backfill._cninfo_governance_qualification(
        context=sealed_context,
        phase="cninfo-documents",
        normalized_manifest=expected_manifest,
        publication_signature_verified=True,
    )
    assert qualification["source_lineage_complete"] is False
    assert qualification["quarantined"] is True
    assert qualification["governed_evidence_eligible"] is False
    assert "legacy_2011_document_source_ancestry_incomplete" in qualification[
        "blockers"
    ]

    clone_root, clone_identity = _write_cninfo_activity_context(
        tmp_path / "clone",
        [request],
        phase="cninfo-documents",
        input_capture_hash=fixture_input_root,
        source_binding=fixture_binding,
        legacy=True,
    )
    clone_path = clone_root / relative
    clone_path.parent.mkdir()
    clone_path.write_text(json.dumps(wrapper), encoding="utf-8")
    assert clone_identity["request_plan_hash"] == identity["request_plan_hash"]
    assert clone_identity["contract_id"] != identity["contract_id"]
    with pytest.raises(ValueError, match="source_ancestry_missing"):
        normalize_cninfo_documents(
            clone_root,
            [request],
            {
                request.request_id: {
                    "raw_envelope_relative_path": relative,
                    "status_code": 200,
                }
            },
        )


def test_cninfo_document_url_is_confined_to_official_static_host() -> None:
    assert _cninfo_document_url("finalpage/2012-01-01/a.PDF") == (
        "https://static.cninfo.com.cn/finalpage/2012-01-01/a.PDF"
    )
    with pytest.raises(ValueError, match="path_invalid"):
        _cninfo_document_url("../secrets")
    for escaped in (
        "%2e%2e/secrets.pdf",
        "%252e%252e/secrets.pdf",
        "finalpage/a%2Fb.pdf",
        "finalpage/a%5Cb.pdf",
        "finalpage/a%00b.pdf",
        "finalpage\\a.pdf",
    ):
        with pytest.raises(ValueError, match="path_invalid"):
            _cninfo_document_url(escaped)
    with pytest.raises(ValueError, match="not_relative"):
        _cninfo_document_url("https://example.com/a.pdf")
    with pytest.raises(ValueError, match="not_relative"):
        _cninfo_document_url("/finalpage/a.pdf")


def test_csindex_publication_dates_require_exact_valid_iso_dates() -> None:
    assert _strict_iso_date("2012-02-29").isoformat() == "2012-02-29"
    assert _strict_iso_date("2012-02-30") is None
    assert _strict_iso_date("2012-2-09") is None
    assert _strict_iso_date("2012-02-09T00:00:00") is None


def test_csindex_attachment_url_policy_confines_hosts_and_paths() -> None:
    assert _canonical_csindex_attachment_url(
        "https://oss-ch.csindex.com.cn/static/files/a%20b.XLSX"
    ) == "https://oss-ch.csindex.com.cn/static/files/a%20b.XLSX"
    assert _canonical_csindex_attachment_url("file/成分股.xls") == (
        "https://www.csindex.com.cn/file/%E6%88%90%E5%88%86%E8%82%A1.xls"
    )
    invalid = (
        "file/../secret.xls",
        "file/%2e%2e/secret.xls",
        "file/%252e%252e/secret.xls",
        "file/a%2fb.xls",
        "file/a.xls?download=1",
        "file/a.xls#section",
        "https://oss-ch.csindex.com.cn/a.xls?x=1",
        "https://www.csindex.com.cn/file/a.xls",
        "http://oss-ch.csindex.com.cn/a.xls",
        "http://www.csindex.com.cnhttps://oss-ch.csindex.com.cn/a.xls",
        "https://oss-ch.csindex.com.cn/a.exe",
    )
    for value in invalid:
        with pytest.raises(ValueError, match="csindex_attachment"):
            _canonical_csindex_attachment_url(value)


def test_csindex_attachment_population_deduplicates_filters_and_prioritizes_oss() -> None:
    oss_url = "https://oss-ch.csindex.com.cn/20120102/constituents.xlsx"
    details = [
        {
            "announcement_id": "10",
            "publish_date": "2012-01-02",
            "source_request_id": "detail_10",
            "source_payload_sha256": "a" * 64,
            "content_html": (
                f'<a href="{oss_url}">a</a><img src="{oss_url}">'
                '<a href="file/20120102/local.xls">b</a>'
                '<a href="https://example.com/other.xls">external</a>'
                '<a href="http://www.csindex.com.cnhttps://oss-ch.csindex.com.cn/b.xls">bad</a>'
            ),
        },
        {
            "announcement_id": "11",
            "publish_date": "2012-01-03",
            "source_request_id": "detail_11",
            "source_payload_sha256": "b" * 64,
            "content_html": f'<img src="{oss_url}">',
        },
    ]

    population = _attachment_population_from_details(details)
    oss_only = _attachment_population_from_details(
        details, include_hosts=("oss-ch.csindex.com.cn",)
    )
    accepted = [row for row in population if row["attachment_url"] is not None]
    accepted_oss = [row for row in oss_only if row["attachment_url"] is not None]
    rejected = [row for row in population if row["attachment_url"] is None]

    assert [row["host"] for row in accepted] == [
        "oss-ch.csindex.com.cn",
        "www.csindex.com.cn",
    ]
    assert len(accepted_oss) == 1
    assert accepted_oss[0]["attachment_url"] == oss_url
    assert len(rejected) == 2
    assert {row["reference_disposition"] for row in rejected} == {
        "blocked_rejected_reference"
    }
    assert [
        source["announcement_id"]
        for source in accepted_oss[0]["source_announcements"]
    ] == ["10", "11"]
    assert accepted_oss[0]["source_announcements"][0]["reference_attributes"] == [
        "href",
        "src",
    ]
    assert accepted_oss[0]["temporal_blocker"].startswith(
        "current_attachment_retrieval"
    )


def test_csindex_attachment_plan_blocks_unproven_or_out_of_scope_path_dates() -> None:
    details = [
        {
            "announcement_id": "42",
            "publish_date": "2019-12-09",
            "source_request_id": "detail_42",
            "source_payload_sha256": "a" * 64,
            "content_html": (
                '<a href="https://oss-ch.csindex.com.cn/20191201/a.xlsx">ok</a>'
                '<a href="https://oss-ch.csindex.com.cn/20191220/b.xlsx">migrated</a>'
                '<a href="https://oss-ch.csindex.com.cn/static/c.xlsx">unknown</a>'
                '<a href="https://oss-ch.csindex.com.cn/20250513/d.xlsx">future</a>'
            ),
        }
    ]

    population = _attachment_population_from_details(details)
    requests = _fixture_attachment_requests(population)
    by_name = {
        row["attachment_url"].rsplit("/", 1)[-1]: row for row in population
    }

    assert [request.url.rsplit("/", 1)[-1] for request in requests] == [
        "a.xlsx",
        "b.xlsx",
    ]
    assert by_name["a.xlsx"]["reference_disposition"] == "capture_eligible"
    assert by_name["a.xlsx"]["source_announcements"][0]["edge_disposition"] == (
        "historical_edge_candidate"
    )
    assert by_name["b.xlsx"]["source_announcements"][0]["edge_disposition"] == (
        "value_only_migrated_reference"
    )
    assert by_name["c.xlsx"]["reference_disposition"] == (
        "blocked_attachment_path_date_unproven"
    )
    assert by_name["d.xlsx"]["reference_disposition"] == (
        "blocked_out_of_scope_reference"
    )
    assert requests[0].metadata["blocked_reference_count"] == 2
    assert len(requests[0].metadata["blocked_references"]) == 2
    assert "blocked_references" not in requests[1].metadata


def test_csindex_legacy_cons_repair_selects_only_two_exact_blocked_urls() -> None:
    rows = csindex_backfill.CSINDEX_LEGACY_CONS_REPAIR_ROWS
    details = []
    for spec in rows:
        for announcement_id in spec["announcement_ids"]:
            details.append(
                {
                    "announcement_id": announcement_id,
                    "publish_date": spec["announcement_publish_date"],
                    "source_request_id": f"detail_{announcement_id}",
                    "source_payload_sha256": str(announcement_id).zfill(64),
                    "contains_csi300": (
                        announcement_id == spec["csi300_announcement_id"]
                    ),
                    "content_html": f'<a href="{spec["attachment_url"]}">xls</a>',
                }
            )
    details[0]["content_html"] += (
        '<a href="https://oss-ch.csindex.com.cn/static/generic.xls">other</a>'
    )

    population = csindex_backfill._legacy_cons_repair_population(details)
    eligible = [
        row
        for row in population
        if row["reference_disposition"] == "capture_eligible"
    ]
    generic = next(
        row for row in population if row.get("attachment_url", "").endswith("generic.xls")
    )

    assert [row["attachment_url"] for row in eligible] == [
        spec["attachment_url"] for spec in rows
    ]
    assert [row["path_dates"] for row in eligible] == [
        [str(spec["announcement_publish_date"]).replace("-", "")]
        for spec in rows
    ]
    assert generic["reference_disposition"] == (
        "blocked_outside_legacy_cons_exact_repair_profile"
    )


def test_csindex_legacy_cons_repair_rejects_weak_details_ancestry() -> None:
    population = _legacy_cons_repair_fixture_population()
    ancestry = _attachment_source_ancestry(weak=True)
    binding = _legacy_cons_repair_source_binding(ancestry, population)

    with pytest.raises(ValueError, match="weak_source_blocked"):
        _attachment_requests(
            population,
            source_ancestry=ancestry,
            source_binding=binding,
            capture_profile=csindex_backfill.CSINDEX_LEGACY_CONS_REPAIR_PROFILE,
        )


@pytest.mark.parametrize(
    "mutation",
    ("missing", "extra", "date", "host", "extension", "source"),
)
def test_csindex_legacy_cons_repair_rejects_any_exact_profile_drift(
    mutation: str,
) -> None:
    population = copy.deepcopy(_legacy_cons_repair_fixture_population())
    eligible = [
        row
        for row in population
        if row["reference_disposition"] == "capture_eligible"
    ]
    if mutation == "missing":
        population.remove(eligible[0])
    elif mutation == "extra":
        extra = copy.deepcopy(eligible[0])
        extra["attachment_url"] = (
            "https://oss-ch.csindex.com.cn/static/html/csindex/public/"
            "sseportal/upload/files/upload/20170101cons.xls"
        )
        population.append(extra)
    elif mutation == "date":
        eligible[0]["path_dates"] = ["20151201"]
    elif mutation == "host":
        eligible[0]["host"] = "www.csindex.com.cn"
    elif mutation == "extension":
        eligible[0]["extension"] = "xlsx"
    else:
        eligible[0]["source_announcements"] = eligible[0][
            "source_announcements"
        ][:-1]

    with pytest.raises(ValueError, match="exact_population_invalid"):
        csindex_backfill._validate_legacy_cons_repair_population(population)


def test_csindex_governance_recursively_validates_real_chain_and_missing_upstream(
    tmp_path: Path,
    approved_csindex_signer: EphemeralReceiptSigner,
    capsys: pytest.CaptureFixture[str],
) -> None:
    provider_root = tmp_path / "csindex"
    rows_by_leaf = _csindex_repair_announcement_rows()
    discovery_manifest = _publish_csindex_discovery_capture(
        provider_root / "discovery",
        signer=approved_csindex_signer,
        rows_by_leaf=rows_by_leaf,
    )
    inventory_population, inventory_requests, inventory_input = (
        build_csindex_inventory_plan(discovery_manifest)
    )
    inventory_manifest = _publish_csindex_json_capture(
        provider_root / "inventory",
        phase="csindex-inventory",
        population=inventory_population,
        requests=inventory_requests,
        input_capture_hash=inventory_input,
        normalizer=csindex_backfill.normalize_csindex_inventory,
        signer=approved_csindex_signer,
        rows_by_leaf=rows_by_leaf,
    )
    detail_population, detail_requests, detail_input = (
        csindex_backfill.build_csindex_detail_plan(inventory_manifest)
    )
    details_manifest = _publish_csindex_json_capture(
        provider_root / "details",
        phase="csindex-details",
        population=detail_population,
        requests=detail_requests,
        input_capture_hash=detail_input,
        normalizer=csindex_backfill.normalize_csindex_details,
        signer=approved_csindex_signer,
        rows_by_leaf=rows_by_leaf,
    )
    repair_population, repair_requests, repair_input = (
        csindex_backfill.build_csindex_legacy_cons_repair_plan(details_manifest)
    )
    policy = csindex_backfill.CSINDEX_PHASE_RUNTIME_POLICY[
        "csindex-legacy-cons-repair"
    ]
    repair_binding = repair_requests[0].metadata["source_binding"]
    contract = csindex_contract(
        phase="csindex-legacy-cons-repair",
        output_root=provider_root / "legacy_cons_repair",
        signer=approved_csindex_signer,
        population_root=canonical_hash(
            {
                "population": repair_population,
                "input_capture_content_hash": repair_input,
            }
        ),
        request_count=2,
        input_capture_hash=repair_input,
        delay=float(policy["minimum_delay_seconds"]),
        timeout=float(policy["timeout_seconds"]),
        retries=int(policy["max_retries"]),
        permission_context_id=csindex_backfill.DEFAULT_PERMISSION_CONTEXT,
        allowed_hosts=("oss-ch.csindex.com.cn",),
        capture_profile=csindex_backfill.CSINDEX_LEGACY_CONS_REPAIR_PROFILE,
        source_binding=repair_binding,
    )
    body = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"real-chain-xls" * 8

    def repair_transport(
        request: ProviderProbeRequest,
        _timeout: float,
    ) -> ProviderProbeObservation:
        raw = json.dumps(
            {
                "schema_version": "official_http_probe_envelope_v1",
                "url": request.url,
                "method": "GET",
                "status_code": 200,
                "response_headers": {
                    "Content-Length": str(len(body)),
                    "Content-Type": "application/vnd.ms-excel",
                },
                "body_base64": base64.b64encode(body).decode(),
                "body_sha256": hashlib.sha256(body).hexdigest(),
                "redirect_followed": False,
            },
            sort_keys=True,
        ).encode()
        return ProviderProbeObservation(
            terminal_state="positive",
            raw_payload=raw,
            row_count=1,
            status_code=200,
            checks={name: True for name in request.required_checks},
            transport_exchange_count=1,
        )

    with patch.object(capture_backfill.time, "sleep", return_value=None):
        published = run_free_provider_backfill(
            contract,
            repair_requests,
            transport=repair_transport,
            signer=approved_csindex_signer,
            normalizer=csindex_backfill.normalize_csindex_legacy_cons_repair,
            runtime_implementation_root=csindex_implementation_root(),
        )
    governed = csindex_backfill.validate_csindex_governance(
        published["manifest_path"]
    )

    assert governed["signature_integrity_verified"] is True
    assert governed["approved_capture_key_verified"] is True
    assert governed["csindex_downstream_eligible"] is True
    assert governed["pit_membership_authorized"] is False
    assert governed["historical_known_at_proven"] is False
    assert all(value is False for value in governed["safety"].values())
    assert csindex_backfill.main(
        [
            "--phase",
            "csindex-legacy-cons-repair",
            "--validate",
            str(published["manifest_path"]),
        ]
    ) == 0
    cli_payload = json.loads(capsys.readouterr().out)
    assert cli_payload["approved_capture_key_verified"] is True
    assert cli_payload["pit_membership_authorized"] is False

    discovery_generation = Path(discovery_manifest).parent
    hidden = discovery_generation.with_name(discovery_generation.name + ".missing")
    discovery_generation.rename(hidden)
    try:
        with pytest.raises(ValueError, match="upstream_generation_missing"):
            csindex_backfill.validate_csindex_governance(
                published["manifest_path"]
            )
    finally:
        hidden.rename(discovery_generation)


def test_csindex_attachment_magic_and_wire_metadata_are_extension_specific() -> None:
    workbook = io.BytesIO()
    with zipfile.ZipFile(workbook, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", "<workbook/>")
    document = io.BytesIO()
    with zipfile.ZipFile(document, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")

    assert _attachment_magic_valid(workbook.getvalue(), "xlsx") is True
    assert _attachment_magic_valid(workbook.getvalue(), "docx") is False
    assert _attachment_magic_valid(document.getvalue(), "docx") is True
    assert _attachment_magic_valid(b"%PDF-1.7\nstartxref\n1\n%%EOF\n", "pdf") is True
    assert _attachment_magic_valid(b"<html>Access Denied</html>", "txt") is False
    assert _attachment_magic_valid(b"a,b\n1,2\n", "csv") is True
    assert _attachment_magic_valid(b"\x89PNG\r\n\x1a\nrest", "png") is True
    assert _attachment_content_length_matches("8", 8) is True
    assert _attachment_content_length_matches(None, 8) is False
    assert _attachment_content_type_compatible(
        "xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ) is True
    assert _attachment_content_type_compatible("xlsx", "text/html") is False


def test_csindex_attachment_transport_and_normalizer_keep_binary_only_in_raw(
    tmp_path: Path,
) -> None:
    workbook = io.BytesIO()
    with zipfile.ZipFile(workbook, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", "<workbook/>")
    body = workbook.getvalue()
    population = [
        {
            "attachment_url": "https://oss-ch.csindex.com.cn/20120102/a.xlsx",
            "host": "oss-ch.csindex.com.cn",
            "extension": "xlsx",
            "path_dates": ["20120102"],
            "reference_disposition": "capture_eligible",
            "source_announcements": [
                {
                    "announcement_id": "42",
                    "announcement_publish_date": "2012-01-02",
                    "historical_known_at_proven": False,
                }
            ],
        },
        {
            "attachment_url": None,
            "raw_reference": "https://example.com/rebalance.xls",
            "host": None,
            "extension": None,
            "path_dates": [],
            "reference_disposition": "blocked_rejected_reference",
            "rejection_reason": "csindex_attachment_absolute_url_invalid",
            "source_announcements": [
                {
                    "announcement_id": "43",
                    "announcement_publish_date": "2012-01-03",
                    "edge_disposition": "blocked_rejected_reference",
                    "historical_known_at_proven": False,
                }
            ],
        },
        {
            "attachment_url": (
                "https://oss-ch.csindex.com.cn/20250513/future.xlsx"
            ),
            "host": "oss-ch.csindex.com.cn",
            "extension": "xlsx",
            "path_dates": ["20250513"],
            "reference_disposition": "blocked_out_of_scope_reference",
            "source_announcements": [
                {
                    "announcement_id": "44",
                    "announcement_publish_date": "2012-01-04",
                    "edge_disposition": "blocked_out_of_scope_reference",
                    "historical_known_at_proven": False,
                }
            ],
        },
    ]
    request = _fixture_attachment_requests(population)[0]
    official_payload = {
        "schema_version": "official_http_probe_envelope_v1",
        "url": request.url,
        "method": "GET",
        "status_code": 200,
        "response_headers": {
            "Content-Length": str(len(body)),
            "Content-Type": (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        },
        "body_base64": base64.b64encode(body).decode(),
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "redirect_followed": False,
    }
    official = json.dumps(official_payload, sort_keys=True).encode()
    base_observation = ProviderProbeObservation(
        terminal_state="positive",
        raw_payload=official,
        row_count=1,
        status_code=200,
        checks={"pdf_signature": False},
        transport_exchange_count=1,
    )
    transport = CSIndexAttachmentTransport(minimum_delay_seconds=0)
    transport._transport = lambda _request, _timeout: base_observation

    observed = transport(request, 3)

    assert observed.terminal_state == "positive"
    assert observed.error_code is None
    assert all(observed.checks.values())

    redirected_payload = dict(official_payload) | {"redirect_followed": True}
    redirected = ProviderProbeObservation(
        terminal_state="positive",
        raw_payload=json.dumps(redirected_payload, sort_keys=True).encode(),
        row_count=1,
        status_code=200,
        transport_exchange_count=1,
    )
    transport._transport = lambda _request, _timeout: redirected
    redirected_observation = transport(request, 3)
    assert redirected_observation.terminal_state == "error"
    assert redirected_observation.checks["http_envelope_decoded"] is False

    wrapper = {
        "schema_version": "free_provider_backfill_raw_envelope_v1",
        "request_id": request.request_id,
        "raw_payload_base64": base64.b64encode(official).decode(),
        "raw_payload_sha256": hashlib.sha256(official).hexdigest(),
        "raw_payload_size_bytes": len(official),
    }
    relative = f"raw_envelopes/{request.request_id}.json"
    wrapper_path = tmp_path / relative
    wrapper_path.parent.mkdir()
    wrapper_path.write_text(json.dumps(wrapper), encoding="utf-8")
    normalize_csindex_attachments(
        tmp_path,
        [request],
        {
            request.request_id: {
                "raw_envelope_relative_path": relative,
                "terminal_state": "positive",
                "status_code": 200,
            }
        },
    )
    index_row = json.loads(
        (tmp_path / "normalized/attachment_index.jsonl").read_text().strip()
    )

    assert index_row["attachment_sha256"] == hashlib.sha256(body).hexdigest()
    assert index_row["source_announcements"][0]["announcement_id"] == "42"
    assert index_row["historical_known_at"] is None
    assert index_row["historical_known_at_proven"] is False
    assert "body_base64" not in index_row


def test_csindex_attachment_signed_capture_validates_and_replays(
    tmp_path: Path,
    approved_csindex_signer: EphemeralReceiptSigner,
) -> None:
    workbook = io.BytesIO()
    with zipfile.ZipFile(workbook, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", "<workbook/>")
    body = workbook.getvalue()
    population = [
        {
            "attachment_url": (
                "https://oss-ch.csindex.com.cn/20120102/a.xlsx"
            ),
            "host": "oss-ch.csindex.com.cn",
            "extension": "xlsx",
            "path_dates": ["20120102"],
            "reference_disposition": "capture_eligible",
            "temporal_blocker": "fixture",
            "source_announcements": [
                {
                    "announcement_id": "42",
                    "announcement_publish_date": "2012-01-02",
                    "edge_disposition": "historical_edge_candidate",
                    "historical_known_at_proven": False,
                }
            ],
        },
        {
            "attachment_url": None,
            "raw_reference": "https://example.com/rebalance.xls",
            "host": None,
            "extension": None,
            "path_dates": [],
            "reference_disposition": "blocked_rejected_reference",
            "rejection_reason": "csindex_attachment_absolute_url_invalid",
            "source_announcements": [
                {
                    "announcement_id": "43",
                    "announcement_publish_date": "2012-01-03",
                    "edge_disposition": "blocked_rejected_reference",
                    "historical_known_at_proven": False,
                }
            ],
        },
        {
            "attachment_url": (
                "https://oss-ch.csindex.com.cn/20250513/future.xlsx"
            ),
            "host": "oss-ch.csindex.com.cn",
            "extension": "xlsx",
            "path_dates": ["20250513"],
            "reference_disposition": "blocked_out_of_scope_reference",
            "source_announcements": [
                {
                    "announcement_id": "44",
                    "announcement_publish_date": "2012-01-04",
                    "edge_disposition": "blocked_out_of_scope_reference",
                    "historical_known_at_proven": False,
                }
            ],
        },
    ]
    requests = _fixture_attachment_requests(population)
    request = requests[0]
    official_payload = {
        "schema_version": "official_http_probe_envelope_v1",
        "url": request.url,
        "method": "GET",
        "status_code": 200,
        "response_headers": {
            "Content-Length": str(len(body)),
            "Content-Type": (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        },
        "body_base64": base64.b64encode(body).decode(),
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "redirect_followed": False,
    }
    base_observation = ProviderProbeObservation(
        terminal_state="positive",
        raw_payload=json.dumps(official_payload, sort_keys=True).encode(),
        row_count=1,
        status_code=200,
        transport_exchange_count=1,
    )
    transport = CSIndexAttachmentTransport(minimum_delay_seconds=0)
    transport._transport = lambda _request, _timeout: base_observation
    signer = approved_csindex_signer
    policy = csindex_backfill.CSINDEX_PHASE_RUNTIME_POLICY["csindex-attachments"]
    source_binding = request.metadata["source_binding"]
    contract = csindex_contract(
        phase="csindex-attachments",
        output_root=tmp_path / "capture",
        signer=signer,
        population_root=canonical_hash(population),
        request_count=1,
        input_capture_hash=str(source_binding["input_capture_content_hash"]),
        delay=float(policy["minimum_delay_seconds"]),
        timeout=float(policy["timeout_seconds"]),
        retries=int(policy["max_retries"]),
        permission_context_id=csindex_backfill.DEFAULT_PERMISSION_CONTEXT,
        allowed_hosts=csindex_backfill.CSINDEX_ATTACHMENT_HOSTS,
        capture_profile=csindex_backfill.CSINDEX_ATTACHMENT_FULL_PROFILE,
        source_binding=source_binding,
    )

    published = run_free_provider_backfill(
        contract,
        requests,
        transport=transport,
        signer=signer,
        normalizer=normalize_csindex_attachments,
        runtime_implementation_root=csindex_implementation_root(),
    )
    validated = validate_free_provider_backfill(published["manifest_path"])
    replayed, replay_root = replay_normalized_artifacts(
        validated["manifest_path"],
        normalizer=normalize_csindex_attachments,
        required_roles=(
            "csindex_attachment_index",
            "csindex_blocked_reference_index",
        ),
    )

    assert validated["status"] == "succeeded"
    assert validated["publication_signature_verified"] is True
    assert json.loads(replayed["csindex_attachment_index"].decode())[
        "attachment_sha256"
    ] == hashlib.sha256(body).hexdigest()
    blocked = [
        json.loads(line)
        for line in replayed["csindex_blocked_reference_index"]
        .decode()
        .splitlines()
    ]
    assert [row["reference_disposition"] for row in blocked] == [
        "blocked_rejected_reference",
        "blocked_out_of_scope_reference",
    ]
    assert blocked[0]["source_announcements"][0]["announcement_id"] == "43"
    assert len(replay_root) == 64


def test_csindex_attachment_identity_binds_shared_http_transport_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = csindex_implementation_root()
    original = csindex_backfill.sha256_file

    def changed(path: Path) -> str:
        if Path(path).resolve() == Path(
            csindex_backfill.run_provider_probe_module.__file__
        ).resolve():
            return "0" * 64
        return original(path)

    monkeypatch.setattr(csindex_backfill, "sha256_file", changed)

    assert csindex_implementation_root() != baseline


def test_csindex_identity_binds_shared_capture_engine_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = csindex_implementation_root()
    original = csindex_backfill.sha256_file

    def changed(path: Path) -> str:
        if Path(path).resolve() == Path(
            csindex_backfill.free_provider_backfill_module.__file__
        ).resolve():
            return "0" * 64
        return original(path)

    monkeypatch.setattr(csindex_backfill, "sha256_file", changed)

    assert csindex_implementation_root() != baseline


def test_cninfo_identity_binds_capture_replay_helpers_and_page_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = cninfo_implementation_root()

    with monkeypatch.context() as scoped:
        scoped.setattr(
            cninfo_backfill,
            "_captured_cninfo_pages",
            lambda _path: {},
        )
        assert cninfo_implementation_root() != baseline

    with monkeypatch.context() as scoped:
        scoped.setattr(
            cninfo_backfill,
            "_nonnegative_int",
            lambda _value: 0,
        )
        assert cninfo_implementation_root() != baseline

    with monkeypatch.context() as scoped:
        scoped.setattr(cninfo_backfill, "CNINFO_PAGE_SIZE", 31)
        assert cninfo_implementation_root() != baseline

    with monkeypatch.context() as scoped:
        scoped.setattr(cninfo_backfill, "CNINFO_MAX_PAGES_PER_LEAF", 101)
        assert cninfo_implementation_root() != baseline

    with monkeypatch.context() as scoped:
        scoped.setattr(cninfo_backfill, "CNINFO_STATIC_DATE_SPLITS", {})
        assert cninfo_implementation_root() != baseline


def test_cninfo_identity_excludes_unrelated_baostock_transport_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = cninfo_implementation_root()
    original_module = Path(str(cninfo_backfill.run_provider_probe_module.__file__))
    changed_module = tmp_path / "run_provider_probe_baostock_only_change.py"
    changed_module.write_bytes(
        original_module.read_bytes()
        + b"\n# unrelated Baostock-only transport change\n"
    )
    monkeypatch.setattr(
        cninfo_backfill.run_provider_probe_module,
        "__file__",
        str(changed_module),
    )

    assert cninfo_implementation_root() == baseline
    assert cninfo_backfill._implementation_root_compatible(
        "35c27d2670d231ee07a6026a8e8d1d451b321f0837b047db26f3dcd87ae3c49e"
    ) is True


def test_csindex_attachment_transport_marks_html_block_as_waf() -> None:
    body = "\ufeff<html>访问被阻断</html>".encode()
    population = [
        {
            "attachment_url": "https://oss-ch.csindex.com.cn/20120102/a.xlsx",
            "host": "oss-ch.csindex.com.cn",
            "extension": "xlsx",
            "path_dates": ["20120102"],
            "reference_disposition": "capture_eligible",
            "source_announcements": [],
        }
    ]
    request = _fixture_attachment_requests(population)[0]
    official = json.dumps(
        {
            "schema_version": "official_http_probe_envelope_v1",
            "url": request.url,
            "method": "GET",
            "status_code": 200,
            "response_headers": {
                "Content-Length": str(len(body)),
                "Content-Type": "text/html",
            },
            "body_base64": base64.b64encode(body).decode(),
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "redirect_followed": False,
        }
    ).encode()
    base_observation = ProviderProbeObservation(
        terminal_state="positive",
        raw_payload=official,
        row_count=1,
        status_code=200,
        transport_exchange_count=1,
    )
    transport = CSIndexAttachmentTransport(minimum_delay_seconds=0)
    transport._transport = lambda _request, _timeout: base_observation

    observed = transport(request, 3)

    assert observed.terminal_state == "error"
    assert observed.checks["not_html_or_waf"] is False
    assert observed.diagnostics["waf_html_observed"] is True


def test_csindex_attachment_invalid_envelope_preserves_exchange_evidence() -> None:
    request = _fixture_attachment_requests(
        [
            {
                "attachment_url": (
                    "https://oss-ch.csindex.com.cn/20120102/a.xlsx"
                ),
                "host": "oss-ch.csindex.com.cn",
                "extension": "xlsx",
                "path_dates": ["20120102"],
                "reference_disposition": "capture_eligible",
                "source_announcements": [],
            }
        ]
    )[0]
    inner = ProviderProbeObservation(
        terminal_state="positive",
        raw_payload=b"not-json",
        row_count=1,
        status_code=200,
        transport_exchange_count=1,
    )
    transport = CSIndexAttachmentTransport(minimum_delay_seconds=0)
    transport._transport = lambda _request, _timeout: inner

    observed = transport(request, 3)

    assert observed.terminal_state == "error"
    assert observed.error_code == "csindex_attachment_http_envelope_invalid"
    assert observed.raw_payload == b"not-json"
    assert observed.transport_exchange_count == 1


def test_cninfo_structured_empty_month_is_valid_negative_evidence(tmp_path: Path) -> None:
    _leaves, requests = build_cninfo_discovery_plan(["suspensions_sh_201511"])
    terminal = {}
    for request, body in zip(
        requests,
        ({"stockList": [{"code": "600000"}]}, {"totalAnnouncement": 0, "announcements": None, "hasMore": False}),
    ):
        wrapper, receipt = _official_wrapper(body, request)
        path = tmp_path / receipt["raw_envelope_relative_path"]
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(wrapper), encoding="utf-8")
        terminal[request.request_id] = receipt | {
            "terminal_state": "empty" if request.metadata.get("case") == "cninfo_list" else "positive"
        }

    artifacts = normalize_cninfo_discovery(tmp_path, requests, terminal)
    conflicts = (tmp_path / "normalized/conflicts.jsonl").read_text(encoding="utf-8")

    assert conflicts == ""
    assert {row.role for row in artifacts} >= {"cninfo_page_coverage", "conflicts"}


def test_baostock_additional_free_reconciliation_plans_are_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    securities = tmp_path / "securities.jsonl"
    securities.write_text(
        json.dumps(
            {
                "ts_code": "600000.SH",
                "exchange": "SSE",
                "list_date": "19991110",
                "delist_date": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _approve_baostock_securities_fixture(monkeypatch, securities)

    stock_population, stock_requests = build_security_basic_plan(securities)
    turnover_population, turnover_requests = build_turnover_plan(securities)
    index_population, index_requests = build_index_daily_plan()
    _adjustment_population, adjustment_requests = build_adjustment_plan(securities)
    _dividend_population, dividend_requests = build_dividend_plan(securities)

    assert [row["ts_code"] for row in stock_population] == ["600000.SH"]
    assert stock_requests[0].metadata["case"] == "stock_basic"
    assert stock_requests[0].expected_terminal_states == ("positive", "empty")
    assert turnover_population == stock_population
    assert turnover_requests[0].metadata["expected_fields"] == (
        "date",
        "code",
        "turn",
    )
    assert turnover_requests[0].metadata["provider_code"] == "sh.600000"
    assert "provider_code_matches_request" in turnover_requests[0].required_checks
    assert "fields=date,code,turn" in turnover_requests[0].url
    assert index_population == ["000300.SH"]
    assert index_requests[0].metadata["case"] == "history_custom"
    assert index_requests[0].metadata["provider_code"] == "sh.000300"
    assert "provider_code_matches_request" in index_requests[0].required_checks
    for request in (adjustment_requests[0], dividend_requests[0]):
        assert request.metadata["provider_code"] == "sh.600000"
        assert "provider_code_matches_request" in request.required_checks


def test_baostock_custom_history_checks_bind_rows_to_requested_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    securities = tmp_path / "securities.jsonl"
    securities.write_text(
        json.dumps(
            {
                "ts_code": "600000.SH",
                "exchange": "SSE",
                "list_date": "19991110",
                "delist_date": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _approve_baostock_securities_fixture(monkeypatch, securities)
    _population, requests = build_turnover_plan(securities)
    request = requests[0]
    transport = BaostockProbeTransport()

    correct = transport._checks(
        request,
        fields=("date", "code", "turn"),
        rows=(("2012-01-04", "sh.600000", "1.2"),),
        clean_terminal=True,
    )
    wrong = transport._checks(
        request,
        fields=("date", "code", "turn"),
        rows=(("2012-01-04", "sz.000001", "1.2"),),
        clean_terminal=True,
    )

    assert correct["provider_code_matches_request"] is True
    assert wrong["provider_code_matches_request"] is False


def test_baostock_pre_send_peer_failure_does_not_invent_wire_exchange() -> None:
    request = ProviderProbeRequest(
        request_id="baostock_dividend_300795_SZ_2018",
        provider="baostock",
        endpoint="dividend_reconciliation",
        method="BAOSTOCK",
        url=(
            "baostock://public-api.baostock.com/dividend"
            "?code=sz.300795&year=2018"
        ),
        disposition="provider_cannot_prove",
        evidence_semantics="raw_custom_socket_response_plus_locked_parser",
        expected_terminal_states=("positive", "empty"),
        required_checks=("raw_wire_captured",),
        metadata={"case": "dividend"},
    )

    class DisconnectedSocket:
        def getpeername(self) -> object:
            raise OSError(107, "Transport endpoint is not connected")

    class Context:
        default_socket = DisconnectedSocket()

    transport = BaostockProbeTransport()
    transport._context = Context()
    transport._constants = object()
    transport._ensure_session = lambda _timeout: None  # type: ignore[method-assign]

    class FakeBaostock:
        def query_dividend_data(
            self, *, code: str, year: str, yearType: str
        ) -> object:
            assert (code, year, yearType) == ("sz.300795", "2018", "report")
            transport._safe_send("dividend-request")
            raise AssertionError("unreachable")

    transport._bs = FakeBaostock()

    observation = transport(request, 3)
    raw = json.loads(observation.raw_payload)

    assert observation.terminal_state == "error"
    assert observation.error_code == "baostock_transport:OSError"
    assert observation.transport_exchange_count == 0
    assert raw["wire_exchanges"] == []


def test_baostock_reconciliation_adapter_scopes_connection_reset_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _population, requests = build_index_daily_plan()
    instances: list[ResetTransport] = []

    class ResetTransport:
        def __init__(self) -> None:
            self.closed = False
            instances.append(self)

        def __call__(
            self,
            _request: ProviderProbeRequest,
            _timeout_seconds: float,
        ) -> ProviderProbeObservation:
            return ProviderProbeObservation(
                terminal_state="error",
                raw_payload=b"signed-wire-reset-fixture",
                row_count=None,
                error_code="baostock_transport:ConnectionResetError",
                diagnostics={"wire_capture_count": 2},
                checks={"transport_completed": False},
                transport_exchange_count=2,
            )

        def close(self) -> None:
            self.closed = True

        def restore(
            self,
            _request: ProviderProbeRequest,
            _record: object,
        ) -> None:
            return None

    monkeypatch.setattr(
        capture_backfill,
        "BaostockProbeTransport",
        ResetTransport,
    )
    transport = (
        baostock_reconciliation.BoundedBaostockReconciliationTransport()
    )
    observation = transport(requests[0], 3.0)

    assert observation.error_code == "baostock_transport:ConnectionError"
    assert observation.diagnostics["transient_error_normalization"] == {
        "adapter": "BoundedBaostockReconciliationTransport",
        "original_error_code": "baostock_transport:ConnectionResetError",
        "normalized_error_code": "baostock_transport:ConnectionError",
        "transport_replaced": True,
    }
    assert capture_backfill._retryable(
        observation.error_code,
        provider="baostock",
    ) is True
    assert len(instances) == 2
    assert instances[0].closed is True


def test_baostock_hs300_retry_v2_applies_bounded_connection_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _population, requests = build_index_daily_plan()
    sleeps: list[float] = []
    observations = [
        ProviderProbeObservation(
            terminal_state="error",
            raw_payload=b"connection-reset",
            row_count=None,
            error_code="baostock_transport:ConnectionResetError",
            diagnostics={},
            checks={"transport_completed": False},
            transport_exchange_count=1,
        ),
        ProviderProbeObservation(
            terminal_state="error",
            raw_payload=b"socket-os-error",
            row_count=None,
            error_code="baostock_transport:OSError",
            diagnostics={},
            checks={"transport_completed": False},
            transport_exchange_count=1,
        ),
        ProviderProbeObservation(
            terminal_state="positive",
            raw_payload=b"success",
            row_count=1,
            diagnostics={},
            checks={"transport_completed": True},
            transport_exchange_count=1,
        ),
        ProviderProbeObservation(
            terminal_state="error",
            raw_payload=b"connection-reset-after-success",
            row_count=None,
            error_code="baostock_transport:ConnectionResetError",
            diagnostics={},
            checks={"transport_completed": False},
            transport_exchange_count=1,
        ),
    ]

    class SequenceTransport:
        def __init__(self) -> None:
            self.closed = False

        def __call__(
            self,
            _request: ProviderProbeRequest,
            _timeout_seconds: float,
        ) -> ProviderProbeObservation:
            return observations.pop(0)

        def close(self) -> None:
            self.closed = True

        def restore(
            self,
            _request: ProviderProbeRequest,
            _record: object,
        ) -> None:
            return None

    monkeypatch.setattr(
        capture_backfill,
        "BaostockProbeTransport",
        SequenceTransport,
    )
    transport = baostock_reconciliation.BoundedBaostockReconciliationTransport(
        connection_failure_cooldowns=(5.0, 15.0, 30.0, 60.0, 120.0),
        sleeper=sleeps.append,
    )

    first = transport(requests[0], 3.0)
    second = transport(requests[0], 3.0)
    third = transport(requests[0], 3.0)
    fourth = transport(requests[0], 3.0)

    assert first.error_code == "baostock_transport:ConnectionError"
    assert first.diagnostics["connection_failure_cooldown"] == {
        "consecutive_failure_ordinal": 1,
        "seconds": 5.0,
    }
    assert second.diagnostics["connection_failure_cooldown"] == {
        "consecutive_failure_ordinal": 2,
        "seconds": 15.0,
    }
    assert third.terminal_state == "positive"
    assert fourth.diagnostics["connection_failure_cooldown"] == {
        "consecutive_failure_ordinal": 1,
        "seconds": 5.0,
    }
    assert sleeps == [5.0, 15.0, 5.0]


def test_baostock_hs300_retry_v2_contract_is_full_plan_and_phase_scoped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signer = EphemeralReceiptSigner.generate()
    monkeypatch.setattr(
        baostock_reconciliation,
        "_approved_source_file_hashes",
        lambda **_kwargs: {"securities": "a" * 64, "calendar": "b" * 64},
    )
    monkeypatch.setattr(
        baostock_reconciliation,
        "_verify_hs300_retry_v2_pause_evidence",
        lambda: (
            baostock_reconciliation.BAOSTOCK_HS300_RETRY_V2_APPROVED_PAUSE_CONTENT_HASH
        ),
    )
    monkeypatch.setattr(baostock_reconciliation, "SCOPE_ROOT", tmp_path)
    baseline = baostock_reconciliation._BAOSTOCK_PHASE_BASELINES[
        "hs300-snapshots"
    ]

    contract = baostock_reconciliation._contract(
        phase="hs300-snapshots",
        output_root=tmp_path / "hs300_snapshots_v2",
        signer=signer,
        population_root=str(baseline["population_root"]),
        request_count=int(baseline["request_count"]),
        delay=1.0,
        timeout=30.0,
        retries=5,
        permission_context_id=(
            baostock_reconciliation.BAOSTOCK_HS300_RETRY_V2_PERMISSION_CONTEXT
        ),
        acquisition_policy_id=(
            baostock_reconciliation.BAOSTOCK_HS300_RETRY_V2_POLICY_ID
        ),
        request_plan_hash=str(baseline["request_plan_hash"]),
    )

    assert contract.activity_name.endswith("_v2")
    assert contract.budget.max_retries == 5
    assert contract.budget.max_requests == 11_676
    assert contract.budget.max_wire_exchanges == 23_352
    assert contract.adapter_identity["full_plan_replay_required"] == "true"
    assert contract.adapter_identity["partial_activity_reuse"] == "forbidden"
    assert contract.adapter_identity["approved_pause_content_hash"] == (
        baostock_reconciliation.BAOSTOCK_HS300_RETRY_V2_APPROVED_PAUSE_CONTENT_HASH
    )
    assert contract.adapter_identity["approved_paused_activity_id"] == (
        baostock_reconciliation.BAOSTOCK_HS300_RETRY_V2_PAUSED_ACTIVITY_ID
    )
    assert contract.adapter_identity["approved_request_plan_hash"] == str(
        baseline["request_plan_hash"]
    )
    assert contract.adapter_identity["connection_failure_cooldowns_seconds"] == (
        "5,15,30,60,120"
    )
    with pytest.raises(
        ValueError,
        match="baostock_hs300_retry_v2_contract_closure_invalid",
    ):
        baostock_reconciliation._contract(
            phase="hs300-snapshots",
            output_root=tmp_path / "hs300_snapshots_v2",
            signer=signer,
            population_root=str(baseline["population_root"]),
            request_count=int(baseline["request_count"]) - 1,
            delay=1.0,
            timeout=30.0,
            retries=5,
            permission_context_id=(
                baostock_reconciliation.BAOSTOCK_HS300_RETRY_V2_PERMISSION_CONTEXT
            ),
            acquisition_policy_id=(
                baostock_reconciliation.BAOSTOCK_HS300_RETRY_V2_POLICY_ID
            ),
            request_plan_hash=str(baseline["request_plan_hash"]),
        )
    with pytest.raises(
        ValueError,
        match="baostock_acquisition_policy_phase_invalid",
    ):
        baostock_reconciliation._contract(
            phase="turnover",
            output_root=tmp_path / "hs300_snapshots_v2",
            signer=signer,
            population_root="c" * 64,
            request_count=1,
            delay=1.0,
            timeout=30.0,
            retries=5,
            permission_context_id=(
                baostock_reconciliation.BAOSTOCK_HS300_RETRY_V2_PERMISSION_CONTEXT
            ),
            acquisition_policy_id=(
                baostock_reconciliation.BAOSTOCK_HS300_RETRY_V2_POLICY_ID
            ),
            request_plan_hash=str(baseline["request_plan_hash"]),
        )


def test_baostock_hs300_retry_v3_contract_binds_new_full_replay_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signer = EphemeralReceiptSigner.generate()
    monkeypatch.setattr(
        baostock_reconciliation,
        "_approved_source_file_hashes",
        lambda **_kwargs: {"securities": "a" * 64, "calendar": "b" * 64},
    )
    monkeypatch.setattr(
        baostock_reconciliation,
        "_verify_hs300_retry_v3_pause_evidence",
        lambda: (
            baostock_reconciliation.BAOSTOCK_HS300_RETRY_V3_APPROVED_PAUSE_CONTENT_HASH
        ),
    )
    monkeypatch.setattr(baostock_reconciliation, "SCOPE_ROOT", tmp_path)
    baseline = baostock_reconciliation._BAOSTOCK_PHASE_BASELINES[
        "hs300-snapshots"
    ]

    contract = baostock_reconciliation._contract(
        phase="hs300-snapshots",
        output_root=tmp_path / "hs300_snapshots_v3",
        signer=signer,
        population_root=str(baseline["population_root"]),
        request_count=int(baseline["request_count"]),
        delay=1.0,
        timeout=30.0,
        retries=6,
        permission_context_id=(
            baostock_reconciliation.BAOSTOCK_HS300_RETRY_V3_PERMISSION_CONTEXT
        ),
        acquisition_policy_id=(
            baostock_reconciliation.BAOSTOCK_HS300_RETRY_V3_POLICY_ID
        ),
        request_plan_hash=str(baseline["request_plan_hash"]),
    )

    assert contract.activity_name.endswith("_v3")
    assert contract.budget.max_retries == 6
    assert contract.budget.max_requests == 13_622
    assert contract.budget.max_wire_exchanges == 27_244
    assert contract.adapter_identity["partial_activity_reuse"] == "forbidden"
    assert contract.adapter_identity["max_attempts_per_request"] == "7"
    assert contract.adapter_identity["approved_pause_content_hash"] == (
        baostock_reconciliation.BAOSTOCK_HS300_RETRY_V3_APPROVED_PAUSE_CONTENT_HASH
    )
    assert contract.adapter_identity["approved_paused_activity_id"] == (
        baostock_reconciliation.BAOSTOCK_HS300_RETRY_V3_PAUSED_ACTIVITY_ID
    )
    assert contract.adapter_identity["connection_failure_cooldowns_seconds"] == (
        "5,15,30,60,120,300"
    )
    with pytest.raises(
        ValueError,
        match="baostock_hs300_retry_v3_contract_closure_invalid",
    ):
        baostock_reconciliation._contract(
            phase="hs300-snapshots",
            output_root=tmp_path / "hs300_snapshots_v2",
            signer=signer,
            population_root=str(baseline["population_root"]),
            request_count=int(baseline["request_count"]),
            delay=1.0,
            timeout=30.0,
            retries=6,
            permission_context_id=(
                baostock_reconciliation.BAOSTOCK_HS300_RETRY_V3_PERMISSION_CONTEXT
            ),
            acquisition_policy_id=(
                baostock_reconciliation.BAOSTOCK_HS300_RETRY_V3_POLICY_ID
            ),
            request_plan_hash=str(baseline["request_plan_hash"]),
        )


def test_baostock_hs300_retry_v2_sixth_failure_has_no_seventh_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _population, requests = build_index_daily_plan()
    sleeps: list[float] = []

    class FailingTransport:
        def __call__(
            self,
            _request: ProviderProbeRequest,
            _timeout_seconds: float,
        ) -> ProviderProbeObservation:
            return ProviderProbeObservation(
                terminal_state="error",
                raw_payload=b"connection-error",
                row_count=None,
                error_code="baostock_transport:OSError",
                diagnostics={},
                checks={"transport_completed": False},
                transport_exchange_count=1,
            )

        def close(self) -> None:
            return None

        def restore(
            self,
            _request: ProviderProbeRequest,
            _record: object,
        ) -> None:
            return None

    monkeypatch.setattr(
        capture_backfill,
        "BaostockProbeTransport",
        FailingTransport,
    )
    transport = baostock_reconciliation.BoundedBaostockReconciliationTransport(
        connection_failure_cooldowns=(5.0, 15.0, 30.0, 60.0, 120.0),
        sleeper=sleeps.append,
    )

    observations = [transport(requests[0], 3.0) for _ in range(6)]

    assert sleeps == [5.0, 15.0, 30.0, 60.0, 120.0]
    assert all(
        "connection_failure_cooldown" in row.diagnostics
        for row in observations[:5]
    )
    assert "connection_failure_cooldown" not in observations[5].diagnostics


def test_baostock_hs300_retry_v3_seventh_failure_has_no_eighth_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _population, requests = build_index_daily_plan()
    sleeps: list[float] = []

    class FailingTransport:
        def __call__(
            self,
            _request: ProviderProbeRequest,
            _timeout_seconds: float,
        ) -> ProviderProbeObservation:
            return ProviderProbeObservation(
                terminal_state="error",
                raw_payload=b"connection-error",
                row_count=None,
                error_code="baostock_transport:OSError",
                checks={"transport_completed": False},
                transport_exchange_count=1,
            )

        def close(self) -> None:
            return None

        def restore(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(
        capture_backfill,
        "BaostockProbeTransport",
        FailingTransport,
    )
    transport = baostock_reconciliation.BoundedBaostockReconciliationTransport(
        connection_failure_cooldowns=(
            5.0,
            15.0,
            30.0,
            60.0,
            120.0,
            300.0,
        ),
        sleeper=sleeps.append,
    )

    observations = [transport(requests[0], 3.0) for _ in range(7)]

    assert sleeps == [5.0, 15.0, 30.0, 60.0, 120.0, 300.0]
    assert all(
        "connection_failure_cooldown" in row.diagnostics
        for row in observations[:6]
    )
    assert "connection_failure_cooldown" not in observations[6].diagnostics


def test_baostock_non_connection_error_does_not_trigger_v2_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _population, requests = build_index_daily_plan()
    sleeps: list[float] = []

    class BusinessErrorTransport:
        def __call__(
            self,
            _request: ProviderProbeRequest,
            _timeout_seconds: float,
        ) -> ProviderProbeObservation:
            return ProviderProbeObservation(
                terminal_state="error",
                raw_payload=b"business-error",
                row_count=None,
                error_code="baostock:10002007",
                diagnostics={},
                checks={"provider_success": False},
                transport_exchange_count=1,
            )

        def close(self) -> None:
            return None

        def restore(
            self,
            _request: ProviderProbeRequest,
            _record: object,
        ) -> None:
            return None

    monkeypatch.setattr(
        capture_backfill,
        "BaostockProbeTransport",
        BusinessErrorTransport,
    )
    transport = baostock_reconciliation.BoundedBaostockReconciliationTransport(
        connection_failure_cooldowns=(5.0, 15.0, 30.0, 60.0, 120.0),
        sleeper=sleeps.append,
    )

    observation = transport(requests[0], 3.0)

    assert observation.error_code == "baostock:10002007"
    assert sleeps == []
    assert "connection_failure_cooldown" not in observation.diagnostics


def test_baostock_hs300_retry_v2_restores_connection_failure_ordinal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _population, requests = build_index_daily_plan()
    sleeps: list[float] = []

    class FailingTransport:
        def __call__(
            self,
            _request: ProviderProbeRequest,
            _timeout_seconds: float,
        ) -> ProviderProbeObservation:
            return ProviderProbeObservation(
                terminal_state="error",
                raw_payload=b"third-consecutive-failure",
                row_count=None,
                error_code="baostock_transport:OSError",
                checks={"transport_completed": False},
                transport_exchange_count=1,
            )

        def close(self) -> None:
            return None

        def restore(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(
        capture_backfill,
        "BaostockProbeTransport",
        FailingTransport,
    )
    transport = baostock_reconciliation.BoundedBaostockReconciliationTransport(
        connection_failure_cooldowns=(5.0, 15.0, 30.0, 60.0, 120.0),
        recovery_state_loader=lambda _request: (2, False),
        sleeper=sleeps.append,
    )

    observation = transport(requests[0], 3.0)

    assert sleeps == [30.0]
    assert observation.diagnostics["connection_failure_cooldown"] == {
        "consecutive_failure_ordinal": 3,
        "seconds": 30.0,
    }


def test_baostock_hs300_retry_v2_replays_interrupted_pending_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _population, requests = build_index_daily_plan()
    sleeps: list[float] = []

    class SuccessfulTransport:
        def __call__(
            self,
            _request: ProviderProbeRequest,
            _timeout_seconds: float,
        ) -> ProviderProbeObservation:
            return ProviderProbeObservation(
                terminal_state="positive",
                raw_payload=b"success-after-restart",
                row_count=1,
                checks={"transport_completed": True},
                transport_exchange_count=1,
            )

        def close(self) -> None:
            return None

        def restore(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(
        capture_backfill,
        "BaostockProbeTransport",
        SuccessfulTransport,
    )
    transport = baostock_reconciliation.BoundedBaostockReconciliationTransport(
        connection_failure_cooldowns=(5.0, 15.0, 30.0, 60.0, 120.0),
        recovery_state_loader=lambda _request: (3, True),
        sleeper=sleeps.append,
    )

    observation = transport(requests[0], 3.0)

    assert sleeps == [30.0]
    assert observation.diagnostics[
        "recovered_connection_failure_cooldown"
    ] == {
        "consecutive_failure_ordinal": 3,
        "seconds": 30.0,
    }


def test_baostock_historical_replay_allowlist_rejects_near_match() -> None:
    approved = next(
        row
        for row in baostock_reconciliation.BAOSTOCK_HISTORICAL_REPLAY_ALLOWLIST
        if row[0] == "index-daily"
    )
    near_match = (
        approved[0],
        "0" * 64,
        approved[2],
        approved[3],
        approved[4],
    )

    assert approved in (
        baostock_reconciliation.BAOSTOCK_HISTORICAL_REPLAY_ALLOWLIST
    )
    assert near_match not in (
        baostock_reconciliation.BAOSTOCK_HISTORICAL_REPLAY_ALLOWLIST
    )


def test_baostock_hs300_retry_v2_pause_evidence_is_exact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signer = EphemeralReceiptSigner.generate()
    request_id = "baostock_hs300_fixture"
    request = ProviderProbeRequest(
        request_id=request_id,
        provider="baostock",
        method="BAOSTOCK",
        url="baostock://public-api.baostock.com/query_hs300_stocks?date=2012-01-04",
        endpoint="query_hs300_stocks",
        metadata={"case": "hs300", "query_date": "20120104"},
    )
    request_rows = [request.semantic()]
    request_plan_hash = canonical_hash(request_rows)
    approved_key = capture_backfill._public_key_hash(signer.public_key_pem)
    contract = {
        "activity_name": (
            "free_domestic_baostock_hs300-snapshots_2012_2019_v1"
        ),
        "provider": "baostock",
        "permission_context_id": baostock_reconciliation.PERMISSION_CONTEXT,
        "population_root": (
            baostock_reconciliation.SECURITY_SNAPSHOT_APPROVED_POPULATION_ROOT
        ),
        "capture_public_key_sha256": approved_key,
        "capture_public_key_pem_b64": base64.b64encode(
            signer.public_key_pem
        ).decode(),
        "source_profile_id": baostock_reconciliation.BAOSTOCK_SOURCE_PROFILE_ID,
        "budget": {
            "max_requests": 3,
            "max_response_bytes": 64 * 1024 * 1024,
            "max_retries": 2,
            "max_total_response_bytes": 16 * 1024 * 1024 * 1024,
            "max_wire_exchanges": 6,
            "minimum_delay_seconds": 1.0,
            "timeout_seconds": 30.0,
        },
    }
    contract_id = canonical_hash(contract)
    activity_id = canonical_hash(
        {
            "contract_id": contract_id,
            "request_plan_hash": request_plan_hash,
        }
    )
    raw = b"signed-parent-raw-fixture"
    raw_hash = hashlib.sha256(raw).hexdigest()
    terminal = {
        "event_type": "capture_attempt_terminal",
        "request_id": request_id,
        "attempt_id": f"{request_id}:2",
        "terminal_state": "error",
        "error_code": "baostock_transport:OSError",
        "status_code": None,
        "transport_exchange_count": 3,
        "raw_envelope_relative_path": "raw_envelopes/terminal.json",
        "raw_envelope_sha256": raw_hash,
    }
    semantic = {
        "schema_version": "free_provider_backfill_pause_v1",
        "reason": "provider_terminal_error_or_circuit_breaker",
        "request_id": request_id,
        "attempt_id": f"{request_id}:2",
        "terminal_state": "error",
        "error_code": "baostock_transport:OSError",
        "status_code": None,
        "usage": {
            "attempt_count": 1,
            "response_bytes": len(raw),
            "wire_exchange_count": 3,
        },
        "paused_at": "2026-08-17T09:27:56Z",
        "automatic_resume_authorized": False,
    }
    pause_hash = canonical_hash(semantic)
    pause = semantic | {"content_hash": pause_hash}
    monkeypatch.setattr(baostock_reconciliation, "SCOPE_ROOT", tmp_path)
    monkeypatch.setattr(
        baostock_reconciliation,
        "BAOSTOCK_APPROVED_CAPTURE_KEY_SHA256",
        approved_key,
    )
    monkeypatch.setattr(
        baostock_reconciliation,
        "BAOSTOCK_HS300_RETRY_V2_PAUSED_ACTIVITY_ID",
        activity_id,
    )
    monkeypatch.setattr(
        baostock_reconciliation,
        "BAOSTOCK_HS300_RETRY_V2_PAUSED_CONTRACT_ID",
        contract_id,
    )
    monkeypatch.setattr(
        baostock_reconciliation,
        "BAOSTOCK_HS300_RETRY_V2_PAUSED_REQUEST_PLAN_HASH",
        request_plan_hash,
    )
    monkeypatch.setattr(
        baostock_reconciliation,
        "BAOSTOCK_HS300_RETRY_V2_PAUSED_REQUEST_COUNT",
        1,
    )
    monkeypatch.setattr(
        baostock_reconciliation,
        "BAOSTOCK_HS300_RETRY_V2_PAUSED_REQUEST_ID",
        request_id,
    )
    monkeypatch.setattr(
        baostock_reconciliation,
        "BAOSTOCK_HS300_RETRY_V2_APPROVED_PAUSE_CONTENT_HASH",
        pause_hash,
    )
    path = (
        tmp_path
        / ".hs300_snapshots.activities"
        / activity_id
        / "pauses"
        / f"pause_{pause_hash[:24]}.json"
    )
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(pause), encoding="utf-8")
    activity_root = path.parents[1]
    (activity_root / "raw_envelopes").mkdir()
    (activity_root / "raw_envelopes" / "terminal.json").write_bytes(raw)
    (activity_root / "activity_contract.json").write_text(
        json.dumps(contract), encoding="utf-8"
    )
    (activity_root / "request_plan.json").write_text(
        json.dumps(
            {
                "schema_version": "free_provider_backfill_request_plan_v1",
                "request_plan_hash": request_plan_hash,
                "requests": request_rows,
            }
        ),
        encoding="utf-8",
    )
    (activity_root / "capture_journal.jsonl").write_text(
        "signed-journal-fixture\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        baostock_reconciliation,
        "_read_and_validate_journal",
        lambda *_args, **_kwargs: [terminal],
    )

    assert baostock_reconciliation._verify_hs300_retry_v2_pause_evidence() == (
        pause_hash
    )
    pause["automatic_resume_authorized"] = True
    path.write_text(json.dumps(pause), encoding="utf-8")
    with pytest.raises(
        ValueError,
        match="baostock_hs300_retry_v2_pause_evidence_invalid",
    ):
        baostock_reconciliation._verify_hs300_retry_v2_pause_evidence()


def test_baostock_hs300_retry_v2_cli_rejects_runtime_budget_drift(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = baostock_reconciliation.main(
        [
            "--phase",
            "hs300-snapshots",
            "--plan-only",
            "--max-retries",
            "2",
        ]
    )

    assert result == 2
    assert "governed_phase_runtime_policy_drift" in capsys.readouterr().out


def test_baostock_wire_validator_binds_actual_protocol_request_bytes() -> None:
    _population, requests = build_index_daily_plan()
    request = requests[0]
    rows = [["2012-01-04", "sh.000300", *(["1"] * 9)]]
    raw = _security_or_history_payload(
        request, fields=BAOSTOCK_FIELDS.split(","), rows=rows
    )

    _validate_baostock_wire_envelope(
        raw,
        expected_exchange_count=1,
        request=request.semantic(),
        terminal_state="positive",
    )
    envelope = json.loads(raw)
    wrong_request = _baostock_request_frame(
        "query_history_k_data_plus",
        [
            "anonymous",
            "1",
            "2000",
            "sz.000001",
            BAOSTOCK_FIELDS,
            "2012-01-01",
            "2019-12-31",
            "d",
            "3",
        ],
        message_type="95",
    )
    exchange = envelope["wire_exchanges"][0]
    exchange["wire_request_base64"] = base64.b64encode(wrong_request).decode()
    exchange["request_sha256"] = hashlib.sha256(wrong_request).hexdigest()
    exchange["request_size_bytes"] = len(wrong_request)
    with pytest.raises(ValueError, match="parameter_mismatch"):
        _validate_baostock_wire_envelope(
            json.dumps(envelope).encode(),
            expected_exchange_count=1,
            request=request.semantic(),
            terminal_state="positive",
        )


def test_baostock_wire_validator_rejects_forged_parsed_page_identity() -> None:
    _population, requests = build_index_daily_plan()
    request = requests[0]
    rows = [["2012-01-04", "sh.000300", *(["1"] * 9)]]
    envelope = json.loads(
        _security_or_history_payload(
            request,
            fields=BAOSTOCK_FIELDS.split(","),
            rows=rows,
        )
    )
    envelope["parsed"]["pages"] = [
        {"page": 999, "row_count": 1, "provider_page_size": 1}
    ]

    with pytest.raises(ValueError, match="baostock.*page.*binding"):
        _validate_baostock_wire_envelope(
            json.dumps(envelope).encode(),
            expected_exchange_count=1,
            request=request.semantic(),
            terminal_state="positive",
        )


@pytest.mark.parametrize(
    ("request_pages", "response_pages", "response_users", "error"),
    [
        ([2], [2], None, "page_binding"),
        ([1], [2], None, "page_binding"),
        ([1], [1], ["forged-user"], "user_binding"),
    ],
)
def test_baostock_wire_validator_binds_page_and_user_echo(
    request_pages: list[int],
    response_pages: list[int],
    response_users: list[str] | None,
    error: str,
) -> None:
    _population, requests = build_index_daily_plan()
    request = requests[0]
    rows = [["2012-01-04", "sh.000300", *(["1"] * 9)]]

    with pytest.raises(ValueError, match=error):
        _validate_baostock_wire_envelope(
            _history_pages_payload(
                request,
                page_rows=[rows],
                request_pages=request_pages,
                response_pages=response_pages,
                response_users=response_users,
            ),
            expected_exchange_count=1,
            request=request.semantic(),
            terminal_state="positive",
        )


@pytest.mark.parametrize(
    ("request_type", "response_type"),
    [("35", "96"), ("95", "99")],
)
def test_baostock_wire_validator_rejects_message_type_confusion(
    request_type: str,
    response_type: str,
) -> None:
    _population, requests = build_index_daily_plan()
    request = requests[0]
    rows = [["2012-01-04", "sh.000300", *(["1"] * 9)]]

    with pytest.raises(ValueError, match="message_type"):
        _validate_baostock_wire_envelope(
            _history_pages_payload(
                request,
                page_rows=[rows],
                request_type=request_type,
                response_type=response_type,
            ),
            expected_exchange_count=1,
            request=request.semantic(),
            terminal_state="positive",
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda tokens: tokens.__setitem__(1, "forged-user"),
        lambda tokens: tokens.__setitem__(8, "w"),
        lambda tokens: tokens.__setitem__(9, "2"),
        lambda tokens: tokens.__setitem__(2, "999"),
        lambda tokens: tokens.__setitem__(4, "sz.000001"),
        lambda tokens: tokens.append("unexpected"),
        lambda tokens: tokens.pop(),
    ],
)
def test_baostock_history_request_contract_is_exact_even_on_provider_error(
    mutate: object,
) -> None:
    _population, requests = build_index_daily_plan()
    request = requests[0]
    query = {
        key: values[-1]
        for key, values in parse_qs(urlsplit(request.url).query).items()
    }
    raw = _baostock_error_payload(
        request,
        operation="query_history_k_data_plus",
        request_arguments=[
            "anonymous",
            "1",
            "2000",
            query["code"],
            query["fields"],
            query["start"],
            query["end"],
            "d",
            "3",
        ],
        request_type="95",
    )
    forged = _replace_first_baostock_request(raw, mutate)

    with pytest.raises(
        ValueError,
        match="request_contract|user_binding|page_binding|request_binding",
    ):
        _validate_baostock_wire_envelope(
            forged,
            expected_exchange_count=1,
            request=request.semantic(),
            terminal_state="error",
        )


@pytest.mark.parametrize(
    ("kind", "replacement"),
    [("dividend", "annual"), ("stock_basic", "forged-name")],
)
def test_baostock_special_request_literals_are_locked_on_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    replacement: str,
) -> None:
    securities = tmp_path / "securities.jsonl"
    securities.write_text(
        json.dumps(
            {
                "ts_code": "600000.SH",
                "exchange": "SSE",
                "list_date": "19991110",
                "delist_date": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _approve_baostock_securities_fixture(monkeypatch, securities)
    if kind == "dividend":
        _population, requests = build_dividend_plan(securities)
        request = requests[0]
        arguments = [
            "anonymous",
            "1",
            "2000",
            "sh.600000",
            "2011",
            replacement,
        ]
        operation, request_type = "query_dividend_data", "13"
    else:
        _population, requests = build_security_basic_plan(securities)
        request = requests[0]
        arguments = [
            "anonymous",
            "1",
            "2000",
            "sh.600000",
            replacement,
        ]
        operation, request_type = "query_stock_basic", "45"

    with pytest.raises(ValueError, match="request_contract"):
        _validate_baostock_wire_envelope(
            _baostock_error_payload(
                request,
                operation=operation,
                request_arguments=arguments,
                request_type=request_type,
            ),
            expected_exchange_count=1,
            request=request.semantic(),
            terminal_state="error",
        )


def test_baostock_partial_error_still_validates_request_protocol_first() -> None:
    _population, requests = build_index_daily_plan()
    request = requests[0]
    raw = _security_or_history_payload(
        request,
        fields=BAOSTOCK_FIELDS.split(","),
        rows=[["2012-01-04", "sh.000300", *(["1"] * 9)]],
    )
    forged = json.loads(
        _replace_first_baostock_request(
            raw, lambda tokens: tokens.__setitem__(8, "w")
        )
    )
    exchange = forged["wire_exchanges"][0]
    exchange["wire_response_base64"] = ""
    exchange["wire_response_sha256"] = hashlib.sha256(b"").hexdigest()
    exchange["wire_size_bytes"] = 0
    exchange["terminal_marker_present"] = False
    forged["parsed"] = {
        "fields": [],
        "row_count": 0,
        "pages": [],
        "first_rows": [],
        "last_rows": [],
        "canonical_logical_payload_sha256": canonical_hash(
            {"fields": [], "rows": []}
        ),
    }
    forged["provider_error"] = {
        "type": "TimeoutError",
        "message": "partial",
    }

    with pytest.raises(ValueError, match="request_contract"):
        _validate_baostock_wire_envelope(
            json.dumps(forged, sort_keys=True).encode(),
            expected_exchange_count=1,
            request=request.semantic(),
            terminal_state="error",
        )


def test_baostock_partial_response_must_be_final_exchange() -> None:
    _population, requests = build_index_daily_plan()
    request = requests[0]
    envelope = json.loads(
        _history_pages_payload(request, page_rows=[[], []])
    )
    first, second = envelope["wire_exchanges"]
    first["wire_response_base64"] = ""
    first["wire_response_sha256"] = hashlib.sha256(b"").hexdigest()
    first["wire_size_bytes"] = 0
    first["terminal_marker_present"] = False
    provider_error = _baostock_uncompressed_response_frame(
        ["10001001", "session expired"], message_type="04"
    )
    second["wire_response_base64"] = base64.b64encode(provider_error).decode()
    second["wire_response_sha256"] = hashlib.sha256(provider_error).hexdigest()
    second["wire_size_bytes"] = len(provider_error)
    envelope["parsed"] = {
        "fields": [],
        "row_count": 0,
        "pages": [],
        "first_rows": [],
        "last_rows": [],
        "canonical_logical_payload_sha256": canonical_hash(
            {"fields": [], "rows": []}
        ),
    }
    envelope["provider_error"] = {
        "code": "10001001",
        "message": "session expired",
    }

    with pytest.raises(ValueError, match="partial.*final"):
        _validate_baostock_wire_envelope(
            json.dumps(envelope, sort_keys=True).encode(),
            expected_exchange_count=2,
            request=request.semantic(),
            terminal_state="error",
        )


def test_baostock_wire_validator_rejects_exchange_after_short_page() -> None:
    _population, requests = build_index_daily_plan()
    request = requests[0]
    row = ["2012-01-04", "sh.000300", *(["1"] * 9)]

    with pytest.raises(ValueError, match="pagination_terminal"):
        _validate_baostock_wire_envelope(
            _history_pages_payload(request, page_rows=[[row], [row]]),
            expected_exchange_count=2,
            request=request.semantic(),
            terminal_state="positive",
        )


def test_baostock_wire_validator_requires_terminal_page_after_full_page() -> None:
    _population, requests = build_index_daily_plan()
    request = requests[0]
    rows = [
        [f"2012-01-{(index % 28) + 1:02d}", "sh.000300", *(["1"] * 9)]
        for index in range(2000)
    ]

    with pytest.raises(ValueError, match="pagination_terminal"):
        _validate_baostock_wire_envelope(
            _history_pages_payload(request, page_rows=[rows]),
            expected_exchange_count=1,
            request=request.semantic(),
            terminal_state="positive",
        )


def test_baostock_wire_validator_accepts_full_page_plus_empty_terminal() -> None:
    _population, requests = build_index_daily_plan()
    request = requests[0]
    rows = [
        [f"2012-01-{(index % 28) + 1:02d}", "sh.000300", *(["1"] * 9)]
        for index in range(2000)
    ]

    _validate_baostock_wire_envelope(
        _history_pages_payload(request, page_rows=[rows, []]),
        expected_exchange_count=2,
        request=request.semantic(),
        terminal_state="positive",
    )


def test_baostock_compressed_response_preserves_but_does_not_verify_trailer() -> None:
    rows = [["2012-01-04", "sh.000300", *(["1"] * 9)]]
    response = _baostock_response_frame(
        "query_history_k_data_plus",
        rows=rows,
        response_suffix=[
            "sh.000300",
            BAOSTOCK_FIELDS,
            "2012-01-01",
            "2019-12-31",
            "d",
            "3",
        ],
        message_type="96",
    )
    response = re.sub(
        rb"\x01[0-9]{1,10}\n<!\[CDATA\[\]\]>",
        b"\x017\n<![CDATA[]]>",
        response,
    )

    _tokens, evidence = capture_backfill._parse_baostock_response_frame(
        response
    )

    assert evidence == {
        "message_type": "96",
        "compressed": True,
        "provider_trailer_decimal_preserved": "7",
        "provider_trailer_integrity_verified": False,
        "provider_trailer_integrity_semantics": (
            "unverified_opaque_decimal_for_compressed_response"
        ),
        "zlib_stream_checksum_verified": True,
    }


def test_baostock_compressed_response_rejects_trailing_zlib_bytes() -> None:
    rows = [["2012-01-04", "sh.000300", *(["1"] * 9)]]
    response = _baostock_response_frame(
        "query_history_k_data_plus",
        rows=rows,
        response_suffix=[
            "sh.000300",
            BAOSTOCK_FIELDS,
            "2012-01-01",
            "2019-12-31",
            "d",
            "3",
        ],
        message_type="96",
    )
    compressed_length = int(response[11:21])
    body = response[21 : 21 + compressed_length] + b"junk"
    header = f"00.9.00\x0196\x01{len(body):010d}".encode()
    forged = header + body + b"\x017\n<![CDATA[]]>\n"

    with pytest.raises(ValueError, match="compression_invalid"):
        capture_backfill._parse_baostock_response_frame(forged)


def test_baostock_normalizer_rejects_archived_rows_for_another_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    securities = tmp_path / "securities.jsonl"
    securities.write_text(
        json.dumps(
            {
                "ts_code": "600000.SH",
                "exchange": "SSE",
                "list_date": "19991110",
                "delist_date": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _approve_baostock_securities_fixture(monkeypatch, securities)
    _population, requests = build_turnover_plan(securities)
    request = requests[0]
    wrapper, receipt = _baostock_wrapper(
        request=request,
        fields=["date", "code", "turn"],
        rows=[["2012-01-04", "sz.000001", "1.2"]],
    )
    wrapper_path = tmp_path / f"raw_envelopes/{request.request_id}.json"
    wrapper_path.parent.mkdir()
    wrapper_path.write_text(json.dumps(wrapper), encoding="utf-8")

    with pytest.raises(ValueError, match="provider_code_mismatch"):
        normalize_turnover(
            tmp_path,
            [request],
            {request.request_id: receipt},
        )

    conflicts = [
        json.loads(line)
        for line in (tmp_path / "normalized/conflicts.jsonl").read_text().splitlines()
    ]
    assert conflicts == [
        {
            "expected_provider_code": "sh.600000",
            "observed_provider_codes": ["sz.000001"],
            "reason": "provider_code_mismatch",
            "request_id": request.request_id,
        }
    ]


def test_baostock_reconciliation_identity_binds_transport_and_wire_decoder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = baostock_reconciliation_implementation_root()
    original = baostock_reconciliation.inspect.getsource
    seen: list[object] = []

    def tracked(value: object) -> str:
        seen.append(value)
        source = original(value)
        if value is baostock_reconciliation.BaostockProbeTransport:
            return source + "\n# transport identity mutation"
        return source

    monkeypatch.setattr(baostock_reconciliation.inspect, "getsource", tracked)
    mutated = baostock_reconciliation_implementation_root()

    assert mutated != baseline
    assert {
        baostock_reconciliation.BaostockProbeTransport,
        baostock_reconciliation.RecoveringBaostockTransport,
        baostock_reconciliation._baostock_logical_rows,
    }.issubset(set(seen))


def test_baostock_snapshot_identity_binds_contract_protocol_and_output_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = baostock_reconciliation_implementation_root()

    with monkeypatch.context() as scoped:
        scoped.setattr(
            baostock_reconciliation,
            "SECURITY_SNAPSHOT_APPROVED_OPEN_DATE_ROOT",
            "f" * 64,
        )
        assert baostock_reconciliation_implementation_root() != baseline

    with monkeypatch.context() as scoped:
        scoped.setattr(
            baostock_reconciliation,
            "baostock_wire_protocol_root",
            lambda: "e" * 64,
        )
        assert baostock_reconciliation_implementation_root() != baseline

    original_write_row = baostock_reconciliation._write_row

    def changed_write_row(handle: object, row: object) -> None:
        original_write_row(handle, row)  # type: ignore[arg-type]

    with monkeypatch.context() as scoped:
        scoped.setattr(
            baostock_reconciliation, "_write_row", changed_write_row
        )
        assert baostock_reconciliation_implementation_root() != baseline


def test_baostock_protocol_change_creates_new_contract_and_activity_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signer = EphemeralReceiptSigner.generate()
    contract = baostock_reconciliation._contract(
        phase="turnover",
        output_root=tmp_path / "capture",
        signer=signer,
        population_root="a" * 64,
        request_count=1,
        delay=0,
        timeout=3,
        retries=0,
        permission_context_id="human-approved-fixture",
    )
    request_plan_hash = "b" * 64
    contract_id = canonical_hash(contract.semantic())
    activity_id = canonical_hash(
        {
            "contract_id": contract_id,
            "request_plan_hash": request_plan_hash,
        }
    )

    monkeypatch.setattr(
        baostock_reconciliation,
        "baostock_wire_protocol_root",
        lambda: "e" * 64,
    )
    changed_contract = baostock_reconciliation._contract(
        phase="turnover",
        output_root=tmp_path / "capture",
        signer=signer,
        population_root="a" * 64,
        request_count=1,
        delay=0,
        timeout=3,
        retries=0,
        permission_context_id="human-approved-fixture",
    )
    changed_contract_id = canonical_hash(changed_contract.semantic())
    changed_activity_id = canonical_hash(
        {
            "contract_id": changed_contract_id,
            "request_plan_hash": request_plan_hash,
        }
    )

    assert changed_contract.adapter_identity["implementation_root"] != (
        contract.adapter_identity["implementation_root"]
    )
    assert changed_contract_id != contract_id
    assert changed_activity_id != activity_id


def _write_security_snapshot_calendar(
    path: Path, *, count: int = 1_945
) -> list[str]:
    values = [
        (date(2012, 1, 4) + timedelta(days=offset)).strftime("%Y%m%d")
        for offset in range(max(count - 1, 0))
    ]
    if count:
        values.append("20191231")
    path.write_text(
        "".join(
            json.dumps({"trade_date": value, "is_open": True}) + "\n"
            for value in values
        ),
        encoding="utf-8",
    )
    return values


def _approve_security_snapshot_calendar(
    monkeypatch: pytest.MonkeyPatch, path: Path
) -> list[str]:
    values = _write_security_snapshot_calendar(path)
    monkeypatch.setattr(
        baostock_reconciliation,
        "SECURITY_SNAPSHOT_APPROVED_OPEN_DATE_ROOT",
        canonical_hash(values),
    )
    monkeypatch.setattr(
        baostock_reconciliation,
        "SECURITY_SNAPSHOT_APPROVED_POPULATION_ROOT",
        canonical_hash(["20111230", *values]),
    )
    monkeypatch.setattr(
        baostock_reconciliation,
        "BAOSTOCK_CALENDAR_SOURCE_SHA256",
        hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    return values


def _approve_baostock_securities_fixture(
    monkeypatch: pytest.MonkeyPatch, path: Path
) -> None:
    monkeypatch.setattr(
        baostock_reconciliation,
        "BAOSTOCK_SECURITIES_SOURCE_SHA256",
        hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def _narrow_security_snapshot_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        baostock_reconciliation, "SECURITY_SNAPSHOT_OPEN_DATE_COUNT", 0
    )
    monkeypatch.setattr(
        baostock_reconciliation, "SECURITY_SNAPSHOT_REQUEST_COUNT", 1
    )
    monkeypatch.setattr(
        baostock_reconciliation,
        "SECURITY_SNAPSHOT_APPROVED_OPEN_DATE_ROOT",
        canonical_hash([]),
    )
    monkeypatch.setattr(
        baostock_reconciliation,
        "SECURITY_SNAPSHOT_APPROVED_POPULATION_ROOT",
        canonical_hash(["20111230"]),
    )


def _security_snapshot_raw_payload(
    request: ProviderProbeRequest,
    *,
    rows: list[list[str]],
    wire_date: str | None = None,
    operation: str | None = None,
    parsed_rows: list[list[str]] | None = None,
    parsed_items: list[list[str]] | None = None,
    response_override: bytes | None = None,
) -> bytes:
    fields = ["code", "tradeStatus", "code_name"]
    return _security_or_history_payload(
        request,
        fields=fields,
        rows=rows,
        parsed_rows=parsed_rows,
        parsed_items=parsed_items,
        operation_override=operation,
        response_override=response_override,
        query_overrides={"date": wire_date} if wire_date else None,
    )


def _archive_security_snapshot_raw(
    root: Path,
    request: ProviderProbeRequest,
    *,
    rows: list[list[str]],
) -> dict[str, object]:
    payload = _security_snapshot_raw_payload(request, rows=rows)
    wrapper = {
        "schema_version": "free_provider_backfill_raw_envelope_v1",
        "request_id": request.request_id,
        "raw_payload_base64": base64.b64encode(payload).decode(),
        "raw_payload_sha256": hashlib.sha256(payload).hexdigest(),
    }
    relative = f"raw_envelopes/{request.request_id}.json"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(wrapper), encoding="utf-8")
    return {
        "raw_envelope_relative_path": relative,
        "terminal_state": "positive",
    }


def test_baostock_security_snapshot_plan_is_exact_and_content_addressed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calendar = tmp_path / "calendar.jsonl"
    _approve_security_snapshot_calendar(monkeypatch, calendar)

    population, requests = build_security_snapshot_plan(calendar)
    first_root = canonical_hash([request.semantic() for request in requests])
    calendar.write_text(
        "".join(reversed(calendar.read_text(encoding="utf-8").splitlines(True))),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        baostock_reconciliation,
        "BAOSTOCK_CALENDAR_SOURCE_SHA256",
        hashlib.sha256(calendar.read_bytes()).hexdigest(),
    )
    repeated_population, repeated_requests = build_security_snapshot_plan(calendar)

    assert len(population) == len(requests) == 1_946
    assert population[:2] == ["20111230", "20120104"]
    assert population[-1] == "20191231"
    assert repeated_population == population
    assert canonical_hash(
        [request.semantic() for request in repeated_requests]
    ) == first_root
    assert requests[0].url.endswith("date=2011-12-30")
    assert requests[0].metadata == {
        "case": "all_stock",
        "snapshot_query_date": "20111230",
        "provider_code_name_pit_proven": False,
        "alias_adjudicated": False,
        "usage": "provider_reconciliation_only",
    }
    assert requests[0].expected_terminal_states == ("positive",)
    assert "snapshot_query_date_bound" in requests[0].required_checks


def test_baostock_security_snapshot_plan_rejects_incomplete_calendar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calendar = tmp_path / "calendar.jsonl"
    _approve_security_snapshot_calendar(monkeypatch, calendar)
    _write_security_snapshot_calendar(calendar, count=1_944)
    monkeypatch.setattr(
        baostock_reconciliation,
        "BAOSTOCK_CALENDAR_SOURCE_SHA256",
        hashlib.sha256(calendar.read_bytes()).hexdigest(),
    )

    with pytest.raises(
        ValueError, match="baostock_security_snapshot_calendar_unexpected"
    ):
        build_security_snapshot_plan(calendar)


def test_baostock_security_snapshot_plan_rejects_wrong_same_count_calendar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calendar = tmp_path / "calendar.jsonl"
    values = _approve_security_snapshot_calendar(monkeypatch, calendar)
    values[100] = "20180101"
    calendar.write_text(
        "".join(
            json.dumps({"trade_date": value, "is_open": True}) + "\n"
            for value in values
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        baostock_reconciliation,
        "BAOSTOCK_CALENDAR_SOURCE_SHA256",
        hashlib.sha256(calendar.read_bytes()).hexdigest(),
    )

    with pytest.raises(
        ValueError, match="baostock_security_snapshot_calendar_unexpected"
    ):
        build_security_snapshot_plan(calendar)


def test_baostock_security_snapshot_transport_uses_exact_sdk_call_and_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calendar = tmp_path / "calendar.jsonl"
    _approve_security_snapshot_calendar(monkeypatch, calendar)
    _population, requests = build_security_snapshot_plan(calendar)
    request = requests[0]
    called: list[str] = []

    class Result:
        fields = ["code", "tradeStatus", "code_name"]
        error_code = "0"
        error_msg = ""

    class FakeBaostock:
        def query_all_stock(self, day: str) -> Result:
            called.append(day)
            return Result()

    transport = BaostockProbeTransport()
    transport._bs = FakeBaostock()
    transport._ensure_session = lambda _timeout: None  # type: ignore[method-assign]
    transport._collect = lambda _result: (  # type: ignore[method-assign]
        [["sh.600000", "1", "浦发银行"]],
        [{"page": 0, "row_count": 1, "provider_page_size": 2000}],
        True,
    )

    observation = transport(request, 3)

    assert called == ["2011-12-30"]
    assert observation.terminal_state == "positive"
    assert observation.checks["all_stock_fields_exact"] is True
    assert observation.checks["snapshot_query_date_bound"] is True
    assert observation.checks["unique_provider_code"] is True
    assert observation.checks["all_stock_values_nonempty"] is True
    assert observation.checks["trade_status_domain_valid"] is True


def test_baostock_security_snapshot_wire_binding_rejects_another_query_date(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calendar = tmp_path / "calendar.jsonl"
    _approve_security_snapshot_calendar(monkeypatch, calendar)
    _population, requests = build_security_snapshot_plan(calendar)
    request = requests[0]

    with pytest.raises(ValueError, match="request_binding_invalid"):
        _validate_baostock_wire_envelope(
            _security_snapshot_raw_payload(
                request,
                rows=[["sh.600000", "1", "浦发银行"]],
                wire_date="2012-01-04",
            ),
            expected_exchange_count=1,
            request=request.semantic(),
            terminal_state="positive",
        )


def test_baostock_security_snapshot_wire_binding_rejects_same_date_wrong_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calendar = tmp_path / "calendar.jsonl"
    _approve_security_snapshot_calendar(monkeypatch, calendar)
    _population, requests = build_security_snapshot_plan(calendar)
    request = requests[0]

    with pytest.raises(ValueError, match="message_type_invalid"):
        _validate_baostock_wire_envelope(
            _security_snapshot_raw_payload(
                request,
                rows=[["sh.600000", "1", "浦发银行"]],
                operation="query_hs300_stocks",
            ),
            expected_exchange_count=1,
            request=request.semantic(),
            terminal_state="positive",
        )


def test_baostock_security_snapshot_wire_replay_rejects_parsed_item_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calendar = tmp_path / "calendar.jsonl"
    _approve_security_snapshot_calendar(monkeypatch, calendar)
    _population, requests = build_security_snapshot_plan(calendar)
    request = requests[0]

    with pytest.raises(ValueError, match="logical_binding_invalid"):
        _validate_baostock_wire_envelope(
            _security_snapshot_raw_payload(
                request,
                rows=[["sh.600000", "1", "浦发银行"]],
                parsed_items=[["sz.000001", "1", "伪造解析行"]],
            ),
            expected_exchange_count=1,
            request=request.semantic(),
            terminal_state="positive",
        )


def test_baostock_wire_is_authoritative_when_pinned_sdk_removes_whitespace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calendar = tmp_path / "calendar.jsonl"
    _approve_security_snapshot_calendar(monkeypatch, calendar)
    _population, requests = build_security_snapshot_plan(calendar)
    request = requests[0]
    _narrow_security_snapshot_fixture(monkeypatch)
    wire_rows = [
        [
            "sh.000846",
            "1",
            "中证财通中国可持续发展100(ECPI ESG)指数",
        ],
        ["sz.000001", "1", "平安\u3000银行"],
    ]
    package_rows = [
        [
            "sh.000846",
            "1",
            "中证财通中国可持续发展100(ECPIESG)指数",
        ],
        ["sz.000001", "1", "平安银行"],
    ]
    raw = _security_snapshot_raw_payload(
        request,
        rows=wire_rows,
        parsed_rows=package_rows,
    )

    _validate_baostock_wire_envelope(
        raw,
        expected_exchange_count=1,
        request=request.semantic(),
        terminal_state="positive",
    )
    fields, authoritative_rows, diagnostics = (
        capture_backfill._baostock_logical_rows_with_reconciliation(raw)
    )
    wrapper = {
        "schema_version": "free_provider_backfill_raw_envelope_v1",
        "request_id": request.request_id,
        "raw_payload_base64": base64.b64encode(raw).decode(),
        "raw_payload_sha256": hashlib.sha256(raw).hexdigest(),
    }
    relative = f"raw_envelopes/{request.request_id}.json"
    wrapper_path = tmp_path / relative
    wrapper_path.parent.mkdir(parents=True)
    wrapper_path.write_text(json.dumps(wrapper), encoding="utf-8")
    normalize_security_snapshots(
        tmp_path,
        [request],
        {
            request.request_id: {
                "raw_envelope_relative_path": relative,
                "terminal_state": "positive",
            }
        },
    )
    normalized_rows = [
        json.loads(line)
        for line in (tmp_path / "normalized/security_snapshots.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    coverage = json.loads(
        (tmp_path / "normalized/security_snapshot_coverage.jsonl")
        .read_text(encoding="utf-8")
        .strip()
    )
    manifest = json.loads(
        (tmp_path / "normalized/normalized_manifest.json").read_text(
            encoding="utf-8"
        )
    )

    assert fields == ["code", "tradeStatus", "code_name"]
    assert authoritative_rows == wire_rows
    assert diagnostics == {
        "authoritative_value_source": "raw_wire_response_record",
        "package_parser_usage": "reconciliation_only",
        "package_parser_semantics": "baostock_0_9_3_setData_split_join",
        "package_parser_loss_detected": True,
        "package_parser_loss_row_count": 2,
        "package_parser_loss_cell_count": 2,
    }
    assert [row["provider_code_name"] for row in normalized_rows] == [
        wire_rows[0][2],
        wire_rows[1][2],
    ]
    assert coverage["package_parser_loss_detected"] is True
    assert coverage["package_parser_loss_row_count"] == 2
    assert coverage["package_parser_loss_cell_count"] == 2
    assert manifest["authoritative_value_source"] == "raw_wire_response_record"
    assert manifest["package_parser_usage"] == "reconciliation_only"
    assert manifest["package_parser_loss_request_count"] == 1
    assert manifest["package_parser_loss_row_count"] == 2
    assert manifest["package_parser_loss_cell_count"] == 2


def test_baostock_wire_accepts_exact_empty_terminal_record_slot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calendar = tmp_path / "calendar.jsonl"
    _approve_security_snapshot_calendar(monkeypatch, calendar)
    _population, requests = build_security_snapshot_plan(calendar)
    request = requests[0]
    query_date = str(request.metadata["snapshot_query_date"])
    fields = ["code", "tradeStatus", "code_name"]
    response = _baostock_uncompressed_response_frame(
        [
            "0",
            "success",
            "query_all_stock",
            "anonymous",
            "1",
            "2000",
            "",
            f"{query_date[:4]}-{query_date[4:6]}-{query_date[6:]}",
            ",".join(fields),
        ],
        message_type="36",
    )
    raw = _security_snapshot_raw_payload(
        request,
        rows=[],
        response_override=response,
    )

    _validate_baostock_wire_envelope(
        raw,
        expected_exchange_count=1,
        request=request.semantic(),
        terminal_state="empty",
    )
    observed_fields, rows, diagnostics = (
        capture_backfill._baostock_logical_rows_with_reconciliation(raw)
    )

    assert observed_fields == fields
    assert rows == []
    assert diagnostics["package_parser_loss_detected"] is False


def test_baostock_wire_rejects_whitespace_terminal_record_slot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calendar = tmp_path / "calendar.jsonl"
    _approve_security_snapshot_calendar(monkeypatch, calendar)
    _population, requests = build_security_snapshot_plan(calendar)
    request = requests[0]
    query_date = str(request.metadata["snapshot_query_date"])
    response = _baostock_uncompressed_response_frame(
        [
            "0",
            "success",
            "query_all_stock",
            "anonymous",
            "1",
            "2000",
            " ",
            f"{query_date[:4]}-{query_date[4:6]}-{query_date[6:]}",
            "code,tradeStatus,code_name",
        ],
        message_type="36",
    )

    with pytest.raises(ValueError, match="wire_records_invalid"):
        _validate_baostock_wire_envelope(
            _security_snapshot_raw_payload(
                request,
                rows=[],
                response_override=response,
            ),
            expected_exchange_count=1,
            request=request.semantic(),
            terminal_state="empty",
        )


def test_baostock_wire_rejects_non_reproducible_parsed_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calendar = tmp_path / "calendar.jsonl"
    _approve_security_snapshot_calendar(monkeypatch, calendar)
    _population, requests = build_security_snapshot_plan(calendar)
    request = requests[0]

    with pytest.raises(ValueError, match="logical_binding_invalid"):
        _validate_baostock_wire_envelope(
            _security_snapshot_raw_payload(
                request,
                rows=[["sh.600000", "1", "浦发银行"]],
                parsed_rows=[["sh.600000", "1", "伪造解析行"]],
            ),
            expected_exchange_count=1,
            request=request.semantic(),
            terminal_state="positive",
        )


def test_baostock_security_snapshot_wire_replay_rejects_bad_response_frame(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calendar = tmp_path / "calendar.jsonl"
    _approve_security_snapshot_calendar(monkeypatch, calendar)
    _population, requests = build_security_snapshot_plan(calendar)
    request = requests[0]

    with pytest.raises(ValueError, match="wire_response"):
        _validate_baostock_wire_envelope(
            _security_snapshot_raw_payload(
                request,
                rows=[["sh.600000", "1", "浦发银行"]],
                response_override=b"not-a-baostock-frame<![CDATA[]]>\n",
            ),
            expected_exchange_count=1,
            request=request.semantic(),
            terminal_state="positive",
        )


def test_baostock_security_snapshot_partial_request_set_cannot_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calendar = tmp_path / "calendar.jsonl"
    values = _approve_security_snapshot_calendar(monkeypatch, calendar)
    population, requests = build_security_snapshot_plan(calendar)
    signer = EphemeralReceiptSigner.generate()

    with pytest.raises(
        ValueError, match="baostock_security_snapshot_request_count_invalid"
    ):
        normalize_security_snapshots(tmp_path, requests[:1], {})
    with pytest.raises(
        ValueError, match="baostock_security_snapshot_contract_closure_invalid"
    ):
        baostock_reconciliation._contract(
            phase="security-snapshots",
            output_root=tmp_path / "capture",
            signer=signer,
            population_root=canonical_hash(population),
            request_count=1,
            delay=0,
            timeout=3,
            retries=0,
            permission_context_id="human-approved-fixture",
            calendar_path=calendar,
        )
    with pytest.raises(
        ValueError, match="baostock_security_snapshot_contract_closure_invalid"
    ):
        baostock_reconciliation._contract(
            phase="security-snapshots",
            output_root=tmp_path / "capture",
            signer=signer,
            population_root=canonical_hash(population),
            request_count=len(population) + 1,
            delay=0,
            timeout=3,
            retries=0,
            permission_context_id="human-approved-fixture",
            calendar_path=calendar,
        )
    assert len(values) == 1_945


def test_baostock_security_snapshot_signed_capture_replays_identically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calendar = tmp_path / "calendar.jsonl"
    _approve_security_snapshot_calendar(monkeypatch, calendar)
    population, requests = build_security_snapshot_plan(calendar)
    request = requests[0]
    _narrow_security_snapshot_fixture(monkeypatch)
    rows = [
        ["sz.000001", "1", "平安银行"],
        ["sh.600000", "1", "浦发银行"],
    ]
    raw = _security_snapshot_raw_payload(request, rows=rows)
    signer = EphemeralReceiptSigner.generate()
    contract = baostock_reconciliation._contract(
        phase="security-snapshots",
        output_root=tmp_path / "capture",
        signer=signer,
        population_root=canonical_hash([population[0]]),
        request_count=1,
        delay=0,
        timeout=3,
        retries=0,
        permission_context_id="human-approved-fixture",
        calendar_path=calendar,
    )

    def transport(
        captured_request: ProviderProbeRequest,
        _timeout: float,
    ) -> ProviderProbeObservation:
        assert captured_request == request
        return ProviderProbeObservation(
            terminal_state="positive",
            raw_payload=raw,
            row_count=2,
            status_code=0,
            checks={name: True for name in request.required_checks},
            transport_exchange_count=1,
        )

    published = run_free_provider_backfill(
        contract,
        [request],
        transport=transport,
        signer=signer,
        normalizer=normalize_security_snapshots,
        runtime_implementation_root=(
            baostock_reconciliation_implementation_root()
        ),
    )
    validated = validate_free_provider_backfill(published["manifest_path"])
    replayed, replay_root = replay_normalized_artifacts(
        validated["manifest_path"],
        normalizer=normalize_security_snapshots,
        required_roles=(
            "security_snapshot_reconciliation",
            "security_snapshot_coverage",
            "normalized_manifest",
        ),
    )
    replayed_rows = [
        json.loads(line)
        for line in replayed["security_snapshot_reconciliation"]
        .decode()
        .splitlines()
    ]
    manifest = json.loads(replayed["normalized_manifest"])

    assert validated["status"] == "succeeded"
    assert validated["publication_signature_verified"] is True
    assert [row["provider_code"] for row in replayed_rows] == [
        "sh.600000",
        "sz.000001",
    ]
    assert all(row["provider_code_name_pit_proven"] is False for row in replayed_rows)
    assert all(row["alias_adjudicated"] is False for row in replayed_rows)
    assert manifest["usage"] == "provider_reconciliation_only"
    assert manifest["raw_market_data_rewritten"] is False
    assert len(replay_root) == 64


def _publish_index_daily_capture(
    tmp_path: Path,
    signer: EphemeralReceiptSigner,
    *,
    delay: float = 1.0,
    timeout: float = 30.0,
    retries: int = 2,
) -> dict[str, object]:
    population, requests = build_index_daily_plan()
    request = requests[0]
    rows = [["2012-01-04", "sh.000300", *(["1"] * 9)]]
    raw = _security_or_history_payload(
        request, fields=BAOSTOCK_FIELDS.split(","), rows=rows
    )
    contract = baostock_reconciliation._contract(
        phase="index-daily",
        output_root=tmp_path / "index_daily",
        signer=signer,
        population_root=canonical_hash(population),
        request_count=1,
        delay=delay,
        timeout=timeout,
        retries=retries,
        permission_context_id=baostock_reconciliation.PERMISSION_CONTEXT,
    )

    def transport(
        captured_request: ProviderProbeRequest,
        _timeout: float,
    ) -> ProviderProbeObservation:
        assert captured_request == request
        return ProviderProbeObservation(
            terminal_state="positive",
            raw_payload=raw,
            row_count=1,
            status_code=0,
            checks={name: True for name in request.required_checks},
            transport_exchange_count=1,
        )

    return run_free_provider_backfill(
        contract,
        requests,
        transport=transport,
        signer=signer,
        normalizer=normalize_index_daily,
        runtime_implementation_root=(
            baostock_reconciliation_implementation_root()
        ),
    )


def test_baostock_specialized_validator_requires_approved_key_and_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    signer = EphemeralReceiptSigner.generate()
    monkeypatch.setattr(baostock_reconciliation, "SCOPE_ROOT", tmp_path)
    monkeypatch.setattr(
        baostock_reconciliation,
        "BAOSTOCK_APPROVED_CAPTURE_KEY_SHA256",
        capture_backfill._public_key_hash(signer.public_key_pem),
    )
    published = _publish_index_daily_capture(tmp_path, signer)

    validated = (
        baostock_reconciliation.validate_baostock_reconciliation_capture(
            published["manifest_path"], expected_phase="index-daily"
        )
    )

    assert validated["signed_integrity_verified"] is True
    assert validated["approved_capture_key_verified"] is True
    assert validated["normalized_replay_identical"] is True
    assert validated["provider_origin_attested"] is False
    assert validated["capture_runtime_isolation_verified"] is False
    assert validated["data_admission_eligible"] is False
    assert validated["downstream_ineligible"] is True
    assert validated["qualification"] == "quarantined_reconciliation_only"
    with pytest.raises(ValueError, match="phase_mismatch"):
        baostock_reconciliation.validate_baostock_reconciliation_capture(
            published["manifest_path"], expected_phase="turnover"
        )
    assert (
        baostock_reconciliation.main(
            [
                "--phase",
                "turnover",
                "--validate",
                str(published["manifest_path"]),
            ]
        )
        == 2
    )
    assert "phase_mismatch" in capsys.readouterr().out


def test_baostock_specialized_validator_rejects_ephemeral_self_authorization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signer = EphemeralReceiptSigner.generate()
    monkeypatch.setattr(baostock_reconciliation, "SCOPE_ROOT", tmp_path)
    published = _publish_index_daily_capture(tmp_path, signer)

    with pytest.raises(ValueError, match="capture_key_unauthorized"):
        baostock_reconciliation.validate_baostock_reconciliation_capture(
            published["manifest_path"], expected_phase="index-daily"
        )


def test_baostock_specialized_validator_separates_historical_integrity_from_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signer = EphemeralReceiptSigner.generate()
    monkeypatch.setattr(baostock_reconciliation, "SCOPE_ROOT", tmp_path)
    monkeypatch.setattr(
        baostock_reconciliation,
        "BAOSTOCK_APPROVED_CAPTURE_KEY_SHA256",
        capture_backfill._public_key_hash(signer.public_key_pem),
    )
    current_wire_root = baostock_reconciliation.baostock_wire_protocol_root
    monkeypatch.setattr(
        baostock_reconciliation,
        "baostock_wire_protocol_root",
        lambda: "e" * 64,
    )
    published = _publish_index_daily_capture(tmp_path, signer)
    monkeypatch.setattr(
        baostock_reconciliation,
        "baostock_wire_protocol_root",
        current_wire_root,
    )

    with pytest.raises(ValueError, match="current_replay_incompatible"):
        baostock_reconciliation.validate_baostock_reconciliation_capture(
            published["manifest_path"], expected_phase="index-daily"
        )
    inspected = (
        baostock_reconciliation.validate_baostock_reconciliation_capture(
            published["manifest_path"],
            expected_phase="index-daily",
            require_current_replay_compatible=False,
        )
    )

    assert inspected["signed_integrity_verified"] is True
    assert inspected["current_replay_compatible"] is False
    assert inspected["phase_contract_verified"] is True
    assert inspected["historical_contract_closure_verified"] is True
    assert inspected["operator_capture_contract_authorized"] is False
    assert inspected["normalized_replay_identical"] is False
    assert inspected["data_admission_eligible"] is False


def test_baostock_cli_blocks_drifted_approved_source_files(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    securities = tmp_path / "securities.jsonl"
    calendar = tmp_path / "calendar.jsonl"
    securities.write_text('{"drifted":true}\n', encoding="utf-8")
    calendar.write_text('{"drifted":true}\n', encoding="utf-8")

    result = baostock_reconciliation.main(
        [
            "--phase",
            "index-daily",
            "--plan-only",
            "--securities-path",
            str(securities),
            "--calendar-path",
            str(calendar),
        ]
    )

    assert result == 2
    assert "source_file_sha256_mismatch" in capsys.readouterr().out


def test_baostock_direct_plan_and_contract_recompute_source_hashes(
    tmp_path: Path,
) -> None:
    securities = tmp_path / "securities.jsonl"
    calendar = tmp_path / "calendar.jsonl"
    securities.write_text('{"drifted":true}\n', encoding="utf-8")
    calendar.write_text('{"drifted":true}\n', encoding="utf-8")
    signer = EphemeralReceiptSigner.generate()

    with pytest.raises(ValueError, match="source_file_sha256_mismatch"):
        build_turnover_plan(securities)
    with pytest.raises(ValueError, match="source_file_sha256_mismatch"):
        baostock_reconciliation._contract(
            phase="index-daily",
            output_root=tmp_path / "capture",
            signer=signer,
            population_root="a" * 64,
            request_count=1,
            delay=1.0,
            timeout=30.0,
            retries=2,
            permission_context_id=baostock_reconciliation.PERMISSION_CONTEXT,
            securities_path=securities,
            calendar_path=calendar,
        )


def test_baostock_historical_mode_cannot_bypass_budget_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signer = EphemeralReceiptSigner.generate()
    monkeypatch.setattr(baostock_reconciliation, "SCOPE_ROOT", tmp_path)
    monkeypatch.setattr(
        baostock_reconciliation,
        "BAOSTOCK_APPROVED_CAPTURE_KEY_SHA256",
        capture_backfill._public_key_hash(signer.public_key_pem),
    )
    current_wire_root = baostock_reconciliation.baostock_wire_protocol_root
    monkeypatch.setattr(
        baostock_reconciliation,
        "baostock_wire_protocol_root",
        lambda: "e" * 64,
    )
    published = _publish_index_daily_capture(
        tmp_path, signer, delay=0.0, timeout=3.0, retries=0
    )
    monkeypatch.setattr(
        baostock_reconciliation,
        "baostock_wire_protocol_root",
        current_wire_root,
    )

    with pytest.raises(ValueError, match="contract_closure_invalid"):
        baostock_reconciliation.validate_baostock_reconciliation_capture(
            published["manifest_path"],
            expected_phase="index-daily",
            require_current_replay_compatible=False,
        )


@pytest.mark.parametrize(
    ("rows", "reason"),
    [
        (
            [["sh.600000", "1", "浦发银行"], ["sh.600000", "1", "重复"]],
            "snapshot_provider_code_duplicate",
        ),
        ([["sh.600000", "2", "浦发银行"]], "snapshot_trade_status_invalid"),
        ([["sh.600000", "1", ""]], "snapshot_row_value_empty_or_width_invalid"),
    ],
)
def test_baostock_security_snapshot_normalizer_rejects_invalid_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rows: list[list[str]],
    reason: str,
) -> None:
    calendar = tmp_path / "calendar.jsonl"
    _approve_security_snapshot_calendar(monkeypatch, calendar)
    _population, requests = build_security_snapshot_plan(calendar)
    request = requests[0]
    _narrow_security_snapshot_fixture(monkeypatch)
    receipt = _archive_security_snapshot_raw(tmp_path, request, rows=rows)

    with pytest.raises(ValueError, match=reason):
        normalize_security_snapshots(
            tmp_path,
            [request],
            {request.request_id: receipt},
        )


def test_baostock_security_snapshot_normalizer_rejects_query_date_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calendar = tmp_path / "calendar.jsonl"
    _approve_security_snapshot_calendar(monkeypatch, calendar)
    _population, requests = build_security_snapshot_plan(calendar)
    _narrow_security_snapshot_fixture(monkeypatch)
    request = replace(
        requests[0],
        url=(
            "baostock://public-api.baostock.com/all_stock?date=2012-01-04"
        ),
    )
    receipt = _archive_security_snapshot_raw(
        tmp_path,
        request,
        rows=[["sh.600000", "1", "浦发银行"]],
    )

    with pytest.raises(ValueError, match="snapshot_query_date_binding_invalid"):
        normalize_security_snapshots(
            tmp_path,
            [request],
            {request.request_id: receipt},
        )

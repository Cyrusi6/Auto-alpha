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
    page_ones: dict[str, dict[str, Any]] = {}
    for validated in validated_rows:
        for request_id, payload in _captured_cninfo_pages(
            validated["manifest_path"]
        ).items():
            prior = page_ones.setdefault(request_id, payload)
            if prior != payload:
                raise ValueError(f"cninfo_discovery_duplicate_conflict:{request_id}")
    leaves = _cninfo_month_leaves(leaf_profile)
    requests: list[ProviderProbeRequest] = [_cninfo_org_map_request()]
    resolved: list[dict[str, Any]] = []
    for leaf in leaves:
        request_id = _cninfo_request_id(leaf, 1)
        capture = page_ones.get(request_id)
        if capture is None:
            raise ValueError(f"cninfo_discovery_leaf_missing:{leaf['leaf_id']}")
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
            _cninfo_leaf_request(resolved_leaf, page=page)
            for page in range(1, page_count + 1)
        )
    input_root = canonical_hash(
        {
            "leaf_profile": leaf_profile,
            "discovery_capture_content_hashes": sorted(
                str(row["content_hash"]) for row in validated_rows
            ),
        }
    )
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
    source_scope = dict(source_contract.get("scope") or {})
    source_adapter = str(
        (source_contract.get("adapter_identity") or {}).get("adapter") or ""
    )
    if (
        source_contract.get("provider") != "cninfo"
        or not source_adapter.startswith("cninfo_cninfo-inventory_")
        or source_scope.get("date_start") != "20120101"
        or source_scope.get("date_end") != "20191231"
        or source_scope.get("request_start") != "20110101"
        or source_scope.get("request_end") != "20191231"
    ):
        raise ValueError("cninfo_document_source_contract_invalid")
    source_signed = validated.get("publication_signature_verified") is True
    source_ancestry = {
        "source_capture_schema": validated.get("schema_version"),
        "source_generation_id": validated.get("generation_id"),
        "source_content_hash": validated.get("content_hash"),
        "source_contract_id": validated.get("contract_id"),
        "source_contract_content_hash": canonical_hash(source_contract),
        "source_provider": source_contract.get("provider"),
        "source_adapter": source_adapter,
        "source_scope": source_scope,
        "source_publication_signature_verified": source_signed,
        "source_normalized_artifacts_trusted": source_signed,
        "weak_source_ancestry": not source_signed,
    }
    replayed, replay_root = replay_normalized_artifacts(
        validated["manifest_path"],
        normalizer=normalize_cninfo_inventory,
        required_roles=("cninfo_announcement_inventory",),
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
            },
        )
        for announcement_id, row in sorted(unique.items())
    ]
    input_root = canonical_hash(
        {
            "capture_content_hash": validated["content_hash"],
            "normalized_replay_root": replay_root,
            "source_ancestry": source_ancestry,
            "implementation_root": _implementation_root(),
        }
    )
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
    return _normalize_cninfo_pages(
        run_root,
        requests,
        terminal,
        require_full_page_chains=True,
    )


def normalize_cninfo_documents(
    run_root: Path,
    requests: Sequence[ProviderProbeRequest],
    terminal: Mapping[str, Mapping[str, Any]],
) -> Sequence[NormalizedArtifact]:
    output = run_root / "normalized"
    output.mkdir(exist_ok=True)
    index_path = output / "document_index.jsonl"
    ancestry_rows = [request.metadata.get("source_ancestry") for request in requests]
    source_ancestry = ancestry_rows[0] if ancestry_rows else None
    if any(row != source_ancestry for row in ancestry_rows):
        raise ValueError("cninfo_document_source_ancestry_mixed")
    if source_ancestry is not None and (
        not isinstance(source_ancestry, Mapping)
        or source_ancestry.get("source_provider") != "cninfo"
        or not isinstance(source_ancestry.get("weak_source_ancestry"), bool)
        or source_ancestry.get("source_publication_signature_verified")
        is not (not source_ancestry["weak_source_ancestry"])
        or source_ancestry.get("source_normalized_artifacts_trusted")
        is not (not source_ancestry["weak_source_ancestry"])
    ):
        raise ValueError("cninfo_document_source_ancestry_invalid")
    count = 0
    with index_path.open("wb") as handle:
        for request in requests:
            receipt = terminal[request.request_id]
            wrapper = read_json(run_root / str(receipt["raw_envelope_relative_path"]))
            official = json.loads(
                base64.b64decode(wrapper["raw_payload_base64"], validate=True)
            )
            body = base64.b64decode(str(official.get("body_base64") or ""), validate=True)
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
                official.get("schema_version")
                != "official_http_probe_envelope_v1"
                or official.get("method") != "GET"
                or official.get("redirect_followed") is not False
                or str(official.get("url") or "") != request.url
                or str(official.get("body_sha256") or "")
                != hashlib.sha256(body).hexdigest()
                or document_format is None
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
) -> Sequence[NormalizedArtifact]:
    output = run_root / "normalized"
    output.mkdir(exist_ok=True)
    inventory_path = output / "announcement_inventory.jsonl"
    coverage_path = output / "page_coverage.jsonl"
    conflicts_path = output / "conflicts.jsonl"
    pages_by_leaf: dict[str, list[tuple[ProviderProbeRequest, dict[str, Any], str]]] = defaultdict(list)
    org_map_count = 0
    for request in requests:
        wrapper = read_json(
            run_root / str(terminal[request.request_id]["raw_envelope_relative_path"])
        )
        official = json.loads(base64.b64decode(wrapper["raw_payload_base64"], validate=True))
        body = json.loads(base64.b64decode(official["body_base64"], validate=True))
        if request.metadata.get("case") == "cninfo_org_map":
            rows = body.get("stockList") if isinstance(body, Mapping) else None
            if not isinstance(rows, list):
                rows = body if isinstance(body, list) else []
            org_map_count = len(rows)
            continue
        leaf_id = str(request.metadata.get("leaf_id") or "")
        pages_by_leaf[leaf_id].append((request, body, wrapper["raw_payload_sha256"]))

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
        "schema_version": "cninfo_announcement_inventory_normalization_v1",
        "require_full_page_chains": require_full_page_chains,
        "leaf_count": len(pages_by_leaf),
        "org_map_count": org_map_count,
        "announcement_count": len(inventory),
        "conflict_count": len(conflicts),
        "all_page_chains_valid": require_full_page_chains and not conflicts,
        "announcement_inventory_sha256": sha256_file(inventory_path),
        "page_coverage_sha256": sha256_file(coverage_path),
        "conflicts_sha256": sha256_file(conflicts_path),
        "pit_field_parsing_complete": False,
    }
    manifest["content_hash"] = canonical_hash(manifest)
    _atomic_json(manifest_path, manifest)
    return (
        NormalizedArtifact("cninfo_announcement_inventory", "normalized/announcement_inventory.jsonl", len(inventory)),
        NormalizedArtifact("cninfo_page_coverage", "normalized/page_coverage.jsonl", len(coverage_rows)),
        NormalizedArtifact("conflicts", "normalized/conflicts.jsonl", len(conflicts)),
        NormalizedArtifact("normalized_manifest", "normalized/normalized_manifest.json", 1),
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
                leaves.append(
                    {
                        "leaf_id": leaf_id,
                        "kind": kind,
                        "category": category,
                        "column": column,
                        "plate": plate,
                        "date_start": start,
                        "date_end": end,
                    }
                )
    return leaves


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
        match = re.search(rb"startxref\s+(\d+)\s+%%EOF\s*$", trailer)
        if match is None:
            return False
        xref_offset = int(match.group(1))
        return 0 <= xref_offset < len(stripped)
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
    for row in rows:
        announcement_id = str(row.get("announcement_id") or "")
        observed_date = _announcement_date(row.get("announcement_time"))
        matched_months = {
            str(value).rsplit("_", 1)[-1]
            for value in row.get("matched_leaves") or ()
            if re.fullmatch(r".*_\d{6}", str(value))
        }
        if (
            not announcement_id
            or observed_date is None
            or observed_date[:7].replace("-", "") not in matched_months
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
        if not request or (request.get("metadata") or {}).get("case") != "cninfo_list":
            continue
        official = json.loads(base64.b64decode(wrapper["raw_payload_base64"], validate=True))
        pages[request_id] = json.loads(
            base64.b64decode(official["body_base64"], validate=True)
        )
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
) -> FreeProviderBackfillContract:
    adapter_identity = {
        "adapter": f"cninfo_{phase}_signed_http_capture_v1",
        "http": "python_urllib_no_redirect_v1",
        "implementation_root": _implementation_root(),
        "leaf_profile": leaf_profile,
    }
    if input_capture_hash:
        adapter_identity["input_capture_content_hash"] = input_capture_hash
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
            "month_leaf_plan": inspect.getsource(_cninfo_month_leaves)
            + inspect.getsource(_cninfo_leaf_request),
            "document_plan": inspect.getsource(build_cninfo_document_plan),
            "page_normalizer": inspect.getsource(_normalize_cninfo_pages),
            "document_normalizer": inspect.getsource(normalize_cninfo_documents),
            "document_transport": inspect.getsource(CNINFODocumentTransport),
            "document_url": inspect.getsource(_cninfo_document_url),
            "document_format": inspect.getsource(_document_format),
            "document_block_detection": inspect.getsource(_document_block_reason),
            "document_content_length": inspect.getsource(_content_length_matches),
            "document_content_type": inspect.getsource(_content_type_compatible),
            "document_size": inspect.getsource(_adjunct_size_reasonable),
            "document_structure": inspect.getsource(_document_structure_valid),
            "announcement_date": inspect.getsource(_announcement_date),
            "inventory_date_validator": inspect.getsource(
                _validate_inventory_announcement_dates
            ),
            "official_http_transport": inspect.getsource(OfficialHttpProbeTransport),
            "official_http_transport_module_sha256": sha256_file(
                Path(run_provider_probe_module.__file__)
            ),
            "cninfo_leaf_profiles": {
                name: [list(row) for row in rows]
                for name, rows in sorted(CNINFO_LEAF_PROFILES.items())
            },
            "cninfo_document_body_max_bytes": CNINFO_DOCUMENT_BODY_MAX_BYTES,
        }
    )


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
            payload = validate_free_provider_backfill(args.validate)
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
            else "inventory_bound"
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
    transport = (
        CNINFODocumentTransport(
            minimum_delay_seconds=args.minimum_delay_seconds
        )
        if args.phase == "cninfo-documents"
        else OfficialHttpProbeTransport(
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

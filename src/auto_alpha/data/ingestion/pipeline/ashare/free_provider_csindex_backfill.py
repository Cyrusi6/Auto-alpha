"""Signed CSI official announcement inventory and detail acquisition."""

from __future__ import annotations

import argparse
import base64
import calendar
import inspect
import json
import math
import os
from collections import defaultdict
from datetime import date
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
from .provider_probe import ProviderProbeObservation, ProviderProbeRequest
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
        official = json.loads(observation.raw_payload)
        payload = json.loads(
            base64.b64decode(str(official.get("body_base64") or ""), validate=True)
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
    requests = [_filter_request()]
    requests.extend(_list_request(leaf, page=1) for leaf in leaves)
    return leaves, requests


def build_csindex_inventory_plan(
    discovery_capture: str | Path,
) -> tuple[list[dict[str, Any]], list[ProviderProbeRequest], str]:
    validated = validate_free_provider_backfill(discovery_capture)
    if validated.get("status") != "succeeded":
        raise ValueError("csindex_discovery_capture_blocked")
    pages = _captured_list_pages(validated["manifest_path"])
    leaves = _month_leaves()
    resolved: list[dict[str, Any]] = []
    requests = [_filter_request()]
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
        requests.extend(
            _list_request(resolved_leaf, page=page)
            for page in range(1, page_count + 1)
        )
    return resolved, requests, str(validated["content_hash"])


def build_csindex_detail_plan(
    inventory_capture: str | Path,
) -> tuple[list[dict[str, Any]], list[ProviderProbeRequest], str]:
    validated = validate_free_provider_backfill(inventory_capture)
    if validated.get("status") != "succeeded":
        raise ValueError("csindex_inventory_capture_blocked")
    replayed, replay_root = replay_normalized_artifacts(
        validated["manifest_path"],
        normalizer=normalize_csindex_inventory,
        required_roles=("csindex_announcement_inventory",),
    )
    rows = [
        json.loads(line)
        for line in replayed["csindex_announcement_inventory"].decode("utf-8").splitlines()
        if line.strip()
    ]
    requests = [
        ProviderProbeRequest(
            request_id=f"csindex_detail_{row['announcement_id']}",
            provider="csindex",
            endpoint="index_rebalance_announcement_detail",
            method="GET",
            url=(
                "https://www.csindex.com.cn/csindex-home/announcement/"
                f"queryAnnouncementById?id={row['announcement_id']}"
            ),
            headers={"Referer": "https://www.csindex.com.cn/", "User-Agent": USER_AGENT},
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
            },
        )
        for row in rows
    ]
    input_root = canonical_hash(
        {
            "capture_content_hash": validated["content_hash"],
            "normalized_replay_root": replay_root,
            "implementation_root": _implementation_root(),
        }
    )
    return rows, requests, input_root


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
            official = json.loads(base64.b64decode(wrapper["raw_payload_base64"], validate=True))
            provider = json.loads(base64.b64decode(official["body_base64"], validate=True))
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
        "schema_version": "csindex_detail_normalization_v1",
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
    }
    manifest["content_hash"] = canonical_hash(manifest)
    _atomic_json(manifest_path, manifest)
    return (
        NormalizedArtifact("csindex_announcement_details", "normalized/announcement_details.jsonl", detail_count),
        NormalizedArtifact("csi300_candidate_announcements", "normalized/csi300_candidate_announcements.jsonl", candidate_count),
        NormalizedArtifact("conflicts", "normalized/conflicts.jsonl", 0),
        NormalizedArtifact("normalized_manifest", "normalized/normalized_manifest.json", 1),
    )


def _normalize_list_pages(
    run_root: Path,
    requests: Sequence[ProviderProbeRequest],
    terminal: Mapping[str, Mapping[str, Any]],
    *,
    require_full_page_chains: bool,
) -> Sequence[NormalizedArtifact]:
    output = run_root / "normalized"
    output.mkdir(exist_ok=True)
    pages_by_leaf: dict[str, list[tuple[ProviderProbeRequest, dict[str, Any], str]]] = defaultdict(list)
    filter_captured = False
    for request in requests:
        wrapper = read_json(run_root / str(terminal[request.request_id]["raw_envelope_relative_path"]))
        official = json.loads(base64.b64decode(wrapper["raw_payload_base64"], validate=True))
        body = json.loads(base64.b64decode(official["body_base64"], validate=True))
        if request.metadata.get("case") == "csindex_filter":
            filter_captured = True
            continue
        pages_by_leaf[str(request.metadata["leaf_id"])].append(
            (request, body, wrapper["raw_payload_sha256"])
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
        "schema_version": "csindex_announcement_inventory_normalization_v1",
        "require_full_page_chains": require_full_page_chains,
        "filter_topics_captured": filter_captured,
        "leaf_count": len(pages_by_leaf),
        "announcement_count": len(inventory),
        "conflict_count": len(conflicts),
        "all_page_chains_valid": require_full_page_chains and not conflicts,
        "inventory_sha256": sha256_file(inventory_path),
        "coverage_sha256": sha256_file(coverage_path),
    }
    manifest["content_hash"] = canonical_hash(manifest)
    _atomic_json(manifest_path, manifest)
    return (
        NormalizedArtifact("csindex_announcement_inventory", "normalized/announcement_inventory.jsonl", len(inventory)),
        NormalizedArtifact("csindex_page_coverage", "normalized/page_coverage.jsonl", len(coverage)),
        NormalizedArtifact("conflicts", "normalized/conflicts.jsonl", len(conflicts)),
        NormalizedArtifact("normalized_manifest", "normalized/normalized_manifest.json", 1),
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
        official = json.loads(base64.b64decode(wrapper["raw_payload_base64"], validate=True))
        pages[request_id] = json.loads(base64.b64decode(official["body_base64"], validate=True))
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
    retries: int,
    permission_context_id: str,
) -> FreeProviderBackfillContract:
    identity = {
        "adapter": f"csindex_{phase}_signed_http_capture_v1",
        "implementation_root": _implementation_root(),
        "http": "python_urllib_no_redirect_v1",
    }
    if input_capture_hash:
        identity["input_capture_content_hash"] = input_capture_hash
    return FreeProviderBackfillContract(
        activity_name=f"free_domestic_csindex_{phase}_2011_2019_v1",
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
        allowed_hosts=("www.csindex.com.cn",),
        budget=BackfillResourceBudget(
            max_requests=request_count * (retries + 1),
            max_wire_exchanges=request_count * (retries + 1),
            max_response_bytes=64 * 1024 * 1024,
            max_total_response_bytes=8 * 1024 * 1024 * 1024,
            timeout_seconds=timeout,
            minimum_delay_seconds=delay,
            max_retries=retries,
        ),
        adapter_identity=identity,
    )


def _implementation_root() -> str:
    return canonical_hash(
        {
            "discovery_plan": inspect.getsource(build_csindex_discovery_plan),
            "inventory_plan": inspect.getsource(build_csindex_inventory_plan),
            "detail_plan": inspect.getsource(build_csindex_detail_plan),
            "list_normalizer": inspect.getsource(_normalize_list_pages),
            "detail_normalizer": inspect.getsource(normalize_csindex_details),
            "transport": inspect.getsource(CSIndexBackfillTransport),
            "official_http_transport": inspect.getsource(OfficialHttpProbeTransport),
        }
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run CSI official archive backfill.")
    parser.add_argument(
        "--phase",
        choices=("csindex-discovery", "csindex-inventory", "csindex-details"),
        required=True,
    )
    parser.add_argument("--leaf-id", action="append")
    parser.add_argument("--input-capture")
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
        payload = validate_free_provider_backfill(args.validate)
        print(_render(payload, pretty=args.pretty))
        return 0
    input_hash: str | None = None
    if args.phase == "csindex-discovery":
        population, requests = build_csindex_discovery_plan(args.leaf_id)
        normalizer = normalize_csindex_discovery
        default_delay = 5.0
    elif args.phase == "csindex-inventory":
        if not args.input_capture:
            raise SystemExit("--input-capture is required for csindex-inventory")
        population, requests, input_hash = build_csindex_inventory_plan(args.input_capture)
        normalizer = normalize_csindex_inventory
        default_delay = 5.0
    else:
        if not args.input_capture:
            raise SystemExit("--input-capture is required for csindex-details")
        population, requests, input_hash = build_csindex_detail_plan(args.input_capture)
        normalizer = normalize_csindex_details
        default_delay = 7.5
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
        transport=CSIndexBackfillTransport(minimum_delay_seconds=delay),
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

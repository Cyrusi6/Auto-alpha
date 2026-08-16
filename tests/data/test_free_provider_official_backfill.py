from __future__ import annotations

import base64
import hashlib
import io
import json
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from auto_alpha.data.ingestion.pipeline.ashare import (
    free_provider_baostock_reconciliation as baostock_reconciliation,
    free_provider_csindex_backfill as csindex_backfill,
)
from auto_alpha.data.ingestion.pipeline.ashare.free_provider_baostock_reconciliation import (
    _implementation_root as baostock_reconciliation_implementation_root,
    build_adjustment_plan,
    build_dividend_plan,
    build_index_daily_plan,
    build_security_basic_plan,
    build_turnover_plan,
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
    normalize_csindex_attachments,
    normalize_csindex_discovery,
)
from auto_alpha.data.ingestion.pipeline.ashare.free_provider_http_backfill import (
    _cninfo_document_url,
    _adjunct_size_reasonable,
    _content_length_matches,
    _content_type_compatible,
    _document_block_reason,
    _document_format,
    _document_structure_valid,
    build_cninfo_discovery_plan,
    normalize_cninfo_discovery,
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


def _attachment_source_ancestry(*, weak: bool = False) -> dict[str, object]:
    return {
        "source_capture_schema": "free_provider_backfill_capture_v2",
        "source_generation_id": "fixture-details",
        "source_content_hash": "a" * 64,
        "source_contract_id": "b" * 64,
        "source_contract_content_hash": "c" * 64,
        "source_provider": "csindex",
        "source_adapter": "csindex_csindex-details_signed_http_capture_v1",
        "source_scope": {
            "date_start": "20120101",
            "date_end": "20191231",
            "request_start": "20110101",
            "request_end": "20191231",
        },
        "source_publication_signature_verified": not weak,
        "source_normalized_artifacts_trusted": not weak,
        "weak_source_ancestry": weak,
    }


def _official_wrapper(body: dict, request_id: str) -> tuple[dict, dict]:
    provider_body = json.dumps(body, ensure_ascii=False, sort_keys=True).encode()
    official = json.dumps(
        {
            "schema_version": "official_http_probe_envelope_v1",
            "status_code": 200,
            "body_base64": base64.b64encode(provider_body).decode(),
        },
        sort_keys=True,
    ).encode()
    wrapper = {
        "schema_version": "free_provider_backfill_raw_envelope_v1",
        "request_id": request_id,
        "raw_payload_base64": base64.b64encode(official).decode(),
        "raw_payload_sha256": __import__("hashlib").sha256(official).hexdigest(),
    }
    terminal = {
        "raw_envelope_relative_path": f"raw_envelopes/{request_id}.json",
        "terminal_state": "positive",
    }
    return wrapper, terminal


def _baostock_wrapper(
    *, request_id: str, fields: list[str], rows: list[list[str]]
) -> tuple[dict, dict]:
    raw = {
        "parsed": {
            "fields": fields,
            "items": rows,
            "canonical_logical_payload_sha256": canonical_hash(
                {"fields": fields, "rows": rows}
            ),
        },
        "wire_exchanges": [],
    }
    payload = json.dumps(raw, sort_keys=True).encode()
    wrapper = {
        "schema_version": "free_provider_backfill_raw_envelope_v1",
        "request_id": request_id,
        "raw_payload_base64": base64.b64encode(payload).decode(),
        "raw_payload_sha256": __import__("hashlib").sha256(payload).hexdigest(),
    }
    terminal = {
        "raw_envelope_relative_path": f"raw_envelopes/{request_id}.json",
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
        {"status_code": 200, "body_base64": base64.b64encode(provider_body).decode()}
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
        wrapper, receipt = _official_wrapper(body, request.request_id)
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


def test_csindex_discovery_normalizer_detects_canonical_list_identity(tmp_path: Path) -> None:
    _leaves, requests = build_csindex_discovery_plan(["index_rebalance_201201"])
    filter_body = {"data": [{"key": "index_rebalance"}]}
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
        "success": True,
        "code": "200",
    }
    terminal = {}
    for request, body in zip(requests, (filter_body, list_body)):
        wrapper, receipt = _official_wrapper(body, request.request_id)
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
    requests = _attachment_requests(
        population, source_ancestry=_attachment_source_ancestry()
    )
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
    request = _attachment_requests(
        population, source_ancestry=_attachment_source_ancestry()
    )[0]
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
    assert redirected_observation.checks["redirect_not_followed"] is False

    wrapper = {
        "schema_version": "free_provider_backfill_raw_envelope_v1",
        "request_id": request.request_id,
        "raw_payload_base64": base64.b64encode(official).decode(),
        "raw_payload_sha256": hashlib.sha256(official).hexdigest(),
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
    requests = _attachment_requests(
        population, source_ancestry=_attachment_source_ancestry()
    )
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
    signer = EphemeralReceiptSigner.generate()
    contract = csindex_contract(
        phase="csindex-attachments",
        output_root=tmp_path / "capture",
        signer=signer,
        population_root=canonical_hash(population),
        request_count=1,
        input_capture_hash="a" * 64,
        delay=0,
        timeout=3,
        retries=0,
        permission_context_id="human-approved-fixture",
        allowed_hosts=("oss-ch.csindex.com.cn",),
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
    request = _attachment_requests(
        population, source_ancestry=_attachment_source_ancestry()
    )[0]
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
    request = _attachment_requests(
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
        ],
        source_ancestry=_attachment_source_ancestry(),
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
        wrapper, receipt = _official_wrapper(body, request.request_id)
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


def test_baostock_wire_validator_binds_actual_protocol_request_bytes() -> None:
    _population, requests = build_index_daily_plan()
    request = requests[0].semantic()
    wire_request = (
        "00.9.30\x01000\x01000\x01query_history_k_data_plus\x01"
        "sh.000300\x012012-01-01\x012019-12-31\x01"
        + BAOSTOCK_FIELDS
        + "\n"
    ).encode()
    wire_response = b"response<![CDATA[]]>\n"
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
    envelope = {
        "schema_version": "baostock_wire_probe_envelope_v1",
        "package_distribution_version": "0.9.3",
        "client_protocol_version": "00.9.30",
        "wire_exchanges": [exchange],
    }

    _validate_baostock_wire_envelope(
        json.dumps(envelope).encode(),
        expected_exchange_count=1,
        request=request,
        terminal_state="positive",
    )
    exchange["wire_request_base64"] = base64.b64encode(
        wire_request.replace(b"sh.000300", b"sz.000001")
    ).decode()
    with pytest.raises(ValueError, match="wire_closure_invalid"):
        _validate_baostock_wire_envelope(
            json.dumps(envelope).encode(),
            expected_exchange_count=1,
            request=request,
            terminal_state="positive",
        )


def test_baostock_normalizer_rejects_archived_rows_for_another_code(
    tmp_path: Path,
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
    _population, requests = build_turnover_plan(securities)
    request = requests[0]
    wrapper, receipt = _baostock_wrapper(
        request_id=request.request_id,
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

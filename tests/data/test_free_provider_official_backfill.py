from __future__ import annotations

import base64
import hashlib
import json
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from auto_alpha.data.ingestion.pipeline.ashare import (
    free_provider_baostock_reconciliation as baostock_reconciliation,
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
    _validate_baostock_wire_envelope,
)
from auto_alpha.data.ingestion.pipeline.ashare.free_provider_csindex_backfill import (
    CSIndexBackfillTransport,
    _strict_iso_date,
    build_csindex_discovery_plan,
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
from auto_alpha.data.ingestion.pipeline.ashare.provider_probe import ProviderProbeObservation
from auto_alpha.data.ingestion.pipeline.ashare.run_provider_probe import (
    BAOSTOCK_FIELDS,
    BaostockProbeTransport,
    OfficialHttpProbeTransport,
)
from auto_alpha.platform.artifacts.storage import canonical_hash


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

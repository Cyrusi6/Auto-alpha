"""CLI and provider adapters for the locked free-source capability probe."""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import importlib.metadata
import json
import random
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from auto_alpha.platform.artifacts.storage import canonical_hash, read_json, sha256_file

from . import provider_probe as provider_probe_module
from .provider_probe import (
    ProviderProbeContract,
    ProviderProbeObservation,
    ProviderProbeRequest,
    run_provider_capability_probe,
    validate_provider_capability_probe,
)


DEFAULT_OUTPUT_ROOT = Path(
    "/home/lijunsi/data/auto-alpha/ashare_lake/provider_probes/"
    "free_domestic_missing_data_v1"
)
USER_AGENT = "Auto-alpha/0.1 bounded-provider-probe (research evidence; contact local operator)"
BAOSTOCK_FIELDS = (
    "date,code,open,high,low,close,preclose,volume,amount,tradestatus,isST"
)
BAOSTOCK_MAX_PAGES_PER_REQUEST = 4
BAOSTOCK_STABLE_CODES = frozenset(
    {
        "sh.600000",
        "sh.600519",
        "sh.601318",
        "sz.000001",
        "sz.000002",
        "sz.000063",
        "sz.000651",
        "sz.000858",
    }
)
BAOSTOCK_SAMPLE_CODES = (
    "sh.600000",
    "sh.600519",
    "sh.601318",
    "sz.000001",
    "sz.000002",
    "sz.000063",
    "sz.000651",
    "sz.000858",
    "sh.688001",
    "sz.300750",
    "sh.600145",
    "sh.600656",
    "sh.600680",
    "sh.600747",
    "sh.601268",
    "sz.000033",
    "sz.000511",
    "sz.000693",
    "sz.000787",
    "sz.000805",
    "sh.600005",
    "sh.600102",
    "sh.600263",
    "sh.600832",
    "sh.601299",
    "sz.000024",
    "sz.000418",
    "sz.000527",
    "sz.002680",
    "sz.300372",
)


def baostock_distribution_record_root() -> str:
    """Bind installed Baostock package bytes through its wheel RECORD."""

    try:
        distribution = importlib.metadata.distribution("baostock")
    except importlib.metadata.PackageNotFoundError:
        return canonical_hash({"distribution": "baostock", "status": "not_installed"})
    record = distribution.read_text("RECORD")
    if distribution.version != "0.9.3" or not record:
        raise RuntimeError("baostock_distribution_record_unavailable_or_unpinned")
    return canonical_hash(
        {
            "distribution": "baostock",
            "version": distribution.version,
            "record": record.splitlines(),
        }
    )


CNINFO_QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
CNINFO_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Referer": "https://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
    "User-Agent": USER_AGENT,
    "X-Requested-With": "XMLHttpRequest",
}
CSINDEX_LIST_URL = (
    "https://www.csindex.com.cn/csindex-home/announcement/queryAnnouncementByVo"
)
CSINDEX_HEADERS = {
    "Content-Type": "application/json; charset=UTF-8",
    "Referer": "https://www.csindex.com.cn/",
    "User-Agent": USER_AGENT,
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a bounded, non-admissible free A-share provider probe."
    )
    parser.add_argument(
        "--provider",
        choices=["all", "baostock", "csindex", "cninfo"],
        default="all",
    )
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument("--max-requests", type=int, default=160)
    parser.add_argument("--official-delay-seconds", type=float, default=1.25)
    parser.add_argument("--validate")
    parser.add_argument(
        "--replay-manifest",
        help="Reclassify an archived probe conservatively without network access",
    )
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.validate:
        try:
            payload = validate_provider_capability_probe(args.validate)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(json.dumps({"status": "blocked", "reason": str(exc)}, sort_keys=True))
            return 1
        print(_render(_public_summary(payload), pretty=args.pretty))
        return 0

    requests = build_locked_probe_requests(args.provider)
    if args.plan_only:
        payload = {
            "schema_version": "free_domestic_provider_probe_plan_preview_v1",
            "mode": "bounded_provider_probe",
            "provider": args.provider,
            "request_count": len(requests),
            "request_plan_hash": canonical_hash([request.semantic() for request in requests]),
            "endpoints": sorted({request.endpoint for request in requests}),
            "data_admission_eligible": False,
        }
        print(_render(payload, pretty=args.pretty))
        return 0
    if not args.allow_network and not args.replay_manifest:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "reason": "provider_probe_network_authority_missing",
                    "hint": "review the finite plan with --plan-only, then pass --allow-network",
                },
                sort_keys=True,
            )
        )
        return 2
    if args.max_requests < len(requests):
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "reason": "provider_probe_request_budget_too_small",
                    "required": len(requests),
                    "configured": args.max_requests,
                },
                sort_keys=True,
            )
        )
        return 2

    adapter_identity = {
        "probe_adapter": "free_domestic_provider_probe_v1",
        "probe_implementation_root": _probe_implementation_root(),
        "baostock_distribution": "0.9.3",
        "baostock_client": "00.9.30",
        "http": "python_urllib_no_redirect_v1",
    }
    if args.replay_manifest:
        replay_source = validate_provider_capability_probe(args.replay_manifest)
        adapter_identity["replay_source_content_hash"] = str(
            replay_source["content_hash"]
        )
    contract = ProviderProbeContract(
        probe_id=f"free_domestic_missing_data_v1:{args.provider}",
        output_root=args.output_root,
        allowed_hosts=(
            "public-api.baostock.com",
            "www.csindex.com.cn",
            "www.cninfo.com.cn",
            "static.cninfo.com.cn",
        ),
        max_requests=args.max_requests,
        timeout_seconds=args.timeout_seconds,
        max_response_bytes=64 * 1024 * 1024,
        max_total_response_bytes=256 * 1024 * 1024,
        max_wire_exchanges=256,
        adapter_identity=adapter_identity,
    )
    if args.replay_manifest:
        transport: Any = ArchivedProbeReplayTransport(args.replay_manifest)
    else:
        transport = CompositeProbeTransport(
            official_delay_seconds=args.official_delay_seconds,
        )
    try:
        result = run_provider_capability_probe(
            contract,
            requests,
            transport=transport,
        )
    finally:
        close = getattr(transport, "close", None)
        if close is not None:
            close()
    print(_render(_public_summary(result), pretty=args.pretty))
    return 0 if result.get("status") == "succeeded" else 1


def build_locked_probe_requests(provider: str = "all") -> list[ProviderProbeRequest]:
    """Return the exact finite request plan; no target or research result is read."""

    selected = {"baostock", "csindex", "cninfo"} if provider == "all" else {provider}
    requests: list[ProviderProbeRequest] = []
    if "baostock" in selected:
        requests.extend(_baostock_requests())
    if "cninfo" in selected:
        requests.extend(_cninfo_requests())
    if "csindex" in selected:
        # CSI is deliberately last: its WAF must never cause tight retries or
        # prevent already planned official CNINFO evidence from terminating.
        requests.extend(_csindex_requests())
    return requests


class CompositeProbeTransport:
    def __init__(self, *, official_delay_seconds: float) -> None:
        self.baostock = BaostockProbeTransport()
        self.http = OfficialHttpProbeTransport(
            minimum_delay_seconds=official_delay_seconds
        )

    def __call__(
        self,
        request: ProviderProbeRequest,
        timeout_seconds: float,
    ) -> ProviderProbeObservation:
        if request.provider == "baostock":
            return self.baostock(request, timeout_seconds)
        return self.http(request, timeout_seconds)

    def close(self) -> None:
        self.baostock.close()

    def restore(
        self,
        request: ProviderProbeRequest,
        record: Mapping[str, Any],
    ) -> None:
        if request.provider == "baostock":
            self.baostock.restore(request, record)
        else:
            self.http.restore(request, record)


class ArchivedProbeReplayTransport:
    """Replay exact archived bytes; only conservative WAF reclassification is allowed."""

    def __init__(
        self,
        manifest: str | Path,
        *,
        require_current_implementation: bool = True,
    ) -> None:
        validated = validate_provider_capability_probe(manifest)
        root = Path(str(validated["manifest_path"])).parent
        contract = read_json(root / "probe_contract.json")
        source_implementation = str(
            (contract.get("adapter_identity") or {}).get(
                "probe_implementation_root", ""
            )
        )
        if (
            require_current_implementation
            and source_implementation != _probe_implementation_root()
        ):
            raise ValueError("provider_probe_replay_implementation_identity_mismatch")
        request_plan = read_json(root / "request_plan.json")
        self._prior_requests = {
            str(row["request_id"]): row for row in request_plan["requests"]
        }
        raw_path = root / str(validated["raw_evidence"]["path"])
        self._records = {
            str(row["request_id"]): row
            for row in (
                json.loads(line)
                for line in raw_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        }

    def __call__(
        self,
        request: ProviderProbeRequest,
        _timeout_seconds: float,
    ) -> ProviderProbeObservation:
        prior_request = self._prior_requests.get(request.request_id)
        record = self._records.get(request.request_id)
        if prior_request is None or record is None:
            raise ValueError(f"provider_probe_replay_request_missing:{request.request_id}")
        current = request.semantic()
        network_keys = ("provider", "method", "url", "headers", "body_base64", "body_sha256")
        if any(prior_request.get(key) != current.get(key) for key in network_keys):
            raise ValueError(f"provider_probe_replay_transport_identity_changed:{request.request_id}")
        for key in ("endpoint", "evidence_semantics"):
            if prior_request.get(key) != current.get(key):
                raise ValueError(
                    f"provider_probe_replay_evidence_identity_changed:{request.request_id}:{key}"
                )
        prior_disposition = str(prior_request.get("disposition") or "")
        current_disposition = str(current.get("disposition") or "")
        conservative_waf_downgrade = (
            request.metadata.get("accepted_terminal_waf") is True
            and prior_disposition == "bounded_backfill"
            and current_disposition == "provider_cannot_prove"
        )
        if current_disposition != prior_disposition and not conservative_waf_downgrade:
            raise ValueError(
                f"provider_probe_replay_disposition_upgrade_or_change:{request.request_id}"
            )
        prior_metadata = dict(prior_request.get("metadata") or {})
        current_metadata = dict(current.get("metadata") or {})
        if conservative_waf_downgrade:
            current_metadata.pop("accepted_terminal_waf", None)
        if prior_metadata != current_metadata:
            raise ValueError(f"provider_probe_replay_metadata_changed:{request.request_id}")
        prior_expected = tuple(prior_request.get("expected_terminal_states") or ())
        current_expected = tuple(current.get("expected_terminal_states") or ())
        if current_expected != prior_expected and not (
            conservative_waf_downgrade
            and set(current_expected) == set(prior_expected) | {"error"}
        ):
            raise ValueError(
                f"provider_probe_replay_expected_terminal_changed:{request.request_id}"
            )
        if tuple(prior_request.get("required_checks") or ()) != tuple(
            current.get("required_checks") or ()
        ):
            raise ValueError(
                f"provider_probe_replay_required_checks_changed:{request.request_id}"
            )
        raw = base64.b64decode(record["raw_payload_base64"], validate=True)
        checks = {str(key): bool(value) for key, value in record.get("checks", {}).items()}
        if request.metadata.get("accepted_terminal_waf"):
            if request.disposition != "provider_cannot_prove":
                raise ValueError("provider_probe_waf_replay_cannot_authorize_backfill")
            envelope = json.loads(raw)
            body = base64.b64decode(envelope.get("body_base64") or "", validate=True)
            waf_captured = (
                record.get("terminal_state") == "error"
                and record.get("status_code") == 403
                and body.lstrip().startswith(b"<")
                and (b"blocked" in body.lower() or "访问被阻断".encode() in body)
            )
            checks = {
                "waf_terminal_response_archived": waf_captured,
                "no_retry_or_coverage_claim": True,
                "detail_or_waf_evidence_captured": waf_captured,
            }
        return ProviderProbeObservation(
            terminal_state=str(record["terminal_state"]),
            raw_payload=raw,
            row_count=record.get("row_count"),
            status_code=record.get("status_code"),
            error_code=record.get("error_code"),
            diagnostics=dict(record.get("diagnostics", {}))
            | {"archived_replay": True},
            checks=checks,
            transport_exchange_count=0,
        )


class OfficialHttpProbeTransport:
    """Single-connection, no-redirect HTTP evidence capture for official sites."""

    def __init__(
        self,
        *,
        minimum_delay_seconds: float,
        max_response_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        if minimum_delay_seconds < 0:
            raise ValueError("provider_probe_delay_invalid")
        if not 0 < max_response_bytes <= 256 * 1024 * 1024:
            raise ValueError("provider_probe_response_budget_invalid")
        self.minimum_delay_seconds = minimum_delay_seconds
        self.max_response_bytes = max_response_bytes
        self._last_request_at: dict[str, float] = {}
        self._cninfo_groups: dict[str, dict[str, Any]] = {}
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirectHandler(),
        )

    def __call__(
        self,
        request: ProviderProbeRequest,
        timeout_seconds: float,
    ) -> ProviderProbeObservation:
        host = urllib.parse.urlsplit(request.url).hostname or ""
        self._wait(host)
        started = time.monotonic()
        status: int | None = None
        response_headers: Mapping[str, str] = {}
        try:
            upstream = self._opener.open(
                urllib.request.Request(
                    request.url,
                    data=request.body,
                    headers=dict(request.headers),
                    method=request.method.upper(),
                ),
                timeout=timeout_seconds,
            )
            status = int(upstream.status)
            response_headers = dict(upstream.headers.items())
            body = upstream.read(self.max_response_bytes + 1)
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            response_headers = dict(exc.headers.items()) if exc.headers else {}
            body = exc.read(self.max_response_bytes + 1)
        finally:
            self._last_request_at[host] = time.monotonic()
        if len(body) > self.max_response_bytes:
            raise ValueError("official_http_response_budget_exceeded")

        envelope = {
            "schema_version": "official_http_probe_envelope_v1",
            "url": request.url,
            "method": request.method.upper(),
            "status_code": status,
            "response_headers": _safe_response_headers(response_headers),
            "body_base64": base64.b64encode(body).decode("ascii"),
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "redirect_followed": False,
        }
        raw = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
        if status is None or not 200 <= status < 300:
            return ProviderProbeObservation(
                terminal_state="error",
                raw_payload=raw,
                row_count=None,
                status_code=status,
                error_code=f"http_status:{status}",
                diagnostics={
                    "content_type": response_headers.get("Content-Type", ""),
                    "waf_html_observed": status == 403 and body.lstrip().startswith(b"<"),
                },
                checks={"http_success": False},
            )
        return self._parse_success(request, raw=raw, body=body, status=status)

    def restore(
        self,
        request: ProviderProbeRequest,
        record: Mapping[str, Any],
    ) -> None:
        """Restore pagination state from archived raw bytes before a resumed page."""

        if request.metadata.get("case") != "cninfo_list" or not request.metadata.get(
            "pagination_group"
        ):
            return
        raw = base64.b64decode(str(record["raw_payload_base64"]), validate=True)
        envelope = json.loads(raw)
        body = base64.b64decode(envelope.get("body_base64") or "", validate=True)
        payload = json.loads(body)
        self._parse_cninfo_list(
            request,
            raw=raw,
            status=int(record.get("status_code") or 200),
            payload=payload,
        )

    def _wait(self, host: str) -> None:
        previous = self._last_request_at.get(host)
        if previous is None:
            return
        jitter = random.uniform(0.0, min(0.25, self.minimum_delay_seconds / 4))
        remaining = self.minimum_delay_seconds + jitter - (time.monotonic() - previous)
        if remaining > 0:
            time.sleep(remaining)

    def _parse_success(
        self,
        request: ProviderProbeRequest,
        *,
        raw: bytes,
        body: bytes,
        status: int,
    ) -> ProviderProbeObservation:
        case = str(request.metadata.get("case") or "")
        if case == "cninfo_pdf":
            checks = {
                "pdf_signature": body.startswith(b"%PDF"),
                "nonempty_pdf": len(body) > 1024,
            }
            return ProviderProbeObservation(
                terminal_state="positive" if body else "empty",
                raw_payload=raw,
                row_count=1 if body else 0,
                status_code=status,
                diagnostics={"pdf_sha256": hashlib.sha256(body).hexdigest()},
                checks=checks,
            )
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return ProviderProbeObservation(
                terminal_state="error",
                raw_payload=raw,
                row_count=None,
                status_code=status,
                error_code=f"json_parse:{type(exc).__name__}",
                checks={"json_parse": False},
            )
        if case == "cninfo_org_map":
            rows = payload.get("stockList") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                rows = payload if isinstance(payload, list) else []
            codes = {
                str(row.get("code") or row.get("zwjc") or "")
                for row in rows
                if isinstance(row, dict)
            }
            checks = {"known_code_mapped": "600000" in codes, "list_shape": bool(rows)}
            return _json_observation(raw, status, rows, checks=checks)
        if case == "cninfo_list":
            return self._parse_cninfo_list(request, raw=raw, status=status, payload=payload)
        if case == "csindex_filter":
            serialized = json.dumps(payload, ensure_ascii=False)
            rows = _find_first_list(payload)
            return _json_observation(
                raw,
                status,
                rows,
                checks={"index_rebalance_topic_present": "index_rebalance" in serialized},
            )
        if case == "csindex_list":
            rows, total, current_page = _csindex_list_rows(payload)
            expected = request.metadata.get("expected_count")
            checks = {
                "list_shape": isinstance(rows, list),
                "total_semantics": total >= len(rows),
                "current_page_present": current_page is not None,
            }
            if expected is not None:
                checks["expected_page_count"] = len(rows) == int(expected)
            expected_page = request.metadata.get("expected_current_page")
            if expected_page is not None:
                checks["current_page_semantics"] = current_page == int(expected_page)
            required_token = str(request.metadata.get("required_row_token") or "")
            if required_token:
                checks["required_row_token_present"] = any(
                    required_token in json.dumps(row, ensure_ascii=False)
                    for row in rows
                )
            return _json_observation(
                raw,
                status,
                rows,
                checks=checks,
                diagnostics={"reported_total": total, "current_page": current_page},
            )
        if case == "csindex_detail":
            detail = _csindex_detail(payload)
            text = json.dumps(detail, ensure_ascii=False)
            required_tokens = [str(item) for item in request.metadata.get("tokens", [])]
            normalized_text = _normalize_document_text(text)
            checks = {
                "detail_object": bool(detail),
                "publication_metadata": any(
                    key in detail for key in ("publishDate", "releaseDate", "createTime")
                ),
                "required_tokens": all(
                    _normalize_document_text(token) in normalized_text
                    for token in required_tokens
                ),
                "detail_or_waf_evidence_captured": bool(detail),
            }
            return ProviderProbeObservation(
                terminal_state="positive" if detail else "empty",
                raw_payload=raw,
                row_count=1 if detail else 0,
                status_code=status,
                diagnostics={"detail_keys": sorted(detail)},
                checks=checks,
            )
        return ProviderProbeObservation(
            terminal_state="error",
            raw_payload=raw,
            row_count=None,
            status_code=status,
            error_code="unknown_http_probe_case",
            checks={"known_probe_case": False},
        )

    def _parse_cninfo_list(
        self,
        request: ProviderProbeRequest,
        *,
        raw: bytes,
        status: int,
        payload: Any,
    ) -> ProviderProbeObservation:
        if not isinstance(payload, dict):
            return ProviderProbeObservation(
                terminal_state="error",
                raw_payload=raw,
                row_count=None,
                status_code=status,
                error_code="cninfo_payload_shape",
                checks={"json_object": False},
            )
        rows = payload.get("announcements")
        if not isinstance(rows, list):
            rows = []
        total = int(payload.get("totalAnnouncement") or payload.get("totalRecordNum") or 0)
        has_more = bool(payload.get("hasMore"))
        ids = {
            str(row.get("announcementId") or "")
            for row in rows
            if isinstance(row, dict) and row.get("announcementId")
        }
        checks = {
            "announcement_list_shape": isinstance(payload.get("announcements"), list)
            or (payload.get("announcements") is None and total == 0),
            "unique_ids_within_page": len(ids) == len(rows),
            "page_size_server_cap": len(rows) <= 30,
            "total_semantics": total >= len(rows),
        }
        if rows:
            checks["announcement_identity_and_time_present"] = all(
                isinstance(row, dict)
                and bool(row.get("announcementId"))
                and row.get("announcementTime") is not None
                for row in rows
            )
        group_name = str(request.metadata.get("pagination_group") or "")
        if group_name:
            group = self._cninfo_groups.setdefault(
                group_name, {"ids": set(), "total": total, "pages": []}
            )
            duplicate_ids = set(group["ids"]) & ids
            group["ids"].update(ids)
            group["pages"].append(int(request.metadata.get("page") or 0))
            checks["no_duplicate_ids_across_pages"] = not duplicate_ids
            checks["stable_reported_total"] = total == group["total"]
            if request.metadata.get("terminal_page"):
                checks["terminal_has_more_false"] = has_more is False
                checks["terminal_unique_count_matches_total"] = len(group["ids"]) == total
        expected = request.metadata.get("expected_terminal")
        if expected == "empty":
            checks["structured_empty"] = len(rows) == 0 and total == 0
        elif expected == "positive":
            checks["known_positive"] = bool(rows)
        diagnostics = {
            "reported_total": total,
            "has_more": has_more,
            "unique_ids": len(ids),
            "sample_announcement_ids": sorted(ids)[:5],
        }
        return _json_observation(
            raw,
            status,
            rows,
            checks=checks,
            diagnostics=diagnostics,
        )


class BaostockProbeTransport:
    """Pinned SDK parser with a safe socket shim and raw wire capture."""

    def __init__(self) -> None:
        self._bs: Any | None = None
        self._context: Any | None = None
        self._constants: Any | None = None
        self._captures: list[dict[str, Any]] = []
        self._capture_enabled = False
        self._calendar_open_days: int | None = None
        self._repeat_hashes: dict[str, str] = {}
        self._socketutil: Any | None = None
        self._upstream_send: Any | None = None
        self._wire_exchange_count = 0
        self._socket_peer: list[Any] | None = None
        self._closed = False

    def __call__(
        self,
        request: ProviderProbeRequest,
        timeout_seconds: float,
    ) -> ProviderProbeObservation:
        exchanges_before = self._wire_exchange_count
        self._captures = []
        self._capture_enabled = True
        parsed = urllib.parse.urlsplit(request.url)
        params = {
            key: values[-1]
            for key, values in urllib.parse.parse_qs(parsed.query).items()
        }
        case = str(request.metadata.get("case") or "")
        try:
            self._ensure_session(timeout_seconds)
            assert self._bs is not None
            if case == "history":
                result = self._bs.query_history_k_data_plus(
                    params["code"],
                    BAOSTOCK_FIELDS,
                    start_date=params["start"],
                    end_date=params["end"],
                    frequency="d",
                    adjustflag="3",
                )
            elif case == "history_custom":
                fields = str(params.get("fields") or "")
                expected_fields = ",".join(
                    str(value) for value in request.metadata.get("expected_fields") or ()
                )
                if not fields or fields != expected_fields:
                    raise ValueError("baostock_custom_history_fields_invalid")
                result = self._bs.query_history_k_data_plus(
                    params["code"],
                    fields,
                    start_date=params["start"],
                    end_date=params["end"],
                    frequency="d",
                    adjustflag="3",
                )
            elif case == "stock_basic":
                result = self._bs.query_stock_basic(code=params["code"])
            elif case == "trade_calendar":
                result = self._bs.query_trade_dates(
                    start_date=params["start"], end_date=params["end"]
                )
            elif case == "hs300":
                result = self._bs.query_hs300_stocks(params["date"])
            elif case == "dividend":
                result = self._bs.query_dividend_data(
                    code=params["code"], year=params["year"], yearType="report"
                )
            elif case == "adjust_factor":
                result = self._bs.query_adjust_factor(
                    code=params["code"],
                    start_date=params["start"],
                    end_date=params["end"],
                )
            else:
                raise ValueError(f"unknown_baostock_probe_case:{case}")
            rows, pages, clean_terminal = self._collect(result)
        except Exception as exc:
            raw = self._raw_envelope(
                request,
                fields=[],
                rows=[],
                pages=[],
                error={"type": type(exc).__name__, "message": str(exc)[:1000]},
            )
            return ProviderProbeObservation(
                terminal_state="error",
                raw_payload=raw,
                row_count=None,
                error_code=f"baostock_transport:{type(exc).__name__}",
                diagnostics={"wire_capture_count": len(self._captures)},
                checks={"transport_completed": False},
                transport_exchange_count=(
                    self._wire_exchange_count - exchanges_before
                ),
            )
        finally:
            self._capture_enabled = False

        fields = [str(value) for value in getattr(result, "fields", [])]
        error_code = str(getattr(result, "error_code", ""))
        error_message = str(getattr(result, "error_msg", ""))
        raw = self._raw_envelope(
            request,
            fields=fields,
            rows=rows,
            pages=pages,
            error={"code": error_code, "message": error_message},
        )
        if error_code != "0":
            return ProviderProbeObservation(
                terminal_state="error",
                raw_payload=raw,
                row_count=None,
                error_code=f"baostock:{error_code}",
                diagnostics={"provider_error_message": error_message},
                checks={"provider_success": False},
                transport_exchange_count=(
                    self._wire_exchange_count - exchanges_before
                ),
            )
        checks = self._checks(request, fields=fields, rows=rows, clean_terminal=clean_terminal)
        diagnostics = self._diagnostics(request, fields=fields, rows=rows, pages=pages)
        if case == "trade_calendar":
            self._calendar_open_days = sum(
                1 for row in rows if len(row) >= 2 and str(row[1]) == "1"
            )
            diagnostics["open_days"] = self._calendar_open_days
        logical_hash = canonical_hash({"fields": fields, "rows": rows})
        repeat_group = str(request.metadata.get("repeat_group") or "")
        if repeat_group:
            previous = self._repeat_hashes.setdefault(repeat_group, logical_hash)
            checks["repeat_logical_payload_equal"] = previous == logical_hash
            diagnostics["canonical_logical_payload_sha256"] = logical_hash
        return ProviderProbeObservation(
            terminal_state="positive" if rows else "empty",
            raw_payload=raw,
            row_count=len(rows),
            status_code=0,
            diagnostics=diagnostics,
            checks=checks,
            transport_exchange_count=(self._wire_exchange_count - exchanges_before),
        )

    def close(self) -> None:
        if self._closed:
            return
        self._capture_enabled = False
        sock = (
            getattr(self._context, "default_socket", None)
            if self._context is not None
            else None
        )
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        if self._socketutil is not None and self._upstream_send is not None:
            self._socketutil.send_msg = self._upstream_send
        self._bs = None
        self._closed = True

    def restore(
        self,
        request: ProviderProbeRequest,
        record: Mapping[str, Any],
    ) -> None:
        """Restore calendar/repeat state without opening a provider connection."""

        diagnostics = dict(record.get("diagnostics") or {})
        if request.metadata.get("case") == "trade_calendar":
            open_days = diagnostics.get("open_days")
            self._calendar_open_days = int(open_days) if open_days is not None else None
        repeat_group = str(request.metadata.get("repeat_group") or "")
        logical_hash = diagnostics.get("canonical_logical_payload_sha256")
        if repeat_group and logical_hash:
            self._repeat_hashes.setdefault(repeat_group, str(logical_hash))

    def _ensure_session(self, timeout_seconds: float) -> None:
        if self._bs is not None:
            sock = getattr(self._context, "default_socket", None)
            if sock is not None:
                sock.settimeout(timeout_seconds)
            return
        try:
            distribution = importlib.metadata.version("baostock")
        except importlib.metadata.PackageNotFoundError as exc:
            raise RuntimeError(
                "baostock 0.9.3 is required; run with `uv run --with baostock==0.9.3`"
            ) from exc
        if distribution != "0.9.3":
            raise RuntimeError(f"baostock_distribution_unpinned:{distribution}")
        import baostock as bs
        import baostock.common.context as context
        import baostock.common.contants as constants
        import baostock.util.socketutil as socketutil

        if str(getattr(bs, "__version__", "")) != "00.9.30":
            raise RuntimeError(
                f"baostock_client_unpinned:{getattr(bs, '__version__', '')}"
            )
        self._bs = bs
        self._context = context
        self._constants = constants
        self._socketutil = socketutil
        self._upstream_send = socketutil.send_msg
        socketutil.send_msg = self._safe_send
        previous_default_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(timeout_seconds)
        try:
            login = bs.login()
        finally:
            socket.setdefaulttimeout(previous_default_timeout)
        if str(getattr(login, "error_code", "")) != "0":
            self._bs = None
            raise RuntimeError(
                f"baostock_login_failed:{getattr(login, 'error_code', '')}:"
                f"{getattr(login, 'error_msg', '')}"
            )
        sock = getattr(context, "default_socket", None)
        if sock is None:
            self._bs = None
            raise RuntimeError("baostock_socket_missing_after_login")
        sock.settimeout(timeout_seconds)
        peer = sock.getpeername()
        self._socket_peer = list(peer) if isinstance(peer, tuple) else [str(peer)]

    def _safe_send(self, message: str) -> str:
        assert self._context is not None and self._constants is not None
        sock = getattr(self._context, "default_socket", None)
        if sock is None:
            raise ConnectionError("baostock_socket_missing")
        request_bytes = (message + "\n").encode("utf-8")
        self._wire_exchange_count += 1
        capture: dict[str, Any] | None = None
        if self._capture_enabled:
            peer = sock.getpeername()
            observed_peer = list(peer) if isinstance(peer, tuple) else [str(peer)]
            capture = {
                "wire_request_base64": base64.b64encode(request_bytes).decode("ascii"),
                "request_sha256": hashlib.sha256(request_bytes).hexdigest(),
                "request_size_bytes": len(request_bytes),
                "socket_peer": observed_peer,
                "wire_response_base64": "",
                "wire_response_sha256": hashlib.sha256(b"").hexdigest(),
                "wire_size_bytes": 0,
                "terminal_marker_present": False,
            }
            self._captures.append(capture)
        sock.sendall(request_bytes)
        received = bytearray()
        marker = b"<![CDATA[]]>\n"
        try:
            while not received.endswith(marker):
                chunk = sock.recv(8192)
                if not chunk:
                    raise ConnectionError(
                        "baostock_socket_closed_before_terminal_marker"
                    )
                received.extend(chunk)
                if len(received) > 64 * 1024 * 1024:
                    raise ValueError("baostock_wire_response_budget_exceeded")
        except BaseException:
            if capture is not None:
                partial = bytes(received)
                capture.update(
                    {
                        "wire_response_base64": base64.b64encode(partial).decode(
                            "ascii"
                        ),
                        "wire_response_sha256": hashlib.sha256(partial).hexdigest(),
                        "wire_size_bytes": len(partial),
                    }
                )
            raise
        wire = bytes(received)
        if len(wire) < self._constants.MESSAGE_HEADER_LENGTH:
            raise ValueError("baostock_wire_header_truncated")
        header_bytes = wire[: self._constants.MESSAGE_HEADER_LENGTH]
        header = header_bytes.decode("utf-8")
        header_parts = header.split(self._constants.MESSAGE_SPLIT)
        if len(header_parts) != 3:
            raise ValueError("baostock_wire_header_shape_invalid")
        message_type = header_parts[1]
        declared_length = int(header_parts[2])
        if message_type in self._constants.COMPRESSED_MESSAGE_TYPE_TUPLE:
            compressed = wire[
                self._constants.MESSAGE_HEADER_LENGTH :
                self._constants.MESSAGE_HEADER_LENGTH + declared_length
            ]
            if len(compressed) != declared_length:
                raise ValueError("baostock_compressed_body_truncated")
            decoded_body = zlib.decompress(compressed).decode("utf-8")
            decoded = header + decoded_body
        else:
            decoded = wire.decode("utf-8")
        if capture is not None:
            capture.update(
                {
                    "wire_response_base64": base64.b64encode(wire).decode("ascii"),
                    "wire_response_sha256": hashlib.sha256(wire).hexdigest(),
                    "wire_size_bytes": len(wire),
                    "response_protocol_version": header_parts[0],
                    "response_message_type": message_type,
                    "declared_body_length": declared_length,
                    "decoded_logical_sha256": hashlib.sha256(decoded.encode()).hexdigest(),
                    "terminal_marker_present": wire.endswith(marker),
                }
            )
        return decoded

    def _collect(self, result: Any) -> tuple[list[list[str]], list[dict[str, Any]], bool]:
        if result is None:
            raise ValueError("baostock_result_missing")
        rows: list[list[str]] = []
        pages: list[dict[str, Any]] = []
        while True:
            page_rows = [[str(value) for value in row] for row in result.data]
            rows.extend(page_rows)
            pages.append(
                {
                    "page": int(result.cur_page_num),
                    "row_count": len(page_rows),
                    "provider_page_size": int(result.per_page_count),
                }
            )
            if str(result.error_code) != "0":
                return rows, pages, False
            if len(page_rows) < 2000:
                return rows, pages, True
            if len(pages) >= BAOSTOCK_MAX_PAGES_PER_REQUEST:
                raise ValueError("baostock_page_budget_exceeded")
            result.cur_row_num = len(result.data)
            captures_before = len(self._captures)
            advanced = result.next()
            if advanced:
                continue
            if (
                len(self._captures) > captures_before
                and str(result.error_code) == "0"
                and len(result.data) == 0
            ):
                pages.append(
                    {
                        "page": int(result.cur_page_num),
                        "row_count": 0,
                        "provider_page_size": int(result.per_page_count),
                    }
                )
                return rows, pages, True
            return rows, pages, False

    def _raw_envelope(
        self,
        request: ProviderProbeRequest,
        *,
        fields: Sequence[str],
        rows: Sequence[Sequence[str]],
        pages: Sequence[Mapping[str, Any]],
        error: Mapping[str, Any],
    ) -> bytes:
        payload = {
            "schema_version": "baostock_wire_probe_envelope_v1",
            "package_distribution_version": "0.9.3",
            "client_protocol_version": "00.9.30",
            "request_id": request.request_id,
            "socket_peer": list(self._socket_peer or ()),
            "wire_exchanges": self._captures,
            "parsed": {
                "fields": list(fields),
                "row_count": len(rows),
                "pages": list(pages),
                "first_rows": list(rows[:3]),
                "last_rows": list(rows[-3:]),
                "canonical_logical_payload_sha256": canonical_hash(
                    {"fields": list(fields), "rows": list(rows)}
                ),
            },
            "provider_error": dict(error),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    def _checks(
        self,
        request: ProviderProbeRequest,
        *,
        fields: Sequence[str],
        rows: Sequence[Sequence[str]],
        clean_terminal: bool,
    ) -> dict[str, bool]:
        checks = {
            "provider_success": True,
            "raw_wire_captured": bool(self._captures),
            "terminal_marker_complete": all(
                bool(row["terminal_marker_present"]) for row in self._captures
            ),
            "pagination_terminal_unambiguous": clean_terminal,
            "row_width_matches_fields": all(len(row) == len(fields) for row in rows),
        }
        case = request.metadata.get("case")
        if case == "history":
            checks["history_fields_exact"] = list(fields) == BAOSTOCK_FIELDS.split(",")
            checks["unique_security_day"] = len(
                {(row[0], row[1]) for row in rows if len(row) >= 2}
            ) == len(rows)
            if request.metadata.get("exact_calendar_cover"):
                checks["exact_open_day_cover"] = (
                    self._calendar_open_days is not None
                    and len(rows) == self._calendar_open_days
                )
            if request.metadata.get("expect_st"):
                checks["known_st_observed"] = any(
                    len(row) >= 11 and row[10] == "1" for row in rows
                )
            if request.metadata.get("expect_suspension"):
                checks["known_suspension_observed"] = any(
                    len(row) >= 10 and row[9] == "0" for row in rows
                )
            if request.metadata.get("expected_empty"):
                checks["successful_empty_observed"] = not rows
        elif case == "history_custom":
            expected_fields = [
                str(value) for value in request.metadata.get("expected_fields") or ()
            ]
            expected_provider_code = str(request.metadata.get("provider_code") or "")
            checks["history_fields_exact"] = list(fields) == expected_fields
            checks["unique_security_day"] = len(
                {(row[0], row[1]) for row in rows if len(row) >= 2}
            ) == len(rows)
            checks["provider_code_matches_request"] = bool(
                expected_provider_code
            ) and all(
                len(row) >= 2 and str(row[1]) == expected_provider_code
                for row in rows
            )
        elif case == "stock_basic":
            checks["stock_basic_fields_exact"] = list(fields) == [
                "code",
                "code_name",
                "ipoDate",
                "outDate",
                "type",
                "status",
            ]
            checks["stock_basic_identity_unique"] = (
                len(rows) <= 1
                and all(
                    len(row) >= 1
                    and row[0] == str(request.metadata.get("provider_code") or "")
                    for row in rows
                )
            )
        elif case == "trade_calendar":
            checks["calendar_fields"] = list(fields) == [
                "calendar_date",
                "is_trading_day",
            ]
            checks["calendar_has_open_and_closed_days"] = (
                {row[1] for row in rows if len(row) >= 2} >= {"0", "1"}
            )
        elif case == "hs300":
            checks["exactly_300_unique_members"] = (
                len(rows) == 300
                and len({row[1] for row in rows if len(row) >= 2}) == 300
            )
            checks["snapshot_update_date_present"] = all(
                bool(row[0]) for row in rows if row
            )
        elif case in {"dividend", "adjust_factor"}:
            checks["reconciliation_payload_available"] = bool(rows)
            checks["historical_revision_timestamp_absent"] = not any(
                name.lower() in {"as_of", "revision_time", "publish_time"}
                for name in fields
            )
            expected_provider_code = str(request.metadata.get("provider_code") or "")
            checks["provider_code_matches_request"] = bool(
                expected_provider_code
            ) and all(
                len(row) >= 1 and str(row[0]) == expected_provider_code
                for row in rows
            )
        return checks

    def _diagnostics(
        self,
        request: ProviderProbeRequest,
        *,
        fields: Sequence[str],
        rows: Sequence[Sequence[str]],
        pages: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        diagnostics: dict[str, Any] = {
            "fields": list(fields),
            "page_row_counts": [int(page["row_count"]) for page in pages],
            "wire_capture_count": len(self._captures),
        }
        if request.metadata.get("case") == "history":
            diagnostics.update(
                {
                    "st_rows": sum(
                        1 for row in rows if len(row) >= 11 and row[10] == "1"
                    ),
                    "suspension_rows": sum(
                        1 for row in rows if len(row) >= 10 and row[9] == "0"
                    ),
                    "blank_volume_rows": sum(
                        1 for row in rows if len(row) >= 8 and not row[7]
                    ),
                }
            )
        return diagnostics


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _baostock_requests() -> list[ProviderProbeRequest]:
    requests: list[ProviderProbeRequest] = [
        _baostock_request(
            "baostock_calendar_2012_2019",
            "trade_calendar",
            "/trade_calendar?start=2012-01-01&end=2019-12-31",
            metadata={"case": "trade_calendar"},
        )
    ]
    for code in BAOSTOCK_SAMPLE_CODES:
        slug = code.replace(".", "_")
        full_metadata: dict[str, Any] = {"case": "history"}
        if code in BAOSTOCK_STABLE_CODES:
            full_metadata["exact_calendar_cover"] = True
        if code == "sh.600145":
            full_metadata.update({"expect_st": True, "expect_suspension": True})
        requests.append(
            _baostock_request(
                f"baostock_history_full_{slug}",
                "history_state_daily",
                f"/history?code={code}&start=2012-01-01&end=2019-12-31",
                metadata=full_metadata,
            )
        )
        seed_metadata: dict[str, Any] = {"case": "history"}
        expected = ("positive", "empty")
        if code in {"sh.688001", "sz.300750"}:
            seed_metadata["expected_empty"] = True
            expected = ("empty",)
        requests.append(
            _baostock_request(
                f"baostock_history_seed_{slug}",
                "history_state_daily",
                f"/history?code={code}&start=2011-12-01&end=2012-01-31",
                metadata=seed_metadata,
                expected=expected,
            )
        )
    requests.extend(
        [
            _baostock_request(
                "baostock_history_cap_600000",
                "history_state_daily",
                "/history?code=sh.600000&start=2011-01-01&end=2019-12-31",
                metadata={"case": "history", "repeat_group": "history_cap_600000"},
            ),
            _baostock_request(
                "baostock_history_cap_600000_repeat",
                "history_state_daily",
                "/history?code=sh.600000&start=2011-01-01&end=2019-12-31",
                metadata={"case": "history", "repeat_group": "history_cap_600000"},
            ),
            *[
                _baostock_request(
                    f"baostock_hs300_{date.replace('-', '')}",
                    "hs300_snapshot",
                    f"/hs300?date={date}",
                    disposition="provider_cannot_prove",
                    metadata={"case": "hs300"},
                )
                for date in ("2011-12-30", "2012-06-29", "2019-12-31")
            ],
            _baostock_request(
                "baostock_dividend_600000_2012",
                "dividend_reconciliation",
                "/dividend?code=sh.600000&year=2012",
                disposition="provider_cannot_prove",
                metadata={"case": "dividend"},
            ),
            _baostock_request(
                "baostock_adjust_factor_600000",
                "adjust_factor_reconciliation",
                "/adjust_factor?code=sh.600000&start=2012-01-01&end=2019-12-31",
                disposition="provider_cannot_prove",
                metadata={"case": "adjust_factor"},
            ),
        ]
    )
    return requests


def _baostock_request(
    request_id: str,
    endpoint: str,
    path: str,
    *,
    disposition: str = "bounded_backfill",
    metadata: Mapping[str, Any],
    expected: tuple[str, ...] = ("positive",),
) -> ProviderProbeRequest:
    case = str(metadata.get("case") or "")
    required_checks = [
        "provider_success",
        "raw_wire_captured",
        "terminal_marker_complete",
        "pagination_terminal_unambiguous",
        "row_width_matches_fields",
    ]
    if case == "history":
        required_checks.extend(["history_fields_exact", "unique_security_day"])
        if metadata.get("exact_calendar_cover"):
            required_checks.append("exact_open_day_cover")
        if metadata.get("expect_st"):
            required_checks.append("known_st_observed")
        if metadata.get("expect_suspension"):
            required_checks.append("known_suspension_observed")
        if metadata.get("expected_empty"):
            required_checks.append("successful_empty_observed")
        if metadata.get("repeat_group"):
            required_checks.append("repeat_logical_payload_equal")
    elif case == "trade_calendar":
        required_checks.extend(
            ["calendar_fields", "calendar_has_open_and_closed_days"]
        )
    elif case == "hs300":
        required_checks.extend(
            ["exactly_300_unique_members", "snapshot_update_date_present"]
        )
    elif case in {"dividend", "adjust_factor"}:
        required_checks.extend(
            [
                "reconciliation_payload_available",
                "historical_revision_timestamp_absent",
            ]
        )
    return ProviderProbeRequest(
        request_id=request_id,
        provider="baostock",
        endpoint=endpoint,
        method="BAOSTOCK",
        url=f"baostock://public-api.baostock.com{path}",
        disposition=disposition,
        evidence_semantics="raw_custom_socket_response_plus_locked_parser",
        expected_terminal_states=expected,
        required_checks=tuple(required_checks),
        metadata=metadata,
    )


def _csindex_requests() -> list[ProviderProbeRequest]:
    requests = [
        ProviderProbeRequest(
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
    ]
    # Generic 2012 pages prove page progression, the clamped terminal page and
    # the provider's non-empty-page termination trap.
    for page in range(1, 6):
        metadata: dict[str, Any] = {
            "case": "csindex_list",
            "expected_current_page": 4 if page == 5 else page,
            "expected_count": 10 if page in {1, 2, 3} else 4,
        }
        requests.append(
            _csindex_list_request(
                request_id=f"csindex_rebalance_2012_geometry_page_{page}",
                endpoint="index_rebalance_announcement_list",
                start_date="2012-01-01",
                end_date="2012-12-31",
                page=page,
                metadata=metadata,
            )
        )
    # A filtered request in every research year proves that the reachable
    # history is about CSI300, not merely unrelated index-rebalance traffic.
    for year in range(2011, 2020):
        requests.append(
            _csindex_list_request(
                request_id=f"csindex_csi300_search_{year}",
                endpoint="csi300_announcement_search",
                start_date=f"{year}-01-01",
                end_date=f"{year}-12-31",
                page=1,
                search_input="沪深300",
                metadata={
                    "case": "csindex_list",
                    "expected_current_page": 1,
                    "required_row_token": "沪深300",
                },
            )
        )
    requests.append(
        ProviderProbeRequest(
            request_id="csindex_rebalance_empty_1990",
            provider="csindex",
            endpoint="index_rebalance_announcement_list",
            method="POST",
            url=CSINDEX_LIST_URL,
            headers=CSINDEX_HEADERS,
            body=json.dumps(
                {
                    "startDate": "1990-01-01",
                    "endDate": "1990-02-01",
                    "classList": [],
                    "typeList": [],
                    "relatedTopics": ["index_rebalance"],
                    "indexList": [],
                    "page": {"key": "", "page": 1, "rows": 10, "sortBy": ""},
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode(),
            disposition="bounded_backfill",
            evidence_semantics="official_http_response_envelope",
            expected_terminal_states=("empty",),
            required_checks=(
                "list_shape",
                "total_semantics",
                "current_page_present",
                "current_page_semantics",
                "expected_page_count",
            ),
            metadata={"case": "csindex_list", "expected_count": 0, "expected_current_page": 1},
        )
    )
    for announcement_id, tokens in (
        ("6699", ["沪深300", "2012年7月2日"]),
        ("3242", ["沪深300"]),
    ):
        requests.append(
            ProviderProbeRequest(
                request_id=f"csindex_detail_{announcement_id}",
                provider="csindex",
                endpoint="index_rebalance_announcement_detail",
                method="GET",
                url=(
                    "https://www.csindex.com.cn/csindex-home/announcement/"
                    f"queryAnnouncementById?id={announcement_id}"
                ),
                headers={"Referer": "https://www.csindex.com.cn/", "User-Agent": USER_AGENT},
                disposition="provider_cannot_prove",
                evidence_semantics="official_http_response_envelope",
                expected_terminal_states=("positive", "error"),
                required_checks=("detail_or_waf_evidence_captured",),
                metadata={
                    "case": "csindex_detail",
                    "tokens": tokens,
                    "accepted_terminal_waf": True,
                },
            )
        )
    return requests


def _csindex_list_request(
    *,
    request_id: str,
    endpoint: str,
    start_date: str,
    end_date: str,
    page: int,
    metadata: Mapping[str, Any],
    search_input: str = "",
) -> ProviderProbeRequest:
    body: dict[str, Any] = {
        "startDate": start_date,
        "endDate": end_date,
        "classList": [],
        "typeList": [],
        "relatedTopics": ["index_rebalance"],
        "indexList": [],
        "page": {"key": "", "page": page, "rows": 10, "sortBy": ""},
    }
    if search_input:
        body["searchInput"] = search_input
    required_checks = [
        "list_shape",
        "total_semantics",
        "current_page_present",
        "current_page_semantics",
    ]
    if "expected_count" in metadata:
        required_checks.append("expected_page_count")
    if metadata.get("required_row_token"):
        required_checks.append("required_row_token_present")
    return ProviderProbeRequest(
        request_id=request_id,
        provider="csindex",
        endpoint=endpoint,
        method="POST",
        url=CSINDEX_LIST_URL,
        headers=CSINDEX_HEADERS,
        body=json.dumps(body, sort_keys=True, separators=(",", ":")).encode(),
        disposition="bounded_backfill",
        evidence_semantics="official_http_response_envelope",
        expected_terminal_states=("positive",),
        required_checks=tuple(required_checks),
        metadata=metadata,
    )


def _cninfo_requests() -> list[ProviderProbeRequest]:
    requests: list[ProviderProbeRequest] = [
        ProviderProbeRequest(
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
    ]
    for page in range(1, 18):
        requests.append(
            _cninfo_query_request(
                request_id=f"cninfo_st_delist_2012_page_{page}",
                endpoint="st_delist_announcement_list",
                page=page,
                category="category_tbclts_szsh;",
                pagination_group="st_delist_2012",
                terminal_page=page == 17,
            )
        )
    requests.extend(
        [
            _cninfo_query_request(
                request_id="cninfo_rights_600000_2012",
                endpoint="corporate_action_announcement_list",
                page=1,
                category="category_qyfpxzcs_szsh;",
                stock="600000,gssh0600000",
                expected_terminal="positive",
            ),
            _cninfo_query_request(
                request_id="cninfo_st_empty_600000_2012",
                endpoint="st_delist_security_lookup",
                page=1,
                category="category_tbclts_szsh;",
                stock="600000,gssh0600000",
                expected_terminal="empty",
                expected=("empty",),
            ),
            _cninfo_query_request(
                request_id="cninfo_st_known_600145_2012",
                endpoint="st_delist_security_lookup",
                page=1,
                category="category_tbclts_szsh;",
                stock="600145,gssh0600145",
                expected_terminal="positive",
            ),
            _cninfo_query_request(
                request_id="cninfo_st_delisted_600432_2012_2019",
                endpoint="st_delist_security_lookup",
                page=1,
                category="category_tbclts_szsh;",
                stock="600432,gssh0600432",
                expected_terminal="positive",
                date_span="2012-01-01~2019-12-31",
            ),
            _cninfo_query_request(
                request_id="cninfo_suspension_sh_2012_page_1",
                endpoint="suspension_announcement_list",
                page=1,
                category="category_jgjg_tfp",
                column="regulator",
                plate="jgjg_sh",
                expected_terminal="positive",
            ),
            _cninfo_query_request(
                request_id="cninfo_suspension_security_600005_2012",
                endpoint="suspension_security_lookup",
                page=1,
                category="category_jgjg_tfp",
                stock="600005,gssh0600005",
                column="regulator",
                plate="jgjg_sh",
                disposition="provider_cannot_prove",
                expected_terminal="empty",
                expected=("empty",),
            ),
            ProviderProbeRequest(
                request_id="cninfo_pdf_61154261",
                provider="cninfo",
                endpoint="announcement_pdf",
                method="GET",
                url="https://static.cninfo.com.cn/finalpage/2012-06-19/61154261.PDF",
                headers={"Referer": "https://www.cninfo.com.cn/", "User-Agent": USER_AGENT},
                disposition="bounded_backfill",
                evidence_semantics="official_http_response_envelope",
                expected_terminal_states=("positive",),
                required_checks=("pdf_signature", "nonempty_pdf"),
                metadata={"case": "cninfo_pdf"},
            ),
        ]
    )
    return requests


def _cninfo_query_request(
    *,
    request_id: str,
    endpoint: str,
    page: int,
    category: str,
    stock: str = "",
    column: str = "szse",
    plate: str = "",
    pagination_group: str = "",
    terminal_page: bool = False,
    expected_terminal: str | None = None,
    expected: tuple[str, ...] = ("positive",),
    date_span: str = "2012-01-01~2012-12-31",
    disposition: str = "bounded_backfill",
) -> ProviderProbeRequest:
    body = urllib.parse.urlencode(
        {
            "pageNum": page,
            "pageSize": 30,
            "column": column,
            "tabName": "fulltext",
            "plate": plate,
            "stock": stock,
            "searchkey": "",
            "secid": "",
            "category": category,
            "trade": "",
            "seDate": date_span,
            "sortName": "",
            "sortType": "",
            "isHLtitle": "true",
        }
    ).encode()
    required_checks = [
        "announcement_list_shape",
        "unique_ids_within_page",
        "page_size_server_cap",
        "total_semantics",
    ]
    if expected_terminal == "positive":
        required_checks.extend(
            ["known_positive", "announcement_identity_and_time_present"]
        )
    elif expected_terminal == "empty":
        required_checks.append("structured_empty")
    if pagination_group:
        required_checks.extend(
            ["no_duplicate_ids_across_pages", "stable_reported_total"]
        )
    if terminal_page:
        required_checks.extend(
            ["terminal_has_more_false", "terminal_unique_count_matches_total"]
        )
    return ProviderProbeRequest(
        request_id=request_id,
        provider="cninfo",
        endpoint=endpoint,
        method="POST",
        url=CNINFO_QUERY_URL,
        headers=CNINFO_HEADERS,
        body=body,
        disposition=disposition,
        evidence_semantics="official_http_response_envelope",
        expected_terminal_states=expected,
        required_checks=tuple(required_checks),
        metadata={
            "case": "cninfo_list",
            "page": page,
            "pagination_group": pagination_group,
            "terminal_page": terminal_page,
            "expected_terminal": expected_terminal,
        },
    )


def _json_observation(
    raw: bytes,
    status: int,
    rows: Sequence[Any],
    *,
    checks: Mapping[str, bool],
    diagnostics: Mapping[str, Any] | None = None,
) -> ProviderProbeObservation:
    return ProviderProbeObservation(
        terminal_state="positive" if rows else "empty",
        raw_payload=raw,
        row_count=len(rows),
        status_code=status,
        diagnostics=dict(diagnostics or {}),
        checks=checks,
    )


def _find_first_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for item in value.values():
            found = _find_first_list(item)
            if found:
                return found
    return []


def _csindex_list_rows(payload: Any) -> tuple[list[Any], int, int | None]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, dict):
        candidate = data
    elif isinstance(payload, dict):
        candidate = payload
    else:
        return [], 0, None
    rows: list[Any] = []
    for key in ("data", "rows", "records", "list"):
        if isinstance(candidate.get(key), list):
            rows = candidate[key]
            break
    page = candidate.get("page") if isinstance(candidate.get("page"), dict) else candidate
    total = int(
        candidate.get("total")
        or candidate.get("totalCount")
        or page.get("total")
        or 0
    )
    current = (
        page.get("currentPage")
        or page.get("page")
        or candidate.get("currentPage")
    )
    return rows, total, int(current) if current is not None else None


def _csindex_detail(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    return payload if any(key in payload for key in ("title", "content", "publishDate")) else {}


def _safe_response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {
        str(key): str(value)
        for key, value in headers.items()
        if key.lower() not in {"set-cookie", "cookie", "authorization"}
    }


def _normalize_document_text(value: str) -> str:
    return re.sub(r"\s+", "", html.unescape(value)).replace("&nbsp;", "")


def _public_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: payload.get(key)
        for key in (
            "schema_version",
            "mode",
            "probe_id",
            "status",
            "generation_id",
            "content_hash",
            "request_count",
            "terminal_counts",
            "endpoint_dispositions",
            "safety",
            "manifest_path",
            "cache_hit",
        )
        if key in payload
    }


def _render(payload: Mapping[str, Any], *, pretty: bool) -> str:
    return json.dumps(
        dict(payload),
        ensure_ascii=False,
        indent=2 if pretty else None,
        sort_keys=True,
    )


def _probe_implementation_root() -> str:
    return canonical_hash(
        {
            "provider_probe.py": sha256_file(Path(provider_probe_module.__file__)),
            "run_provider_probe.py": sha256_file(Path(__file__)),
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())

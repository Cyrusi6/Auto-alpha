"""Signed CSI attachment capture with a bounded byte-range fallback.

The legacy attachment activity used one full-body HTTP exchange per logical
object.  A small set of old OSS objects returns headers quickly but stalls
while a full body is read.  This module defines a new capture identity that
always re-runs the complete current attachment plan.  A logical request first
attempts a normal GET and falls back to immutable 64 KiB ranges only when the
body times out, is truncated, or exceeds the locked body limit.

Every physical exchange has its own signed started/headers/terminal chain.
Range chunks are accepted only with a stable strong ETag, exact Content-Range,
and If-Range on every chunk after the first.  Failed partial chunks remain in
the raw logical envelope and are never presented as an assembled attachment.
This capture cannot authorize PIT membership or any research/trading stage.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import inspect
import json
import math
import os
import re
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from auto_alpha.platform.artifacts.storage import (
    atomic_json,
    canonical_hash,
    read_json,
    sha256_file,
)
from auto_alpha.platform.governance.network.signing import (
    PersistentReceiptSigner,
    ReceiptSigningError,
    verify_signature,
)

from . import free_provider_backfill as capture_module
from . import free_provider_csindex_backfill as csindex_backfill
from .free_provider_backfill import (
    BackfillResourceBudget,
    CaptureSigner,
    FreeProviderBackfillContract,
    NormalizedArtifact,
    _public_key_hash,
    _request_from_semantic,
    replay_normalized_artifacts,
    run_free_provider_backfill,
    validate_free_provider_backfill,
)
from .provider_probe import ProviderProbeObservation, ProviderProbeRequest
from .run_provider_probe import USER_AGENT


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

PHASE = "csindex-range-attachments"
CAPTURE_PROFILE = "csindex_attachment_archive_full_range_v1"
ACTIVITY_NAME = "free_domestic_csindex_range_attachments_2011_2019_v1"
ADAPTER_IDENTITY = "csindex_range_attachments_signed_multiexchange_capture_v1"
LEGACY_PHASE = "csindex-range-legacy-cons-repair"
LEGACY_CAPTURE_PROFILE = "csindex_legacy_cons_exact_range_repair_v1"
LEGACY_ACTIVITY_NAME = "free_domestic_csindex_range_legacy_cons_exact_repair_v1"
LEGACY_ADAPTER_IDENTITY = (
    "csindex_range_legacy_cons_exact_signed_multiexchange_capture_v1"
)
HTTP_IDENTITY = "python_urllib_no_redirect_strong_etag_range_v1"
LOGICAL_ENVELOPE_SCHEMA = "csindex_range_attachment_logical_envelope_v1"
SOURCE_BINDING_SCHEMA = "csindex_range_attachment_source_binding_v1"
NORMALIZATION_SCHEMA = "csindex_range_attachment_normalization_v1"
DURABLE_EXCHANGE_JOURNAL_NAME = "range_exchange_journal.jsonl"
APPROVED_CAPTURE_KEY_SHA256 = csindex_backfill.CSINDEX_APPROVED_CAPTURE_KEY_SHA256

EXPECTED_POPULATION_COUNT = 608
EXPECTED_REQUEST_COUNT = 439
EXPECTED_LEGACY_REQUEST_COUNT = 2
RANGE_CHUNK_BYTES = 65_536
ATTACHMENT_BODY_MAX_BYTES = 128 * 1024 * 1024
MAX_WIRE_EXCHANGES = 50_000
MAX_TOTAL_RESPONSE_BYTES = 16 * 1024 * 1024 * 1024
# One failed full-body prefix plus a complete ranged body may coexist in a raw
# logical envelope.  The object itself remains capped at 128 MiB.
MAX_LOGICAL_ENVELOPE_BYTES = 384 * 1024 * 1024
TIMEOUT_SECONDS = 30.0
MINIMUM_LOGICAL_DELAY_SECONDS = 2.0
MINIMUM_EXCHANGE_DELAY_SECONDS = 0.0
MAX_RETRIES = 2
TEMPORAL_BLOCKER = (
    "current_attachment_retrieval_does_not_prove_historical_known_at_or_vintage"
)
REQUIRED_CHECKS = (
    "logical_envelope_schema_exact",
    "logical_request_identity_bound",
    "wire_exchange_signatures_valid",
    "wire_exchange_protocol_valid",
    "attachment_complete",
    "attachment_hash_valid",
    "attachment_content_type_compatible",
    "attachment_magic_valid",
    "attachment_not_html_or_waf",
)
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_STRONG_ETAG = re.compile(r'^"[^"\r\n]+"$')
_CONTENT_RANGE = re.compile(r"^bytes (\d+)-(\d+)/(\d+)$")
_FALLBACK_STATES = frozenset(
    {"body_timeout", "body_truncated", "response_limit"}
)


@dataclass(frozen=True)
class _HttpExchangeResult:
    status_code: int | None
    response_headers: Mapping[str, str]
    body: bytes
    headers_received: bool
    completion_state: str
    error_code: str | None
    elapsed_seconds: float
    redirect_followed: bool = False


class _ExchangeClient(Protocol):
    def exchange(
        self,
        *,
        url: str,
        headers: dict[str, str],
        timeout_seconds: float,
        max_body_bytes: int,
    ) -> _HttpExchangeResult: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Mapping[str, str],
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


class _UrllibExchangeClient:
    """One no-proxy, no-redirect physical HTTP exchange."""

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirect(),
        )

    def exchange(
        self,
        *,
        url: str,
        headers: dict[str, str],
        timeout_seconds: float,
        max_body_bytes: int,
    ) -> _HttpExchangeResult:
        started = time.monotonic()
        upstream: Any | None = None
        status: int | None = None
        response_headers: dict[str, str] = {}
        headers_received = False
        body = bytearray()
        completion_state = "complete"
        error_code: str | None = None
        try:
            try:
                upstream = self._opener.open(
                    urllib.request.Request(
                        url,
                        headers=headers,
                        method="GET",
                    ),
                    timeout=timeout_seconds,
                )
            except urllib.error.HTTPError as exc:
                upstream = exc
            status = int(upstream.status if hasattr(upstream, "status") else upstream.code)
            response_headers = _normalized_headers(upstream.headers)
            headers_received = True
            declared_length = _nonnegative_header_int(
                response_headers.get("content-length")
            )
            if declared_length is not None and declared_length > max_body_bytes:
                completion_state = "response_limit"
                error_code = "csindex_range_attachment_response_limit"
            else:
                while True:
                    remaining = max_body_bytes + 1 - len(body)
                    if remaining <= 0:
                        completion_state = "response_limit"
                        error_code = "csindex_range_attachment_response_limit"
                        break
                    try:
                        chunk = upstream.read(min(64 * 1024, remaining))
                    except (TimeoutError, socket.timeout):
                        completion_state = "body_timeout"
                        error_code = "transport_exception:TimeoutError"
                        break
                    if not chunk:
                        break
                    body.extend(chunk)
            if completion_state == "complete":
                content_length = _nonnegative_header_int(
                    response_headers.get("content-length")
                )
                if content_length is not None and content_length != len(body):
                    completion_state = "body_truncated"
                    error_code = "csindex_range_attachment_body_truncated"
        except (TimeoutError, socket.timeout) as exc:
            error_code = "transport_exception:TimeoutError"
            completion_state = "headers_timeout"
            return _HttpExchangeResult(
                status_code=None,
                response_headers={},
                body=bytes(body),
                headers_received=False,
                completion_state=completion_state,
                error_code=error_code,
                elapsed_seconds=round(time.monotonic() - started, 6),
            )
        except (ConnectionError, OSError) as exc:
            return _HttpExchangeResult(
                status_code=status,
                response_headers=response_headers,
                body=bytes(body),
                headers_received=headers_received,
                completion_state="transport_error",
                error_code=f"transport_exception:{type(exc).__name__}",
                elapsed_seconds=round(time.monotonic() - started, 6),
            )
        finally:
            if upstream is not None:
                try:
                    upstream.close()
                except OSError:
                    pass
        return _HttpExchangeResult(
            status_code=status,
            response_headers=response_headers,
            body=bytes(body),
            headers_received=headers_received,
            completion_state=completion_state,
            error_code=error_code,
            elapsed_seconds=round(time.monotonic() - started, 6),
        )


@dataclass(frozen=True)
class _GenericAttemptContext:
    activity_id: str
    contract_id: str
    request_plan_hash: str
    attempt_id: str
    request_id: str
    request_semantic_hash: str
    retry_ordinal: int
    capture_started_at: str


class _DurableExchangeJournal:
    """Fsync every physical exchange edge before logical publication."""

    def __init__(
        self,
        *,
        contract: FreeProviderBackfillContract,
        requests: Sequence[ProviderProbeRequest],
        signer: CaptureSigner,
    ) -> None:
        self._contract = contract
        self._requests = tuple(requests)
        self._request_rows = [request.semantic() for request in requests]
        self._request_by_id = {request.request_id: request for request in requests}
        self._signer = signer
        self._contract_id = canonical_hash(contract.semantic())
        self._request_plan_hash = canonical_hash(self._request_rows)
        self._activity_id = canonical_hash(
            {
                "contract_id": self._contract_id,
                "request_plan_hash": self._request_plan_hash,
            }
        )
        output = Path(contract.output_root).resolve()
        self._run_root = (
            output.parent
            / f".{output.name}.activities"
            / self._activity_id
        )
        self.path = self._run_root / DURABLE_EXCHANGE_JOURNAL_NAME
        self._events: list[dict[str, Any]] | None = None
        self._current: _GenericAttemptContext | None = None

    def begin_logical_attempt(
        self, request: ProviderProbeRequest
    ) -> tuple[_GenericAttemptContext, list[dict[str, Any]]]:
        self._load_and_recover()
        context = self._current_generic_attempt(request)
        if self.started_count() != self._generic_terminal_wire_count():
            raise ValueError(
                "csindex_range_attachment_durable_generic_count_mismatch"
            )
        self._current = context
        prior = [
            exchange
            for exchange in self.physical_exchanges()
            if exchange["events"][0].get("request_id") == request.request_id
            and exchange["events"][0].get("generic_attempt_id")
            != context.attempt_id
        ]
        return context, prior

    def append_started(
        self,
        *,
        context: _GenericAttemptContext,
        ordinal: int,
        request: ProviderProbeRequest,
        headers: Mapping[str, str],
    ) -> dict[str, Any]:
        exchange_id = canonical_hash(
            {
                "activity_id": context.activity_id,
                "contract_id": context.contract_id,
                "request_plan_hash": context.request_plan_hash,
                "generic_attempt_id": context.attempt_id,
                "request_id": request.request_id,
                "request_semantic_hash": context.request_semantic_hash,
                "exchange_ordinal": ordinal,
                "request_headers": dict(sorted(headers.items())),
            }
        )
        event = self._append(
            context,
            {
                "schema_version": "csindex_range_exchange_event_v1",
                "event_type": "exchange_started",
                "event_sequence": 1,
                "exchange_id": exchange_id,
                "exchange_ordinal": ordinal,
                "request_id": request.request_id,
                "previous_event_hash": "",
                "observed_at": _utc_now(),
                "request": {
                    "url": request.url,
                    "method": "GET",
                    "headers": dict(sorted(headers.items())),
                },
            },
        )
        self._write_interrupted_checkpoint(context)
        return event

    def append_headers(
        self,
        *,
        context: _GenericAttemptContext,
        started: Mapping[str, Any],
        result: _HttpExchangeResult,
    ) -> dict[str, Any]:
        event = self._append(
            context,
            {
                "schema_version": "csindex_range_exchange_event_v1",
                "event_type": "exchange_headers",
                "event_sequence": 2,
                "exchange_id": started["exchange_id"],
                "exchange_ordinal": started["exchange_ordinal"],
                "request_id": context.request_id,
                "previous_event_hash": started["event_hash"],
                "observed_at": _utc_now(),
                "headers_received": result.headers_received,
                "status_code": result.status_code,
                "response_headers": dict(
                    sorted(_normalized_headers(result.response_headers).items())
                ),
                "redirect_followed": result.redirect_followed,
            },
        )
        self._write_interrupted_checkpoint(context)
        return event

    def append_terminal(
        self,
        *,
        context: _GenericAttemptContext,
        started: Mapping[str, Any],
        headers: Mapping[str, Any],
        result: _HttpExchangeResult,
    ) -> dict[str, Any]:
        event = self._append(
            context,
            {
                "schema_version": "csindex_range_exchange_event_v1",
                "event_type": "exchange_terminal",
                "event_sequence": 3,
                "exchange_id": started["exchange_id"],
                "exchange_ordinal": started["exchange_ordinal"],
                "request_id": context.request_id,
                "previous_event_hash": headers["event_hash"],
                "observed_at": _utc_now(),
                "completion_state": result.completion_state,
                "error_code": result.error_code,
                "elapsed_seconds": result.elapsed_seconds,
                "body_base64": base64.b64encode(result.body).decode("ascii"),
                "body_sha256": hashlib.sha256(result.body).hexdigest(),
                "body_size_bytes": len(result.body),
                "recovered_after_interruption": False,
            },
        )
        self._write_interrupted_checkpoint(context)
        return event

    def clear_interrupted_checkpoint(self) -> None:
        if self._current is None:
            return
        path = self._provisional_path(self._current)
        try:
            path.unlink()
        except FileNotFoundError:
            return
        _fsync_directory(path.parent)

    def started_count(self) -> int:
        self._load_and_recover()
        return sum(
            event.get("event_type") == "exchange_started"
            for event in (self._events or ())
        )

    def physical_exchanges(self) -> list[dict[str, Any]]:
        self._load_and_recover()
        grouped: dict[str, list[dict[str, Any]]] = {}
        order: list[str] = []
        for event in self._events or ():
            exchange_id = str(event.get("exchange_id") or "")
            if exchange_id not in grouped:
                grouped[exchange_id] = []
                order.append(exchange_id)
            grouped[exchange_id].append(event)
        rows: list[dict[str, Any]] = []
        for exchange_id in order:
            events = grouped[exchange_id]
            if [event.get("event_type") for event in events] != [
                "exchange_started",
                "exchange_headers",
                "exchange_terminal",
            ]:
                raise ValueError(
                    "csindex_range_attachment_durable_exchange_incomplete"
                )
            rows.append(
                {
                    "schema_version": "csindex_range_physical_exchange_v1",
                    "exchange_id": exchange_id,
                    "exchange_ordinal": events[0]["exchange_ordinal"],
                    "events": events,
                }
            )
        return rows

    def _load_and_recover(self) -> None:
        if self._events is not None:
            return
        _repair_durable_torn_tail(self.path)
        self._events = _read_durable_exchange_events(
            self.path,
            activity_id=self._activity_id,
            contract_id=self._contract_id,
            request_plan_hash=self._request_plan_hash,
            public_key=self._signer.public_key_pem,
        )
        grouped: dict[str, list[dict[str, Any]]] = {}
        for event in self._events:
            grouped.setdefault(str(event["exchange_id"]), []).append(event)
        for events in list(grouped.values()):
            if len(events) == 3:
                continue
            started = events[0]
            context = _context_from_exchange_event(started)
            if len(events) == 1:
                headers = self._append(
                    context,
                    {
                        "schema_version": "csindex_range_exchange_event_v1",
                        "event_type": "exchange_headers",
                        "event_sequence": 2,
                        "exchange_id": started["exchange_id"],
                        "exchange_ordinal": started["exchange_ordinal"],
                        "request_id": started["request_id"],
                        "previous_event_hash": started["event_hash"],
                        "observed_at": _utc_now(),
                        "headers_received": False,
                        "status_code": None,
                        "response_headers": {},
                        "redirect_followed": False,
                    },
                )
            elif len(events) == 2:
                headers = events[1]
            else:
                raise ValueError(
                    "csindex_range_attachment_durable_exchange_invalid"
                )
            self._append(
                context,
                {
                    "schema_version": "csindex_range_exchange_event_v1",
                    "event_type": "exchange_terminal",
                    "event_sequence": 3,
                    "exchange_id": started["exchange_id"],
                    "exchange_ordinal": started["exchange_ordinal"],
                    "request_id": started["request_id"],
                    "previous_event_hash": headers["event_hash"],
                    "observed_at": _utc_now(),
                    "completion_state": "ambiguous_after_interruption",
                    "error_code": "ambiguous_transport",
                    "elapsed_seconds": 0.0,
                    "body_base64": "",
                    "body_sha256": hashlib.sha256(b"").hexdigest(),
                    "body_size_bytes": 0,
                    "recovered_after_interruption": True,
                },
            )

    def _current_generic_attempt(
        self, request: ProviderProbeRequest
    ) -> _GenericAttemptContext:
        generic_path = self._run_root / capture_module.JOURNAL_NAME
        events = capture_module._read_and_validate_journal(
            generic_path,
            expected_activity_id=self._activity_id,
            expected_contract_id=self._contract_id,
            public_key=self._signer.public_key_pem,
            request_rows=self._request_rows,
            request_plan_hash=self._request_plan_hash,
            max_retries=self._contract.budget.max_retries,
        )
        terminal_ids = {
            str(event.get("attempt_id") or "")
            for event in events
            if event.get("event_type") == "capture_attempt_terminal"
        }
        candidates = [
            event
            for event in events
            if event.get("event_type") == "capture_attempt_started"
            and event.get("request_id") == request.request_id
            and str(event.get("attempt_id") or "") not in terminal_ids
        ]
        if len(candidates) != 1:
            raise ValueError(
                "csindex_range_attachment_generic_attempt_binding_invalid"
            )
        row = candidates[0]
        return _GenericAttemptContext(
            activity_id=self._activity_id,
            contract_id=self._contract_id,
            request_plan_hash=self._request_plan_hash,
            attempt_id=str(row["attempt_id"]),
            request_id=request.request_id,
            request_semantic_hash=str(row["request_semantic_hash"]),
            retry_ordinal=int(row["retry_ordinal"]),
            capture_started_at=str(row["capture_started_at"]),
        )

    def _generic_terminal_wire_count(self) -> int:
        events = capture_module._read_and_validate_journal(
            self._run_root / capture_module.JOURNAL_NAME,
            expected_activity_id=self._activity_id,
            expected_contract_id=self._contract_id,
            public_key=self._signer.public_key_pem,
            request_rows=self._request_rows,
            request_plan_hash=self._request_plan_hash,
            max_retries=self._contract.budget.max_retries,
        )
        total = 0
        for event in events:
            if event.get("event_type") != "capture_attempt_terminal":
                continue
            count = event.get("transport_exchange_count")
            if (
                not isinstance(count, int)
                or isinstance(count, bool)
                or count < 0
            ):
                raise ValueError(
                    "csindex_range_attachment_generic_wire_count_invalid"
                )
            total += count
        return total

    def _append(
        self,
        context: _GenericAttemptContext,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        if self._events is None:
            raise ValueError("csindex_range_attachment_durable_journal_not_loaded")
        previous = self._events[-1] if self._events else {}
        unsigned = dict(payload) | {
            "activity_id": context.activity_id,
            "contract_id": context.contract_id,
            "request_plan_hash": context.request_plan_hash,
            "generic_attempt_id": context.attempt_id,
            "generic_retry_ordinal": context.retry_ordinal,
            "generic_request_semantic_hash": context.request_semantic_hash,
            "journal_sequence": len(self._events) + 1,
            "previous_journal_event_hash": str(
                previous.get("event_hash") or ""
            ),
        }
        event = _signed_exchange_event(self._signer, unsigned)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("ab") as handle:
            handle.write(_json_bytes(event) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_directory(self.path.parent)
        self._events.append(event)
        return event

    def _write_interrupted_checkpoint(
        self, context: _GenericAttemptContext
    ) -> None:
        current_events = [
            event
            for event in self._events or ()
            if event.get("generic_attempt_id") == context.attempt_id
        ]
        started_count = sum(
            event.get("event_type") == "exchange_started"
            for event in current_events
        )
        raw = _json_bytes(
            {
                "schema_version": (
                    "csindex_range_attachment_interrupted_attempt_v1"
                ),
                "activity_id": context.activity_id,
                "contract_id": context.contract_id,
                "request_plan_hash": context.request_plan_hash,
                "attempt_id": context.attempt_id,
                "request_id": context.request_id,
                "durable_events": current_events,
                "transport_exchange_count": started_count,
            }
        )
        wrapper = {
            "schema_version": "free_provider_backfill_raw_envelope_v1",
            "attempt_id": context.attempt_id,
            "request_id": context.request_id,
            "request_semantic_hash": context.request_semantic_hash,
            "retry_ordinal": context.retry_ordinal,
            "capture_started_at": context.capture_started_at,
            "capture_completed_at": _utc_now(),
            "terminal_state": "error",
            "row_count": None,
            "status_code": None,
            "error_code": "ambiguous_transport",
            "diagnostics": {
                "durable_exchange_started_count": started_count,
                "assembled_attachment_present": False,
            },
            "checks": {"transport_completed": False},
            "transport_exchange_count": started_count,
            "raw_payload_base64": base64.b64encode(raw).decode("ascii"),
            "raw_payload_sha256": hashlib.sha256(raw).hexdigest(),
            "raw_payload_size_bytes": len(raw),
        }
        atomic_json(self._provisional_path(context), wrapper)

    def _provisional_path(self, context: _GenericAttemptContext) -> Path:
        return self._run_root / capture_module._raw_relative_path(
            context.attempt_id
        )


class _RangeAttachmentTransport:
    """Turn one logical attachment request into signed physical exchanges."""

    def __init__(
        self,
        *,
        signer: CaptureSigner,
        client: _ExchangeClient,
        minimum_exchange_delay_seconds: float,
        max_wire_exchanges: int,
        durable_journal: _DurableExchangeJournal,
    ) -> None:
        self._signer = signer
        self._client = client
        self._minimum_delay = minimum_exchange_delay_seconds
        self._max_wire_exchanges = max_wire_exchanges
        self._durable_journal = durable_journal
        self._used_wire_exchanges = 0
        self._last_exchange_at: float | None = None
        self._current_context: _GenericAttemptContext | None = None
        self._prior_exchanges: list[dict[str, Any]] = []

    def __call__(
        self,
        request: ProviderProbeRequest,
        timeout_seconds: float,
    ) -> ProviderProbeObservation:
        context, prior = self._durable_journal.begin_logical_attempt(request)
        self._current_context = context
        self._prior_exchanges = prior
        self._used_wire_exchanges = max(
            self._used_wire_exchanges,
            self._durable_journal.started_count(),
        )
        exchanges: list[dict[str, Any]] = []
        full = self._physical_exchange(
            request=request,
            ordinal=0,
            headers=dict(request.headers),
            timeout_seconds=timeout_seconds,
        )
        if full is None:
            return self._error_observation(
                request,
                exchanges,
                "csindex_range_attachment_exchange_budget_exhausted",
            )
        exchanges.append(full)
        full_result = _exchange_result(full)
        if (
            full_result.status_code == 200
            and full_result.completion_state == "complete"
        ):
            reason = _assembled_attachment_reason(
                full_result.body,
                extension=str(request.metadata.get("extension") or ""),
                content_type=_header(
                    full_result.response_headers, "content-type"
                ),
            )
            if (
                reason is None
                and _content_length_matches(
                    _header(full_result.response_headers, "content-length"),
                    len(full_result.body),
                )
                and _identity_content_encoding(full_result.response_headers)
            ):
                return self._positive_observation(
                    request,
                    exchanges,
                    body=full_result.body,
                    retrieval_method="full_get",
                )
            return self._error_observation(
                request,
                exchanges,
                reason
                or "csindex_range_attachment_full_get_wire_evidence_invalid",
                status_code=full_result.status_code,
            )
        if not (
            full_result.headers_received
            and full_result.status_code == 200
            and full_result.completion_state in _FALLBACK_STATES
        ):
            return self._error_observation(
                request,
                exchanges,
                full_result.error_code
                or f"http_status:{full_result.status_code}",
                status_code=full_result.status_code,
            )

        assembled = bytearray()
        total_size: int | None = None
        etag: str | None = None
        content_type: str | None = None
        start = 0
        ordinal = 1
        while total_size is None or start < total_size:
            if total_size is not None:
                end = min(start + RANGE_CHUNK_BYTES - 1, total_size - 1)
            else:
                end = start + RANGE_CHUNK_BYTES - 1
            headers = dict(request.headers) | {"Range": f"bytes={start}-{end}"}
            if etag is not None:
                headers["If-Range"] = etag
            exchange = self._physical_exchange(
                request=request,
                ordinal=ordinal,
                headers=headers,
                timeout_seconds=timeout_seconds,
            )
            if exchange is None:
                return self._error_observation(
                    request,
                    exchanges,
                    "csindex_range_attachment_exchange_budget_exhausted",
                )
            exchanges.append(exchange)
            result = _exchange_result(exchange)
            response_etag = _header(result.response_headers, "etag")
            parsed = _parse_content_range(
                _header(result.response_headers, "content-range")
            )
            expected_end = (
                min(start + RANGE_CHUNK_BYTES - 1, parsed[2] - 1)
                if parsed is not None
                else -1
            )
            range_type = _header(result.response_headers, "content-type")
            valid = bool(
                result.headers_received
                and result.status_code == 206
                and result.completion_state == "complete"
                and parsed is not None
                and parsed[0] == start
                and parsed[1] == expected_end
                and parsed[2] <= ATTACHMENT_BODY_MAX_BYTES
                and len(result.body) == parsed[1] - parsed[0] + 1
                and _content_length_matches(
                    _header(result.response_headers, "content-length"),
                    len(result.body),
                )
                and _identity_content_encoding(result.response_headers)
                and _strong_etag(response_etag)
                and (etag is None or response_etag == etag)
                and (total_size is None or parsed[2] == total_size)
                and (content_type is None or range_type == content_type)
            )
            if not valid:
                return self._error_observation(
                    request,
                    exchanges,
                    result.error_code
                    or "csindex_range_attachment_range_identity_or_geometry_invalid",
                    status_code=result.status_code,
                )
            if etag is None:
                etag = response_etag
                total_size = parsed[2]
                content_type = range_type
                if total_size <= 0:
                    return self._error_observation(
                        request,
                        exchanges,
                        "csindex_range_attachment_total_size_invalid",
                        status_code=result.status_code,
                    )
            assembled.extend(result.body)
            start = parsed[1] + 1
            ordinal += 1

        body = bytes(assembled)
        reason = _assembled_attachment_reason(
            body,
            extension=str(request.metadata.get("extension") or ""),
            content_type=content_type,
        )
        if reason is not None or len(body) != total_size:
            return self._error_observation(
                request,
                exchanges,
                reason or "csindex_range_attachment_assembly_size_invalid",
                status_code=206,
            )
        return self._positive_observation(
            request,
            exchanges,
            body=body,
            retrieval_method="range_if_range",
            strong_etag=etag,
        )

    def restore(
        self,
        request: ProviderProbeRequest,
        record: Mapping[str, Any],
    ) -> None:
        count = record.get("transport_exchange_count")
        if (
            record.get("request_id") != request.request_id
            or record.get("terminal_state") != "positive"
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count <= 0
        ):
            raise ValueError("csindex_range_attachment_restore_envelope_invalid")
        self._used_wire_exchanges += count
        if self._used_wire_exchanges > self._max_wire_exchanges:
            raise ValueError("csindex_range_attachment_restore_budget_invalid")

    def _physical_exchange(
        self,
        *,
        request: ProviderProbeRequest,
        ordinal: int,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> dict[str, Any] | None:
        if self._used_wire_exchanges >= self._max_wire_exchanges:
            return None
        if self._last_exchange_at is not None:
            remaining = self._minimum_delay - (
                time.monotonic() - self._last_exchange_at
            )
            if remaining > 0:
                time.sleep(remaining)
        context = self._current_context
        if context is None:
            raise ValueError(
                "csindex_range_attachment_generic_attempt_context_missing"
            )
        started = self._durable_journal.append_started(
            context=context,
            ordinal=ordinal,
            request=request,
            headers=headers,
        )
        try:
            result = self._client.exchange(
                url=request.url,
                headers=dict(headers),
                timeout_seconds=timeout_seconds,
                max_body_bytes=ATTACHMENT_BODY_MAX_BYTES,
            )
        except Exception as exc:
            result = _HttpExchangeResult(
                status_code=None,
                response_headers={},
                body=b"",
                headers_received=False,
                completion_state="transport_error",
                error_code=f"transport_exception:{type(exc).__name__}",
                elapsed_seconds=0.0,
            )
        self._last_exchange_at = time.monotonic()
        header_event = self._durable_journal.append_headers(
            context=context,
            started=started,
            result=result,
        )
        terminal_event = self._durable_journal.append_terminal(
            context=context,
            started=started,
            headers=header_event,
            result=result,
        )
        self._used_wire_exchanges += 1
        return {
            "schema_version": "csindex_range_physical_exchange_v1",
            "exchange_id": started["exchange_id"],
            "exchange_ordinal": ordinal,
            "events": [started, header_event, terminal_event],
        }

    def _positive_observation(
        self,
        request: ProviderProbeRequest,
        exchanges: Sequence[Mapping[str, Any]],
        *,
        body: bytes,
        retrieval_method: str,
        strong_etag: str | None = None,
    ) -> ProviderProbeObservation:
        envelope = _logical_envelope(
            request,
            exchanges,
            prior_exchanges=self._prior_exchanges,
            terminal_state="positive",
            error_code=None,
            retrieval_method=retrieval_method,
            attachment_sha256=hashlib.sha256(body).hexdigest(),
            attachment_size_bytes=len(body),
            strong_etag=strong_etag,
        )
        observation = ProviderProbeObservation(
            terminal_state="positive",
            raw_payload=_json_bytes(envelope),
            row_count=1,
            status_code=200,
            error_code=None,
            diagnostics={
                "attachment_sha256": hashlib.sha256(body).hexdigest(),
                "attachment_size_bytes": len(body),
                "retrieval_method": retrieval_method,
                "strong_etag": strong_etag,
            },
            checks={name: True for name in REQUIRED_CHECKS},
            transport_exchange_count=len(exchanges),
        )
        self._durable_journal.clear_interrupted_checkpoint()
        return observation

    def _error_observation(
        self,
        request: ProviderProbeRequest,
        exchanges: Sequence[Mapping[str, Any]],
        error_code: str,
        *,
        status_code: int | None = None,
    ) -> ProviderProbeObservation:
        envelope = _logical_envelope(
            request,
            exchanges,
            prior_exchanges=self._prior_exchanges,
            terminal_state="error",
            error_code=error_code,
            retrieval_method=None,
            attachment_sha256=None,
            attachment_size_bytes=None,
            strong_etag=None,
        )
        waf = any(
            _looks_like_waf(_exchange_result(row).body)
            for row in exchanges
        )
        observation = ProviderProbeObservation(
            terminal_state="error",
            raw_payload=_json_bytes(envelope),
            row_count=None,
            status_code=status_code,
            error_code=error_code,
            diagnostics={
                "partial_exchange_count": len(exchanges),
                "partial_bytes_retained": sum(
                    len(_exchange_result(row).body) for row in exchanges
                ),
                "assembled_attachment_present": False,
                "waf_html_observed": waf,
            },
            checks={name: False for name in REQUIRED_CHECKS},
            transport_exchange_count=len(exchanges),
        )
        self._durable_journal.clear_interrupted_checkpoint()
        return observation


def _prepare_from_governed_details(
    details_capture: str | Path,
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    list[ProviderProbeRequest],
    str,
]:
    governed = csindex_backfill.validate_csindex_governance(details_capture)
    ancestry = governed.get("csindex_downstream_ancestry")
    if (
        governed.get("status") != "succeeded"
        or governed.get("csindex_phase") != "csindex-details"
        or governed.get("csindex_downstream_eligible") is not True
        or not isinstance(ancestry, Mapping)
        or ancestry.get("source_stage") != "details_capture"
        or ancestry.get("weak_source_ancestry") is not False
    ):
        raise ValueError("csindex_range_attachment_strong_details_required")
    population, legacy_requests, legacy_input_root = (
        csindex_backfill.build_csindex_attachment_plan(details_capture)
    )
    _validate_population_geometry(
        population, legacy_requests, capture_profile=CAPTURE_PROFILE
    )
    if any(
        request.metadata.get("source_ancestry") != ancestry
        or request.metadata.get("capture_profile")
        != csindex_backfill.CSINDEX_ATTACHMENT_FULL_PROFILE
        or request.metadata.get("profile_complete") is not True
        for request in legacy_requests
    ):
        raise ValueError("csindex_range_attachment_legacy_plan_lineage_invalid")
    binding = _range_source_binding(
        governed,
        population=population,
        legacy_input_root=legacy_input_root,
        legacy_request_plan_root=canonical_hash(
            [request.semantic() for request in legacy_requests]
        ),
    )
    requests = _range_attachment_requests(population, binding)
    return governed, population, requests, str(binding["content_hash"])


def _prepare_legacy_from_governed_details(
    details_capture: str | Path,
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    list[ProviderProbeRequest],
    str,
]:
    governed = csindex_backfill.validate_csindex_governance(details_capture)
    ancestry = governed.get("csindex_downstream_ancestry")
    if (
        governed.get("status") != "succeeded"
        or governed.get("csindex_phase") != "csindex-details"
        or governed.get("csindex_downstream_eligible") is not True
        or not isinstance(ancestry, Mapping)
        or ancestry.get("source_stage") != "details_capture"
        or ancestry.get("weak_source_ancestry") is not False
    ):
        raise ValueError("csindex_range_attachment_strong_details_required")
    population, legacy_requests, legacy_input_root = (
        csindex_backfill.build_csindex_legacy_cons_repair_plan(details_capture)
    )
    _validate_population_geometry(
        population,
        legacy_requests,
        capture_profile=LEGACY_CAPTURE_PROFILE,
    )
    if any(
        request.metadata.get("source_ancestry") != ancestry
        or request.metadata.get("capture_profile")
        != csindex_backfill.CSINDEX_LEGACY_CONS_REPAIR_PROFILE
        or request.metadata.get("profile_complete") is not True
        for request in legacy_requests
    ):
        raise ValueError("csindex_range_legacy_cons_plan_lineage_invalid")
    binding = _range_source_binding(
        governed,
        population=population,
        legacy_input_root=legacy_input_root,
        legacy_request_plan_root=canonical_hash(
            [request.semantic() for request in legacy_requests]
        ),
        capture_profile=LEGACY_CAPTURE_PROFILE,
    )
    requests = _range_attachment_requests(population, binding)
    return governed, population, requests, str(binding["content_hash"])


def _range_source_binding(
    details: Mapping[str, Any],
    *,
    population: Sequence[Mapping[str, Any]],
    legacy_input_root: str,
    legacy_request_plan_root: str,
    capture_profile: str = CAPTURE_PROFILE,
) -> dict[str, Any]:
    ancestry = details.get("csindex_downstream_ancestry")
    eligible = [
        dict(row)
        for row in population
        if row.get("reference_disposition") == "capture_eligible"
    ]
    semantic = {
        "schema_version": SOURCE_BINDING_SCHEMA,
        "details_generation_id": details.get("generation_id"),
        "details_content_hash": details.get("content_hash"),
        "details_contract_id": details.get("contract_id"),
        "details_request_plan_hash": details.get("request_plan_hash"),
        "details_activity_id": details.get("activity_id"),
        "details_ancestry": ancestry,
        "details_ancestry_root": (
            ancestry.get("ancestry_root") if isinstance(ancestry, Mapping) else None
        ),
        "legacy_attachment_input_root": legacy_input_root,
        "legacy_attachment_request_plan_root": legacy_request_plan_root,
        "range_phase": (
            LEGACY_PHASE
            if capture_profile == LEGACY_CAPTURE_PROFILE
            else PHASE
        ),
        "range_capture_profile": capture_profile,
        "population_contract": (
            "legacy_cons_exact_two"
            if capture_profile == LEGACY_CAPTURE_PROFILE
            else "full_archive_608_population_439_eligible"
        ),
        "legacy_repair_profile_root": (
            canonical_hash(csindex_backfill.CSINDEX_LEGACY_CONS_REPAIR_ROWS)
            if capture_profile == LEGACY_CAPTURE_PROFILE
            else None
        ),
        "legacy_repair_population_root": (
            canonical_hash(list(population))
            if capture_profile == LEGACY_CAPTURE_PROFILE
            else None
        ),
        "population_root": canonical_hash(list(population)),
        "eligible_population_root": canonical_hash(eligible),
        "population_count": len(population),
        "eligible_count": len(eligible),
        "implementation_root": _implementation_root(),
    }
    _validate_source_binding_semantic(semantic)
    return semantic | {"content_hash": canonical_hash(semantic)}


def _validate_source_binding(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("csindex_range_attachment_source_binding_invalid")
    semantic = {key: item for key, item in value.items() if key != "content_hash"}
    if (
        set(value) != set(semantic) | {"content_hash"}
        or value.get("content_hash") != canonical_hash(semantic)
    ):
        raise ValueError("csindex_range_attachment_source_binding_invalid")
    _validate_source_binding_semantic(semantic)
    return dict(value)


def _validate_source_binding_semantic(value: Mapping[str, Any]) -> None:
    expected_keys = {
        "schema_version",
        "details_generation_id",
        "details_content_hash",
        "details_contract_id",
        "details_request_plan_hash",
        "details_activity_id",
        "details_ancestry",
        "details_ancestry_root",
        "legacy_attachment_input_root",
        "legacy_attachment_request_plan_root",
        "range_phase",
        "range_capture_profile",
        "population_contract",
        "legacy_repair_profile_root",
        "legacy_repair_population_root",
        "population_root",
        "eligible_population_root",
        "population_count",
        "eligible_count",
        "implementation_root",
    }
    ancestry = value.get("details_ancestry")
    capture_profile = value.get("range_capture_profile")
    legacy = capture_profile == LEGACY_CAPTURE_PROFILE
    if (
        set(value) != expected_keys
        or value.get("schema_version") != SOURCE_BINDING_SCHEMA
        or capture_profile not in {CAPTURE_PROFILE, LEGACY_CAPTURE_PROFILE}
        or value.get("range_phase") != (LEGACY_PHASE if legacy else PHASE)
        or value.get("population_contract")
        != (
            "legacy_cons_exact_two"
            if legacy
            else "full_archive_608_population_439_eligible"
        )
        or value.get("legacy_repair_profile_root")
        != (
            canonical_hash(csindex_backfill.CSINDEX_LEGACY_CONS_REPAIR_ROWS)
            if legacy
            else None
        )
        or (
            legacy
            and _HEX_64.fullmatch(
                str(value.get("legacy_repair_population_root") or "")
            )
            is None
        )
        or (
            not legacy
            and value.get("legacy_repair_population_root") is not None
        )
        or not isinstance(ancestry, Mapping)
        or ancestry.get("source_stage") != "details_capture"
        or ancestry.get("weak_source_ancestry") is not False
        or value.get("details_ancestry_root") != ancestry.get("ancestry_root")
        or any(
            _HEX_64.fullmatch(str(value.get(key) or "")) is None
            for key in (
                "details_content_hash",
                "details_contract_id",
                "details_request_plan_hash",
                "details_activity_id",
                "details_ancestry_root",
                "legacy_attachment_input_root",
                "legacy_attachment_request_plan_root",
                "population_root",
                "eligible_population_root",
                "implementation_root",
            )
        )
        or value.get("details_generation_id")
        != "free_provider_backfill_"
        + str(value.get("details_content_hash") or "")[:24]
        or not isinstance(value.get("population_count"), int)
        or isinstance(value.get("population_count"), bool)
        or not isinstance(value.get("eligible_count"), int)
        or isinstance(value.get("eligible_count"), bool)
        or int(value.get("population_count") or 0) <= 0
        or int(value.get("eligible_count") or 0) <= 0
    ):
        raise ValueError("csindex_range_attachment_source_binding_invalid")


def _range_attachment_requests(
    population: Sequence[Mapping[str, Any]],
    source_binding: Mapping[str, Any],
) -> list[ProviderProbeRequest]:
    binding = _validate_source_binding(source_binding)
    capture_profile = str(binding["range_capture_profile"])
    eligible = [
        dict(row)
        for row in population
        if row.get("reference_disposition") == "capture_eligible"
    ]
    blocked = [
        dict(row)
        for row in population
        if row.get("reference_disposition") != "capture_eligible"
    ]
    if (
        binding["population_root"] != canonical_hash(list(population))
        or binding["eligible_population_root"] != canonical_hash(eligible)
        or binding["population_count"] != len(population)
        or binding["eligible_count"] != len(eligible)
        or (
            capture_profile == LEGACY_CAPTURE_PROFILE
            and binding.get("legacy_repair_population_root")
            != canonical_hash(list(population))
        )
    ):
        raise ValueError("csindex_range_attachment_source_population_mismatch")
    requests = [
        ProviderProbeRequest(
            request_id="csindex_range_attachment_"
            + hashlib.sha256(str(row["attachment_url"]).encode()).hexdigest()[:24],
            provider="csindex",
            endpoint="index_rebalance_announcement_attachment_range_v1",
            method="GET",
            url=str(row["attachment_url"]),
            headers={
                "Accept-Encoding": "identity",
                "Referer": "https://www.csindex.com.cn/",
                "User-Agent": USER_AGENT,
            },
            disposition="bounded_backfill",
            evidence_semantics=LOGICAL_ENVELOPE_SCHEMA,
            expected_terminal_states=("positive",),
            required_checks=REQUIRED_CHECKS,
            metadata={
                "case": "csindex_range_attachment",
                "capture_profile": capture_profile,
                "profile_complete": True,
                "extension": row.get("extension"),
                "attachment_host": row.get("host"),
                "path_dates": row.get("path_dates"),
                "source_announcements": row.get("source_announcements"),
                "reference_disposition": "capture_eligible",
                "population_count": len(population),
                "population_root": canonical_hash(list(population)),
                "blocked_reference_count": len(blocked),
                "blocked_reference_root": canonical_hash(blocked),
                "source_binding": binding,
                "historical_known_at_proven": False,
                "pit_membership_authorized": False,
                "temporal_blocker": TEMPORAL_BLOCKER,
            },
        )
        for row in eligible
    ]
    if not requests:
        raise ValueError("csindex_range_attachment_plan_empty")
    requests[0] = ProviderProbeRequest(
        **{
            **requests[0].__dict__,
            "metadata": dict(requests[0].metadata)
            | {
                "attachment_population": [dict(row) for row in population],
                "blocked_references": blocked,
            },
        }
    )
    return requests


def run_csindex_range_attachment_capture(
    details_capture: str | Path,
    allow_network: bool,
    plan_only: bool = False,
) -> dict[str, Any]:
    """Plan or execute the new complete 439-object attachment activity."""

    details, population, requests, input_root = _prepare_from_governed_details(
        details_capture
    )
    _validate_population_geometry(population, requests)
    population_root = canonical_hash(
        {
            "population": population,
            "input_capture_content_hash": input_root,
        }
    )
    preview = {
        "schema_version": "csindex_range_attachment_plan_preview_v1",
        "phase": PHASE,
        "capture_profile": CAPTURE_PROFILE,
        "population_count": len(population),
        "population_root": population_root,
        "request_count": len(requests),
        "request_plan_hash": canonical_hash(
            [request.semantic() for request in requests]
        ),
        "input_capture_content_hash": input_root,
        "source_details_generation_id": details.get("generation_id"),
        "max_wire_exchanges": MAX_WIRE_EXCHANGES,
        "network_called": False,
    }
    if plan_only:
        return preview
    if not allow_network:
        return preview | {
            "status": "blocked",
            "reason": "free_provider_backfill_network_authority_missing",
        }
    signer = _load_capture_signer()
    contract = _contract(
        output_root=SCOPE_ROOT / "csindex" / "range_attachments",
        signer=signer,
        population_root=population_root,
        request_count=len(requests),
        input_root=input_root,
        details=details,
    )
    _authorize_contract_before_network(
        contract,
        request_count=len(requests),
        expected_profile=CAPTURE_PROFILE,
        signer=signer,
    )
    durable_journal = _DurableExchangeJournal(
        contract=contract,
        requests=requests,
        signer=signer,
    )
    transport = _RangeAttachmentTransport(
        signer=signer,
        client=_new_exchange_client(),
        minimum_exchange_delay_seconds=MINIMUM_EXCHANGE_DELAY_SECONDS,
        max_wire_exchanges=MAX_WIRE_EXCHANGES,
        durable_journal=durable_journal,
    )
    published = run_free_provider_backfill(
        contract,
        requests,
        transport=transport,
        signer=signer,
        normalizer=_normalize_range_attachments,
        runtime_implementation_root=_implementation_root(),
    )
    return validate_csindex_range_attachment_capture(published["manifest_path"])


def run_csindex_range_legacy_cons_capture(
    details_capture: str | Path,
    allow_network: bool,
    plan_only: bool = False,
) -> dict[str, Any]:
    """Plan or execute the exact two-object legacy constituent repair."""

    details, population, requests, input_root = (
        _prepare_legacy_from_governed_details(details_capture)
    )
    _validate_population_geometry(
        population,
        requests,
        capture_profile=LEGACY_CAPTURE_PROFILE,
    )
    population_root = canonical_hash(
        {
            "population": population,
            "input_capture_content_hash": input_root,
        }
    )
    preview = {
        "schema_version": "csindex_range_legacy_cons_plan_preview_v1",
        "phase": LEGACY_PHASE,
        "capture_profile": LEGACY_CAPTURE_PROFILE,
        "population_count": len(population),
        "population_root": population_root,
        "request_count": len(requests),
        "request_plan_hash": canonical_hash(
            [request.semantic() for request in requests]
        ),
        "input_capture_content_hash": input_root,
        "source_details_generation_id": details.get("generation_id"),
        "legacy_repair_profile_root": canonical_hash(
            csindex_backfill.CSINDEX_LEGACY_CONS_REPAIR_ROWS
        ),
        "max_wire_exchanges": MAX_WIRE_EXCHANGES,
        "network_called": False,
    }
    if plan_only:
        return preview
    if not allow_network:
        return preview | {
            "status": "blocked",
            "reason": "free_provider_backfill_network_authority_missing",
        }
    signer = _load_capture_signer()
    contract = _contract(
        output_root=SCOPE_ROOT / "csindex" / "range_legacy_cons_repair",
        signer=signer,
        population_root=population_root,
        request_count=len(requests),
        input_root=input_root,
        details=details,
        capture_profile=LEGACY_CAPTURE_PROFILE,
    )
    _authorize_contract_before_network(
        contract,
        request_count=len(requests),
        expected_profile=LEGACY_CAPTURE_PROFILE,
        signer=signer,
    )
    durable_journal = _DurableExchangeJournal(
        contract=contract,
        requests=requests,
        signer=signer,
    )
    transport = _RangeAttachmentTransport(
        signer=signer,
        client=_new_exchange_client(),
        minimum_exchange_delay_seconds=MINIMUM_EXCHANGE_DELAY_SECONDS,
        max_wire_exchanges=MAX_WIRE_EXCHANGES,
        durable_journal=durable_journal,
    )
    published = run_free_provider_backfill(
        contract,
        requests,
        transport=transport,
        signer=signer,
        normalizer=_normalize_range_attachments,
        runtime_implementation_root=_implementation_root(),
    )
    return validate_csindex_range_legacy_cons_capture(
        published["manifest_path"]
    )


def replay_csindex_range_attachment_capture(
    path: str | Path,
) -> tuple[dict[str, bytes], str]:
    """Replay every normalized role only from signed raw logical envelopes."""

    return replay_normalized_artifacts(
        path,
        normalizer=_normalize_range_attachments,
        required_roles=(
            "csindex_range_attachment_index",
            "csindex_range_wire_exchange_index",
            "csindex_range_blocked_reference_index",
            "normalized_manifest",
        ),
    )


def replay_csindex_range_legacy_cons_capture(
    path: str | Path,
) -> tuple[dict[str, bytes], str]:
    """Replay the exact legacy-cons range generation from signed raw bytes."""

    return replay_csindex_range_attachment_capture(path)


def validate_csindex_range_attachment_capture(path: str | Path) -> dict[str, Any]:
    """Validate contract, signatures, ranges, replay bytes and real ancestry."""

    return _validate_range_capture(path, expected_profile=CAPTURE_PROFILE)


def validate_csindex_range_legacy_cons_capture(path: str | Path) -> dict[str, Any]:
    """Validate the independently governed exact-two legacy repair."""

    return _validate_range_capture(
        path,
        expected_profile=LEGACY_CAPTURE_PROFILE,
    )


def _validate_range_capture(
    path: str | Path,
    *,
    expected_profile: str,
) -> dict[str, Any]:
    if expected_profile not in {CAPTURE_PROFILE, LEGACY_CAPTURE_PROFILE}:
        raise ValueError("csindex_range_attachment_capture_profile_invalid")

    validated = validate_free_provider_backfill(path)
    if validated.get("status") != "succeeded":
        raise ValueError("csindex_range_attachment_capture_blocked")
    manifest_path = Path(str(validated["manifest_path"]))
    root = manifest_path.parent
    contract = read_json(root / capture_module.CONTRACT_NAME)
    plan = read_json(root / capture_module.PLAN_NAME)
    requests = [
        _request_from_semantic(row) for row in plan.get("requests") or ()
    ]
    if root.parent.parent.resolve() != _expected_output_root(
        expected_profile
    ).resolve():
        raise ValueError("csindex_range_attachment_output_geometry_invalid")
    _validate_authorized_contract(
        contract,
        request_count=len(requests),
        expected_profile=expected_profile,
    )
    population, binding = _request_plan_evidence(requests)
    if binding.get("range_capture_profile") != expected_profile:
        raise ValueError("csindex_range_attachment_capture_profile_mismatch")
    adapter = contract.get("adapter_identity") or {}
    expected_population_root = canonical_hash(
        {
            "population": population,
            "input_capture_content_hash": binding["content_hash"],
        }
    )
    if (
        adapter.get("input_capture_content_hash") != binding["content_hash"]
        or adapter.get("source_details_generation_id")
        != binding.get("details_generation_id")
        or adapter.get("source_details_content_hash")
        != binding.get("details_content_hash")
        or adapter.get("implementation_root")
        != binding.get("implementation_root")
        or contract.get("population_root") != expected_population_root
    ):
        raise ValueError("csindex_range_attachment_contract_source_binding_invalid")
    _validate_population_geometry(
        population,
        requests,
        capture_profile=str(binding["range_capture_profile"]),
    )
    parent = _validate_details_parent(manifest_path, binding)
    _validate_rebuilt_parent_plan(
        parent,
        population=population,
        requests=requests,
        binding=binding,
        capture_profile=expected_profile,
    )
    replayed, replay_root = replay_csindex_range_attachment_capture(manifest_path)
    artifacts = {
        str(row.get("role") or ""): row
        for row in validated.get("normalized_artifacts") or ()
    }
    for role, payload in replayed.items():
        artifact = artifacts.get(role)
        if artifact is None:
            raise ValueError("csindex_range_attachment_normalized_role_missing")
        published_path = root / str(artifact.get("relative_path") or "")
        if not published_path.is_file() or published_path.read_bytes() != payload:
            raise ValueError(
                "csindex_range_attachment_normalized_replay_bytes_mismatch"
            )
    durable_artifact = artifacts.get(
        "csindex_range_durable_exchange_journal"
    )
    if (
        durable_artifact is None
        or durable_artifact.get("relative_path")
        != DURABLE_EXCHANGE_JOURNAL_NAME
    ):
        raise ValueError("csindex_range_attachment_durable_artifact_missing")
    wire_rows = [
        _exact_json_object(line)
        for line in replayed["csindex_range_wire_exchange_index"].splitlines()
        if line.strip()
    ]
    durable_event_count, durable_exchange_count = _validate_durable_closure(
        root,
        requests=requests,
        public_key=base64.b64decode(
            str(contract.get("capture_public_key_pem_b64") or ""),
            validate=True,
        ),
        expected_wire_rows=wire_rows,
    )
    terminal_exchange_count, terminal_attempt_map = (
        _validate_all_terminal_attempt_counts(
        root,
        requests=requests,
        public_key=base64.b64decode(
            str(contract.get("capture_public_key_pem_b64") or ""),
            validate=True,
        ),
        expected_wire_rows=wire_rows,
        )
    )
    _validate_durable_attempt_map(
        wire_rows, terminal_attempt_map=terminal_attempt_map
    )
    normalized_manifest = json.loads(replayed["normalized_manifest"])
    if (
        normalized_manifest.get("source_binding") != binding
        or normalized_manifest.get("population_count") != len(population)
        or normalized_manifest.get("attachment_count") != len(requests)
        or normalized_manifest.get("profile_complete") is not True
        or normalized_manifest.get("wire_exchange_count")
        != durable_exchange_count
        or normalized_manifest.get("durable_exchange_event_count")
        != durable_event_count
        or terminal_exchange_count != durable_exchange_count
        or (validated.get("resource_usage") or {}).get("wire_exchange_count")
        != durable_exchange_count
        or normalized_manifest.get("historical_known_at_proven") is not False
        or normalized_manifest.get("pit_membership_authorized") is not False
    ):
        raise ValueError("csindex_range_attachment_normalized_manifest_invalid")
    return validated | {
        "csindex_phase": (
            LEGACY_PHASE if expected_profile == LEGACY_CAPTURE_PROFILE else PHASE
        ),
        "capture_profile": expected_profile,
        "strong_details_ancestry_verified": bool(
            parent.get("csindex_downstream_eligible") is True
        ),
        "range_protocol_verified": True,
        "normalized_replay_root": replay_root,
        "normalized_artifacts_trusted": True,
        "historical_known_at_proven": False,
        "pit_membership_authorized": False,
        "blockers": [
            TEMPORAL_BLOCKER,
            "csi300_attachment_semantic_parser_not_run",
        ],
    }


def _normalize_range_attachments(
    run_root: Path,
    requests: Sequence[ProviderProbeRequest],
    terminal: Mapping[str, Mapping[str, Any]],
) -> Sequence[NormalizedArtifact]:
    population, binding = _request_plan_evidence(requests)
    _validate_population_geometry(
        population,
        requests,
        capture_profile=str(binding["range_capture_profile"]),
    )
    output = run_root / "normalized"
    output.mkdir(exist_ok=True)
    attachment_path = output / "attachment_index.jsonl"
    wire_path = output / "wire_exchange_index.jsonl"
    blocked_path = output / "blocked_reference_index.jsonl"
    manifest_path = output / "normalized_manifest.json"
    durable_path = run_root / DURABLE_EXCHANGE_JOURNAL_NAME
    live_capture = (run_root / capture_module.CONTRACT_NAME).is_file()
    if live_capture and not durable_path.is_file():
        raise ValueError(
            "csindex_range_attachment_durable_journal_missing_live_capture"
        )
    public_key = _capture_public_key_from_terminal(terminal)
    attachment_rows: list[dict[str, Any]] = []
    wire_rows: list[dict[str, Any]] = []
    method_counts = {"full_get": 0, "range_if_range": 0}
    for request in requests:
        receipt = terminal.get(request.request_id)
        if receipt is None or receipt.get("terminal_state") != "positive":
            raise ValueError(
                f"csindex_range_attachment_terminal_missing:{request.request_id}"
            )
        wrapper = _read_exact_json(
            run_root / str(receipt.get("raw_envelope_relative_path") or "")
        )
        raw = _raw_logical_payload(wrapper, request=request, terminal=receipt)
        body, exchanges, retrieval_method, etag = _validate_and_assemble_logical(
            raw,
            request=request,
            public_key=public_key,
            expected_attempt_id=str(receipt.get("attempt_id") or ""),
            expected_retry_ordinal=int(receipt.get("retry_ordinal", -1)),
        )
        method_counts[retrieval_method] += 1
        attachment_rows.append(
            {
                "attachment_url": request.url,
                "attachment_host": request.metadata.get("attachment_host"),
                "attachment_extension": request.metadata.get("extension"),
                "path_dates": request.metadata.get("path_dates"),
                "reference_disposition": "capture_eligible",
                "source_announcements": request.metadata.get(
                    "source_announcements"
                ),
                "source_request_id": request.request_id,
                "source_logical_payload_sha256": hashlib.sha256(raw).hexdigest(),
                "attachment_sha256": hashlib.sha256(body).hexdigest(),
                "attachment_size_bytes": len(body),
                "retrieval_method": retrieval_method,
                "strong_etag": etag,
                "wire_exchange_count": len(exchanges),
                "historical_known_at": None,
                "historical_known_at_proven": False,
                "pit_membership_authorized": False,
                "temporal_blocker": TEMPORAL_BLOCKER,
            }
        )
        wire_rows.extend(exchanges)
    wire_ids = [str(row.get("exchange_id") or "") for row in wire_rows]
    if len(set(wire_ids)) != len(wire_ids):
        raise ValueError("csindex_range_attachment_wire_exchange_duplicate")
    durable_event_count = len(wire_rows) * 3
    if durable_path.is_file():
        observed_event_count, durable_exchange_count = _validate_durable_closure(
            run_root,
            requests=requests,
            public_key=public_key,
            expected_wire_rows=wire_rows,
        )
        terminal_exchange_count, terminal_attempt_map = (
            _validate_all_terminal_attempt_counts(
                run_root,
                requests=requests,
                public_key=public_key,
                expected_wire_rows=wire_rows,
            )
        )
        _validate_durable_attempt_map(
            wire_rows, terminal_attempt_map=terminal_attempt_map
        )
        if (
            observed_event_count != durable_event_count
            or
            durable_exchange_count != len(wire_rows)
            or terminal_exchange_count != durable_exchange_count
            or durable_exchange_count > MAX_WIRE_EXCHANGES
        ):
            raise ValueError(
                "csindex_range_attachment_cross_layer_wire_count_invalid"
            )
    blocked = [
        dict(row)
        for row in population
        if row.get("reference_disposition") != "capture_eligible"
    ]
    _write_jsonl(attachment_path, attachment_rows)
    _write_jsonl(wire_path, wire_rows)
    _write_jsonl(blocked_path, blocked)
    manifest = {
        "schema_version": NORMALIZATION_SCHEMA,
        "capture_profile": str(binding["range_capture_profile"]),
        "profile_complete": True,
        "population_count": len(population),
        "population_root": canonical_hash(population),
        "attachment_count": len(attachment_rows),
        "blocked_reference_count": len(blocked),
        "wire_exchange_count": len(wire_rows),
        "durable_exchange_event_count": durable_event_count,
        "retrieval_method_counts": method_counts,
        "attachment_index_sha256": sha256_file(attachment_path),
        "wire_exchange_index_sha256": sha256_file(wire_path),
        "blocked_reference_index_sha256": sha256_file(blocked_path),
        "source_binding": binding,
        "raw_capture_contains_exact_exchange_bytes": True,
        "range_chunks_assembled_only_after_validation": True,
        "historical_known_at_proven": False,
        "pit_membership_authorized": False,
        "blockers": [
            TEMPORAL_BLOCKER,
            "csi300_attachment_semantic_parser_not_run",
        ],
    }
    manifest["content_hash"] = canonical_hash(manifest)
    atomic_json(manifest_path, manifest)
    artifacts: tuple[NormalizedArtifact, ...] = (
        NormalizedArtifact(
            "csindex_range_attachment_index",
            "normalized/attachment_index.jsonl",
            len(attachment_rows),
        ),
        NormalizedArtifact(
            "csindex_range_wire_exchange_index",
            "normalized/wire_exchange_index.jsonl",
            len(wire_rows),
        ),
        NormalizedArtifact(
            "csindex_range_blocked_reference_index",
            "normalized/blocked_reference_index.jsonl",
            len(blocked),
        ),
        NormalizedArtifact(
            "normalized_manifest",
            "normalized/normalized_manifest.json",
            1,
        ),
    )
    if durable_path.is_file():
        artifacts += (
            NormalizedArtifact(
                "csindex_range_durable_exchange_journal",
                DURABLE_EXCHANGE_JOURNAL_NAME,
                durable_event_count,
            ),
        )
    fragment_root = run_root / "range_exchange_journal_torn_fragments"
    if fragment_root.is_dir():
        for fragment_path in sorted(fragment_root.glob("fragment_*.bin")):
            fragment_hash = hashlib.sha256(fragment_path.read_bytes()).hexdigest()
            if fragment_path.name != f"fragment_{fragment_hash}.bin":
                raise ValueError(
                    "csindex_range_attachment_torn_fragment_identity_invalid"
                )
            artifacts += (
                NormalizedArtifact(
                    f"csindex_range_torn_fragment_{fragment_hash[:24]}",
                    fragment_path.relative_to(run_root).as_posix(),
                    1,
                ),
            )
    return artifacts


def _validate_and_assemble_logical(
    raw: bytes,
    *,
    request: ProviderProbeRequest,
    public_key: bytes,
    expected_attempt_id: str,
    expected_retry_ordinal: int,
) -> tuple[bytes, list[dict[str, Any]], str, str | None]:
    envelope = _exact_json_object(raw)
    expected_keys = {
        "schema_version",
        "request_id",
        "request_semantic_hash",
        "terminal_state",
        "error_code",
        "retrieval_method",
        "attachment_sha256",
        "attachment_size_bytes",
        "strong_etag",
        "exchanges",
        "exchange_count",
        "prior_attempt_exchanges",
        "prior_attempt_exchange_count",
    }
    exchanges = envelope.get("exchanges")
    prior_exchanges = envelope.get("prior_attempt_exchanges")
    if (
        set(envelope) != expected_keys
        or envelope.get("schema_version") != LOGICAL_ENVELOPE_SCHEMA
        or envelope.get("request_id") != request.request_id
        or envelope.get("request_semantic_hash")
        != canonical_hash(request.semantic())
        or envelope.get("terminal_state") != "positive"
        or envelope.get("error_code") is not None
        or envelope.get("retrieval_method")
        not in {"full_get", "range_if_range"}
        or not isinstance(exchanges, list)
        or not exchanges
        or envelope.get("exchange_count") != len(exchanges)
        or not isinstance(prior_exchanges, list)
        or envelope.get("prior_attempt_exchange_count")
        != len(prior_exchanges)
    ):
        raise ValueError("csindex_range_attachment_logical_envelope_invalid")
    decoded = [
        _validate_physical_exchange(
            row,
            request=request,
            public_key=public_key,
            expected_ordinal=ordinal,
        )
        for ordinal, row in enumerate(exchanges)
    ]
    decoded_prior = [
        _validate_physical_exchange(
            row,
            request=request,
            public_key=public_key,
            expected_ordinal=int(
                row.get("exchange_ordinal", -1)
                if isinstance(row, Mapping)
                else -1
            ),
        )
        for row in prior_exchanges
    ]
    all_decoded = decoded_prior + decoded
    exchange_ids = [str(row["exchange_id"]) for row in all_decoded]
    if len(set(exchange_ids)) != len(exchange_ids):
        raise ValueError("csindex_range_attachment_exchange_id_duplicate")
    by_attempt: dict[str, list[int]] = {}
    current_attempts = {str(row["generic_attempt_id"]) for row in decoded}
    prior_attempts = {str(row["generic_attempt_id"]) for row in decoded_prior}
    if (
        current_attempts != {expected_attempt_id}
        or current_attempts & prior_attempts
        or any(
            int(row["generic_retry_ordinal"]) != expected_retry_ordinal
            for row in decoded
        )
        or any(
            int(row["generic_retry_ordinal"]) >= expected_retry_ordinal
            for row in decoded_prior
        )
    ):
        raise ValueError("csindex_range_attachment_attempt_partition_invalid")
    for row in all_decoded:
        by_attempt.setdefault(str(row["generic_attempt_id"]), []).append(
            int(row["exchange_ordinal"])
        )
    if any(ordinals != list(range(len(ordinals))) for ordinals in by_attempt.values()):
        raise ValueError("csindex_range_attachment_exchange_ordinal_invalid")
    full = decoded[0]
    if full["request_headers"] != dict(sorted(request.headers.items())):
        raise ValueError("csindex_range_attachment_full_request_invalid")
    method = str(envelope["retrieval_method"])
    etag: str | None = None
    wire_rows: list[dict[str, Any]] = []
    for row in all_decoded:
        wire_rows.append(_wire_row_from_decoded(request.request_id, row))
    if method == "full_get":
        if len(decoded) != 1:
            raise ValueError("csindex_range_attachment_full_exchange_count_invalid")
        if not (
            full["headers_received"] is True
            and full["status_code"] == 200
            and full["completion_state"] == "complete"
            and _content_length_matches(
                _header(full["response_headers"], "content-length"),
                len(full["body"]),
            )
            and _identity_content_encoding(full["response_headers"])
        ):
            raise ValueError("csindex_range_attachment_full_wire_invalid")
        body = full["body"]
    else:
        if not (
            full["headers_received"] is True
            and full["status_code"] == 200
            and full["completion_state"] in _FALLBACK_STATES
            and len(decoded) >= 2
        ):
            raise ValueError("csindex_range_attachment_fallback_not_authorized")
        chunks = decoded[1:]
        assembled = bytearray()
        total: int | None = None
        content_type: str | None = None
        start = 0
        for ordinal, chunk in enumerate(chunks, start=1):
            headers = chunk["request_headers"]
            expected_end = (
                start + RANGE_CHUNK_BYTES - 1
                if total is None
                else min(start + RANGE_CHUNK_BYTES - 1, total - 1)
            )
            if headers.get("Range") != f"bytes={start}-{expected_end}":
                raise ValueError("csindex_range_attachment_range_request_invalid")
            expected_headers = dict(request.headers) | {
                "Range": f"bytes={start}-{expected_end}"
            }
            if ordinal == 1:
                if "If-Range" in headers:
                    raise ValueError("csindex_range_attachment_first_if_range_invalid")
            elif headers.get("If-Range") != etag:
                raise ValueError("csindex_range_attachment_if_range_invalid")
            else:
                expected_headers["If-Range"] = str(etag)
            if headers != expected_headers:
                raise ValueError("csindex_range_attachment_range_headers_invalid")
            parsed = _parse_content_range(
                _header(chunk["response_headers"], "content-range")
            )
            response_etag = _header(chunk["response_headers"], "etag")
            range_type = _header(chunk["response_headers"], "content-type")
            if total is None and parsed is not None:
                total = parsed[2]
                expected_end = min(start + RANGE_CHUNK_BYTES - 1, total - 1)
                etag = response_etag
                content_type = range_type
            if not (
                chunk["headers_received"] is True
                and chunk["status_code"] == 206
                and chunk["completion_state"] == "complete"
                and parsed is not None
                and parsed == (start, expected_end, total)
                and total is not None
                and 0 < total <= ATTACHMENT_BODY_MAX_BYTES
                and _strong_etag(response_etag)
                and response_etag == etag
                and range_type == content_type
                and _content_length_matches(
                    _header(chunk["response_headers"], "content-length"),
                    len(chunk["body"]),
                )
                and len(chunk["body"]) == expected_end - start + 1
                and _identity_content_encoding(chunk["response_headers"])
            ):
                raise ValueError("csindex_range_attachment_range_wire_invalid")
            assembled.extend(chunk["body"])
            start = expected_end + 1
        body = bytes(assembled)
        if total is None or start != total or len(body) != total:
            raise ValueError("csindex_range_attachment_range_closure_invalid")
    reason = _assembled_attachment_reason(
        body,
        extension=str(request.metadata.get("extension") or ""),
        content_type=(
            _header(full["response_headers"], "content-type")
            if method == "full_get"
            else _header(decoded[1]["response_headers"], "content-type")
        ),
    )
    if (
        reason is not None
        or envelope.get("attachment_sha256") != hashlib.sha256(body).hexdigest()
        or envelope.get("attachment_size_bytes") != len(body)
        or envelope.get("strong_etag") != etag
    ):
        raise ValueError(reason or "csindex_range_attachment_assembly_hash_invalid")
    return body, wire_rows, method, etag


def _validate_physical_exchange(
    value: Any,
    *,
    request: ProviderProbeRequest,
    public_key: bytes,
    expected_ordinal: int,
) -> dict[str, Any]:
    if (
        not isinstance(value, Mapping)
        or set(value)
        != {"schema_version", "exchange_id", "exchange_ordinal", "events"}
        or value.get("schema_version")
        != "csindex_range_physical_exchange_v1"
        or value.get("exchange_ordinal") != expected_ordinal
        or not isinstance(value.get("events"), list)
        or len(value["events"]) != 3
    ):
        raise ValueError("csindex_range_attachment_physical_exchange_invalid")
    events = value["events"]
    expected_types = (
        "exchange_started",
        "exchange_headers",
        "exchange_terminal",
    )
    base_event_keys = {
        "schema_version",
        "event_type",
        "event_sequence",
        "exchange_id",
        "exchange_ordinal",
        "request_id",
        "previous_event_hash",
        "activity_id",
        "contract_id",
        "request_plan_hash",
        "generic_attempt_id",
        "generic_retry_ordinal",
        "generic_request_semantic_hash",
        "journal_sequence",
        "previous_journal_event_hash",
        "observed_at",
        "capture_public_key_sha256",
        "signature",
        "event_hash",
    }
    event_specific_keys = (
        {"request"},
        {
            "headers_received",
            "status_code",
            "response_headers",
            "redirect_followed",
        },
        {
            "completion_state",
            "error_code",
            "elapsed_seconds",
            "body_base64",
            "body_sha256",
            "body_size_bytes",
            "recovered_after_interruption",
        },
    )
    previous = ""
    first_binding: dict[str, Any] | None = None
    previous_journal_sequence: int | None = None
    previous_journal_hash: str | None = None
    for sequence, (event, event_type, specific_keys) in enumerate(
        zip(events, expected_types, event_specific_keys), start=1
    ):
        if not isinstance(event, Mapping):
            raise ValueError("csindex_range_attachment_exchange_event_invalid")
        signed = {key: item for key, item in event.items() if key != "event_hash"}
        unsigned = {key: item for key, item in signed.items() if key != "signature"}
        try:
            verify_signature(
                public_key_pem=public_key,
                payload=_json_bytes(unsigned),
                signature_b64=str(event.get("signature") or ""),
            )
        except ReceiptSigningError as exc:
            raise ValueError(
                "csindex_range_attachment_exchange_signature_invalid"
            ) from exc
        if (
            set(event) != base_event_keys | specific_keys
            or event.get("event_hash") != canonical_hash(signed)
            or event.get("schema_version") != "csindex_range_exchange_event_v1"
            or event.get("event_type") != event_type
            or event.get("event_sequence") != sequence
            or event.get("exchange_id") != value.get("exchange_id")
            or event.get("exchange_ordinal") != expected_ordinal
            or event.get("request_id") != request.request_id
            or event.get("previous_event_hash") != previous
            or event.get("activity_id") is None
            or event.get("contract_id") is None
            or event.get("request_plan_hash") is None
            or event.get("generic_attempt_id") is None
            or event.get("generic_request_semantic_hash")
            != canonical_hash(request.semantic())
            or not isinstance(event.get("generic_retry_ordinal"), int)
            or isinstance(event.get("generic_retry_ordinal"), bool)
            or int(event.get("generic_retry_ordinal")) < 0
            or not isinstance(event.get("journal_sequence"), int)
            or isinstance(event.get("journal_sequence"), bool)
            or int(event.get("journal_sequence")) <= 0
            or (
                previous_journal_sequence is not None
                and event.get("journal_sequence")
                != previous_journal_sequence + 1
            )
            or (
                previous_journal_hash is not None
                and event.get("previous_journal_event_hash")
                != previous_journal_hash
            )
            or event.get("capture_public_key_sha256")
            != _public_key_hash(public_key)
            or not isinstance(event.get("observed_at"), str)
        ):
            raise ValueError("csindex_range_attachment_exchange_event_invalid")
        previous = str(event["event_hash"])
        binding = {
            key: event.get(key)
            for key in (
                "activity_id",
                "contract_id",
                "request_plan_hash",
                "generic_attempt_id",
                "generic_retry_ordinal",
                "generic_request_semantic_hash",
            )
        }
        if first_binding is None:
            first_binding = binding
        elif binding != first_binding:
            raise ValueError("csindex_range_attachment_exchange_binding_mixed")
        previous_journal_sequence = int(event["journal_sequence"])
        previous_journal_hash = str(event["event_hash"])
    started, headers, terminal = events
    request_evidence = started.get("request")
    try:
        body = base64.b64decode(
            str(terminal.get("body_base64") or ""), validate=True
        )
    except (ValueError, TypeError) as exc:
        raise ValueError("csindex_range_attachment_exchange_body_invalid") from exc
    expected_exchange_id = (
        canonical_hash(
            {
                "request_id": request.request_id,
                "request_semantic_hash": canonical_hash(request.semantic()),
                "activity_id": started.get("activity_id"),
                "contract_id": started.get("contract_id"),
                "request_plan_hash": started.get("request_plan_hash"),
                "generic_attempt_id": started.get("generic_attempt_id"),
                "exchange_ordinal": expected_ordinal,
                "request_headers": dict(
                    sorted(
                        dict(request_evidence.get("headers") or {}).items()
                    )
                ),
            }
        )
        if isinstance(request_evidence, Mapping)
        else None
    )
    if (
        not isinstance(request_evidence, Mapping)
        or set(request_evidence) != {"url", "method", "headers"}
        or request_evidence.get("url") != request.url
        or request_evidence.get("method") != "GET"
        or value.get("exchange_id") != expected_exchange_id
        or not isinstance(request_evidence.get("headers"), Mapping)
        or any(
            not isinstance(key, str) or not isinstance(item, str)
            for key, item in request_evidence.get("headers", {}).items()
        )
        or not isinstance(headers.get("headers_received"), bool)
        or (
            headers.get("status_code") is not None
            and (
                not isinstance(headers.get("status_code"), int)
                or isinstance(headers.get("status_code"), bool)
            )
        )
        or headers.get("redirect_followed") is not False
        or not isinstance(headers.get("response_headers"), Mapping)
        or any(
            not isinstance(key, str) or not isinstance(item, str)
            for key, item in headers.get("response_headers", {}).items()
        )
        or terminal.get("body_sha256") != hashlib.sha256(body).hexdigest()
        or terminal.get("body_size_bytes") != len(body)
        or not isinstance(terminal.get("completion_state"), str)
        or terminal.get("completion_state")
        not in {
            "complete",
            "body_timeout",
            "body_truncated",
            "response_limit",
            "headers_timeout",
            "transport_error",
            "ambiguous_after_interruption",
        }
        or not isinstance(terminal.get("recovered_after_interruption"), bool)
        or not isinstance(terminal.get("elapsed_seconds"), (int, float))
        or isinstance(terminal.get("elapsed_seconds"), bool)
        or not math.isfinite(float(terminal.get("elapsed_seconds")))
        or float(terminal.get("elapsed_seconds")) < 0
        or (
            terminal.get("error_code") is not None
            and not isinstance(terminal.get("error_code"), str)
        )
    ):
        raise ValueError("csindex_range_attachment_exchange_body_invalid")
    return {
        "exchange_id": value["exchange_id"],
        "exchange_ordinal": expected_ordinal,
        "activity_id": started["activity_id"],
        "contract_id": started["contract_id"],
        "request_plan_hash": started["request_plan_hash"],
        "generic_attempt_id": started["generic_attempt_id"],
        "generic_retry_ordinal": started["generic_retry_ordinal"],
        "request_headers": dict(request_evidence["headers"]),
        "headers_received": headers.get("headers_received"),
        "status_code": headers.get("status_code"),
        "response_headers": dict(headers["response_headers"]),
        "completion_state": terminal.get("completion_state"),
        "error_code": terminal.get("error_code"),
        "body": body,
        "event_hashes": [str(event["event_hash"]) for event in events],
    }


def _request_plan_evidence(
    requests: Sequence[ProviderProbeRequest],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not requests:
        raise ValueError("csindex_range_attachment_request_plan_empty")
    population = requests[0].metadata.get("attachment_population")
    blocked = requests[0].metadata.get("blocked_references")
    binding = _validate_source_binding(requests[0].metadata.get("source_binding"))
    if not isinstance(population, list) or not isinstance(blocked, list):
        raise ValueError("csindex_range_attachment_population_evidence_missing")
    expected = _range_attachment_requests(population, binding)
    if [row.semantic() for row in requests] != [row.semantic() for row in expected]:
        raise ValueError("csindex_range_attachment_request_plan_closure_invalid")
    expected_blocked = [
        dict(row)
        for row in population
        if row.get("reference_disposition") != "capture_eligible"
    ]
    if blocked != expected_blocked:
        raise ValueError("csindex_range_attachment_blocked_closure_invalid")
    return [dict(row) for row in population], binding


def _wire_row_from_decoded(
    request_id: str, row: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "source_request_id": request_id,
        "exchange_id": row["exchange_id"],
        "exchange_ordinal": row["exchange_ordinal"],
        "generic_attempt_id": row["generic_attempt_id"],
        "generic_retry_ordinal": row["generic_retry_ordinal"],
        "request_range": row["request_headers"].get("Range"),
        "request_if_range": row["request_headers"].get("If-Range"),
        "status_code": row["status_code"],
        "content_range": _header(row["response_headers"], "content-range"),
        "etag": _header(row["response_headers"], "etag"),
        "completion_state": row["completion_state"],
        "body_sha256": hashlib.sha256(row["body"]).hexdigest(),
        "body_size_bytes": len(row["body"]),
        "started_event_hash": row["event_hashes"][0],
        "headers_event_hash": row["event_hashes"][1],
        "terminal_event_hash": row["event_hashes"][2],
    }


def _physical_exchanges_from_events(
    events: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for value in events:
        event = dict(value)
        exchange_id = str(event.get("exchange_id") or "")
        if exchange_id not in grouped:
            grouped[exchange_id] = []
            order.append(exchange_id)
        grouped[exchange_id].append(event)
    rows: list[dict[str, Any]] = []
    for exchange_id in order:
        exchange_events = grouped[exchange_id]
        if [row.get("event_type") for row in exchange_events] != [
            "exchange_started",
            "exchange_headers",
            "exchange_terminal",
        ]:
            raise ValueError(
                "csindex_range_attachment_durable_exchange_incomplete"
            )
        rows.append(
            {
                "schema_version": "csindex_range_physical_exchange_v1",
                "exchange_id": exchange_id,
                "exchange_ordinal": exchange_events[0]["exchange_ordinal"],
                "events": exchange_events,
            }
        )
    return rows


def _validate_durable_closure(
    run_root: Path,
    *,
    requests: Sequence[ProviderProbeRequest],
    public_key: bytes,
    expected_wire_rows: Sequence[Mapping[str, Any]],
) -> tuple[int, int]:
    contract = _read_exact_json(run_root / capture_module.CONTRACT_NAME)
    plan = _read_exact_json(run_root / capture_module.PLAN_NAME)
    request_rows = [request.semantic() for request in requests]
    contract_id = canonical_hash(contract)
    request_plan_hash = canonical_hash(request_rows)
    activity_id = canonical_hash(
        {"contract_id": contract_id, "request_plan_hash": request_plan_hash}
    )
    if (
        plan.get("requests") != request_rows
        or plan.get("request_plan_hash") != request_plan_hash
    ):
        raise ValueError("csindex_range_attachment_durable_plan_invalid")
    events = _read_durable_exchange_events(
        run_root / DURABLE_EXCHANGE_JOURNAL_NAME,
        activity_id=activity_id,
        contract_id=contract_id,
        request_plan_hash=request_plan_hash,
        public_key=public_key,
    )
    by_request = {request.request_id: request for request in requests}
    observed_rows: list[dict[str, Any]] = []
    by_attempt: dict[str, list[int]] = {}
    for exchange in _physical_exchanges_from_events(events):
        first = exchange["events"][0]
        request = by_request.get(str(first.get("request_id") or ""))
        if request is None:
            raise ValueError("csindex_range_attachment_durable_request_invalid")
        decoded = _validate_physical_exchange(
            exchange,
            request=request,
            public_key=public_key,
            expected_ordinal=int(exchange["exchange_ordinal"]),
        )
        if (
            decoded["activity_id"] != activity_id
            or decoded["contract_id"] != contract_id
            or decoded["request_plan_hash"] != request_plan_hash
        ):
            raise ValueError("csindex_range_attachment_durable_binding_invalid")
        by_attempt.setdefault(str(decoded["generic_attempt_id"]), []).append(
            int(decoded["exchange_ordinal"])
        )
        observed_rows.append(
            _wire_row_from_decoded(request.request_id, decoded)
        )
    if any(ordinals != list(range(len(ordinals))) for ordinals in by_attempt.values()):
        raise ValueError("csindex_range_attachment_durable_ordinal_invalid")
    if observed_rows != [dict(row) for row in expected_wire_rows]:
        raise ValueError("csindex_range_attachment_durable_replay_mismatch")
    return len(events), len(observed_rows)


def _validate_all_terminal_attempt_counts(
    run_root: Path,
    *,
    requests: Sequence[ProviderProbeRequest],
    public_key: bytes,
    expected_wire_rows: Sequence[Mapping[str, Any]],
) -> tuple[int, dict[tuple[str, str, int], int]]:
    contract = _read_exact_json(run_root / capture_module.CONTRACT_NAME)
    request_rows = [request.semantic() for request in requests]
    contract_id = canonical_hash(contract)
    request_plan_hash = canonical_hash(request_rows)
    activity_id = canonical_hash(
        {"contract_id": contract_id, "request_plan_hash": request_plan_hash}
    )
    events = capture_module._read_and_validate_journal(
        run_root / capture_module.JOURNAL_NAME,
        expected_activity_id=activity_id,
        expected_contract_id=contract_id,
        public_key=public_key,
        request_rows=request_rows,
        request_plan_hash=request_plan_hash,
        max_retries=int((contract.get("budget") or {}).get("max_retries", -1)),
    )
    request_by_id = {request.request_id: request for request in requests}
    expected_exchange_hashes = {
        (
            str(row.get("generic_attempt_id") or ""),
            int(row.get("exchange_ordinal", -1)),
        ): (
            str(row.get("started_event_hash") or ""),
            str(row.get("headers_event_hash") or ""),
            str(row.get("terminal_event_hash") or ""),
        )
        for row in expected_wire_rows
    }
    if len(expected_exchange_hashes) != len(expected_wire_rows):
        raise ValueError("csindex_range_attachment_expected_wire_duplicate")
    total = 0
    attempt_map: dict[tuple[str, str, int], int] = {}
    for event in events:
        if event.get("event_type") != "capture_attempt_terminal":
            continue
        count = event.get("transport_exchange_count")
        if (
            not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
        ):
            raise ValueError(
                "csindex_range_attachment_terminal_exchange_count_invalid"
            )
        wrapper = _read_exact_json(
            run_root / str(event.get("raw_envelope_relative_path") or "")
        )
        if wrapper.get("transport_exchange_count") != count:
            raise ValueError(
                "csindex_range_attachment_terminal_wrapper_count_mismatch"
            )
        try:
            raw = base64.b64decode(
                str(wrapper.get("raw_payload_base64") or ""), validate=True
            )
        except (ValueError, TypeError) as exc:
            raise ValueError(
                "csindex_range_attachment_terminal_raw_invalid"
            ) from exc
        payload = _exact_json_object(raw)
        request = request_by_id.get(str(event.get("request_id") or ""))
        if request is None:
            raise ValueError(
                "csindex_range_attachment_terminal_request_invalid"
            )
        _validate_terminal_attempt_payload(
            payload,
            request=request,
            terminal=event,
            public_key=public_key,
            expected_exchange_hashes=expected_exchange_hashes,
        )
        key = (
            str(event.get("attempt_id") or ""),
            str(event.get("request_id") or ""),
            int(event.get("retry_ordinal", -1)),
        )
        if (
            not key[0]
            or not key[1]
            or key[2] < 0
            or key in attempt_map
        ):
            raise ValueError(
                "csindex_range_attachment_terminal_attempt_identity_invalid"
            )
        attempt_map[key] = count
        total += count
    return total, attempt_map


def _validate_terminal_attempt_payload(
    payload: Mapping[str, Any],
    *,
    request: ProviderProbeRequest,
    terminal: Mapping[str, Any],
    public_key: bytes,
    expected_exchange_hashes: Mapping[
        tuple[str, int], tuple[str, str, str]
    ],
) -> None:
    count = int(terminal.get("transport_exchange_count") or 0)
    attempt_id = str(terminal.get("attempt_id") or "")
    schema = payload.get("schema_version")
    if schema == LOGICAL_ENVELOPE_SCHEMA:
        exchanges = payload.get("exchanges")
        prior = payload.get("prior_attempt_exchanges")
        if (
            not isinstance(exchanges, list)
            or not isinstance(prior, list)
            or payload.get("exchange_count") != len(exchanges)
            or len(exchanges) != count
            or payload.get("prior_attempt_exchange_count") != len(prior)
            or payload.get("request_id") != request.request_id
            or payload.get("request_semantic_hash")
            != canonical_hash(request.semantic())
            or payload.get("terminal_state")
            != terminal.get("terminal_state")
            or payload.get("error_code") != terminal.get("error_code")
        ):
            raise ValueError(
                "csindex_range_attachment_terminal_logical_shell_invalid"
            )
        for ordinal, exchange in enumerate(exchanges):
            decoded = _validate_physical_exchange(
                exchange,
                request=request,
                public_key=public_key,
                expected_ordinal=ordinal,
            )
            hashes = tuple(decoded["event_hashes"])
            if (
                decoded["generic_attempt_id"] != attempt_id
                or hashes
                != expected_exchange_hashes.get((attempt_id, ordinal))
            ):
                raise ValueError(
                    "csindex_range_attachment_terminal_exchange_mismatch"
                )
        return
    if schema == "csindex_range_attachment_interrupted_attempt_v1":
        durable_events = payload.get("durable_events")
        if (
            not isinstance(durable_events, list)
            or payload.get("attempt_id") != attempt_id
            or payload.get("request_id") != request.request_id
            or payload.get("transport_exchange_count") != count
        ):
            raise ValueError(
                "csindex_range_attachment_interrupted_payload_invalid"
            )
        started = [
            row
            for row in durable_events
            if isinstance(row, Mapping)
            and row.get("event_type") == "exchange_started"
        ]
        if len(started) != count:
            raise ValueError(
                "csindex_range_attachment_interrupted_count_invalid"
            )
        for row in durable_events:
            if not isinstance(row, Mapping):
                raise ValueError(
                    "csindex_range_attachment_interrupted_event_invalid"
                )
            ordinal = int(row.get("exchange_ordinal", -1))
            expected = expected_exchange_hashes.get((attempt_id, ordinal))
            event_position = {
                "exchange_started": 0,
                "exchange_headers": 1,
                "exchange_terminal": 2,
            }.get(str(row.get("event_type") or ""))
            if (
                expected is None
                or event_position is None
                or row.get("event_hash") != expected[event_position]
            ):
                raise ValueError(
                    "csindex_range_attachment_interrupted_event_mismatch"
                )
        return
    if count != 0:
        raise ValueError("csindex_range_attachment_unknown_attempt_payload")


def _validate_durable_attempt_map(
    wire_rows: Sequence[Mapping[str, Any]],
    *,
    terminal_attempt_map: Mapping[tuple[str, str, int], int],
) -> None:
    durable_map: dict[tuple[str, str, int], list[int]] = {}
    for row in wire_rows:
        key = (
            str(row.get("generic_attempt_id") or ""),
            str(row.get("source_request_id") or ""),
            int(row.get("generic_retry_ordinal", -1)),
        )
        durable_map.setdefault(key, []).append(
            int(row.get("exchange_ordinal", -1))
        )
    if any(
        ordinals != list(range(len(ordinals)))
        for ordinals in durable_map.values()
    ):
        raise ValueError("csindex_range_attachment_attempt_ordinal_invalid")
    expected_nonzero = {
        key: count
        for key, count in terminal_attempt_map.items()
        if count > 0
    }
    observed = {key: len(ordinals) for key, ordinals in durable_map.items()}
    if observed != expected_nonzero:
        raise ValueError("csindex_range_attachment_attempt_map_mismatch")


def _validate_population_geometry(
    population: Sequence[Mapping[str, Any]],
    requests: Sequence[Any],
    *,
    capture_profile: str = CAPTURE_PROFILE,
) -> None:
    eligible = [
        row
        for row in population
        if row.get("reference_disposition") == "capture_eligible"
    ]
    expected = (
        EXPECTED_LEGACY_REQUEST_COUNT
        if capture_profile == LEGACY_CAPTURE_PROFILE
        else EXPECTED_REQUEST_COUNT
    )
    if capture_profile not in {CAPTURE_PROFILE, LEGACY_CAPTURE_PROFILE}:
        raise ValueError("csindex_range_attachment_capture_profile_invalid")
    if (
        len(population) != EXPECTED_POPULATION_COUNT
        or len(eligible) != expected
        or len(requests) != expected
    ):
        raise ValueError("csindex_range_attachment_population_geometry_invalid")
    if capture_profile == LEGACY_CAPTURE_PROFILE:
        csindex_backfill._validate_legacy_cons_repair_population(population)
        expected_urls = {
            str(row["attachment_url"])
            for row in csindex_backfill.CSINDEX_LEGACY_CONS_REPAIR_ROWS
        }
        observed_urls = {
            str(row.get("attachment_url") or "") for row in eligible
        }
        if observed_urls != expected_urls:
            raise ValueError("csindex_range_legacy_cons_exact_scope_invalid")


def _validate_details_parent(
    range_manifest: str | Path,
    binding: Mapping[str, Any],
) -> dict[str, Any]:
    manifest = Path(range_manifest).resolve()
    provider_root = manifest.parent.parent.parent.parent
    details_manifest = csindex_backfill._csindex_generation_manifest(
        provider_root,
        phase="csindex-details",
        generation_id=str(binding.get("details_generation_id") or ""),
    )
    governed = csindex_backfill.validate_csindex_governance(details_manifest)
    if (
        governed.get("status") != "succeeded"
        or governed.get("csindex_phase") != "csindex-details"
        or governed.get("csindex_downstream_eligible") is not True
        or governed.get("generation_id") != binding.get("details_generation_id")
        or governed.get("content_hash") != binding.get("details_content_hash")
        or governed.get("contract_id") != binding.get("details_contract_id")
        or governed.get("request_plan_hash")
        != binding.get("details_request_plan_hash")
        or governed.get("activity_id") != binding.get("details_activity_id")
        or governed.get("csindex_downstream_ancestry")
        != binding.get("details_ancestry")
        or (governed.get("csindex_downstream_ancestry") or {}).get(
            "weak_source_ancestry"
        )
        is not False
    ):
        raise ValueError("csindex_range_attachment_real_details_ancestry_invalid")
    return governed


def _validate_rebuilt_parent_plan(
    parent: Mapping[str, Any],
    *,
    population: Sequence[Mapping[str, Any]],
    requests: Sequence[ProviderProbeRequest],
    binding: Mapping[str, Any],
    capture_profile: str,
) -> None:
    manifest_path = parent.get("manifest_path")
    if not isinstance(manifest_path, str) or not manifest_path:
        raise ValueError("csindex_range_attachment_parent_manifest_missing")
    if capture_profile == LEGACY_CAPTURE_PROFILE:
        rebuilt_population, legacy_requests, legacy_input_root = (
            csindex_backfill.build_csindex_legacy_cons_repair_plan(
                manifest_path
            )
        )
    elif capture_profile == CAPTURE_PROFILE:
        rebuilt_population, legacy_requests, legacy_input_root = (
            csindex_backfill.build_csindex_attachment_plan(manifest_path)
        )
    else:
        raise ValueError("csindex_range_attachment_capture_profile_invalid")
    legacy_plan_root = canonical_hash(
        [request.semantic() for request in legacy_requests]
    )
    rebuilt_binding = _range_source_binding(
        parent,
        population=rebuilt_population,
        legacy_input_root=legacy_input_root,
        legacy_request_plan_root=legacy_plan_root,
        capture_profile=capture_profile,
    )
    rebuilt_requests = _range_attachment_requests(
        rebuilt_population, rebuilt_binding
    )
    if (
        [dict(row) for row in population] != rebuilt_population
        or binding != rebuilt_binding
        or [request.semantic() for request in requests]
        != [request.semantic() for request in rebuilt_requests]
        or binding.get("legacy_attachment_input_root")
        != legacy_input_root
        or binding.get("legacy_attachment_request_plan_root")
        != legacy_plan_root
    ):
        raise ValueError("csindex_range_attachment_real_parent_plan_mismatch")


def _contract(
    *,
    output_root: Path,
    signer: CaptureSigner,
    population_root: str,
    request_count: int,
    input_root: str,
    details: Mapping[str, Any],
    capture_profile: str = CAPTURE_PROFILE,
) -> FreeProviderBackfillContract:
    legacy = capture_profile == LEGACY_CAPTURE_PROFILE
    if capture_profile not in {CAPTURE_PROFILE, LEGACY_CAPTURE_PROFILE}:
        raise ValueError("csindex_range_attachment_capture_profile_invalid")
    return FreeProviderBackfillContract(
        activity_name=LEGACY_ACTIVITY_NAME if legacy else ACTIVITY_NAME,
        provider="csindex",
        output_root=output_root,
        permission_context_id=DEFAULT_PERMISSION_CONTEXT,
        population_root=population_root,
        capture_public_key_sha256=_public_key_hash(signer.public_key_pem),
        capture_public_key_pem_b64=base64.b64encode(
            signer.public_key_pem
        ).decode("ascii"),
        scope_start=csindex_backfill.CSINDEX_SCOPE["date_start"],
        scope_end=csindex_backfill.CSINDEX_SCOPE["date_end"],
        request_start=csindex_backfill.CSINDEX_SCOPE["request_start"],
        request_end=csindex_backfill.CSINDEX_SCOPE["request_end"],
        allowed_hosts=(
            ("oss-ch.csindex.com.cn",)
            if legacy
            else tuple(csindex_backfill.CSINDEX_ATTACHMENT_HOSTS)
        ),
        budget=BackfillResourceBudget(
            max_requests=request_count * (MAX_RETRIES + 1),
            max_wire_exchanges=MAX_WIRE_EXCHANGES,
            max_response_bytes=MAX_LOGICAL_ENVELOPE_BYTES,
            max_total_response_bytes=MAX_TOTAL_RESPONSE_BYTES,
            timeout_seconds=TIMEOUT_SECONDS,
            minimum_delay_seconds=MINIMUM_LOGICAL_DELAY_SECONDS,
            max_retries=MAX_RETRIES,
        ),
        adapter_identity={
            "adapter": LEGACY_ADAPTER_IDENTITY if legacy else ADAPTER_IDENTITY,
            "implementation_root": _implementation_root(),
            "http": HTTP_IDENTITY,
            "capture_profile": capture_profile,
            "profile_complete": "true",
            "input_capture_content_hash": input_root,
            "source_details_generation_id": str(details.get("generation_id") or ""),
            "source_details_content_hash": str(details.get("content_hash") or ""),
            "range_chunk_bytes": RANGE_CHUNK_BYTES,
            "range_body_max_bytes": ATTACHMENT_BODY_MAX_BYTES,
            "logical_envelope_schema": LOGICAL_ENVELOPE_SCHEMA,
            "wire_exchange_budget": MAX_WIRE_EXCHANGES,
        },
        source_profile_id=csindex_backfill.CSINDEX_SOURCE_PROFILE_ID,
    )


def _validate_authorized_contract(
    contract: Mapping[str, Any],
    *,
    request_count: int,
    expected_profile: str,
) -> None:
    legacy = expected_profile == LEGACY_CAPTURE_PROFILE
    if expected_profile not in {CAPTURE_PROFILE, LEGACY_CAPTURE_PROFILE}:
        raise ValueError("csindex_range_attachment_capture_profile_invalid")
    adapter = contract.get("adapter_identity") or {}
    expected_budget = {
        "max_requests": request_count * (MAX_RETRIES + 1),
        "max_wire_exchanges": MAX_WIRE_EXCHANGES,
        "max_response_bytes": MAX_LOGICAL_ENVELOPE_BYTES,
        "max_total_response_bytes": MAX_TOTAL_RESPONSE_BYTES,
        "timeout_seconds": TIMEOUT_SECONDS,
        "minimum_delay_seconds": MINIMUM_LOGICAL_DELAY_SECONDS,
        "max_retries": MAX_RETRIES,
    }
    expected_adapter_keys = {
        "adapter",
        "implementation_root",
        "http",
        "capture_profile",
        "profile_complete",
        "input_capture_content_hash",
        "source_details_generation_id",
        "source_details_content_hash",
        "range_chunk_bytes",
        "range_body_max_bytes",
        "logical_envelope_schema",
        "wire_exchange_budget",
    }
    expected_contract_keys = {
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
    try:
        declared_public_key = base64.b64decode(
            str(contract.get("capture_public_key_pem_b64") or ""),
            validate=True,
        )
    except (ValueError, TypeError):
        declared_public_key = b""
    if (
        set(contract) != expected_contract_keys
        or contract.get("schema_version") != "free_provider_backfill_contract_v2"
        or contract.get("activity_name")
        != (LEGACY_ACTIVITY_NAME if legacy else ACTIVITY_NAME)
        or contract.get("provider") != "csindex"
        or contract.get("permission_context_id") != DEFAULT_PERMISSION_CONTEXT
        or contract.get("output_namespace_id")
        != canonical_hash(str(_expected_output_root(expected_profile).resolve()))
        or contract.get("scope") != csindex_backfill.CSINDEX_SCOPE
        or contract.get("allowed_hosts")
        != (
            ["oss-ch.csindex.com.cn"]
            if legacy
            else list(csindex_backfill.CSINDEX_ATTACHMENT_HOSTS)
        )
        or contract.get("budget") != expected_budget
        or contract.get("source_profile_id")
        != csindex_backfill.CSINDEX_SOURCE_PROFILE_ID
        or contract.get("mode") != "signed_raw_provider_capture"
        or contract.get("capture_before_normalization") is not True
        or contract.get("old_lake_mutated") is not False
        or contract.get("safety")
        != {name: False for name in capture_module.SAFETY_FLAGS}
        or contract.get("capture_public_key_sha256")
        != APPROVED_CAPTURE_KEY_SHA256
        or _public_key_hash(declared_public_key)
        != APPROVED_CAPTURE_KEY_SHA256
        or set(adapter) != expected_adapter_keys
        or adapter.get("adapter")
        != (LEGACY_ADAPTER_IDENTITY if legacy else ADAPTER_IDENTITY)
        or adapter.get("implementation_root") != _implementation_root()
        or adapter.get("http") != HTTP_IDENTITY
        or adapter.get("capture_profile") != expected_profile
        or adapter.get("profile_complete") != "true"
        or adapter.get("range_chunk_bytes") != RANGE_CHUNK_BYTES
        or adapter.get("range_body_max_bytes") != ATTACHMENT_BODY_MAX_BYTES
        or adapter.get("logical_envelope_schema") != LOGICAL_ENVELOPE_SCHEMA
        or adapter.get("wire_exchange_budget") != MAX_WIRE_EXCHANGES
        or any(
            _HEX_64.fullmatch(str(adapter.get(key) or "")) is None
            for key in (
                "implementation_root",
                "input_capture_content_hash",
                "source_details_content_hash",
            )
        )
        or adapter.get("source_details_generation_id")
        != "free_provider_backfill_"
        + str(adapter.get("source_details_content_hash") or "")[:24]
    ):
        raise ValueError("csindex_range_attachment_authorized_contract_invalid")


def _authorize_contract_before_network(
    contract: FreeProviderBackfillContract,
    *,
    request_count: int,
    expected_profile: str,
    signer: CaptureSigner,
) -> None:
    if (
        _public_key_hash(signer.public_key_pem)
        != APPROVED_CAPTURE_KEY_SHA256
        or contract.capture_public_key_sha256
        != APPROVED_CAPTURE_KEY_SHA256
    ):
        raise ValueError("csindex_range_attachment_capture_key_not_approved")
    _validate_authorized_contract(
        contract.semantic(),
        request_count=request_count,
        expected_profile=expected_profile,
    )


def _expected_output_root(capture_profile: str) -> Path:
    if capture_profile == CAPTURE_PROFILE:
        return SCOPE_ROOT / "csindex" / "range_attachments"
    if capture_profile == LEGACY_CAPTURE_PROFILE:
        return SCOPE_ROOT / "csindex" / "range_legacy_cons_repair"
    raise ValueError("csindex_range_attachment_capture_profile_invalid")


def _logical_envelope(
    request: ProviderProbeRequest,
    exchanges: Sequence[Mapping[str, Any]],
    *,
    prior_exchanges: Sequence[Mapping[str, Any]],
    terminal_state: str,
    error_code: str | None,
    retrieval_method: str | None,
    attachment_sha256: str | None,
    attachment_size_bytes: int | None,
    strong_etag: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": LOGICAL_ENVELOPE_SCHEMA,
        "request_id": request.request_id,
        "request_semantic_hash": canonical_hash(request.semantic()),
        "terminal_state": terminal_state,
        "error_code": error_code,
        "retrieval_method": retrieval_method,
        "attachment_sha256": attachment_sha256,
        "attachment_size_bytes": attachment_size_bytes,
        "strong_etag": strong_etag,
        "exchanges": [dict(row) for row in exchanges],
        "exchange_count": len(exchanges),
        "prior_attempt_exchanges": [dict(row) for row in prior_exchanges],
        "prior_attempt_exchange_count": len(prior_exchanges),
    }


def _signed_exchange_event(
    signer: CaptureSigner, unsigned: Mapping[str, Any]
) -> dict[str, Any]:
    semantic = dict(unsigned) | {
        "capture_public_key_sha256": _public_key_hash(signer.public_key_pem)
    }
    signed = semantic | {"signature": signer.sign(_json_bytes(semantic))}
    return signed | {"event_hash": canonical_hash(signed)}


def _exchange_result(exchange: Mapping[str, Any]) -> _HttpExchangeResult:
    events = exchange.get("events") or ()
    if len(events) != 3:
        raise ValueError("csindex_range_attachment_physical_exchange_invalid")
    headers = events[1]
    terminal = events[2]
    try:
        body = base64.b64decode(
            str(terminal.get("body_base64") or ""), validate=True
        )
    except (ValueError, TypeError) as exc:
        raise ValueError("csindex_range_attachment_exchange_body_invalid") from exc
    return _HttpExchangeResult(
        status_code=headers.get("status_code"),
        response_headers=dict(headers.get("response_headers") or {}),
        body=body,
        headers_received=headers.get("headers_received") is True,
        completion_state=str(terminal.get("completion_state") or ""),
        error_code=(
            str(terminal.get("error_code"))
            if terminal.get("error_code") is not None
            else None
        ),
        elapsed_seconds=float(terminal.get("elapsed_seconds") or 0.0),
        redirect_followed=headers.get("redirect_followed") is True,
    )


def _raw_logical_payload(
    wrapper: Mapping[str, Any],
    *,
    request: ProviderProbeRequest,
    terminal: Mapping[str, Any],
) -> bytes:
    try:
        raw = base64.b64decode(
            str(wrapper.get("raw_payload_base64") or ""), validate=True
        )
    except (ValueError, TypeError) as exc:
        raise ValueError("csindex_range_attachment_raw_wrapper_invalid") from exc
    logical = _exact_json_object(raw)
    exchange_count = logical.get("exchange_count")
    if (
        wrapper.get("schema_version")
        != "free_provider_backfill_raw_envelope_v1"
        or wrapper.get("request_id") != request.request_id
        or wrapper.get("terminal_state") != "positive"
        or terminal.get("terminal_state") != "positive"
        or wrapper.get("raw_payload_sha256") != hashlib.sha256(raw).hexdigest()
        or wrapper.get("raw_payload_size_bytes") != len(raw)
        or terminal.get("raw_payload_sha256") != hashlib.sha256(raw).hexdigest()
        or terminal.get("raw_payload_size_bytes") != len(raw)
        or wrapper.get("transport_exchange_count")
        != terminal.get("transport_exchange_count")
        or not isinstance(exchange_count, int)
        or isinstance(exchange_count, bool)
        or exchange_count < 0
        or wrapper.get("transport_exchange_count") != exchange_count
    ):
        raise ValueError("csindex_range_attachment_raw_wrapper_invalid")
    return raw


def _capture_public_key_from_terminal(
    terminal: Mapping[str, Mapping[str, Any]],
) -> bytes:
    encoded = {
        str(row.get("capture_public_key_pem_b64") or "")
        for row in terminal.values()
    }
    key_hashes = {
        str(row.get("capture_public_key_sha256") or "")
        for row in terminal.values()
    }
    if len(encoded) != 1 or len(key_hashes) != 1:
        raise ValueError("csindex_range_attachment_capture_key_invalid")
    try:
        public_key = base64.b64decode(
            next(iter(encoded)), validate=True
        )
    except (ValueError, TypeError) as exc:
        raise ValueError("csindex_range_attachment_capture_key_invalid") from exc
    if (
        not public_key
        or key_hashes != {_public_key_hash(public_key)}
    ):
        raise ValueError("csindex_range_attachment_capture_key_invalid")
    return public_key


def _assembled_attachment_reason(
    body: bytes, *, extension: str, content_type: str | None
) -> str | None:
    if not body or len(body) > ATTACHMENT_BODY_MAX_BYTES:
        return "csindex_range_attachment_body_size_invalid"
    if csindex_backfill._attachment_block_reason(body) is not None:
        return "csindex_range_attachment_html_or_waf"
    if not csindex_backfill._attachment_content_type_compatible(
        extension, content_type
    ):
        return "csindex_range_attachment_content_type_invalid"
    if not csindex_backfill._attachment_magic_valid(body, extension):
        return "csindex_range_attachment_magic_invalid"
    return None


def _normalized_headers(value: Mapping[str, Any]) -> dict[str, str]:
    rows: dict[str, str] = {}
    for key, item in value.items():
        name = str(key).strip().lower()
        if not name:
            raise ValueError("csindex_range_attachment_response_header_invalid")
        if name in {"set-cookie", "cookie", "authorization"}:
            continue
        token = str(item).strip()
        rows[name] = f"{rows[name]}, {token}" if name in rows else token
    return rows


def _header(headers: Mapping[str, Any], name: str) -> str | None:
    normalized = _normalized_headers(headers)
    return normalized.get(name.lower())


def _parse_content_range(value: str | None) -> tuple[int, int, int] | None:
    match = _CONTENT_RANGE.fullmatch(str(value or ""))
    if match is None:
        return None
    start, end, total = (int(token) for token in match.groups())
    if start < 0 or end < start or total <= end:
        return None
    return start, end, total


def _strong_etag(value: str | None) -> bool:
    return bool(value and not value.startswith("W/") and _STRONG_ETAG.fullmatch(value))


def _content_length_matches(value: str | None, actual: int) -> bool:
    expected = _nonnegative_header_int(value)
    return expected is not None and expected == actual


def _nonnegative_header_int(value: Any) -> int | None:
    token = str(value or "")
    if not token.isdigit():
        return None
    parsed = int(token)
    return parsed if parsed >= 0 else None


def _identity_content_encoding(headers: Mapping[str, Any]) -> bool:
    value = _header(headers, "content-encoding")
    return value is None or value.lower() == "identity"


def _looks_like_waf(body: bytes) -> bool:
    return csindex_backfill._attachment_block_reason(body) is not None


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    existed = path.exists()
    with path.open("wb") as handle:
        for row in rows:
            handle.write(_json_bytes(row) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    if not existed:
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _exact_json_object(payload: bytes | str) -> dict[str, Any]:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        row: dict[str, Any] = {}
        for key, value in pairs:
            if key in row:
                raise ValueError(
                    f"csindex_range_attachment_json_duplicate_key:{key}"
                )
            row[key] = value
        return row

    try:
        value = json.loads(payload, object_pairs_hook=object_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("csindex_range_attachment_json_invalid") from exc
    if not isinstance(value, dict):
        raise ValueError("csindex_range_attachment_json_object_required")
    return value


def _read_exact_json(path: Path) -> dict[str, Any]:
    return _exact_json_object(path.read_bytes())


def _read_durable_exchange_events(
    path: Path,
    *,
    activity_id: str,
    contract_id: str,
    request_plan_hash: str,
    public_key: bytes,
) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    previous_hash = ""
    for sequence, line in enumerate(path.read_bytes().splitlines(), start=1):
        if not line.strip():
            raise ValueError(
                "csindex_range_attachment_durable_journal_blank_line"
            )
        event = _exact_json_object(line)
        signed = {key: item for key, item in event.items() if key != "event_hash"}
        unsigned = {key: item for key, item in signed.items() if key != "signature"}
        try:
            verify_signature(
                public_key_pem=public_key,
                payload=_json_bytes(unsigned),
                signature_b64=str(event.get("signature") or ""),
            )
        except ReceiptSigningError as exc:
            raise ValueError(
                "csindex_range_attachment_durable_signature_invalid"
            ) from exc
        if (
            event.get("event_hash") != canonical_hash(signed)
            or event.get("activity_id") != activity_id
            or event.get("contract_id") != contract_id
            or event.get("request_plan_hash") != request_plan_hash
            or event.get("journal_sequence") != sequence
            or event.get("previous_journal_event_hash") != previous_hash
            or event.get("capture_public_key_sha256")
            != _public_key_hash(public_key)
            or event.get("event_type")
            not in {
                "exchange_started",
                "exchange_headers",
                "exchange_terminal",
            }
            or not isinstance(event.get("generic_attempt_id"), str)
            or not isinstance(event.get("request_id"), str)
            or not isinstance(event.get("exchange_id"), str)
        ):
            raise ValueError(
                "csindex_range_attachment_durable_journal_invalid"
            )
        events.append(event)
        previous_hash = str(event["event_hash"])
    return events


def _repair_durable_torn_tail(path: Path) -> None:
    if not path.is_file():
        return
    payload = path.read_bytes()
    if not payload or payload.endswith(b"\n"):
        return
    boundary = payload.rfind(b"\n") + 1
    complete = payload[:boundary]
    fragment = payload[boundary:]
    if not fragment:
        return
    fragment_hash = hashlib.sha256(fragment).hexdigest()
    fragment_root = path.parent / "range_exchange_journal_torn_fragments"
    fragment_root.mkdir(exist_ok=True)
    fragment_path = fragment_root / f"fragment_{fragment_hash}.bin"
    if fragment_path.exists():
        if fragment_path.read_bytes() != fragment:
            raise ValueError(
                "csindex_range_attachment_torn_fragment_collision"
            )
    else:
        with fragment_path.open("xb") as handle:
            handle.write(fragment)
            handle.flush()
            os.fsync(handle.fileno())
    with path.open("r+b") as handle:
        handle.truncate(len(complete))
        handle.flush()
        os.fsync(handle.fileno())
    _fsync_directory(fragment_root)
    _fsync_directory(path.parent)


def _context_from_exchange_event(
    event: Mapping[str, Any],
) -> _GenericAttemptContext:
    return _GenericAttemptContext(
        activity_id=str(event["activity_id"]),
        contract_id=str(event["contract_id"]),
        request_plan_hash=str(event["request_plan_hash"]),
        attempt_id=str(event["generic_attempt_id"]),
        request_id=str(event["request_id"]),
        request_semantic_hash=str(event["generic_request_semantic_hash"]),
        retry_ordinal=int(event["generic_retry_ordinal"]),
        capture_started_at=str(event.get("observed_at") or ""),
    )


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _load_capture_signer() -> PersistentReceiptSigner:
    return PersistentReceiptSigner.load(DEFAULT_CAPTURE_KEY)


def _new_exchange_client() -> _ExchangeClient:
    return _UrllibExchangeClient()


def _implementation_root() -> str:
    return canonical_hash(
        {
            "source_plan": inspect.getsource(_prepare_from_governed_details)
            + inspect.getsource(_prepare_legacy_from_governed_details)
            + inspect.getsource(_range_source_binding)
            + inspect.getsource(_validate_source_binding)
            + inspect.getsource(_validate_source_binding_semantic)
            + inspect.getsource(_range_attachment_requests)
            + inspect.getsource(_validate_population_geometry),
            "transport": inspect.getsource(_UrllibExchangeClient)
            + inspect.getsource(_DurableExchangeJournal)
            + inspect.getsource(_repair_durable_torn_tail)
            + inspect.getsource(_read_durable_exchange_events)
            + inspect.getsource(_RangeAttachmentTransport)
            + inspect.getsource(_logical_envelope)
            + inspect.getsource(_signed_exchange_event)
            + inspect.getsource(_exchange_result),
            "protocol_validation": inspect.getsource(
                _validate_and_assemble_logical
            )
            + inspect.getsource(_validate_physical_exchange)
            + inspect.getsource(_raw_logical_payload)
            + inspect.getsource(_exact_json_object)
            + inspect.getsource(_json_bytes)
            + inspect.getsource(_capture_public_key_from_terminal)
            + inspect.getsource(_assembled_attachment_reason)
            + inspect.getsource(_normalized_headers)
            + inspect.getsource(_parse_content_range)
            + inspect.getsource(_strong_etag)
            + inspect.getsource(_identity_content_encoding),
            "normalizer": inspect.getsource(_normalize_range_attachments)
            + inspect.getsource(_request_plan_evidence)
            + inspect.getsource(_validate_durable_closure)
            + inspect.getsource(_validate_all_terminal_attempt_counts)
            + inspect.getsource(_validate_terminal_attempt_payload)
            + inspect.getsource(_validate_durable_attempt_map),
            "governance": inspect.getsource(
                validate_csindex_range_attachment_capture
            )
            + inspect.getsource(validate_csindex_range_legacy_cons_capture)
            + inspect.getsource(_validate_range_capture)
            + inspect.getsource(replay_csindex_range_attachment_capture)
            + inspect.getsource(replay_csindex_range_legacy_cons_capture)
            + inspect.getsource(_validate_details_parent)
            + inspect.getsource(_validate_rebuilt_parent_plan)
            + inspect.getsource(_validate_authorized_contract)
            + inspect.getsource(_authorize_contract_before_network)
            + inspect.getsource(_expected_output_root),
            "contract": inspect.getsource(_contract),
            "constants": {
                "phase": PHASE,
                "capture_profile": CAPTURE_PROFILE,
                "activity_name": ACTIVITY_NAME,
                "adapter": ADAPTER_IDENTITY,
                "legacy_phase": LEGACY_PHASE,
                "legacy_capture_profile": LEGACY_CAPTURE_PROFILE,
                "legacy_activity_name": LEGACY_ACTIVITY_NAME,
                "legacy_adapter": LEGACY_ADAPTER_IDENTITY,
                "http": HTTP_IDENTITY,
                "logical_envelope_schema": LOGICAL_ENVELOPE_SCHEMA,
                "source_binding_schema": SOURCE_BINDING_SCHEMA,
                "expected_population_count": EXPECTED_POPULATION_COUNT,
                "expected_request_count": EXPECTED_REQUEST_COUNT,
                "expected_legacy_request_count": EXPECTED_LEGACY_REQUEST_COUNT,
                "range_chunk_bytes": RANGE_CHUNK_BYTES,
                "attachment_body_max_bytes": ATTACHMENT_BODY_MAX_BYTES,
                "max_wire_exchanges": MAX_WIRE_EXCHANGES,
                "max_total_response_bytes": MAX_TOTAL_RESPONSE_BYTES,
                "max_logical_envelope_bytes": MAX_LOGICAL_ENVELOPE_BYTES,
                "timeout_seconds": TIMEOUT_SECONDS,
                "minimum_logical_delay_seconds": MINIMUM_LOGICAL_DELAY_SECONDS,
                "minimum_exchange_delay_seconds": MINIMUM_EXCHANGE_DELAY_SECONDS,
                "max_retries": MAX_RETRIES,
                "required_checks": REQUIRED_CHECKS,
                "temporal_blocker": TEMPORAL_BLOCKER,
            },
            "shared_capture_engine_module_sha256": sha256_file(
                Path(capture_module.__file__)
            ),
            "csindex_source_module_sha256": sha256_file(
                Path(csindex_backfill.__file__)
            ),
            "range_module_sha256": sha256_file(Path(__file__)),
        }
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the governed full CSI range-attachment capture."
    )
    parser.add_argument("--input-capture")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--validate")
    parser.add_argument("--replay")
    parser.add_argument(
        "--legacy-cons",
        action="store_true",
        help="Use the independent exact-two legacy constituent repair profile.",
    )
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.validate:
            payload = (
                validate_csindex_range_legacy_cons_capture(args.validate)
                if args.legacy_cons
                else validate_csindex_range_attachment_capture(args.validate)
            )
        elif args.replay:
            replayed, replay_root = replay_csindex_range_attachment_capture(
                args.replay
            )
            payload = {
                "status": "succeeded",
                "replay_root": replay_root,
                "roles": {
                    role: {
                        "sha256": hashlib.sha256(value).hexdigest(),
                        "size_bytes": len(value),
                    }
                    for role, value in sorted(replayed.items())
                },
            }
        else:
            if not args.input_capture:
                raise ValueError("--input-capture is required")
            payload = (
                run_csindex_range_legacy_cons_capture(
                    args.input_capture,
                    allow_network=args.allow_network,
                    plan_only=args.plan_only,
                )
                if args.legacy_cons
                else run_csindex_range_attachment_capture(
                    args.input_capture,
                    allow_network=args.allow_network,
                    plan_only=args.plan_only,
                )
            )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}, sort_keys=True))
        return 1
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            indent=2 if args.pretty else None,
        )
    )
    return 0 if payload.get("status", "succeeded") == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Signed, resumable acquisition for the approved free-provider backfill.

This module deliberately separates physical provider capture from Data
Admission coverage-use projection.  A capture preserves the exact request,
raw provider bytes, pagination diagnostics and a signed before/after journal.
It never mutates the existing lake and never authorizes research, holdout,
paper, shadow or live execution.
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import inspect
import json
import os
import re
import shutil
import tempfile
import time
import zlib
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Protocol, Sequence
from urllib.parse import parse_qs, urlsplit

from auto_alpha.platform.artifacts.storage import (
    atomic_json,
    canonical_hash,
    publish_prepared_generation,
    read_json,
    resolve_manifest,
    sha256_file,
)
from auto_alpha.platform.governance.network.signing import (
    PersistentReceiptSigner,
    ReceiptSigningError,
    verify_signature,
)

from .provider_probe import (
    ProviderProbeObservation,
    ProviderProbeRequest,
)
from . import provider_probe as provider_probe_module
from . import run_provider_probe as run_provider_probe_module
from .run_provider_probe import (
    BAOSTOCK_FIELDS,
    BaostockProbeTransport,
    baostock_distribution_record_root,
)


SCHEMA_VERSION = "free_provider_backfill_capture_v2"
LEGACY_SCHEMA_VERSION = "free_provider_backfill_capture_v1"
CONTRACT_SCHEMA = "free_provider_backfill_contract_v2"
PLAN_SCHEMA = "free_provider_backfill_request_plan_v1"
JOURNAL_EVENT_SCHEMA = "free_provider_backfill_journal_event_v1"
RAW_ENVELOPE_SCHEMA = "free_provider_backfill_raw_envelope_v1"
HOST_BREAKER_SCHEMA = "free_provider_host_circuit_breaker_v1"
MANIFEST_NAME = "free_provider_backfill_manifest.json"
POINTER_SCHEMA = "free_provider_backfill_pointer_v1"
GENERATION_PREFIX = "free_provider_backfill"
CONTRACT_NAME = "activity_contract.json"
PLAN_NAME = "request_plan.json"
JOURNAL_NAME = "capture_journal.jsonl"
CATALOG_NAME = "capture_catalog.jsonl"
NORMALIZED_MANIFEST_NAME = "normalized/normalized_manifest.json"

DEFAULT_LAKE_ROOT = Path("/home/lijunsi/data/auto-alpha/ashare_lake")
DEFAULT_SECURITIES_PATH = DEFAULT_LAKE_ROOT / "data/securities/records.jsonl"
DEFAULT_OUTPUT_ROOT = (
    DEFAULT_LAKE_ROOT
    / "staging/data_admission"
    / "dap_d785714ef1b912a20c0f19ca"
    / "research_20120101_20191231_asof_20191231"
    / "baostock/state_daily"
)
DEFAULT_CAPTURE_KEY = (
    DEFAULT_LAKE_ROOT
    / "governance/capture_keys/free_domestic_backfill_20260816.pem"
)
DEFAULT_PERMISSION_CONTEXT = (
    "human_authorization_20260816_free_domestic_missing_data_backfill_v1"
)
SAFETY_FLAGS = (
    "data_admission_eligible",
    "profile_activation_authorized",
    "alpha_search_authorized",
    "holdout_activation_authorized",
    "paper_trading_authorized",
    "shadow_trading_authorized",
    "live_trading_authorized",
)
TERMINAL_STATES = frozenset({"positive", "empty", "error"})
BAOSTOCK_RETRYABLE_ERROR_PREFIXES = (
    "baostock_transport:SessionExpired:",
    "baostock_transport:TimeoutError",
    "baostock_transport:ConnectionError",
    "baostock_transport:OSError",
)
RETRYABLE_ERROR_PREFIXES = (
    "http_status:429",
    "http_status:500",
    "http_status:502",
    "http_status:503",
    "http_status:504",
    "transport_exception:ConnectionError",
    "transport_exception:TimeoutError",
    "transport_exception:OSError",
    "transport_exception:RuntimeError",
)
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_CAPTURE_ENGINE_ROOT_CACHE: str | None = None

_BAOSTOCK_OPERATION_BY_CASE = {
    "adjust_factor": "query_adjust_factor",
    "all_stock": "query_all_stock",
    "dividend": "query_dividend_data",
    "history": "query_history_k_data_plus",
    "history_custom": "query_history_k_data_plus",
    "hs300": "query_hs300_stocks",
    "stock_basic": "query_stock_basic",
    "trade_calendar": "query_trade_dates",
}
_BAOSTOCK_REQUEST_PARAMETER_INDEXES = {
    "adjust_factor": {"code": 4, "start": 5, "end": 6},
    "all_stock": {"date": 4},
    "dividend": {"code": 4, "year": 5},
    "history": {"code": 4, "fields": 5, "start": 6, "end": 7},
    "history_custom": {"code": 4, "fields": 5, "start": 6, "end": 7},
    "hs300": {"date": 4},
    "stock_basic": {"code": 4},
    "trade_calendar": {"start": 4, "end": 5},
}
_BAOSTOCK_RESPONSE_FIELDS_INDEX = {
    "query_adjust_factor": 10,
    "query_all_stock": 8,
    "query_dividend_data": 10,
    "query_history_k_data_plus": 8,
    "query_hs300_stocks": 8,
    "query_stock_basic": 9,
    "query_trade_dates": 9,
}
_BAOSTOCK_RESPONSE_REQUEST_INDEX_PAIRS = {
    "query_adjust_factor": ((7, 4), (8, 5), (9, 6)),
    "query_all_stock": ((7, 4),),
    "query_dividend_data": ((7, 4), (8, 5), (9, 6)),
    "query_history_k_data_plus": (
        (7, 4),
        (8, 5),
        (9, 6),
        (10, 7),
        (11, 8),
        (12, 9),
    ),
    "query_hs300_stocks": ((7, 4),),
    "query_stock_basic": ((7, 4), (8, 5)),
    "query_trade_dates": ((7, 4), (8, 5)),
}
_BAOSTOCK_PROTOCOL_BY_OPERATION = {
    "query_dividend_data": {"request_type": "13", "response_type": "14"},
    "query_adjust_factor": {"request_type": "15", "response_type": "16"},
    "query_trade_dates": {"request_type": "33", "response_type": "34"},
    "query_all_stock": {"request_type": "35", "response_type": "36"},
    "query_stock_basic": {"request_type": "45", "response_type": "46"},
    "query_hs300_stocks": {"request_type": "61", "response_type": "62"},
    "query_history_k_data_plus": {
        "request_type": "95",
        "response_type": "96",
    },
}
_BAOSTOCK_REQUEST_TOKEN_CONTRACTS = {
    "query_dividend_data": {"arity": 7, "literals": {1: "anonymous", 6: "report"}},
    "query_adjust_factor": {"arity": 7, "literals": {1: "anonymous"}},
    "query_trade_dates": {"arity": 6, "literals": {1: "anonymous"}},
    "query_all_stock": {"arity": 5, "literals": {1: "anonymous"}},
    "query_stock_basic": {"arity": 6, "literals": {1: "anonymous", 5: ""}},
    "query_hs300_stocks": {"arity": 5, "literals": {1: "anonymous"}},
    "query_history_k_data_plus": {
        "arity": 10,
        "literals": {1: "anonymous", 8: "d", 9: "3"},
    },
}
_BAOSTOCK_RESPONSE_TOKEN_ARITY = {
    "query_dividend_data": 11,
    "query_adjust_factor": 11,
    "query_trade_dates": 10,
    "query_all_stock": 9,
    "query_stock_basic": 10,
    "query_hs300_stocks": 9,
    "query_history_k_data_plus": 13,
}
_BAOSTOCK_LOGIN_PROTOCOL = {"request_type": "00", "response_type": "01"}
_BAOSTOCK_LOGIN_REQUEST_TOKEN_CONTRACT = {
    "arity": 4,
    "literals": {1: "anonymous", 2: "123456", 3: "0"},
}
_BAOSTOCK_PROVIDER_ERROR_RESPONSE_TYPE = "04"
_BAOSTOCK_PINNED_PAGE_SIZE = 2000
_BAOSTOCK_MAX_PAGES_PER_REQUEST = 4
_BAOSTOCK_MAX_DECOMPRESSED_RESPONSE_BYTES = 64 * 1024 * 1024
_BAOSTOCK_HEADER_LENGTH = 21
_BAOSTOCK_COMPRESSED_RESPONSE_TYPES = frozenset({"96", "99", "9B", "9D"})
_BAOSTOCK_RESPONSE_TRAILER = re.compile(
    rb"\x01(?P<crc>[0-9]{1,10})\n?<!\[CDATA\[\]\]>\n$"
)


class CaptureSigner(Protocol):
    public_key_pem: bytes

    def sign(self, payload: bytes) -> str: ...


class ProviderBackfillPaused(RuntimeError):
    """A sealed activity stopped after preserving its last terminal evidence."""


BackfillTransport = Callable[
    [ProviderProbeRequest, float],
    ProviderProbeObservation,
]


class RecoveringBaostockTransport:
    """Reconnect the pinned Baostock session between bounded retry attempts."""

    SESSION_EXPIRED_PROVIDER_ERROR = "baostock:10001001"
    SESSION_EXPIRED_TRANSPORT_ERROR = (
        "baostock_transport:SessionExpired:10001001"
    )

    def __init__(self) -> None:
        self._transport = BaostockProbeTransport()

    def __call__(
        self, request: ProviderProbeRequest, timeout_seconds: float
    ) -> ProviderProbeObservation:
        try:
            observation = self._transport(request, timeout_seconds)
        except Exception:
            self._replace()
            raise
        if (
            request.provider == "baostock"
            and observation.terminal_state == "error"
            and observation.error_code == self.SESSION_EXPIRED_PROVIDER_ERROR
        ):
            observation = ProviderProbeObservation(
                terminal_state=observation.terminal_state,
                raw_payload=observation.raw_payload,
                row_count=observation.row_count,
                status_code=observation.status_code,
                error_code=self.SESSION_EXPIRED_TRANSPORT_ERROR,
                diagnostics={
                    **dict(observation.diagnostics),
                    "session_recovery": {
                        "adapter": type(self).__name__,
                        "original_error_code": observation.error_code,
                        "transport_replaced": True,
                    },
                },
                checks=observation.checks,
                transport_exchange_count=observation.transport_exchange_count,
            )
        if _retryable(observation.error_code, provider=request.provider):
            self._replace()
        return observation

    def restore(
        self, request: ProviderProbeRequest, record: Mapping[str, Any]
    ) -> None:
        self._transport.restore(request, record)

    def close(self) -> None:
        self._transport.close()

    def _replace(self) -> None:
        self._transport.close()
        self._transport = BaostockProbeTransport()


@dataclass(frozen=True)
class BackfillResourceBudget:
    max_requests: int
    max_wire_exchanges: int
    max_response_bytes: int
    max_total_response_bytes: int
    timeout_seconds: float
    minimum_delay_seconds: float
    max_retries: int = 2

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FreeProviderBackfillContract:
    activity_name: str
    provider: str
    output_root: str | Path
    permission_context_id: str
    population_root: str
    capture_public_key_sha256: str
    capture_public_key_pem_b64: str
    scope_start: str
    scope_end: str
    request_start: str
    request_end: str
    allowed_hosts: tuple[str, ...]
    budget: BackfillResourceBudget
    adapter_identity: Mapping[str, str] = field(default_factory=dict)
    source_profile_id: str = "dap_d785714ef1b912a20c0f19ca"

    def semantic(self) -> dict[str, Any]:
        return {
            "schema_version": CONTRACT_SCHEMA,
            "activity_name": self.activity_name,
            "provider": self.provider,
            "output_namespace_id": canonical_hash(
                str(_safe_output_root(self.output_root))
            ),
            "permission_context_id": self.permission_context_id,
            "population_root": self.population_root,
            "capture_public_key_sha256": self.capture_public_key_sha256,
            "capture_public_key_pem_b64": self.capture_public_key_pem_b64,
            "scope": {
                "date_start": self.scope_start,
                "date_end": self.scope_end,
                "request_start": self.request_start,
                "request_end": self.request_end,
            },
            "allowed_hosts": sorted(self.allowed_hosts),
            "budget": self.budget.to_dict(),
            "adapter_identity": dict(sorted(self.adapter_identity.items())),
            "source_profile_id": self.source_profile_id,
            "mode": "signed_raw_provider_capture",
            "capture_before_normalization": True,
            "old_lake_mutated": False,
            "safety": {name: False for name in SAFETY_FLAGS},
        }


@dataclass(frozen=True)
class NormalizedArtifact:
    role: str
    relative_path: str
    record_count: int


@dataclass(frozen=True)
class PauseResumeAuthorization:
    """One human-authorized retry bound to one immutable pause artifact."""

    authorization_id: str
    pause_content_hash: str
    cooldown_seconds: int = 0


Normalizer = Callable[
    [Path, Sequence[ProviderProbeRequest], Mapping[str, Mapping[str, Any]]],
    Sequence[NormalizedArtifact],
]


def run_free_provider_backfill(
    contract: FreeProviderBackfillContract,
    requests: Sequence[ProviderProbeRequest],
    *,
    transport: BackfillTransport,
    signer: CaptureSigner,
    normalizer: Normalizer | None = None,
    resume_authorization: PauseResumeAuthorization | None = None,
    runtime_implementation_root: str,
) -> dict[str, Any]:
    """Capture a sealed request plan and publish an immutable generation.

    Recovery is keyed by the contract and request-plan hashes.  A start event
    is signed and fsync'd before transport.  The raw wrapper is then fsync'd
    before the signed terminal event.  An interrupted terminal event is
    reconstructed only from that durable wrapper.
    """

    sealed_implementation_root = str(
        contract.adapter_identity.get("implementation_root") or ""
    )
    if (
        not _HEX_64.fullmatch(runtime_implementation_root)
        or sealed_implementation_root != runtime_implementation_root
    ):
        raise ValueError("free_provider_backfill_runtime_implementation_root_mismatch")
    request_rows = _validate_contract_and_plan(contract, requests, signer=signer)
    contract_semantic = contract.semantic()
    contract_id = canonical_hash(contract_semantic)
    request_plan_hash = canonical_hash(request_rows)
    activity_id = canonical_hash(
        {"contract_id": contract_id, "request_plan_hash": request_plan_hash}
    )
    output = _safe_output_root(contract.output_root)
    cached = _matching_generation(
        output,
        contract_id=contract_id,
        request_plan_hash=request_plan_hash,
    )
    if cached is not None:
        return cached | {"cache_hit": True}

    run_parent = output.parent / f".{output.name}.activities"
    run_root = run_parent / activity_id
    with _activity_lock(_global_activity_lock_root(output), activity_id), _activity_lock(
        run_parent, activity_id
    ):
        cached = _matching_generation(
            output,
            contract_id=contract_id,
            request_plan_hash=request_plan_hash,
        )
        if cached is not None:
            return cached | {"cache_hit": True}
        recovered_publication = _resume_interrupted_publication(
            output=output,
            run_parent=run_parent,
            contract_id=contract_id,
            request_plan_hash=request_plan_hash,
            activity_id=activity_id,
        )
        if recovered_publication is not None:
            return recovered_publication | {
                "cache_hit": True,
                "publication_recovered": True,
            }
        run_root.mkdir(parents=True, exist_ok=True)
        (run_root / "raw_envelopes").mkdir(exist_ok=True)
        _write_or_verify_json(run_root / CONTRACT_NAME, contract_semantic)
        _write_or_verify_json(
            run_root / PLAN_NAME,
            {
                "schema_version": PLAN_SCHEMA,
                "request_plan_hash": request_plan_hash,
                "requests": request_rows,
            },
        )
        terminal = _recover_journal(
            run_root,
            activity_id=activity_id,
            contract_id=contract_id,
            request_rows=request_rows,
            signer=signer,
        )
        _restore_transport(transport, requests, terminal)
        usage = _capture_usage(run_root)
        last_call_at: float | None = None
        for request, request_row in zip(requests, request_rows):
            current = terminal.get(request.request_id)
            if current is not None and current.get("terminal_state") in {
                "positive",
                "empty",
            }:
                continue
            if current is not None and (
                not _retryable(
                    str(current.get("error_code") or ""),
                    provider=contract.provider,
                )
                or int(current.get("retry_ordinal", -1))
                >= contract.budget.max_retries
            ):
                authorized = _authorize_paused_retry(
                    run_root,
                    activity_id=activity_id,
                    contract_id=contract_id,
                    request_plan_hash=request_plan_hash,
                    current=current,
                    authorization=resume_authorization,
                    max_retries=contract.budget.max_retries,
                    signer=signer,
                )
                if not authorized:
                    _pause_activity(
                        run_root,
                        request_id=request.request_id,
                        reason="prior_terminal_error_requires_new_authority",
                        terminal=current,
                        usage=usage,
                    )
            retry_ordinal = (
                int(current.get("retry_ordinal", -1)) + 1 if current is not None else 0
            )
            while retry_ordinal <= contract.budget.max_retries:
                _assert_provider_host_breaker_closed(
                    output,
                    provider=contract.provider,
                    host=str(urlsplit(request.url).hostname or ""),
                    expected_public_key=signer.public_key_pem,
                )
                budget_reason = _budget_exceeded_reason(
                    contract.budget,
                    usage,
                    before_request=True,
                )
                if budget_reason:
                    _pause_activity(
                        run_root,
                        request_id=request.request_id,
                        reason=budget_reason,
                        terminal=current or {},
                        usage=usage,
                    )
                if last_call_at is not None:
                    delay = contract.budget.minimum_delay_seconds - (
                        time.monotonic() - last_call_at
                    )
                    if delay > 0:
                        time.sleep(delay)
                attempt_id = f"{request.request_id}:{retry_ordinal}"
                started_at = _utc_now()
                _append_signed_event(
                    run_root / JOURNAL_NAME,
                    {
                        "schema_version": JOURNAL_EVENT_SCHEMA,
                        "event_type": "capture_attempt_started",
                        "event_id": f"capture_attempt_started:{attempt_id}",
                        "activity_id": activity_id,
                        "contract_id": contract_id,
                        "request_plan_hash": request_plan_hash,
                        "attempt_id": attempt_id,
                        "request_id": request.request_id,
                        "request_semantic_hash": canonical_hash(request_row),
                        "retry_ordinal": retry_ordinal,
                        "capture_started_at": started_at,
                        "capture_engine_root": _capture_engine_root(),
                        "request": request_row,
                    },
                    signer=signer,
                )
                try:
                    observation = transport(request, contract.budget.timeout_seconds)
                except Exception as exc:  # the exception bytes still become terminal evidence
                    observation = ProviderProbeObservation(
                        terminal_state="error",
                        raw_payload=json.dumps(
                            {
                                "schema_version": "provider_transport_exception_v1",
                                "error_type": type(exc).__name__,
                                "message": str(exc)[:1000],
                            },
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode(),
                        row_count=None,
                        error_code=f"transport_exception:{type(exc).__name__}",
                        checks={"transport_completed": False},
                        transport_exchange_count=0,
                    )
                finally:
                    last_call_at = time.monotonic()
                wrapper_path, wrapper = _write_raw_wrapper(
                    run_root,
                    request=request,
                    request_semantic_hash=canonical_hash(request_row),
                    attempt_id=attempt_id,
                    retry_ordinal=retry_ordinal,
                    capture_started_at=started_at,
                    observation=observation,
                )
                terminal_event = _terminal_event(
                    activity_id=activity_id,
                    contract_id=contract_id,
                    request_plan_hash=request_plan_hash,
                    request=request,
                    request_row=request_row,
                    attempt_id=attempt_id,
                    retry_ordinal=retry_ordinal,
                    wrapper_path=wrapper_path,
                    wrapper=wrapper,
                    raw_envelope_sha256=sha256_file(run_root / wrapper_path),
                )
                stored = _append_signed_event(
                    run_root / JOURNAL_NAME,
                    terminal_event,
                    signer=signer,
                )
                terminal[request.request_id] = stored
                usage = {
                    "attempt_count": int(usage["attempt_count"]) + 1,
                    "response_bytes": int(usage["response_bytes"])
                    + (run_root / wrapper_path).stat().st_size,
                    "wire_exchange_count": int(usage["wire_exchange_count"])
                    + int(wrapper.get("transport_exchange_count") or 0),
                }
                if int(wrapper.get("raw_payload_size_bytes") or 0) > int(
                    contract.budget.max_response_bytes
                ):
                    _pause_activity(
                        run_root,
                        request_id=request.request_id,
                        reason="single_response_budget_exceeded",
                        terminal=stored,
                        usage=usage,
                    )
                budget_reason = _budget_exceeded_reason(
                    contract.budget,
                    usage,
                    before_request=False,
                )
                if budget_reason:
                    _pause_activity(
                        run_root,
                        request_id=request.request_id,
                        reason=budget_reason,
                        terminal=stored,
                        usage=usage,
                    )
                if observation.terminal_state != "error":
                    break
                if observation.status_code in {403, 429} or bool(
                    observation.diagnostics.get("waf_html_observed")
                ):
                    _open_provider_host_breaker(
                        output,
                        provider=contract.provider,
                        host=str(urlsplit(request.url).hostname or ""),
                        activity_id=activity_id,
                        contract_id=contract_id,
                        terminal=stored,
                        signer=signer,
                    )
                    _pause_activity(
                        run_root,
                        request_id=request.request_id,
                        reason="provider_host_circuit_breaker_open",
                        terminal=stored,
                        usage=usage,
                    )
                if (
                    not _retryable(
                        observation.error_code,
                        provider=contract.provider,
                    )
                    or retry_ordinal >= contract.budget.max_retries
                ):
                    _pause_activity(
                        run_root,
                        request_id=request.request_id,
                        reason="provider_terminal_error_or_circuit_breaker",
                        terminal=stored,
                        usage=usage,
                    )
                retry_ordinal += 1

        missing = [
            request.request_id
            for request in requests
            if request.request_id not in terminal
        ]
        if missing:
            raise RuntimeError(f"provider_backfill_incomplete_journal:{missing[0]}")
        artifacts = tuple(
            normalizer(run_root, requests, terminal) if normalizer is not None else ()
        )
        return _publish_capture_generation(
            output=output,
            run_root=run_root,
            activity_id=activity_id,
            contract_id=contract_id,
            request_plan_hash=request_plan_hash,
            terminal=terminal,
            artifacts=artifacts,
            usage=usage,
            signer=signer,
        )


def resume_free_provider_backfill_activity(
    run_root: str | Path,
    *,
    transport: BackfillTransport,
    signer: CaptureSigner,
    normalizer: Normalizer | None = None,
    output_root: str | Path | None = None,
    resume_authorization: PauseResumeAuthorization | None = None,
) -> dict[str, Any]:
    """Resume an existing sealed identity after an audited engine migration."""

    root = Path(run_root).resolve()
    contract_row = read_json(root / CONTRACT_NAME)
    plan = read_json(root / PLAN_NAME)
    if plan.get("schema_version") != PLAN_SCHEMA:
        raise ValueError("free_provider_backfill_resume_plan_invalid")
    scope = contract_row.get("scope") or {}
    budget_row = contract_row.get("budget") or {}
    inferred_output = _output_from_activity_root(root)
    if output_root is not None and _safe_output_root(output_root) != inferred_output:
        raise ValueError("free_provider_backfill_resume_output_override_mismatch")
    contract = FreeProviderBackfillContract(
        activity_name=str(contract_row["activity_name"]),
        provider=str(contract_row["provider"]),
        output_root=inferred_output,
        permission_context_id=str(contract_row["permission_context_id"]),
        population_root=str(contract_row["population_root"]),
        capture_public_key_sha256=str(contract_row["capture_public_key_sha256"]),
        capture_public_key_pem_b64=str(contract_row["capture_public_key_pem_b64"]),
        scope_start=str(scope["date_start"]),
        scope_end=str(scope["date_end"]),
        request_start=str(scope["request_start"]),
        request_end=str(scope["request_end"]),
        allowed_hosts=tuple(contract_row.get("allowed_hosts") or ()),
        budget=BackfillResourceBudget(
            max_requests=int(budget_row["max_requests"]),
            max_wire_exchanges=int(budget_row["max_wire_exchanges"]),
            max_response_bytes=int(budget_row["max_response_bytes"]),
            max_total_response_bytes=int(budget_row["max_total_response_bytes"]),
            timeout_seconds=float(budget_row["timeout_seconds"]),
            minimum_delay_seconds=float(budget_row["minimum_delay_seconds"]),
            max_retries=int(budget_row["max_retries"]),
        ),
        adapter_identity=dict(contract_row.get("adapter_identity") or {}),
        source_profile_id=str(contract_row["source_profile_id"]),
    )
    if contract.semantic() != contract_row:
        raise ValueError("free_provider_backfill_resume_contract_roundtrip_invalid")
    requests = [
        _request_from_semantic(row) for row in plan.get("requests") or ()
    ]
    expected_activity_id = canonical_hash(
        {
            "contract_id": canonical_hash(contract_row),
            "request_plan_hash": canonical_hash(plan.get("requests") or ()),
        }
    )
    if root.name != expected_activity_id:
        raise ValueError("free_provider_backfill_resume_activity_identity_invalid")
    expected_root = (
        inferred_output.parent
        / f".{inferred_output.name}.activities"
        / expected_activity_id
    ).resolve()
    if expected_root != root:
        raise ValueError("free_provider_backfill_resume_root_geometry_invalid")
    return run_free_provider_backfill(
        contract,
        requests,
        transport=transport,
        signer=signer,
        normalizer=normalizer,
        resume_authorization=resume_authorization,
        runtime_implementation_root=_baostock_implementation_root(),
    )


def validate_free_provider_backfill(path: str | Path) -> dict[str, Any]:
    """Validate identity, signatures, raw closure and normalized artifacts."""

    manifest_path = resolve_manifest(path, MANIFEST_NAME)
    root = manifest_path.parent
    payload = read_json(manifest_path)
    semantic = {
        key: value
        for key, value in payload.items()
        if key
        not in {
            "capture_publication_signature",
            "content_hash",
            "generation_id",
        }
    }
    if (
        payload.get("schema_version") not in {SCHEMA_VERSION, LEGACY_SCHEMA_VERSION}
        or payload.get("content_hash") != canonical_hash(semantic)
        or payload.get("generation_id")
        != f"{GENERATION_PREFIX}_{str(payload.get('content_hash') or '')[:24]}"
        or root.name != payload.get("generation_id")
    ):
        raise ValueError("free_provider_backfill_identity_invalid")
    safety = payload.get("safety")
    if not isinstance(safety, Mapping) or any(
        safety.get(name) is not False for name in SAFETY_FLAGS
    ):
        raise ValueError("free_provider_backfill_safety_invalid")
    contract = read_json(root / CONTRACT_NAME)
    plan = read_json(root / PLAN_NAME)
    if (
        payload.get("contract_id") != canonical_hash(contract)
        or payload.get("request_plan_hash")
        != canonical_hash(plan.get("requests"))
        or plan.get("request_plan_hash") != payload.get("request_plan_hash")
        or payload.get("activity_id")
        != canonical_hash(
            {
                "contract_id": payload.get("contract_id"),
                "request_plan_hash": payload.get("request_plan_hash"),
            }
        )
    ):
        raise ValueError("free_provider_backfill_contract_or_plan_invalid")
    public_key = _public_key_bytes(contract)
    request_by_id = {
        str(row["request_id"]): row for row in plan.get("requests") or ()
    }
    publication_signature_verified = payload.get("schema_version") == SCHEMA_VERSION
    if publication_signature_verified:
        try:
            verify_signature(
                public_key_pem=public_key,
                payload=_canonical_bytes(
                    semantic
                    | {
                        "content_hash": payload["content_hash"],
                        "generation_id": payload["generation_id"],
                    }
                ),
                signature_b64=str(
                    payload.get("capture_publication_signature") or ""
                ),
            )
        except ReceiptSigningError as exc:
            raise ValueError(
                "free_provider_backfill_publication_signature_invalid"
            ) from exc
    events = _read_and_validate_journal(
        root / JOURNAL_NAME,
        expected_activity_id=str(payload.get("activity_id") or ""),
        expected_contract_id=str(payload.get("contract_id") or ""),
        public_key=public_key,
        request_rows=plan.get("requests") or (),
        request_plan_hash=str(payload.get("request_plan_hash") or ""),
        max_retries=int((contract.get("budget") or {}).get("max_retries", -1)),
        allow_legacy_engine_migration=(
            payload.get("schema_version") == LEGACY_SCHEMA_VERSION
        ),
    )
    terminal_events = [
        row
        for row in events
        if row.get("event_type") == "capture_attempt_terminal"
    ]
    terminal = {
        str(row["request_id"]): row
        for row in terminal_events
    }
    if (
        len(terminal) != int(payload.get("request_count") or -1)
        or len(terminal_events) != int(payload.get("terminal_attempt_count") or -1)
        or len(plan.get("requests") or ()) != len(terminal)
        or set(terminal)
        != {str(row["request_id"]) for row in plan.get("requests") or ()}
        or payload.get("capture_journal_sha256") != sha256_file(root / JOURNAL_NAME)
        or payload.get("capture_journal_event_count") != len(events)
    ):
        raise ValueError("free_provider_backfill_terminal_count_invalid")
    catalog_path = root / CATALOG_NAME
    if payload.get("capture_catalog_sha256") != sha256_file(catalog_path):
        raise ValueError("free_provider_backfill_catalog_hash_invalid")
    catalog = _read_jsonl(catalog_path)
    if (
        len(catalog) != len(terminal_events)
        or payload.get("capture_catalog_count") != len(catalog)
    ):
        raise ValueError("free_provider_backfill_catalog_count_invalid")
    event_by_attempt = {str(row["attempt_id"]): row for row in terminal_events}
    if len(event_by_attempt) != len(terminal_events):
        raise ValueError("free_provider_backfill_terminal_attempt_duplicate")
    catalog_attempts: set[str] = set()
    response_bytes = 0
    wire_exchange_count = 0
    budget = contract.get("budget") or {}
    for row in catalog:
        raw_path = _confined_file(root, str(row.get("relative_path") or ""))
        if (
            raw_path is None
            or sha256_file(raw_path) != row.get("sha256")
            or raw_path.stat().st_size != row.get("size_bytes")
        ):
            raise ValueError("free_provider_backfill_raw_capture_invalid")
        wrapper = read_json(raw_path)
        attempt_id = str(wrapper.get("attempt_id") or "")
        request_id = str(wrapper.get("request_id") or "")
        event = event_by_attempt.get(attempt_id)
        try:
            raw_payload = base64.b64decode(
                str(wrapper.get("raw_payload_base64") or ""), validate=True
            )
        except (ValueError, TypeError) as exc:
            raise ValueError("free_provider_backfill_raw_payload_encoding_invalid") from exc
        if (
            event is None
            or attempt_id in catalog_attempts
            or row.get("attempt_id") != attempt_id
            or row.get("request_id") != request_id
            or event.get("request_id") != request_id
            or event.get("request_semantic_hash")
            != wrapper.get("request_semantic_hash")
            or event.get("retry_ordinal") != wrapper.get("retry_ordinal")
            or event.get("capture_started_at") != wrapper.get("capture_started_at")
            or event.get("capture_completed_at") != wrapper.get("capture_completed_at")
            or event.get("terminal_state") != wrapper.get("terminal_state")
            or event.get("row_count") != wrapper.get("row_count")
            or event.get("status_code") != wrapper.get("status_code")
            or event.get("error_code") != wrapper.get("error_code")
            or event.get("diagnostics") != wrapper.get("diagnostics")
            or event.get("checks") != wrapper.get("checks")
            or event.get("transport_exchange_count")
            != wrapper.get("transport_exchange_count")
            or event.get("raw_envelope_sha256") != row.get("sha256")
            or event.get("raw_envelope_relative_path") != row.get("relative_path")
            or wrapper.get("schema_version") != RAW_ENVELOPE_SCHEMA
            or wrapper.get("raw_payload_sha256")
            != hashlib.sha256(raw_payload).hexdigest()
            or wrapper.get("raw_payload_size_bytes") != len(raw_payload)
            or event.get("raw_payload_sha256") != wrapper.get("raw_payload_sha256")
            or event.get("raw_payload_size_bytes") != len(raw_payload)
        ):
            raise ValueError("free_provider_backfill_raw_terminal_binding_invalid")
        if len(raw_payload) > int(budget.get("max_response_bytes") or -1):
            raise ValueError("free_provider_backfill_single_response_budget_invalid")
        if (
            payload.get("schema_version") == SCHEMA_VERSION
            and contract.get("provider") == "baostock"
        ):
            _validate_baostock_wire_envelope(
                raw_payload,
                expected_exchange_count=int(
                    wrapper.get("transport_exchange_count") or 0
                ),
                request=request_by_id[request_id],
                terminal_state=str(wrapper.get("terminal_state") or ""),
            )
        catalog_attempts.add(attempt_id)
        response_bytes += raw_path.stat().st_size
        exchanges = wrapper.get("transport_exchange_count")
        if not isinstance(exchanges, int) or isinstance(exchanges, bool) or exchanges < 0:
            raise ValueError("free_provider_backfill_transport_exchange_count_invalid")
        wire_exchange_count += exchanges
    if catalog_attempts != set(event_by_attempt):
        raise ValueError("free_provider_backfill_catalog_terminal_closure_invalid")
    derived_usage = {
        "attempt_count": len(catalog),
        "response_bytes": response_bytes,
        "wire_exchange_count": wire_exchange_count,
    }
    derived_counts = {state: 0 for state in sorted(TERMINAL_STATES)}
    for event in terminal.values():
        state = str(event.get("terminal_state") or "")
        if state not in derived_counts:
            raise ValueError("free_provider_backfill_terminal_state_invalid")
        derived_counts[state] += 1
    derived_status = (
        "succeeded"
        if all(
            row.get("terminal_state") in {"positive", "empty"}
            and row.get("expectation_met") is True
            for row in terminal.values()
        )
        else "blocked"
    )
    if (
        payload.get("resource_usage") != derived_usage
        or payload.get("terminal_counts") != derived_counts
        or payload.get("status") != derived_status
        or derived_usage["attempt_count"] > int(budget.get("max_requests") or -1)
        or derived_usage["response_bytes"]
        > int(budget.get("max_total_response_bytes") or -1)
        or derived_usage["wire_exchange_count"]
        > int(budget.get("max_wire_exchanges") or -1)
    ):
        raise ValueError("free_provider_backfill_derived_evidence_invalid")
    for artifact in payload.get("normalized_artifacts") or ():
        normalized_path = _confined_file(root, str(artifact.get("relative_path") or ""))
        if (
            normalized_path is None
            or sha256_file(normalized_path) != artifact.get("sha256")
            or normalized_path.stat().st_size != artifact.get("size_bytes")
        ):
            raise ValueError("free_provider_backfill_normalized_artifact_invalid")
    pause_by_hash: dict[str, tuple[Mapping[str, Any], Path]] = {}
    for artifact in payload.get("pause_artifacts") or ():
        pause_path = _confined_file(root, str(artifact.get("relative_path") or ""))
        if (
            pause_path is None
            or sha256_file(pause_path) != artifact.get("sha256")
            or pause_path.stat().st_size != artifact.get("size_bytes")
        ):
            raise ValueError("free_provider_backfill_pause_artifact_invalid")
        pause = read_json(pause_path)
        pause_semantic = {
            key: value for key, value in pause.items() if key != "content_hash"
        }
        pause_hash = str(pause.get("content_hash") or "")
        if (
            pause.get("schema_version") != "free_provider_backfill_pause_v1"
            or pause_hash != canonical_hash(pause_semantic)
            or pause_hash in pause_by_hash
        ):
            raise ValueError("free_provider_backfill_pause_artifact_invalid")
        pause_by_hash[pause_hash] = (artifact, pause_path)
    for event in events:
        if event.get("event_type") != "pause_resume_authorized":
            continue
        binding = pause_by_hash.get(str(event.get("pause_content_hash") or ""))
        if binding is None:
            raise ValueError("free_provider_backfill_pause_resume_artifact_missing")
        artifact, pause_path = binding
        pause = read_json(pause_path)
        terminal_event = event_by_attempt.get(str(event.get("attempt_id") or ""))
        paused_at = _parse_utc(str(pause.get("paused_at") or ""))
        expected_not_before = paused_at + timedelta(
            seconds=int(event.get("cooldown_seconds") or 0)
        )
        if (
            artifact.get("relative_path") != event.get("pause_relative_path")
            or artifact.get("sha256") != event.get("pause_artifact_sha256")
            or pause.get("request_id") != event.get("request_id")
            or pause.get("attempt_id") != event.get("attempt_id")
            or pause.get("terminal_state") != "error"
            or terminal_event is None
            or not _manual_resume_required(
                terminal_event,
                provider=str(
                    (
                        request_by_id.get(str(event.get("request_id") or ""))
                        or {}
                    ).get("provider")
                    or ""
                ),
            )
            or pause.get("error_code") != terminal_event.get("error_code")
            or pause.get("status_code") != terminal_event.get("status_code")
            or _parse_utc(str(event.get("not_before") or ""))
            != expected_not_before
            or _parse_utc(str(event.get("authorized_at") or ""))
            < expected_not_before
        ):
            raise ValueError("free_provider_backfill_pause_resume_binding_invalid")
    expected_files = {
        MANIFEST_NAME,
        CONTRACT_NAME,
        PLAN_NAME,
        JOURNAL_NAME,
        CATALOG_NAME,
        *[str(row["relative_path"]) for row in catalog],
        *[
            str(row["relative_path"])
            for row in payload.get("normalized_artifacts") or ()
        ],
        *[
            str(row["relative_path"])
            for row in payload.get("pause_artifacts") or ()
        ],
    }
    actual_files = {
        item.relative_to(root).as_posix()
        for item in root.rglob("*")
        if item.is_file() and not item.is_symlink()
    }
    if actual_files != expected_files or any(item.is_symlink() for item in root.rglob("*")):
        raise ValueError("free_provider_backfill_file_closure_invalid")
    return payload | {
        "manifest_path": str(manifest_path),
        "publication_signature_verified": publication_signature_verified,
        "normalized_artifacts_trusted": publication_signature_verified,
    }


def replay_normalized_artifacts(
    path: str | Path,
    *,
    normalizer: Normalizer,
    required_roles: Sequence[str],
) -> tuple[dict[str, bytes], str]:
    """Independently replay selected normalized roles from signed raw bytes."""

    capture = validate_free_provider_backfill(path)
    root = Path(str(capture["manifest_path"])).parent
    plan = read_json(root / PLAN_NAME)
    request_rows = plan.get("requests") or ()
    requests = [_request_from_semantic(row) for row in request_rows]
    terminal: dict[str, dict[str, Any]] = {}
    for event in _read_jsonl(root / JOURNAL_NAME):
        if event.get("event_type") == "capture_attempt_terminal":
            terminal[str(event["request_id"])] = event
    if set(terminal) != {request.request_id for request in requests}:
        raise ValueError("free_provider_backfill_replay_terminal_closure_invalid")
    with tempfile.TemporaryDirectory(prefix="auto-alpha-normalized-replay-") as directory:
        replay_root = Path(directory)
        for event in terminal.values():
            relative = Path(str(event.get("raw_envelope_relative_path") or ""))
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("free_provider_backfill_replay_raw_path_invalid")
            source = root / relative
            target = replay_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(source, target)
            except OSError:
                shutil.copyfile(source, target)
        artifacts = normalizer(replay_root, requests, terminal)
        by_role = {artifact.role: artifact for artifact in artifacts}
        if len(by_role) != len(artifacts) or any(
            role not in by_role for role in required_roles
        ):
            raise ValueError("free_provider_backfill_replay_role_closure_invalid")
        payloads = {
            role: (replay_root / by_role[role].relative_path).read_bytes()
            for role in required_roles
        }
    replay_root_hash = canonical_hash(
        [
            {
                "role": role,
                "sha256": hashlib.sha256(payloads[role]).hexdigest(),
                "size_bytes": len(payloads[role]),
            }
            for role in sorted(payloads)
        ]
    )
    return payloads, replay_root_hash


def build_baostock_state_plan(
    securities_path: str | Path,
    *,
    request_start: str = "20111201",
    request_end: str = "20191231",
    include_codes: Sequence[str] | None = None,
) -> tuple[list[dict[str, Any]], list[ProviderProbeRequest]]:
    """Freeze the exact lifecycle population and Baostock state request plan."""

    allowed_codes = {str(value) for value in include_codes or ()}
    population: dict[str, dict[str, Any]] = {}
    for row in _read_jsonl(Path(securities_path)):
        code = str(row.get("ts_code") or "")
        list_date = str(row.get("list_date") or "")
        delist_date = str(row.get("delist_date") or "99999999")
        if (
            not code.endswith((".SH", ".SZ"))
            or allowed_codes and code not in allowed_codes
            or len(list_date) != 8
            or list_date > "20191231"
            or delist_date < "20120101"
        ):
            continue
        population[code] = {
            "ts_code": code,
            "provider_code": _to_baostock_code(code),
            "exchange": str(row.get("exchange") or ""),
            "list_date": list_date,
            "delist_date": (
                None if delist_date == "99999999" else delist_date
            ),
        }
    rows = [population[code] for code in sorted(population)]
    if not rows:
        raise ValueError("baostock_backfill_population_empty")
    requests = [
        ProviderProbeRequest(
            request_id="baostock_calendar_20111201_20191231",
            provider="baostock",
            endpoint="trade_calendar",
            method="BAOSTOCK",
            url=(
                "baostock://public-api.baostock.com/trade_calendar"
                "?start=2011-12-01&end=2019-12-31"
            ),
            disposition="bounded_backfill",
            evidence_semantics="raw_custom_socket_response_plus_locked_parser",
            expected_terminal_states=("positive",),
            required_checks=(
                "provider_success",
                "raw_wire_captured",
                "terminal_marker_complete",
                "pagination_terminal_unambiguous",
                "row_width_matches_fields",
                "calendar_fields",
                "calendar_has_open_and_closed_days",
            ),
            metadata={"case": "trade_calendar"},
        )
    ]
    start = _hyphen_date(request_start)
    end = _hyphen_date(request_end)
    for row in rows:
        provider_code = str(row["provider_code"])
        requests.append(
            ProviderProbeRequest(
                request_id=f"baostock_state_{str(row['ts_code']).replace('.', '_')}",
                provider="baostock",
                endpoint="history_state_daily",
                method="BAOSTOCK",
                url=(
                    "baostock://public-api.baostock.com/history"
                    f"?code={provider_code}&start={start}&end={end}"
                ),
                disposition="bounded_backfill",
                evidence_semantics="raw_custom_socket_response_plus_locked_parser",
                expected_terminal_states=("positive", "empty"),
                required_checks=(
                    "provider_success",
                    "raw_wire_captured",
                    "terminal_marker_complete",
                    "pagination_terminal_unambiguous",
                    "row_width_matches_fields",
                    "history_fields_exact",
                    "unique_security_day",
                ),
                metadata={
                    "case": "history",
                    "ts_code": row["ts_code"],
                    "list_date": row["list_date"],
                    "delist_date": row["delist_date"],
                },
            )
        )
    return rows, requests


def normalize_baostock_state_capture(
    run_root: Path,
    requests: Sequence[ProviderProbeRequest],
    terminal: Mapping[str, Mapping[str, Any]],
) -> Sequence[NormalizedArtifact]:
    """Replay archived socket bytes into daily ST/suspension staging rows."""

    normalized = run_root / "normalized"
    normalized.mkdir(exist_ok=True)
    st_path = normalized / "st_status_daily.jsonl"
    suspension_path = normalized / "suspensions.jsonl"
    state_path = normalized / "provider_state_daily.jsonl"
    st_coverage_path = normalized / "st_status_coverage.jsonl"
    suspension_coverage_path = normalized / "suspension_coverage.jsonl"
    calendar_path = normalized / "trade_calendar.jsonl"
    conflict_path = normalized / "conflicts.jsonl"
    counts = {
        "st_status_daily": 0,
        "suspensions": 0,
        "provider_state_daily": 0,
        "st_status_coverage": 0,
        "suspension_coverage": 0,
        "trade_calendar": 0,
        "conflicts": 0,
    }
    handles = {
        "st": st_path.open("wb"),
        "suspension": suspension_path.open("wb"),
        "state": state_path.open("wb"),
        "st_coverage": st_coverage_path.open("wb"),
        "suspension_coverage": suspension_coverage_path.open("wb"),
        "calendar": calendar_path.open("wb"),
        "conflict": conflict_path.open("wb"),
    }
    try:
        for request in requests:
            receipt = terminal[request.request_id]
            wrapper_path = run_root / str(receipt["raw_envelope_relative_path"])
            wrapper = read_json(wrapper_path)
            raw_payload = base64.b64decode(wrapper["raw_payload_base64"], validate=True)
            fields, items = _baostock_logical_rows(raw_payload)
            source_hash = str(wrapper["raw_payload_sha256"])
            if request.metadata.get("case") == "trade_calendar":
                if fields != ["calendar_date", "is_trading_day"]:
                    _write_line(
                        handles["conflict"],
                        {
                            "request_id": request.request_id,
                            "reason": "calendar_schema_mismatch",
                            "fields": fields,
                        },
                    )
                    counts["conflicts"] += 1
                    continue
                previous_open: str | None = None
                for item in items:
                    trade_date = str(item[0]).replace("-", "")
                    is_open = str(item[1]) == "1"
                    for exchange in ("SSE", "SZSE"):
                        _write_line(
                            handles["calendar"],
                            {
                                "exchange": exchange,
                                "trade_date": trade_date,
                                "is_open": is_open,
                                "prev_trade_date": previous_open,
                                "source_request_id": request.request_id,
                                "source_payload_sha256": source_hash,
                                "shared_calendar_mapping": True,
                            },
                        )
                        counts["trade_calendar"] += 1
                    if is_open:
                        previous_open = trade_date
                continue
            if fields != BAOSTOCK_FIELDS.split(","):
                _write_line(
                    handles["conflict"],
                    {
                        "request_id": request.request_id,
                        "reason": "history_schema_mismatch",
                        "fields": fields,
                    },
                )
                counts["conflicts"] += 1
                continue
            ts_code = str(request.metadata.get("ts_code") or "")
            seen_dates: set[str] = set()
            population_empty = not items
            if population_empty:
                _write_line(
                    handles["conflict"],
                    {
                        "request_id": request.request_id,
                        "reason": "provider_empty_for_lifecycle_population",
                        "ts_code": ts_code,
                    },
                )
                counts["conflicts"] += 1
            for item in items:
                if len(item) != len(fields):
                    raise ValueError(
                        f"baostock_backfill_row_width_invalid:{request.request_id}"
                    )
                trade_date = str(item[0]).replace("-", "")
                observed_code = _from_baostock_code(str(item[1]))
                if observed_code != ts_code or trade_date in seen_dates:
                    _write_line(
                        handles["conflict"],
                        {
                            "request_id": request.request_id,
                            "reason": (
                                "provider_code_mismatch"
                                if observed_code != ts_code
                                else "duplicate_trade_date"
                            ),
                            "expected_ts_code": ts_code,
                            "observed_ts_code": observed_code,
                            "trade_date": trade_date,
                        },
                    )
                    counts["conflicts"] += 1
                    continue
                seen_dates.add(trade_date)
                trade_status = str(item[9])
                is_st_value = str(item[10])
                if trade_status not in {"0", "1"} or is_st_value not in {"0", "1"}:
                    _write_line(
                        handles["conflict"],
                        {
                            "request_id": request.request_id,
                            "reason": "state_value_invalid",
                            "trade_date": trade_date,
                            "tradestatus": trade_status,
                            "isST": is_st_value,
                        },
                    )
                    counts["conflicts"] += 1
                    continue
                _write_line(
                    handles["state"],
                    {
                        "ts_code": ts_code,
                        "trade_date": trade_date,
                        "provider_trade_status": int(trade_status),
                        "provider_is_st": int(is_st_value),
                        "source_request_id": request.request_id,
                        "source_payload_sha256": source_hash,
                    },
                )
                counts["provider_state_daily"] += 1
                if is_st_value == "1":
                    _write_line(
                        handles["st"],
                        {
                            "ts_code": ts_code,
                            "trade_date": trade_date,
                            "type": "ST_FAMILY",
                            "type_name": "Baostock isST=1; subtype unknown",
                            "source_request_id": request.request_id,
                            "source_payload_sha256": source_hash,
                            "st_subtype_known": False,
                        },
                    )
                    counts["st_status_daily"] += 1
                if trade_status == "0":
                    _write_line(
                        handles["suspension"],
                        {
                            "ts_code": ts_code,
                            "trade_date": trade_date,
                            "suspend_type": "S",
                            "suspend_timing": "",
                            "source_request_id": request.request_id,
                            "source_payload_sha256": source_hash,
                            "suspend_timing_known": False,
                        },
                    )
                    counts["suspensions"] += 1
            params = parse_qs(urlsplit(request.url).query)
            coverage = {
                "ts_code": ts_code,
                "start_date": str(params["start"][-1]).replace("-", ""),
                "end_date": str(params["end"][-1]).replace("-", ""),
                "success": receipt.get("terminal_state") in {"positive", "empty"},
                "transport_validated": (
                    not population_empty
                    and not any(
                        check is not True
                        for check in (receipt.get("checks") or {}).values()
                    )
                ),
                "returned_count": len(items),
                "source_request_id": request.request_id,
                "source_payload_sha256": source_hash,
            }
            _write_line(handles["st_coverage"], coverage)
            _write_line(handles["suspension_coverage"], coverage)
            counts["st_status_coverage"] += 1
            counts["suspension_coverage"] += 1
    finally:
        for handle in handles.values():
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
    artifacts = [
        NormalizedArtifact("st_status_daily", "normalized/st_status_daily.jsonl", counts["st_status_daily"]),
        NormalizedArtifact("suspensions", "normalized/suspensions.jsonl", counts["suspensions"]),
        NormalizedArtifact("provider_state_daily", "normalized/provider_state_daily.jsonl", counts["provider_state_daily"]),
        NormalizedArtifact("st_status_coverage", "normalized/st_status_coverage.jsonl", counts["st_status_coverage"]),
        NormalizedArtifact("suspension_coverage", "normalized/suspension_coverage.jsonl", counts["suspension_coverage"]),
        NormalizedArtifact("trade_calendar", "normalized/trade_calendar.jsonl", counts["trade_calendar"]),
        NormalizedArtifact("conflicts", "normalized/conflicts.jsonl", counts["conflicts"]),
    ]
    semantic = {
        "schema_version": "baostock_state_normalization_v1",
        "normalizer": "raw_wire_replay_to_daily_controls_v1",
        "artifacts": [
            {
                **asdict(artifact),
                "sha256": sha256_file(run_root / artifact.relative_path),
                "size_bytes": (run_root / artifact.relative_path).stat().st_size,
            }
            for artifact in artifacts
        ],
        "admission_ready": False,
        "blockers": [
            "st_subtype_unknown",
            "suspension_timing_unknown",
            "coverage_use_projection_not_yet_verified",
            "human_profile_activation_not_bound",
        ],
    }
    semantic["content_hash"] = canonical_hash(semantic)
    atomic_json(run_root / NORMALIZED_MANIFEST_NAME, semantic)
    return [
        *artifacts,
        NormalizedArtifact(
            "normalized_manifest",
            NORMALIZED_MANIFEST_NAME,
            1,
        ),
    ]


def _validate_contract_and_plan(
    contract: FreeProviderBackfillContract,
    requests: Sequence[ProviderProbeRequest],
    *,
    signer: CaptureSigner,
) -> list[dict[str, Any]]:
    semantic = contract.semantic()
    if not contract.activity_name.strip() or not contract.permission_context_id.strip():
        raise ValueError("free_provider_backfill_contract_identity_missing")
    if contract.provider not in {"baostock", "cninfo", "csindex"}:
        raise ValueError("free_provider_backfill_provider_invalid")
    for value in (
        contract.scope_start,
        contract.scope_end,
        contract.request_start,
        contract.request_end,
    ):
        if not _valid_date(value):
            raise ValueError("free_provider_backfill_scope_invalid")
    if not (
        contract.request_start
        <= contract.scope_start
        <= contract.scope_end
        <= contract.request_end
    ):
        raise ValueError("free_provider_backfill_scope_geometry_invalid")
    if not _HEX_64.fullmatch(contract.population_root):
        raise ValueError("free_provider_backfill_population_root_invalid")
    if (
        contract.capture_public_key_sha256 != _public_key_hash(signer.public_key_pem)
        or base64.b64decode(contract.capture_public_key_pem_b64, validate=True)
        != signer.public_key_pem
    ):
        raise ValueError("free_provider_backfill_capture_key_mismatch")
    budget = contract.budget
    if (
        budget.max_requests < len(requests)
        or budget.max_requests <= 0
        or budget.max_wire_exchanges < len(requests)
        or budget.max_response_bytes <= 0
        or budget.max_total_response_bytes < budget.max_response_bytes
        or not 0 < budget.timeout_seconds <= 120
        or not 0 <= budget.minimum_delay_seconds <= 60
        or not 0 <= budget.max_retries <= 6
    ):
        raise ValueError("free_provider_backfill_budget_invalid")
    if semantic.get("safety") != {name: False for name in SAFETY_FLAGS}:
        raise ValueError("free_provider_backfill_safety_contract_invalid")
    allowed_hosts = {host.lower().rstrip(".") for host in contract.allowed_hosts}
    if not allowed_hosts or any(not host for host in allowed_hosts):
        raise ValueError("free_provider_backfill_host_allowlist_invalid")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for request in requests:
        parsed = urlsplit(request.url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if (
            not request.request_id
            or request.request_id in seen
            or request.provider != contract.provider
            or parsed.scheme not in {"https", "baostock"}
            or host not in allowed_hosts
            or request.method.upper() not in {"GET", "POST", "BAOSTOCK"}
        ):
            raise ValueError(
                f"free_provider_backfill_request_invalid:{request.request_id or 'missing'}"
            )
        if any(
            token in header.lower().replace("_", "-")
            for header in request.headers
            for token in ("authorization", "cookie", "password", "secret", "token", "api-key")
        ):
            raise ValueError(
                f"free_provider_backfill_sensitive_header_forbidden:{request.request_id}"
            )
        if not set(request.expected_terminal_states) <= TERMINAL_STATES:
            raise ValueError(
                f"free_provider_backfill_terminal_contract_invalid:{request.request_id}"
            )
        seen.add(request.request_id)
        rows.append(request.semantic())
    if not rows:
        raise ValueError("free_provider_backfill_plan_empty")
    return rows


def _safe_output_root(value: str | Path) -> Path:
    lexical = Path(value)
    absolute = lexical if lexical.is_absolute() else Path.cwd() / lexical
    if any(path.is_symlink() for path in (absolute, *absolute.parents)):
        raise ValueError("free_provider_backfill_output_symlink_forbidden")
    resolved = absolute.resolve()
    protected_lake = DEFAULT_LAKE_ROOT.resolve()
    staging_root = (DEFAULT_LAKE_ROOT / "staging").resolve()
    if resolved == protected_lake or protected_lake in resolved.parents:
        if resolved == staging_root or staging_root not in resolved.parents:
            raise ValueError("free_provider_backfill_protected_lake_write_forbidden")
    return resolved


def _output_from_activity_root(run_root: Path) -> Path:
    parent_name = run_root.parent.name
    suffix = ".activities"
    if not parent_name.startswith(".") or not parent_name.endswith(suffix):
        raise ValueError("free_provider_backfill_resume_root_geometry_invalid")
    output_name = parent_name[1 : -len(suffix)]
    if not output_name:
        raise ValueError("free_provider_backfill_resume_root_geometry_invalid")
    return run_root.parent.parent / output_name


def _global_activity_lock_root(output: Path) -> Path:
    staging = (DEFAULT_LAKE_ROOT / "staging").resolve()
    resolved = output.resolve()
    if resolved == staging or staging in resolved.parents:
        return DEFAULT_LAKE_ROOT / "governance/free_provider_activity_locks"
    return output.parent / ".free_provider_activity_locks"


def _host_breaker_root(output: Path) -> Path:
    staging = (DEFAULT_LAKE_ROOT / "staging").resolve()
    resolved = output.resolve()
    if resolved == staging or staging in resolved.parents:
        return DEFAULT_LAKE_ROOT / "governance/provider_circuit_breakers"
    return output.parent / ".provider_circuit_breakers"


def _host_breaker_path(root: Path, *, provider: str, host: str) -> Path:
    identity = canonical_hash({"provider": provider, "host": host.lower()})
    return root / f"breaker_{identity}.json"


def _validate_host_breaker(
    path: Path,
    *,
    provider: str,
    host: str,
    expected_public_key: bytes,
) -> dict[str, Any]:
    payload = read_json(path)
    signed = {
        key: value
        for key, value in payload.items()
        if key not in {"content_hash", "signature"}
    }
    if (
        payload.get("schema_version") != HOST_BREAKER_SCHEMA
        or payload.get("provider") != provider
        or payload.get("host") != host.lower()
        or payload.get("capture_public_key_sha256")
        != _public_key_hash(expected_public_key)
        or payload.get("content_hash")
        != canonical_hash(signed | {"signature": payload.get("signature")})
        or payload.get("clearance_authorized") is not False
    ):
        raise ValueError("free_provider_host_circuit_breaker_invalid")
    try:
        verify_signature(
            public_key_pem=expected_public_key,
            payload=_canonical_bytes(signed),
            signature_b64=str(payload.get("signature") or ""),
        )
    except ReceiptSigningError as exc:
        raise ValueError("free_provider_host_circuit_breaker_invalid") from exc
    return payload


def _assert_provider_host_breaker_closed(
    output: Path,
    *,
    provider: str,
    host: str,
    expected_public_key: bytes,
) -> None:
    root = _host_breaker_root(output)
    path = _host_breaker_path(root, provider=provider, host=host)
    if not path.is_file():
        return
    payload = _validate_host_breaker(
        path,
        provider=provider,
        host=host,
        expected_public_key=expected_public_key,
    )
    raise ProviderBackfillPaused(
        "free_provider_backfill_paused:provider_host_circuit_breaker_already_open:"
        f"{payload['content_hash']}"
    )


def _open_provider_host_breaker(
    output: Path,
    *,
    provider: str,
    host: str,
    activity_id: str,
    contract_id: str,
    terminal: Mapping[str, Any],
    signer: CaptureSigner,
) -> dict[str, Any]:
    root = _host_breaker_root(output)
    identity = canonical_hash({"provider": provider, "host": host.lower()})
    path = _host_breaker_path(root, provider=provider, host=host)
    with _activity_lock(root, f"host-{identity}"):
        if path.is_file():
            return _validate_host_breaker(
                path,
                provider=provider,
                host=host,
                expected_public_key=signer.public_key_pem,
            )
        signed = {
            "schema_version": HOST_BREAKER_SCHEMA,
            "provider": provider,
            "host": host.lower(),
            "activity_id": activity_id,
            "contract_id": contract_id,
            "request_id": terminal.get("request_id"),
            "attempt_id": terminal.get("attempt_id"),
            "terminal_event_hash": terminal.get("event_hash"),
            "status_code": terminal.get("status_code"),
            "error_code": terminal.get("error_code"),
            "waf_html_observed": bool(
                (terminal.get("diagnostics") or {}).get("waf_html_observed")
            ),
            "opened_at": _utc_now(),
            "capture_public_key_sha256": _public_key_hash(signer.public_key_pem),
            "clearance_authorized": False,
        }
        signature = signer.sign(_canonical_bytes(signed))
        payload = signed | {"signature": signature}
        payload["content_hash"] = canonical_hash(payload)
        atomic_json(path, payload)
        _fsync_directory(root)
        return payload


@contextmanager
def _activity_lock(parent: Path, activity_id: str) -> Iterator[None]:
    parent.mkdir(parents=True, exist_ok=True)
    lock_root = parent / ".locks"
    lock_root.mkdir(exist_ok=True)
    with (lock_root / f"{activity_id}.lock").open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_or_verify_json(path: Path, payload: Mapping[str, Any]) -> None:
    if path.exists():
        if read_json(path) != dict(payload):
            raise ValueError(f"free_provider_backfill_resume_identity_mismatch:{path.name}")
        return
    atomic_json(path, payload)
    _fsync_directory(path.parent)


def _recover_journal(
    run_root: Path,
    *,
    activity_id: str,
    contract_id: str,
    request_rows: Sequence[Mapping[str, Any]],
    signer: CaptureSigner,
) -> dict[str, dict[str, Any]]:
    journal_path = run_root / JOURNAL_NAME
    _repair_trailing_journal_fragment(
        journal_path,
        activity_id=activity_id,
        contract_id=contract_id,
        request_plan_hash=canonical_hash([dict(row) for row in request_rows]),
        signer=signer,
    )
    events = _read_and_validate_journal(
        journal_path,
        expected_activity_id=activity_id,
        expected_contract_id=contract_id,
        public_key=signer.public_key_pem,
        request_rows=request_rows,
        request_plan_hash=canonical_hash([dict(row) for row in request_rows]),
    )
    started: dict[str, dict[str, Any]] = {}
    terminal_attempts: set[str] = set()
    latest: dict[str, dict[str, Any]] = {}
    request_by_id = {str(row["request_id"]): dict(row) for row in request_rows}
    for event in events:
        attempt_id = str(event.get("attempt_id") or "")
        if event.get("event_type") == "capture_attempt_started":
            started[attempt_id] = event
        elif event.get("event_type") == "capture_attempt_terminal":
            terminal_attempts.add(attempt_id)
            latest[str(event["request_id"])] = event
    for attempt_id, start in started.items():
        if attempt_id in terminal_attempts:
            continue
        request_id = str(start["request_id"])
        request_row = request_by_id.get(request_id)
        if request_row is None:
            raise ValueError("free_provider_backfill_recovery_request_missing")
        relative = _raw_relative_path(attempt_id)
        raw_path = run_root / relative
        if raw_path.is_file():
            wrapper = read_json(raw_path)
        else:
            request = _request_from_semantic(request_row)
            relative, wrapper = _write_raw_wrapper(
                run_root,
                request=request,
                request_semantic_hash=str(start["request_semantic_hash"]),
                attempt_id=attempt_id,
                retry_ordinal=int(start["retry_ordinal"]),
                capture_started_at=str(start["capture_started_at"]),
                observation=ProviderProbeObservation(
                    terminal_state="error",
                    raw_payload=b'{"reason":"ambiguous_transport_after_interruption"}',
                    row_count=None,
                    error_code="ambiguous_transport",
                    checks={"transport_completed": False},
                    transport_exchange_count=0,
                ),
            )
        request = _request_from_semantic(request_row)
        event = _append_signed_event(
            journal_path,
            _terminal_event(
                activity_id=activity_id,
                contract_id=contract_id,
                request_plan_hash=str(start["request_plan_hash"]),
                request=request,
                request_row=request_row,
                attempt_id=attempt_id,
                retry_ordinal=int(start["retry_ordinal"]),
                wrapper_path=relative,
                wrapper=wrapper,
                raw_envelope_sha256=sha256_file(run_root / relative),
                recovered_after_interruption=True,
            ),
            signer=signer,
        )
        latest[request_id] = event
    return latest


def _append_signed_event(
    path: Path,
    event: Mapping[str, Any],
    *,
    signer: CaptureSigner,
) -> dict[str, Any]:
    existed = path.exists()
    previous = _last_jsonl_row(path)
    unsigned = dict(event) | {
        "sequence": int(previous.get("sequence", 0)) + 1,
        "previous_event_hash": str(previous.get("event_hash") or ""),
        "capture_public_key_pem_b64": base64.b64encode(
            signer.public_key_pem
        ).decode("ascii"),
        "capture_public_key_sha256": _public_key_hash(signer.public_key_pem),
    }
    signature = signer.sign(_canonical_bytes(unsigned))
    signed = unsigned | {"signature": signature}
    row = signed | {"event_hash": canonical_hash(signed)}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(_json_bytes(row) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    if not existed:
        _fsync_directory(path.parent)
    return row


def _repair_trailing_journal_fragment(
    path: Path,
    *,
    activity_id: str,
    contract_id: str,
    request_plan_hash: str,
    signer: CaptureSigner,
) -> None:
    """Repair only a torn final JSONL write and preserve its exact bytes."""

    if not path.is_file() or path.stat().st_size == 0:
        return
    size = path.stat().st_size
    window_size = min(size, 1024 * 1024 + 1)
    with path.open("rb") as handle:
        handle.seek(size - 1)
        if handle.read(1) == b"\n":
            return
        handle.seek(size - window_size)
        tail = handle.read(window_size)
    relative_boundary = tail.rfind(b"\n") + 1
    if relative_boundary == 0 and size > 1024 * 1024:
        raise ValueError("free_provider_backfill_journal_tail_too_large")
    boundary = size - window_size + relative_boundary
    fragment = tail[relative_boundary:]
    if len(fragment) > 1024 * 1024:
        raise ValueError("free_provider_backfill_journal_tail_too_large")
    try:
        complete = json.loads(fragment)
    except (UnicodeDecodeError, json.JSONDecodeError):
        complete = None
    if isinstance(complete, dict):
        with path.open("ab") as handle:
            handle.write(b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        return
    with path.open("r+b") as handle:
        handle.truncate(boundary)
        handle.flush()
        os.fsync(handle.fileno())
    _append_signed_event(
        path,
        {
            "schema_version": JOURNAL_EVENT_SCHEMA,
            "event_type": "journal_tail_recovered",
            "event_id": f"journal_tail_recovered:{hashlib.sha256(fragment).hexdigest()}",
            "activity_id": activity_id,
            "contract_id": contract_id,
            "request_plan_hash": request_plan_hash,
            "discarded_tail_base64": base64.b64encode(fragment).decode("ascii"),
            "discarded_tail_sha256": hashlib.sha256(fragment).hexdigest(),
            "discarded_tail_size_bytes": len(fragment),
            "capture_engine_root": _capture_engine_root(),
            "occurred_at": _utc_now(),
        },
        signer=signer,
    )


def _read_and_validate_journal(
    path: Path,
    *,
    expected_activity_id: str,
    expected_contract_id: str,
    public_key: bytes,
    request_rows: Sequence[Mapping[str, Any]] | None = None,
    request_plan_hash: str | None = None,
    max_retries: int | None = None,
    allow_legacy_engine_migration: bool = False,
) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events = _read_jsonl(path)
    previous_hash = ""
    request_map = {
        str(row["request_id"]): dict(row) for row in (request_rows or ())
    }
    attempts: dict[str, dict[str, Any]] = {}
    ordinals_by_request: dict[str, list[int]] = {}
    terminal_by_attempt: dict[str, dict[str, Any]] = {}
    resume_pause_hashes: set[str] = set()
    resume_authorization_ids: set[str] = set()
    resume_authorization_by_attempt: dict[str, dict[str, Any]] = {}
    active_engine_root: str | None = None
    for ordinal, event in enumerate(events, start=1):
        event_type = str(event.get("event_type") or "")
        attempt_id = str(event.get("attempt_id") or "")
        signed = {
            key: value
            for key, value in event.items()
            if key not in {"signature", "event_hash"}
        }
        if (
            event.get("schema_version") != JOURNAL_EVENT_SCHEMA
            or event.get("sequence") != ordinal
            or event.get("previous_event_hash") != previous_hash
            or event.get("event_hash")
            != canonical_hash(signed | {"signature": event.get("signature")})
            or event.get("activity_id") != expected_activity_id
            or event.get("contract_id") != expected_contract_id
            or event.get("capture_public_key_sha256") != _public_key_hash(public_key)
            or base64.b64decode(
                str(event.get("capture_public_key_pem_b64") or ""), validate=True
            )
            != public_key
        ):
            raise ValueError("free_provider_backfill_journal_chain_invalid")
        try:
            verify_signature(
                public_key_pem=public_key,
                payload=_canonical_bytes(signed),
                signature_b64=str(event.get("signature") or ""),
            )
        except ReceiptSigningError as exc:
            raise ValueError("free_provider_backfill_journal_signature_invalid") from exc
        if event_type == "capture_engine_migration":
            if (
                not allow_legacy_engine_migration
                or not _HEX_64.fullmatch(str(event.get("from_engine_root") or ""))
                or not _HEX_64.fullmatch(str(event.get("to_engine_root") or ""))
                or event.get("to_engine_root") == event.get("from_engine_root")
                or event.get("request_plan_hash") != request_plan_hash
                or event.get("provider_request_semantics_changed") is not False
                or event.get("automatic_research_authorization_changed") is not False
                or event.get("reason")
                != "operator_applied_durability_or_performance_fix"
            ):
                raise ValueError("free_provider_backfill_engine_migration_invalid")
            active_engine_root = str(event["to_engine_root"])
            previous_hash = str(event["event_hash"])
            continue
        event_engine_root = str(event.get("capture_engine_root") or "")
        if not _HEX_64.fullmatch(event_engine_root):
            if not allow_legacy_engine_migration or event_engine_root:
                raise ValueError("free_provider_backfill_engine_lineage_invalid")
            previous_hash = str(event["event_hash"])
            # Legacy v1 events did not bind an engine root. They remain usable
            # only as untrusted staging evidence, never as signed publication.
            if event_type not in {
                "capture_attempt_started",
                "capture_attempt_terminal",
            }:
                raise ValueError("free_provider_backfill_engine_lineage_invalid")
        elif active_engine_root is None:
            active_engine_root = event_engine_root
        elif event_engine_root != active_engine_root:
            if not allow_legacy_engine_migration:
                raise ValueError("free_provider_backfill_engine_lineage_invalid")
            active_engine_root = event_engine_root
        if event_type == "journal_tail_recovered":
            try:
                discarded = base64.b64decode(
                    str(event.get("discarded_tail_base64") or ""), validate=True
                )
            except (ValueError, TypeError) as exc:
                raise ValueError("free_provider_backfill_journal_tail_recovery_invalid") from exc
            if (
                event.get("request_plan_hash") != request_plan_hash
                or event.get("discarded_tail_sha256")
                != hashlib.sha256(discarded).hexdigest()
                or event.get("discarded_tail_size_bytes") != len(discarded)
                or len(discarded) > 1024 * 1024
                or not discarded
            ):
                raise ValueError("free_provider_backfill_journal_tail_recovery_invalid")
            previous_hash = str(event["event_hash"])
            continue
        request_id = str(event.get("request_id") or "")
        retry_ordinal = event.get("retry_ordinal")
        request_row = request_map.get(request_id) if request_map else None
        if request_map and (
            request_row is None
            or event.get("request_plan_hash") != request_plan_hash
            or event.get("request_semantic_hash") != canonical_hash(request_row)
            or not isinstance(retry_ordinal, int)
            or isinstance(retry_ordinal, bool)
            or retry_ordinal < 0
            or (max_retries is not None and retry_ordinal > max_retries)
            or attempt_id != f"{request_id}:{retry_ordinal}"
        ):
            raise ValueError("free_provider_backfill_journal_plan_binding_invalid")
        if event_type == "pause_resume_authorized":
            terminal_event = terminal_by_attempt.get(attempt_id)
            pause_hash = str(event.get("pause_content_hash") or "")
            authorization_id = str(event.get("authorization_id") or "")
            cooldown = event.get("cooldown_seconds")
            if (
                terminal_event is None
                or terminal_event.get("terminal_state") != "error"
                or not _manual_resume_required(
                    terminal_event,
                    provider=str((request_row or {}).get("provider") or ""),
                )
                or terminal_event.get("request_id") != request_id
                or event.get("authorized_next_retry_ordinal") != int(retry_ordinal) + 1
                or (
                    max_retries is not None
                    and int(event.get("authorized_next_retry_ordinal") or -1)
                    > max_retries
                )
                or not _HEX_64.fullmatch(pause_hash)
                or not authorization_id
                or len(authorization_id) > 200
                or pause_hash in resume_pause_hashes
                or authorization_id in resume_authorization_ids
                or not str(event.get("pause_relative_path") or "").startswith(
                    "pauses/pause_"
                )
                or not _HEX_64.fullmatch(
                    str(event.get("pause_artifact_sha256") or "")
                )
                or isinstance(cooldown, bool)
                or not isinstance(cooldown, int)
                or not 0 <= cooldown <= 7 * 24 * 60 * 60
                or _parse_utc(str(event.get("authorized_at") or ""))
                < _parse_utc(str(event.get("not_before") or ""))
            ):
                raise ValueError(
                    "free_provider_backfill_pause_resume_authorization_invalid"
                )
            resume_pause_hashes.add(pause_hash)
            resume_authorization_ids.add(authorization_id)
            resume_authorization_by_attempt[attempt_id] = event
            previous_hash = str(event["event_hash"])
            continue
        if event_type == "capture_attempt_started":
            prior_attempt = (
                terminal_by_attempt.get(f"{request_id}:{int(retry_ordinal) - 1}")
                if int(retry_ordinal) > 0
                else None
            )
            resume_authorization = (
                resume_authorization_by_attempt.get(
                    str(prior_attempt.get("attempt_id") or "")
                )
                if prior_attempt is not None
                else None
            )
            if (
                prior_attempt is not None
                and _manual_resume_required(
                    prior_attempt,
                    provider=str((request_row or {}).get("provider") or ""),
                )
                and resume_authorization is None
            ):
                raise ValueError(
                    "free_provider_backfill_manual_resume_authorization_missing"
                )
            if resume_authorization is not None and (
                int(resume_authorization["authorized_next_retry_ordinal"])
                != int(retry_ordinal)
                or _parse_utc(str(event.get("capture_started_at") or ""))
                < _parse_utc(str(resume_authorization.get("authorized_at") or ""))
                or _parse_utc(str(event.get("capture_started_at") or ""))
                < _parse_utc(str(resume_authorization.get("not_before") or ""))
            ):
                raise ValueError(
                    "free_provider_backfill_pause_resume_authorization_invalid"
                )
            if attempt_id in attempts or (
                request_row is not None and event.get("request") != request_row
            ):
                raise ValueError("free_provider_backfill_attempt_duplicate")
            attempts[attempt_id] = event
            ordinals_by_request.setdefault(request_id, []).append(int(retry_ordinal))
        elif event_type == "capture_attempt_terminal":
            start = attempts.get(attempt_id)
            if start is None or attempt_id in terminal_by_attempt or any(
                event.get(key) != start.get(key)
                for key in (
                    "activity_id",
                    "contract_id",
                    "request_plan_hash",
                    "request_id",
                    "request_semantic_hash",
                    "retry_ordinal",
                    "capture_started_at",
                )
            ):
                raise ValueError("free_provider_backfill_terminal_without_start")
            terminal_by_attempt[attempt_id] = event
            if request_row is not None:
                checks = {
                    str(key): bool(value)
                    for key, value in (event.get("checks") or {}).items()
                }
                expected = (
                    event.get("terminal_state")
                    in set(request_row.get("expected_terminal_states") or ())
                    and all(checks.values())
                    and all(
                        checks.get(str(name)) is True
                        for name in request_row.get("required_checks") or ()
                    )
                )
                if event.get("expectation_met") is not expected:
                    raise ValueError(
                        "free_provider_backfill_terminal_expectation_invalid"
                    )
        else:
            raise ValueError("free_provider_backfill_journal_event_type_invalid")
        previous_hash = str(event["event_hash"])
    for request_id, values in ordinals_by_request.items():
        if sorted(values) != list(range(max(values) + 1)):
            raise ValueError("free_provider_backfill_retry_lineage_invalid")
        successes = [
            int(event["retry_ordinal"])
            for event in terminal_by_attempt.values()
            if event.get("request_id") == request_id
            and event.get("terminal_state") in {"positive", "empty"}
        ]
        if successes and max(values) != min(successes):
            raise ValueError("free_provider_backfill_retry_after_success_invalid")
    return events


def _last_jsonl_row(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size == 0:
        return {}
    with path.open("rb") as handle:
        position = handle.seek(0, os.SEEK_END)
        buffer = bytearray()
        while position > 0:
            position -= 1
            handle.seek(position)
            value = handle.read(1)
            if value == b"\n" and buffer:
                break
            if value != b"\n":
                buffer.extend(value)
        return json.loads(bytes(reversed(buffer)))


def _write_raw_wrapper(
    run_root: Path,
    *,
    request: ProviderProbeRequest,
    request_semantic_hash: str,
    attempt_id: str,
    retry_ordinal: int,
    capture_started_at: str,
    observation: ProviderProbeObservation,
) -> tuple[str, dict[str, Any]]:
    if observation.terminal_state not in TERMINAL_STATES:
        raise ValueError("free_provider_backfill_terminal_state_invalid")
    raw = bytes(observation.raw_payload)
    wrapper = {
        "schema_version": RAW_ENVELOPE_SCHEMA,
        "attempt_id": attempt_id,
        "request_id": request.request_id,
        "request_semantic_hash": request_semantic_hash,
        "retry_ordinal": retry_ordinal,
        "capture_started_at": capture_started_at,
        "capture_completed_at": _utc_now(),
        "terminal_state": observation.terminal_state,
        "row_count": observation.row_count,
        "status_code": observation.status_code,
        "error_code": observation.error_code,
        "diagnostics": _json_value(observation.diagnostics),
        "checks": {str(key): bool(value) for key, value in observation.checks.items()},
        "transport_exchange_count": observation.transport_exchange_count,
        "raw_payload_base64": base64.b64encode(raw).decode("ascii"),
        "raw_payload_sha256": hashlib.sha256(raw).hexdigest(),
        "raw_payload_size_bytes": len(raw),
    }
    relative = _raw_relative_path(attempt_id)
    target = run_root / relative
    if target.exists():
        if read_json(target) != wrapper:
            raise ValueError("free_provider_backfill_raw_capture_collision")
    else:
        atomic_json(target, wrapper)
        _fsync_directory(target.parent)
    return relative, wrapper


def _terminal_event(
    *,
    activity_id: str,
    contract_id: str,
    request_plan_hash: str,
    request: ProviderProbeRequest,
    request_row: Mapping[str, Any],
    attempt_id: str,
    retry_ordinal: int,
    wrapper_path: str,
    wrapper: Mapping[str, Any],
    raw_envelope_sha256: str,
    recovered_after_interruption: bool = False,
) -> dict[str, Any]:
    checks = {str(key): bool(value) for key, value in (wrapper.get("checks") or {}).items()}
    terminal_state = str(wrapper.get("terminal_state") or "")
    expectation_met = (
        terminal_state in request.expected_terminal_states
        and all(checks.values())
        and all(checks.get(name) is True for name in request.required_checks)
    )
    return {
        "schema_version": JOURNAL_EVENT_SCHEMA,
        "event_type": "capture_attempt_terminal",
        "event_id": f"capture_attempt_terminal:{attempt_id}",
        "activity_id": activity_id,
        "contract_id": contract_id,
        "request_plan_hash": request_plan_hash,
        "attempt_id": attempt_id,
        "request_id": request.request_id,
        "request_semantic_hash": canonical_hash(dict(request_row)),
        "retry_ordinal": retry_ordinal,
        "capture_started_at": wrapper.get("capture_started_at"),
        "capture_completed_at": wrapper.get("capture_completed_at"),
        "capture_engine_root": _capture_engine_root(),
        "terminal_state": terminal_state,
        "row_count": wrapper.get("row_count"),
        "status_code": wrapper.get("status_code"),
        "error_code": wrapper.get("error_code"),
        "diagnostics": wrapper.get("diagnostics") or {},
        "checks": checks,
        "expectation_met": expectation_met,
        "transport_exchange_count": wrapper.get("transport_exchange_count"),
        "raw_envelope_relative_path": wrapper_path,
        "raw_envelope_sha256": raw_envelope_sha256,
        "raw_payload_sha256": wrapper.get("raw_payload_sha256"),
        "raw_payload_size_bytes": wrapper.get("raw_payload_size_bytes"),
        "recovered_after_interruption": recovered_after_interruption,
    }


def _restore_transport(
    transport: BackfillTransport,
    requests: Sequence[ProviderProbeRequest],
    terminal: Mapping[str, Mapping[str, Any]],
) -> None:
    restore = getattr(transport, "restore", None)
    if restore is None:
        return
    for request in requests:
        record = terminal.get(request.request_id)
        if record is not None and record.get("terminal_state") in {"positive", "empty"}:
            restore(request, record)


def _capture_usage(run_root: Path) -> dict[str, int]:
    terminal_events = [
        row
        for row in _read_jsonl(run_root / JOURNAL_NAME)
        if row.get("event_type") == "capture_attempt_terminal"
    ]
    paths: list[Path] = []
    exchanges = 0
    for event in terminal_events:
        path = _confined_file(
            run_root, str(event.get("raw_envelope_relative_path") or "")
        )
        if path is None:
            raise ValueError("free_provider_backfill_raw_usage_invalid")
        paths.append(path)
        exchanges += int(event.get("transport_exchange_count") or 0)
    if len({path.resolve() for path in paths}) != len(paths):
        raise ValueError("free_provider_backfill_raw_usage_duplicate")
    return {
        "attempt_count": len(terminal_events),
        "response_bytes": sum(path.stat().st_size for path in paths),
        "wire_exchange_count": exchanges,
    }


def _budget_exceeded_reason(
    budget: BackfillResourceBudget,
    usage: Mapping[str, int],
    *,
    before_request: bool,
) -> str | None:
    request_limit = budget.max_requests - (1 if before_request else 0)
    if int(usage.get("attempt_count") or 0) > request_limit:
        return "request_budget_exhausted"
    if int(usage.get("response_bytes") or 0) > budget.max_total_response_bytes:
        return "total_response_byte_budget_exhausted"
    if int(usage.get("wire_exchange_count") or 0) > budget.max_wire_exchanges:
        return "wire_exchange_budget_exhausted"
    return None


def _retryable(error_code: str | None, *, provider: str) -> bool:
    value = str(error_code or "")
    return (
        value == "ambiguous_transport"
        or value.startswith(RETRYABLE_ERROR_PREFIXES)
        or (
            provider == "baostock"
            and value.startswith(BAOSTOCK_RETRYABLE_ERROR_PREFIXES)
        )
    )


def _manual_resume_required(
    terminal: Mapping[str, Any],
    *,
    provider: str,
) -> bool:
    return terminal.get("terminal_state") == "error" and (
        not _retryable(
            str(terminal.get("error_code") or ""),
            provider=provider,
        )
        or terminal.get("status_code") in {403, 429}
        or bool((terminal.get("diagnostics") or {}).get("waf_html_observed"))
    )


def _authorize_paused_retry(
    run_root: Path,
    *,
    activity_id: str,
    contract_id: str,
    request_plan_hash: str,
    current: Mapping[str, Any],
    authorization: PauseResumeAuthorization | None,
    max_retries: int,
    signer: CaptureSigner,
) -> bool:
    if authorization is None:
        return False
    raise ValueError(
        "free_provider_backfill_trusted_resume_authority_not_implemented"
    )


def _pause_activity(
    run_root: Path,
    *,
    request_id: str,
    reason: str,
    terminal: Mapping[str, Any],
    usage: Mapping[str, int],
) -> None:
    payload = {
        "schema_version": "free_provider_backfill_pause_v1",
        "reason": reason,
        "request_id": request_id,
        "attempt_id": terminal.get("attempt_id"),
        "terminal_state": terminal.get("terminal_state"),
        "error_code": terminal.get("error_code"),
        "status_code": terminal.get("status_code"),
        "usage": dict(usage),
        "paused_at": _utc_now(),
        "automatic_resume_authorized": False,
    }
    payload["content_hash"] = canonical_hash(payload)
    pause_root = run_root / "pauses"
    pause_root.mkdir(exist_ok=True)
    pause_path = pause_root / f"pause_{payload['content_hash'][:24]}.json"
    atomic_json(pause_path, payload)
    _fsync_directory(pause_root)
    _fsync_directory(run_root)
    raise ProviderBackfillPaused(
        f"free_provider_backfill_paused:{reason}:{request_id}"
    )


def _capture_engine_root() -> str:
    global _CAPTURE_ENGINE_ROOT_CACHE
    if _CAPTURE_ENGINE_ROOT_CACHE is None:
        _CAPTURE_ENGINE_ROOT_CACHE = canonical_hash(
            {
                "capture_engine_module_sha256": sha256_file(Path(__file__)),
                "capture_engine_entrypoint": f"{run_free_provider_backfill.__module__}.run_free_provider_backfill",
                "capture_validator_entrypoint": f"{validate_free_provider_backfill.__module__}.validate_free_provider_backfill",
            }
        )
    return _CAPTURE_ENGINE_ROOT_CACHE


def _publish_capture_generation(
    *,
    output: Path,
    run_root: Path,
    activity_id: str,
    contract_id: str,
    request_plan_hash: str,
    terminal: Mapping[str, Mapping[str, Any]],
    artifacts: Sequence[NormalizedArtifact],
    usage: Mapping[str, int],
    signer: CaptureSigner,
) -> dict[str, Any]:
    journal_events = _read_jsonl(run_root / JOURNAL_NAME)
    terminal_events = [
        row
        for row in journal_events
        if row.get("event_type") == "capture_attempt_terminal"
    ]
    terminal_events_by_path = {
        str(row["raw_envelope_relative_path"]): row for row in terminal_events
    }
    if len(terminal_events_by_path) != len(terminal_events):
        raise ValueError("free_provider_backfill_terminal_raw_path_duplicate")
    catalog = [
        {
            "attempt_id": event.get("attempt_id"),
            "request_id": event.get("request_id"),
            "relative_path": relative_path,
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for relative_path, event in sorted(terminal_events_by_path.items())
        for path in [run_root / relative_path]
    ]
    _atomic_jsonl(run_root / CATALOG_NAME, catalog)
    normalized_rows = [
        {
            **asdict(artifact),
            "sha256": sha256_file(run_root / artifact.relative_path),
            "size_bytes": (run_root / artifact.relative_path).stat().st_size,
        }
        for artifact in artifacts
    ]
    pause_rows = [
        {
            "relative_path": path.relative_to(run_root).as_posix(),
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted((run_root / "pauses").glob("*.json"))
    ] if (run_root / "pauses").is_dir() else []
    terminal_counts = {state: 0 for state in sorted(TERMINAL_STATES)}
    for row in terminal.values():
        terminal_counts[str(row["terminal_state"])] += 1
    status = (
        "succeeded"
        if all(
            row.get("terminal_state") in {"positive", "empty"}
            and row.get("expectation_met") is True
            for row in terminal.values()
        )
        else "blocked"
    )
    semantic = {
        "schema_version": SCHEMA_VERSION,
        "mode": "signed_raw_provider_capture",
        "status": status,
        "activity_id": activity_id,
        "contract_id": contract_id,
        "request_plan_hash": request_plan_hash,
        "request_count": len(terminal),
        "terminal_attempt_count": len(terminal_events),
        "terminal_counts": terminal_counts,
        "capture_catalog_sha256": sha256_file(run_root / CATALOG_NAME),
        "capture_catalog_count": len(catalog),
        "capture_journal_sha256": sha256_file(run_root / JOURNAL_NAME),
        "capture_journal_event_count": len(journal_events),
        "resource_usage": dict(usage),
        "normalized_artifacts": normalized_rows,
        "pause_artifacts": pause_rows,
        "raw_capture_replay_eligible": True,
        "old_lake_mutated": False,
        "safety": {name: False for name in SAFETY_FLAGS},
    }
    content_hash = canonical_hash(semantic)
    generation_id = f"{GENERATION_PREFIX}_{content_hash[:24]}"
    publication_signature = signer.sign(
        _canonical_bytes(
            semantic
            | {"content_hash": content_hash, "generation_id": generation_id}
        )
    )
    atomic_json(
        run_root / MANIFEST_NAME,
        semantic
        | {
            "content_hash": content_hash,
            "generation_id": generation_id,
            "capture_publication_signature": publication_signature,
        },
    )
    prepared = run_root.parent / generation_id
    if prepared.exists():
        raise ValueError("free_provider_backfill_prepared_generation_collision")
    os.replace(run_root, prepared)
    _fsync_directory(prepared.parent)
    return publish_prepared_generation(
        output,
        prepared_directory=prepared,
        manifest_name=MANIFEST_NAME,
        validator=validate_free_provider_backfill,
        pointer_schema=POINTER_SCHEMA,
        pointer_fields={
            "mode": "signed_raw_provider_capture",
            "status": status,
            "data_admission_eligible": False,
        },
    )


def _matching_generation(
    output: Path,
    *,
    contract_id: str,
    request_plan_hash: str,
) -> dict[str, Any] | None:
    if (output / "current.json").is_file():
        try:
            payload = validate_free_provider_backfill(output)
        except (OSError, ValueError, json.JSONDecodeError):
            payload = None
        if payload is not None and (
            payload.get("contract_id") == contract_id
            and payload.get("request_plan_hash") == request_plan_hash
            and payload.get("publication_signature_verified") is True
        ):
            return payload
    matches = _matching_published_generations(
        output,
        contract_id=contract_id,
        request_plan_hash=request_plan_hash,
        activity_id=None,
    )
    if len(matches) > 1:
        raise ValueError("free_provider_backfill_multiple_matching_generations")
    if not matches:
        return None
    payload = matches[0]
    _write_capture_pointer(output, payload)
    return payload


def _resume_interrupted_publication(
    *,
    output: Path,
    run_parent: Path,
    contract_id: str,
    request_plan_hash: str,
    activity_id: str,
) -> dict[str, Any] | None:
    published = _matching_published_generations(
        output,
        contract_id=contract_id,
        request_plan_hash=request_plan_hash,
        activity_id=activity_id,
    )
    if len(published) > 1:
        raise ValueError("free_provider_backfill_multiple_matching_generations")
    if published:
        _write_capture_pointer(output, published[0])
        return published[0]
    prepared_matches: list[tuple[Path, dict[str, Any]]] = []
    if run_parent.is_dir():
        for candidate in sorted(run_parent.glob(f"{GENERATION_PREFIX}_*")):
            manifest = candidate / MANIFEST_NAME
            if not manifest.is_file():
                continue
            try:
                payload = validate_free_provider_backfill(manifest)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if (
                payload.get("contract_id") == contract_id
                and payload.get("request_plan_hash") == request_plan_hash
                and payload.get("activity_id") == activity_id
                and payload.get("publication_signature_verified") is True
            ):
                prepared_matches.append((candidate, payload))
    if len(prepared_matches) > 1:
        raise ValueError("free_provider_backfill_multiple_prepared_generations")
    if not prepared_matches:
        return None
    candidate, payload = prepared_matches[0]
    return publish_prepared_generation(
        output,
        prepared_directory=candidate,
        manifest_name=MANIFEST_NAME,
        validator=validate_free_provider_backfill,
        pointer_schema=POINTER_SCHEMA,
        pointer_fields={
            "mode": "signed_raw_provider_capture",
            "status": payload["status"],
            "data_admission_eligible": False,
        },
    )


def _matching_published_generations(
    output: Path,
    *,
    contract_id: str,
    request_plan_hash: str,
    activity_id: str | None,
) -> list[dict[str, Any]]:
    generations = output / "generations"
    if not generations.is_dir():
        return []
    matches: list[dict[str, Any]] = []
    for manifest in sorted(generations.glob(f"{GENERATION_PREFIX}_*/{MANIFEST_NAME}")):
        try:
            payload = validate_free_provider_backfill(manifest)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if (
            payload.get("contract_id") == contract_id
            and payload.get("request_plan_hash") == request_plan_hash
            and (activity_id is None or payload.get("activity_id") == activity_id)
            and payload.get("publication_signature_verified") is True
        ):
            matches.append(payload)
    return matches


def _write_capture_pointer(output: Path, payload: Mapping[str, Any]) -> None:
    generation_id = str(payload["generation_id"])
    atomic_json(
        output / "current.json",
        {
            "schema_version": POINTER_SCHEMA,
            "content_hash": payload["content_hash"],
            "generation_id": generation_id,
            "manifest": f"generations/{generation_id}/{MANIFEST_NAME}",
            "mode": "signed_raw_provider_capture",
            "status": payload["status"],
            "data_admission_eligible": False,
        },
    )
    _fsync_directory(output)


def _public_key_bytes(contract: Mapping[str, Any]) -> bytes:
    encoded = str(contract.get("capture_public_key_pem_b64") or "")
    public_key = base64.b64decode(encoded, validate=True)
    if _public_key_hash(public_key) != contract.get("capture_public_key_sha256"):
        raise ValueError("free_provider_backfill_contract_public_key_invalid")
    return public_key


def _validate_baostock_wire_envelope(
    raw_payload: bytes,
    *,
    expected_exchange_count: int,
    request: Mapping[str, Any],
    terminal_state: str,
) -> None:
    (
        envelope,
        fields,
        rows,
        package_rows,
        wire_pages,
        business_requests,
        wire_provider_results,
        partial_response_observed,
    ) = _decode_baostock_wire_envelope(
        raw_payload, allow_partial_response=terminal_state == "error"
    )
    exchanges = envelope.get("wire_exchanges")
    conservative_pre_send_overcount = bool(
        terminal_state == "error"
        and expected_exchange_count == 1
        and exchanges == []
        and envelope.get("socket_peer") == []
        and fields == []
        and rows == []
        and package_rows == []
        and wire_pages == []
        and business_requests == []
        and wire_provider_results == []
        and partial_response_observed is True
        and envelope.get("parsed")
        == {
            "canonical_logical_payload_sha256": canonical_hash(
                {"fields": [], "rows": []}
            ),
            "fields": [],
            "first_rows": [],
            "last_rows": [],
            "pages": [],
            "row_count": 0,
        }
        and envelope.get("provider_error")
        == {
            "message": "[Errno 107] Transport endpoint is not connected",
            "type": "OSError",
        }
    )
    if (
        envelope.get("schema_version") != "baostock_wire_probe_envelope_v1"
        or envelope.get("package_distribution_version") != "0.9.3"
        or envelope.get("client_protocol_version") != "00.9.30"
        or envelope.get("request_id") != request.get("request_id")
        or not isinstance(exchanges, list)
        or (
            len(exchanges) != expected_exchange_count
            and not conservative_pre_send_overcount
        )
    ):
        raise ValueError("free_provider_backfill_baostock_wire_closure_invalid")

    metadata = request.get("metadata")
    case = str(metadata.get("case") or "") if isinstance(metadata, Mapping) else ""
    expected_operation = _BAOSTOCK_OPERATION_BY_CASE.get(case)
    if expected_operation is None:
        raise ValueError("free_provider_backfill_baostock_request_binding_invalid")
    parsed = urlsplit(str(request.get("url") or ""))
    query = parse_qs(parsed.query, keep_blank_values=True)
    parameter_indexes = _BAOSTOCK_REQUEST_PARAMETER_INDEXES[case]
    request_bound = bool(business_requests)
    for tokens in business_requests:
        if tokens[0] != expected_operation:
            request_bound = False
            break
        for name, index in parameter_indexes.items():
            values = query.get(name)
            if (
                values is None
                or len(values) != 1
                or len(tokens) <= index
                or tokens[index] != values[0]
            ):
                request_bound = False
                break
        if not request_bound:
            break
    if terminal_state in {"positive", "empty"} and not request_bound:
        raise ValueError("free_provider_backfill_baostock_request_binding_invalid")
    if business_requests and not request_bound:
        raise ValueError("free_provider_backfill_baostock_request_binding_invalid")

    _validate_baostock_page_chain(
        business_requests=business_requests,
        wire_pages=wire_pages,
        require_terminal=terminal_state in {"positive", "empty"},
    )

    _reconcile_baostock_parsed_payload(
        envelope,
        fields=fields,
        rows=rows,
        package_rows=package_rows,
        expected_pages=wire_pages,
    )
    provider_error = envelope.get("provider_error")
    if wire_provider_results and (
        not isinstance(provider_error, Mapping)
        or str(provider_error.get("code") or "")
        != wire_provider_results[-1][0]
        or str(provider_error.get("message") or "")
        != wire_provider_results[-1][1]
        or (
            terminal_state in {"positive", "empty"}
            and any(code != "0" for code, _message in wire_provider_results)
        )
    ):
        raise ValueError("free_provider_backfill_baostock_logical_binding_invalid")
    if not wire_provider_results and (
        terminal_state != "error"
        or not partial_response_observed
        or not isinstance(provider_error, Mapping)
        or not str(provider_error.get("type") or "")
        or "code" in provider_error
    ):
        raise ValueError("free_provider_backfill_baostock_logical_binding_invalid")


def _validate_baostock_page_chain(
    *,
    business_requests: Sequence[Sequence[str]],
    wire_pages: Sequence[Mapping[str, int]],
    require_terminal: bool,
) -> None:
    """Validate the exact pagination state machine implemented by 0.9.3."""

    if len(business_requests) > _BAOSTOCK_MAX_PAGES_PER_REQUEST:
        raise ValueError(
            "free_provider_backfill_baostock_pagination_terminal_invalid"
        )
    for ordinal, tokens in enumerate(business_requests, start=1):
        try:
            page = int(tokens[2])
            page_size = int(tokens[3])
        except (IndexError, ValueError) as exc:
            raise ValueError(
                "free_provider_backfill_baostock_page_binding_invalid"
            ) from exc
        if (
            page != ordinal
            or str(page) != tokens[2]
            or page_size != _BAOSTOCK_PINNED_PAGE_SIZE
            or str(page_size) != tokens[3]
        ):
            raise ValueError(
                "free_provider_backfill_baostock_page_binding_invalid"
            )
    if len(wire_pages) > len(business_requests):
        raise ValueError(
            "free_provider_backfill_baostock_page_binding_invalid"
        )
    for ordinal, page in enumerate(wire_pages, start=1):
        if (
            page.get("page") != ordinal
            or page.get("provider_page_size") != _BAOSTOCK_PINNED_PAGE_SIZE
        ):
            raise ValueError(
                "free_provider_backfill_baostock_page_binding_invalid"
            )
        if ordinal < len(business_requests) and page.get("row_count") != (
            _BAOSTOCK_PINNED_PAGE_SIZE
        ):
            raise ValueError(
                "free_provider_backfill_baostock_pagination_terminal_invalid"
            )
    if require_terminal:
        if (
            not wire_pages
            or len(wire_pages) != len(business_requests)
            or wire_pages[-1].get("row_count", _BAOSTOCK_PINNED_PAGE_SIZE)
            >= _BAOSTOCK_PINNED_PAGE_SIZE
        ):
            raise ValueError(
                "free_provider_backfill_baostock_pagination_terminal_invalid"
            )


def _baostock_logical_rows(raw_payload: bytes) -> tuple[list[str], list[list[str]]]:
    fields, rows, _diagnostics = _baostock_logical_rows_with_reconciliation(
        raw_payload
    )
    return fields, rows


def _baostock_logical_rows_with_reconciliation(
    raw_payload: bytes,
) -> tuple[list[str], list[list[str]], dict[str, Any]]:
    """Return authoritative wire rows plus pinned-SDK reconciliation diagnostics."""

    (
        _envelope,
        fields,
        rows,
        package_rows,
        wire_pages,
        _business_requests,
        _wire_provider_results,
        _partial_response_observed,
    ) = (
        _decode_baostock_wire_envelope(raw_payload)
    )
    diagnostics = _reconcile_baostock_parsed_payload(
        _envelope,
        fields=fields,
        rows=rows,
        package_rows=package_rows,
        expected_pages=wire_pages,
    )
    return fields, rows, diagnostics


def _reconcile_baostock_parsed_payload(
    envelope: Mapping[str, Any],
    *,
    fields: Sequence[str],
    rows: Sequence[Sequence[str]],
    package_rows: Sequence[Sequence[str]],
    expected_pages: Sequence[Mapping[str, int]],
) -> dict[str, Any]:
    """Reconcile SDK output without allowing it to rewrite raw wire values.

    Baostock 0.9.3 ``ResultData.setData`` applies ``split()`` followed by
    ``join()`` to the complete JSON record payload before decoding it.  That
    removes literal whitespace inside string values such as an index name.
    The raw response record is authoritative; the package projection is only
    accepted when it is reproduced exactly from the archived wire bytes.
    """

    parsed_payload = envelope.get("parsed")
    if not isinstance(parsed_payload, Mapping) or not {
        "canonical_logical_payload_sha256",
        "fields",
        "first_rows",
        "last_rows",
        "pages",
        "row_count",
    } <= set(parsed_payload):
        raise ValueError("free_provider_backfill_baostock_logical_binding_invalid")
    parsed_fields = parsed_payload.get("fields")
    parsed_items = parsed_payload.get("items")
    if (
        not isinstance(parsed_fields, list)
        or [str(value) for value in parsed_fields] != list(fields)
        or len(package_rows) != len(rows)
        or parsed_payload.get("row_count") != len(package_rows)
        or parsed_payload.get("canonical_logical_payload_sha256")
        != canonical_hash(
            {"fields": list(fields), "rows": list(package_rows)}
        )
        or (
            parsed_items is not None
            and (
                not isinstance(parsed_items, list)
                or [[str(value) for value in row] for row in parsed_items]
                != list(package_rows)
            )
        )
        or parsed_payload.get("first_rows") != list(package_rows[:3])
        or parsed_payload.get("last_rows") != list(package_rows[-3:])
    ):
        raise ValueError("free_provider_backfill_baostock_logical_binding_invalid")
    pages = parsed_payload.get("pages")
    if (
        not isinstance(pages, list)
        or any(
            not isinstance(page, Mapping)
            or type(page.get("page")) is not int
            or not isinstance(page.get("row_count"), int)
            or isinstance(page.get("row_count"), bool)
            or type(page.get("provider_page_size")) is not int
            or set(page) != {"page", "row_count", "provider_page_size"}
            or int(page["row_count"]) < 0
            for page in pages
        )
        or sum(int(page["row_count"]) for page in pages) != len(package_rows)
        or pages != list(expected_pages)
    ):
        raise ValueError("free_provider_backfill_baostock_page_binding_invalid")
    loss_row_count = 0
    loss_cell_count = 0
    for wire_row, package_row in zip(rows, package_rows, strict=True):
        if len(wire_row) != len(package_row):
            raise ValueError(
                "free_provider_backfill_baostock_logical_binding_invalid"
            )
        row_loss = sum(
            str(wire_value) != str(package_value)
            for wire_value, package_value in zip(
                wire_row, package_row, strict=True
            )
        )
        if row_loss:
            loss_row_count += 1
            loss_cell_count += row_loss
    return {
        "authoritative_value_source": "raw_wire_response_record",
        "package_parser_usage": "reconciliation_only",
        "package_parser_semantics": "baostock_0_9_3_setData_split_join",
        "package_parser_loss_detected": loss_cell_count > 0,
        "package_parser_loss_row_count": loss_row_count,
        "package_parser_loss_cell_count": loss_cell_count,
    }


def _validate_baostock_request_contract(
    tokens: Sequence[str], *, request_frame: Mapping[str, Any]
) -> str:
    if not tokens:
        raise ValueError("baostock_backfill_wire_request_contract_invalid")
    operation = str(tokens[0])
    if operation == "login":
        protocol = _BAOSTOCK_LOGIN_PROTOCOL
        contract = _BAOSTOCK_LOGIN_REQUEST_TOKEN_CONTRACT
    else:
        protocol = _BAOSTOCK_PROTOCOL_BY_OPERATION.get(operation)
        contract = _BAOSTOCK_REQUEST_TOKEN_CONTRACTS.get(operation)
    if (
        protocol is None
        or contract is None
        or request_frame.get("message_type") != protocol["request_type"]
    ):
        raise ValueError("baostock_backfill_wire_message_type_invalid")
    literals = contract["literals"]
    if len(tokens) != contract["arity"] or any(
        tokens[index] != expected for index, expected in literals.items()
    ):
        if len(tokens) > 1 and tokens[1] != "anonymous":
            raise ValueError(
                "free_provider_backfill_baostock_user_binding_invalid"
            )
        raise ValueError("baostock_backfill_wire_request_contract_invalid")
    if operation == "login":
        return operation
    try:
        request_page = int(tokens[2])
        request_page_size = int(tokens[3])
    except ValueError as exc:
        raise ValueError("baostock_backfill_wire_page_invalid") from exc
    if (
        str(request_page) != tokens[2]
        or str(request_page_size) != tokens[3]
        or request_page < 1
        or request_page_size != _BAOSTOCK_PINNED_PAGE_SIZE
    ):
        raise ValueError(
            "free_provider_backfill_baostock_page_binding_invalid"
        )
    return operation


def _decode_baostock_wire_envelope(
    raw_payload: bytes,
    *,
    allow_partial_response: bool = False,
) -> tuple[
    Mapping[str, Any],
    list[str],
    list[list[str]],
    list[list[str]],
    list[dict[str, int]],
    list[list[str]],
    list[tuple[str, str]],
    bool,
]:
    envelope = json.loads(raw_payload)
    if not isinstance(envelope, Mapping):
        raise ValueError("baostock_backfill_raw_envelope_invalid")
    exchanges = envelope.get("wire_exchanges")
    if not isinstance(exchanges, list):
        raise ValueError("baostock_backfill_wire_capture_invalid")
    fields: list[str] | None = None
    rows: list[list[str]] = []
    package_rows: list[list[str]] = []
    wire_pages: list[dict[str, int]] = []
    business_requests: list[list[str]] = []
    wire_provider_results: list[tuple[str, str]] = []
    partial_response_observed = allow_partial_response and not exchanges
    login_seen = False
    login_succeeded = False
    login_user: str | None = None
    provider_terminal_seen = False
    for exchange_index, exchange in enumerate(exchanges):
        if not isinstance(exchange, Mapping):
            raise ValueError("baostock_backfill_wire_capture_invalid")
        request_bytes = _baostock_exchange_bytes(
            exchange,
            payload_key="wire_request_base64",
            hash_key="request_sha256",
            size_key="request_size_bytes",
        )
        response_bytes = _baostock_exchange_bytes(
            exchange,
            payload_key="wire_response_base64",
            hash_key="wire_response_sha256",
            size_key="wire_size_bytes",
        )
        peer = exchange.get("socket_peer")
        if not isinstance(peer, list) or len(peer) < 2:
            raise ValueError("baostock_backfill_wire_capture_invalid")
        request_tokens, request_frame = _parse_baostock_request_frame(
            request_bytes
        )
        operation = _validate_baostock_request_contract(
            request_tokens, request_frame=request_frame
        )
        is_login = operation == "login"
        if provider_terminal_seen:
            raise ValueError("baostock_backfill_wire_exchange_after_error")
        if is_login:
            if login_seen or business_requests:
                raise ValueError("baostock_backfill_wire_login_order_invalid")
            login_seen = True
            login_user = request_tokens[1]
        else:
            if login_seen and not login_succeeded:
                raise ValueError("baostock_backfill_wire_login_session_invalid")
            if login_user is not None and request_tokens[1] != login_user:
                raise ValueError(
                    "free_provider_backfill_baostock_user_binding_invalid"
                )
            business_requests.append(request_tokens)
        marker_value = exchange.get("terminal_marker_present")
        if not isinstance(marker_value, bool):
            raise ValueError("baostock_backfill_wire_response_partial_invalid")
        marker_complete = marker_value is True
        if not marker_complete:
            if (
                not allow_partial_response
                or response_bytes.endswith(b"<![CDATA[]]>\n")
            ):
                raise ValueError("baostock_backfill_wire_response_partial_invalid")
            if exchange_index != len(exchanges) - 1:
                raise ValueError(
                    "baostock_backfill_wire_partial_response_not_final"
                )
            partial_response_observed = True
            continue
        response_tokens, response_frame = _parse_baostock_response_frame(
            response_bytes
        )
        if is_login:
            if response_frame["message_type"] == (
                _BAOSTOCK_PROVIDER_ERROR_RESPONSE_TYPE
            ):
                if len(response_tokens) != 2 or response_tokens[0] == "0":
                    raise ValueError(
                        "baostock_backfill_wire_response_contract_invalid"
                    )
                provider_terminal_seen = True
                wire_provider_results.append(
                    (response_tokens[0], response_tokens[1])
                )
                continue
            if (
                response_frame["message_type"]
                != _BAOSTOCK_LOGIN_PROTOCOL["response_type"]
                or len(response_tokens) != 5
                or response_tokens[0] != "0"
                or response_tokens[2] != "login"
                or response_tokens[3] != login_user
                or not re.fullmatch(r"[0-9]{17}", response_tokens[4])
            ):
                raise ValueError("baostock_backfill_wire_login_session_invalid")
            login_succeeded = True
            continue
        protocol = _BAOSTOCK_PROTOCOL_BY_OPERATION.get(operation)
        if protocol is None:
            raise ValueError("baostock_backfill_wire_message_type_invalid")
        if len(response_tokens) < 2:
            raise ValueError("baostock_backfill_wire_response_invalid")
        wire_provider_results.append((response_tokens[0], response_tokens[1]))
        response_type = str(response_frame["message_type"])
        if response_type == _BAOSTOCK_PROVIDER_ERROR_RESPONSE_TYPE:
            if response_tokens[0] == "0":
                raise ValueError(
                    "baostock_backfill_wire_message_type_invalid"
                )
            if len(response_tokens) != 2:
                raise ValueError(
                    "baostock_backfill_wire_response_contract_invalid"
                )
            provider_terminal_seen = True
            continue
        if response_type != protocol["response_type"]:
            raise ValueError("baostock_backfill_wire_message_type_invalid")
        if response_tokens[0] != "0":
            if len(response_tokens) != 2:
                raise ValueError(
                    "baostock_backfill_wire_response_contract_invalid"
                )
            provider_terminal_seen = True
            continue
        if (
            len(response_tokens)
            != _BAOSTOCK_RESPONSE_TOKEN_ARITY[operation]
            or response_tokens[2] != operation
        ):
            raise ValueError("baostock_backfill_wire_operation_mismatch")
        if response_tokens[3] != request_tokens[1]:
            raise ValueError(
                "free_provider_backfill_baostock_user_binding_invalid"
            )
        if (
            response_tokens[4] != request_tokens[2]
            or response_tokens[5] != request_tokens[3]
        ):
            raise ValueError(
                "free_provider_backfill_baostock_page_binding_invalid"
            )
        response_request_pairs = _BAOSTOCK_RESPONSE_REQUEST_INDEX_PAIRS.get(
            operation
        )
        if response_request_pairs is None or any(
            len(response_tokens) <= response_index
            or len(request_tokens) <= request_index
            or response_tokens[response_index] != request_tokens[request_index]
            for response_index, request_index in response_request_pairs
        ):
            raise ValueError("baostock_backfill_wire_parameter_mismatch")
        field_index = _BAOSTOCK_RESPONSE_FIELDS_INDEX.get(operation)
        if field_index is None or len(response_tokens) <= field_index:
            raise ValueError("baostock_backfill_wire_fields_invalid")
        response_fields = [
            value.strip() for value in response_tokens[field_index].split(",")
        ]
        if not response_fields or any(not value for value in response_fields):
            raise ValueError("baostock_backfill_wire_fields_invalid")
        if fields is None:
            fields = response_fields
        elif fields != response_fields:
            raise ValueError("baostock_backfill_wire_fields_drift")
        wire_records, package_records = _baostock_record_projections(
            response_tokens[6]
        )
        try:
            response_page = int(response_tokens[4])
            response_page_size = int(response_tokens[5])
        except (IndexError, ValueError) as exc:
            raise ValueError("baostock_backfill_wire_page_invalid") from exc
        wire_pages.append(
            {
                "page": response_page,
                "row_count": len(package_records),
                "provider_page_size": response_page_size,
            }
        )
        rows.extend(wire_records)
        package_rows.extend(package_records)
    if allow_partial_response and not wire_provider_results:
        partial_response_observed = True
    return (
        envelope,
        fields or [],
        rows,
        package_rows,
        wire_pages,
        business_requests,
        wire_provider_results,
        partial_response_observed,
    )


def _baostock_record_projections(
    record_payload: str,
) -> tuple[list[list[str]], list[list[str]]]:
    """Decode authoritative records and the exact Baostock 0.9.3 projection."""

    # Baostock represents some successful empty terminal pages as an empty
    # record slot instead of the otherwise equivalent ``{"record":[]}`` JSON.
    # Pagination replay still rejects an empty non-terminal page.
    if record_payload == "":
        return [], []

    try:
        wire_payload = json.loads(record_payload)
        package_payload = json.loads("".join(record_payload.split()))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("baostock_backfill_wire_records_invalid") from exc
    wire_records = (
        wire_payload.get("record")
        if isinstance(wire_payload, Mapping)
        else None
    )
    package_records = (
        package_payload.get("record")
        if isinstance(package_payload, Mapping)
        else None
    )
    if (
        not isinstance(wire_records, list)
        or not isinstance(package_records, list)
        or len(wire_records) != len(package_records)
        or any(not isinstance(row, list) for row in wire_records)
        or any(not isinstance(row, list) for row in package_records)
    ):
        raise ValueError("baostock_backfill_wire_records_invalid")
    return (
        [[str(value) for value in row] for row in wire_records],
        [[str(value) for value in row] for row in package_records],
    )


def _baostock_exchange_bytes(
    exchange: Mapping[str, Any],
    *,
    payload_key: str,
    hash_key: str,
    size_key: str,
) -> bytes:
    try:
        payload = base64.b64decode(
            str(exchange.get(payload_key) or ""), validate=True
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("baostock_backfill_wire_capture_invalid") from exc
    if (
        hashlib.sha256(payload).hexdigest() != exchange.get(hash_key)
        or len(payload) != exchange.get(size_key)
    ):
        raise ValueError("baostock_backfill_wire_capture_invalid")
    return payload


def _parse_baostock_request_frame(
    wire: bytes,
) -> tuple[list[str], dict[str, Any]]:
    if len(wire) <= _BAOSTOCK_HEADER_LENGTH:
        raise ValueError("baostock_backfill_wire_request_truncated")
    header = wire[:_BAOSTOCK_HEADER_LENGTH]
    try:
        header_text = header.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("baostock_backfill_wire_request_header_invalid") from exc
    parts = header_text.split("\x01")
    if (
        len(parts) != 3
        or parts[0] != "00.9.30"
        or not re.fullmatch(r"[0-9A-F]{2}", parts[1])
        or not re.fullmatch(r"[0-9]{10}", parts[2])
    ):
        raise ValueError("baostock_backfill_wire_request_header_invalid")
    trailer = re.search(rb"\x01(?P<crc>[0-9]{1,10})\n$", wire)
    if trailer is None or trailer.start() <= _BAOSTOCK_HEADER_LENGTH:
        raise ValueError("baostock_backfill_wire_request_trailer_invalid")
    body_bytes = wire[_BAOSTOCK_HEADER_LENGTH : trailer.start()]
    try:
        body = body_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("baostock_backfill_wire_request_body_invalid") from exc
    if (
        len(body) != int(parts[2])
        or int(trailer.group("crc")) != zlib.crc32(header + body_bytes)
    ):
        raise ValueError("baostock_backfill_wire_request_length_invalid")
    tokens = body.split("\x01")
    if not tokens or not tokens[0]:
        raise ValueError("baostock_backfill_wire_request_body_invalid")
    return tokens, {
        "message_type": parts[1],
        "provider_trailer_integrity_verified": True,
        "provider_trailer_integrity_semantics": "crc32_header_plus_utf8_body",
    }


def _parse_baostock_response_frame(
    wire: bytes,
) -> tuple[list[str], dict[str, Any]]:
    if len(wire) <= _BAOSTOCK_HEADER_LENGTH:
        raise ValueError("baostock_backfill_wire_response_truncated")
    header = wire[:_BAOSTOCK_HEADER_LENGTH]
    try:
        header_text = header.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("baostock_backfill_wire_response_header_invalid") from exc
    parts = header_text.split("\x01")
    if (
        len(parts) != 3
        or parts[0] != "00.9.00"
        or not re.fullmatch(r"[0-9A-F]{2}", parts[1])
        or not re.fullmatch(r"[0-9]{10}", parts[2])
    ):
        raise ValueError("baostock_backfill_wire_response_header_invalid")
    trailer = _BAOSTOCK_RESPONSE_TRAILER.search(wire)
    if trailer is None or trailer.start() <= _BAOSTOCK_HEADER_LENGTH:
        raise ValueError("baostock_backfill_wire_response_trailer_invalid")
    body_bytes = wire[_BAOSTOCK_HEADER_LENGTH : trailer.start()]
    compressed = parts[1] in _BAOSTOCK_COMPRESSED_RESPONSE_TYPES
    try:
        if compressed:
            if len(body_bytes) != int(parts[2]):
                raise ValueError("baostock_backfill_wire_response_length_invalid")
            decompressor = zlib.decompressobj()
            decoded = decompressor.decompress(
                body_bytes, _BAOSTOCK_MAX_DECOMPRESSED_RESPONSE_BYTES + 1
            )
            if (
                len(decoded) > _BAOSTOCK_MAX_DECOMPRESSED_RESPONSE_BYTES
                or decompressor.unconsumed_tail
            ):
                raise ValueError(
                    "baostock_backfill_wire_response_decompressed_budget_invalid"
                )
            decoded += decompressor.flush()
            if (
                len(decoded) > _BAOSTOCK_MAX_DECOMPRESSED_RESPONSE_BYTES
                or not decompressor.eof
                or decompressor.unused_data
                or decompressor.unconsumed_tail
            ):
                raise ValueError(
                    "baostock_backfill_wire_response_compression_invalid"
                )
            body = decoded.decode("utf-8")
        else:
            body = body_bytes.decode("utf-8")
            if (
                len(body) != int(parts[2])
                or int(trailer.group("crc"))
                != zlib.crc32(header + body_bytes)
            ):
                raise ValueError("baostock_backfill_wire_response_length_invalid")
    except (UnicodeDecodeError, zlib.error) as exc:
        raise ValueError("baostock_backfill_wire_response_body_invalid") from exc
    tokens = body.split("\x01")
    if not tokens or not tokens[0]:
        raise ValueError("baostock_backfill_wire_response_body_invalid")
    return tokens, {
        "message_type": parts[1],
        "compressed": compressed,
        "provider_trailer_decimal_preserved": trailer.group("crc").decode(
            "ascii"
        ),
        "provider_trailer_integrity_verified": not compressed,
        "provider_trailer_integrity_semantics": (
            "unverified_opaque_decimal_for_compressed_response"
            if compressed
            else "crc32_header_plus_utf8_body"
        ),
        "zlib_stream_checksum_verified": compressed,
    }


def baostock_wire_protocol_root() -> str:
    """Content-address the complete v2 Baostock wire replay implementation."""

    return canonical_hash(
        {
            "operation_by_case": _BAOSTOCK_OPERATION_BY_CASE,
            "request_parameter_indexes": _BAOSTOCK_REQUEST_PARAMETER_INDEXES,
            "response_fields_index": _BAOSTOCK_RESPONSE_FIELDS_INDEX,
            "response_request_index_pairs": (
                _BAOSTOCK_RESPONSE_REQUEST_INDEX_PAIRS
            ),
            "protocol_by_operation": _BAOSTOCK_PROTOCOL_BY_OPERATION,
            "request_token_contracts": _BAOSTOCK_REQUEST_TOKEN_CONTRACTS,
            "response_token_arity": _BAOSTOCK_RESPONSE_TOKEN_ARITY,
            "login_protocol": _BAOSTOCK_LOGIN_PROTOCOL,
            "login_request_token_contract": (
                _BAOSTOCK_LOGIN_REQUEST_TOKEN_CONTRACT
            ),
            "provider_error_response_type": (
                _BAOSTOCK_PROVIDER_ERROR_RESPONSE_TYPE
            ),
            "pinned_page_size": _BAOSTOCK_PINNED_PAGE_SIZE,
            "max_pages_per_request": _BAOSTOCK_MAX_PAGES_PER_REQUEST,
            "max_decompressed_response_bytes": (
                _BAOSTOCK_MAX_DECOMPRESSED_RESPONSE_BYTES
            ),
            "header_length": _BAOSTOCK_HEADER_LENGTH,
            "compressed_response_types": sorted(
                _BAOSTOCK_COMPRESSED_RESPONSE_TYPES
            ),
            "response_trailer": _BAOSTOCK_RESPONSE_TRAILER.pattern.decode(
                "latin-1"
            ),
            "validator": inspect.getsource(_validate_baostock_wire_envelope),
            "logical_rows": inspect.getsource(_baostock_logical_rows),
            "logical_rows_with_reconciliation": inspect.getsource(
                _baostock_logical_rows_with_reconciliation
            ),
            "parsed_reconciliation": inspect.getsource(
                _reconcile_baostock_parsed_payload
            ),
            "page_chain": inspect.getsource(_validate_baostock_page_chain),
            "request_contract": inspect.getsource(
                _validate_baostock_request_contract
            ),
            "envelope_decoder": inspect.getsource(
                _decode_baostock_wire_envelope
            ),
            "exchange_decoder": inspect.getsource(_baostock_exchange_bytes),
            "request_frame": inspect.getsource(_parse_baostock_request_frame),
            "response_frame": inspect.getsource(_parse_baostock_response_frame),
            "record_projections": inspect.getsource(
                _baostock_record_projections
            ),
        }
    )


def _request_from_semantic(row: Mapping[str, Any]) -> ProviderProbeRequest:
    return ProviderProbeRequest(
        request_id=str(row["request_id"]),
        provider=str(row["provider"]),
        endpoint=str(row.get("endpoint") or ""),
        method=str(row["method"]),
        url=str(row["url"]),
        body=base64.b64decode(str(row.get("body_base64") or ""), validate=True),
        headers=dict(row.get("headers") or {}),
        disposition=str(row.get("disposition") or "bounded_backfill"),
        evidence_semantics=str(row.get("evidence_semantics") or ""),
        expected_terminal_states=tuple(row.get("expected_terminal_states") or ()),
        required_checks=tuple(row.get("required_checks") or ()),
        metadata=dict(row.get("metadata") or {}),
    )


def _raw_relative_path(attempt_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", attempt_id)
    suffix = canonical_hash(attempt_id)[:12]
    return f"raw_envelopes/{safe}.{suffix}.json"


def _to_baostock_code(ts_code: str) -> str:
    symbol, suffix = ts_code.split(".", 1)
    return f"{'sh' if suffix == 'SH' else 'sz'}.{symbol}"


def _from_baostock_code(provider_code: str) -> str:
    exchange, symbol = provider_code.split(".", 1)
    return f"{symbol}.{'SH' if exchange.lower() == 'sh' else 'SZ'}"


def _hyphen_date(value: str) -> str:
    if not _valid_date(value):
        raise ValueError("free_provider_backfill_date_invalid")
    return f"{value[:4]}-{value[4:6]}-{value[6:]}"


def _valid_date(value: str) -> bool:
    try:
        datetime.strptime(str(value), "%Y%m%d")
    except (TypeError, ValueError):
        return False
    return True


def _public_key_hash(public_key: bytes) -> str:
    return canonical_hash(public_key.decode("ascii"))


def _baostock_implementation_root() -> str:
    return canonical_hash(
        {
            "provider_probe.py": sha256_file(Path(provider_probe_module.__file__)),
            "run_provider_probe.py": sha256_file(
                Path(run_provider_probe_module.__file__)
            ),
            "capture_engine": inspect.getsource(run_free_provider_backfill),
            "capture_validator": inspect.getsource(validate_free_provider_backfill),
            "recovering_transport": inspect.getsource(
                RecoveringBaostockTransport
            ),
            "state_plan": inspect.getsource(build_baostock_state_plan),
            "state_normalizer": inspect.getsource(normalize_baostock_state_capture),
            "wire_decoder": inspect.getsource(_baostock_logical_rows),
            "strict_wire_protocol_root": baostock_wire_protocol_root(),
            "baostock_distribution_record_root": baostock_distribution_record_root(),
        }
    )


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return _json_bytes(dict(payload))


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _json_value(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("free_provider_backfill_timestamp_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("free_provider_backfill_timestamp_invalid")
    return parsed.astimezone(UTC)


def _write_line(handle: Any, payload: Mapping[str, Any]) -> None:
    handle.write(_json_bytes(dict(payload)) + b"\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"jsonl_object_required:{path}")
            rows.append(value)
    return rows


def _atomic_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            for row in rows:
                handle.write(_json_bytes(dict(row)) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _confined_file(root: Path, relative: str) -> Path | None:
    candidate = Path(relative)
    if not relative or candidate.is_absolute() or ".." in candidate.parts:
        return None
    resolved = (root / candidate).resolve()
    if root.resolve() not in resolved.parents or not resolved.is_file() or resolved.is_symlink():
        return None
    return resolved


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the signed, resumable free-provider data backfill."
    )
    parser.add_argument("--phase", choices=("baostock-state",), default="baostock-state")
    parser.add_argument("--securities-path", default=str(DEFAULT_SECURITIES_PATH))
    parser.add_argument("--request-start", default="20111201")
    parser.add_argument("--request-end", default="20191231")
    parser.add_argument(
        "--security-code",
        action="append",
        help="Limit the immutable population to an exact TS code; repeatable for a canary.",
    )
    parser.add_argument(
        "--resume-run",
        help="Resume an existing .activities/<activity_id> using its sealed contract.",
    )
    parser.add_argument("--permission-context-id", default=DEFAULT_PERMISSION_CONTEXT)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--validate")
    parser.add_argument("--minimum-delay-seconds", type=float, default=1.0)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--max-retries", type=int, default=2)
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
        print(_render(_public_summary(payload), pretty=args.pretty))
        return 0
    if args.resume_run:
        if not args.allow_network:
            print(
                _render(
                    {
                        "status": "blocked",
                        "reason": "free_provider_backfill_network_authority_missing",
                        "network_called": False,
                    },
                    pretty=args.pretty,
                )
            )
            return 2
        signer = PersistentReceiptSigner.load(DEFAULT_CAPTURE_KEY)
        transport = RecoveringBaostockTransport()
        try:
            result = resume_free_provider_backfill_activity(
                args.resume_run,
                transport=transport,
                signer=signer,
                normalizer=normalize_baostock_state_capture,
            )
        finally:
            transport.close()
        print(_render(_public_summary(result), pretty=args.pretty))
        return 0 if result.get("status") == "succeeded" else 1
    population, requests = build_baostock_state_plan(
        args.securities_path,
        include_codes=args.security_code,
        request_start=args.request_start,
        request_end=args.request_end,
    )
    population_root = canonical_hash(population)
    if not args.plan_only and not args.allow_network:
        print(
            _render(
                {
                    "status": "blocked",
                    "reason": "free_provider_backfill_network_authority_missing",
                    "phase": args.phase,
                    "population_count": len(population),
                    "request_count": len(requests),
                    "network_called": False,
                },
                pretty=args.pretty,
            )
        )
        return 2
    if args.plan_only and not DEFAULT_CAPTURE_KEY.is_file():
        print(
            _render(
                {
                    "schema_version": "free_provider_backfill_plan_preview_v1",
                    "phase": args.phase,
                    "provider": "baostock",
                    "population_count": len(population),
                    "population_root": population_root,
                    "request_count": len(requests),
                    "request_plan_hash": canonical_hash(
                        [request.semantic() for request in requests]
                    ),
                    "capture_key_status": "not_initialized",
                    "network_called": False,
                },
                pretty=args.pretty,
            )
        )
        return 0
    signer = PersistentReceiptSigner.load(DEFAULT_CAPTURE_KEY)
    budget = BackfillResourceBudget(
        max_requests=len(requests) * (args.max_retries + 1),
        max_wire_exchanges=len(requests) * (args.max_retries + 2),
        max_response_bytes=64 * 1024 * 1024,
        max_total_response_bytes=8 * 1024 * 1024 * 1024,
        timeout_seconds=args.timeout_seconds,
        minimum_delay_seconds=args.minimum_delay_seconds,
        max_retries=args.max_retries,
    )
    contract = FreeProviderBackfillContract(
        activity_name="free_domestic_baostock_state_daily_2012_2019_v1",
        provider="baostock",
        output_root=DEFAULT_OUTPUT_ROOT,
        permission_context_id=args.permission_context_id,
        population_root=population_root,
        capture_public_key_sha256=_public_key_hash(signer.public_key_pem),
        capture_public_key_pem_b64=base64.b64encode(signer.public_key_pem).decode(
            "ascii"
        ),
        scope_start=max("20120101", args.request_start),
        scope_end=min("20191231", args.request_end),
        request_start=args.request_start,
        request_end=args.request_end,
        allowed_hosts=("public-api.baostock.com",),
        budget=budget,
        adapter_identity={
            "adapter": "baostock_signed_socket_capture_v1",
            "baostock_distribution": "0.9.3",
            "baostock_client": "00.9.30",
            "implementation_root": _baostock_implementation_root(),
        },
    )
    preview = {
        "schema_version": "free_provider_backfill_plan_preview_v1",
        "phase": args.phase,
        "provider": contract.provider,
        "contract_id": canonical_hash(contract.semantic()),
        "population_count": len(population),
        "population_root": population_root,
        "request_count": len(requests),
        "request_plan_hash": canonical_hash([request.semantic() for request in requests]),
        "capture_public_key_sha256": contract.capture_public_key_sha256,
        "permission_context_id": contract.permission_context_id,
        "network_called": False,
    }
    if args.plan_only:
        print(_render(preview, pretty=args.pretty))
        return 0
    transport = RecoveringBaostockTransport()
    try:
        result = run_free_provider_backfill(
            contract,
            requests,
            transport=transport,
            signer=signer,
            normalizer=normalize_baostock_state_capture,
            runtime_implementation_root=_baostock_implementation_root(),
        )
    finally:
        transport.close()
    print(_render(_public_summary(result), pretty=args.pretty))
    return 0 if result.get("status") == "succeeded" else 1


def _public_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: payload.get(key)
        for key in (
            "schema_version",
            "generation_id",
            "content_hash",
            "mode",
            "status",
            "activity_id",
            "contract_id",
            "request_plan_hash",
            "request_count",
            "terminal_counts",
            "resource_usage",
            "normalized_artifacts",
            "raw_capture_replay_eligible",
            "old_lake_mutated",
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


if __name__ == "__main__":
    raise SystemExit(main())

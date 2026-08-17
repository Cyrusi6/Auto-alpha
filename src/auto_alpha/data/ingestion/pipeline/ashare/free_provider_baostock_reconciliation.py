"""Baostock reconciliation captures that cannot by themselves prove PIT truth."""

from __future__ import annotations

import argparse
import base64
import hashlib
import inspect
import json
import os
import re
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from auto_alpha.platform.artifacts.storage import canonical_hash, read_json, sha256_file
from auto_alpha.platform.governance.network.signing import PersistentReceiptSigner

from .free_provider_backfill import (
    BackfillResourceBudget,
    FreeProviderBackfillContract,
    NormalizedArtifact,
    RecoveringBaostockTransport,
    _baostock_logical_rows,
    _baostock_logical_rows_with_reconciliation,
    _from_baostock_code,
    _public_key_hash,
    baostock_wire_protocol_root,
    build_baostock_state_plan,
    replay_normalized_artifacts,
    run_free_provider_backfill,
    validate_free_provider_backfill,
)
from .provider_probe import ProviderProbeObservation, ProviderProbeRequest
from .run_provider_probe import (
    BAOSTOCK_FIELDS,
    BaostockProbeTransport,
    baostock_distribution_record_root,
)


LAKE_ROOT = Path("/home/lijunsi/data/auto-alpha/ashare_lake")
SCOPE_ROOT = (
    LAKE_ROOT
    / "staging/data_admission"
    / "dap_d785714ef1b912a20c0f19ca"
    / "research_20120101_20191231_asof_20191231"
    / "baostock"
)
SECURITIES_PATH = LAKE_ROOT / "data/securities/records.jsonl"
CALENDAR_PATH = LAKE_ROOT / "data/trade_calendar/records.jsonl"
CAPTURE_KEY = LAKE_ROOT / "governance/capture_keys/free_domestic_backfill_20260816.pem"
PERMISSION_CONTEXT = "human_authorization_20260816_free_domestic_missing_data_backfill_v1"
SECURITY_SNAPSHOT_SEED_DATE = "20111230"
SECURITY_SNAPSHOT_APPROVED_OPEN_DATE_ROOT = (
    "2b277e1c53c76d17032fb74c676ea260cb150437c394aa35f8a06d81355abbc6"
)
SECURITY_SNAPSHOT_APPROVED_POPULATION_ROOT = (
    "f171e5952f9998abdbd202b0e7fa0876b34c8b29cd34989b8da00cce453b47f2"
)
SECURITY_SNAPSHOT_OPEN_DATE_COUNT = 1_945
SECURITY_SNAPSHOT_REQUEST_COUNT = 1_946
SECURITY_SNAPSHOT_APPROVED_REQUEST_PLAN_HASH = (
    "a63998510e1652b0f9ba345bf9c3f738ef26fc15f6c35770646105dbfc1cf783"
)
BAOSTOCK_APPROVED_CAPTURE_KEY_SHA256 = (
    "0afef940a253b9ef0f3702af5eb099c4ed48209975bc4f1991a471e4c50f446f"
)
BAOSTOCK_SOURCE_PROFILE_ID = "dap_d785714ef1b912a20c0f19ca"
BAOSTOCK_AUTHORIZATION_POLICY = (
    "human_authorized_free_domestic_baostock_reconciliation_v1"
)
BAOSTOCK_SECURITIES_SOURCE_SHA256 = (
    "4d2cac55283a4382169ea96decad333995cf51be081b6896be2142626a512f42"
)
BAOSTOCK_CALENDAR_SOURCE_SHA256 = (
    "2cf3c5996addc76f02d9ee80cef55db391a9753b62b696b4596d9efa63435b9d"
)
BAOSTOCK_COMPRESSED_TRAILER_SEMANTICS = (
    "opaque_decimal_preserved_integrity_unverified_zlib_stream_checksum_verified"
)
BAOSTOCK_PAGINATION_CONTRACT = {
    "first_page": 1,
    "page_size": 2000,
    "max_pages_per_request": 4,
    "continuation_requires_full_page": True,
    "terminal_page_requires_less_than_page_size": True,
    "exact_multiple_requires_empty_terminal_page": True,
}
BAOSTOCK_TRANSIENT_TRANSPORT_ERROR_MAP = {
    "baostock_transport:BrokenPipeError": "baostock_transport:ConnectionError",
    "baostock_transport:ConnectionAbortedError": (
        "baostock_transport:ConnectionError"
    ),
    "baostock_transport:ConnectionRefusedError": (
        "baostock_transport:ConnectionError"
    ),
    "baostock_transport:ConnectionResetError": (
        "baostock_transport:ConnectionError"
    ),
}


class BoundedBaostockReconciliationTransport(RecoveringBaostockTransport):
    """Normalize only reviewed transient socket subclasses before retry."""

    def __call__(
        self, request: ProviderProbeRequest, timeout_seconds: float
    ) -> ProviderProbeObservation:
        observation = super().__call__(request, timeout_seconds)
        original_error = str(observation.error_code or "")
        normalized_error = BAOSTOCK_TRANSIENT_TRANSPORT_ERROR_MAP.get(
            original_error
        )
        if normalized_error is None:
            return observation
        self._replace()
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
                    "transport_replaced": True,
                },
            },
            checks=observation.checks,
            transport_exchange_count=observation.transport_exchange_count,
        )

_BAOSTOCK_PHASE_BASELINES: dict[str, dict[str, Any]] = {
    "adjustments": {
        "population_count": 3798,
        "population_root": "6e5ccc539a4e361c798d0c16e6616d07dedd56a4e81ba5c9f99d0b073e2d12fb",
        "request_count": 3798,
        "request_plan_hash": "1906ec659098c4ad1322a1d33ade56dd6147925d3de1df0dc788fec48c97e933",
    },
    "hs300-snapshots": {
        "population_count": 1946,
        "population_root": "f171e5952f9998abdbd202b0e7fa0876b34c8b29cd34989b8da00cce453b47f2",
        "request_count": 1946,
        "request_plan_hash": "f448161d8538bccdaad47ada56487d8278b2a594f9f053b877be75b5618c26bc",
    },
    "dividends": {
        "population_count": 3798,
        "population_root": "6e5ccc539a4e361c798d0c16e6616d07dedd56a4e81ba5c9f99d0b073e2d12fb",
        "request_count": 34182,
        "request_plan_hash": "54384b5ce6c7d91195d1d41bb519161702c2e271a2c667f50f6b629e8b43ca00",
    },
    "security-basic": {
        "population_count": 3798,
        "population_root": "6e5ccc539a4e361c798d0c16e6616d07dedd56a4e81ba5c9f99d0b073e2d12fb",
        "request_count": 3798,
        "request_plan_hash": "14eae9c872cd8e6a2ed0145904e2d9d636dd5deccb5d2d0e41c0e48e50955e1a",
    },
    "security-snapshots": {
        "population_count": SECURITY_SNAPSHOT_REQUEST_COUNT,
        "population_root": SECURITY_SNAPSHOT_APPROVED_POPULATION_ROOT,
        "request_count": SECURITY_SNAPSHOT_REQUEST_COUNT,
        "request_plan_hash": SECURITY_SNAPSHOT_APPROVED_REQUEST_PLAN_HASH,
    },
    "index-daily": {
        "population_count": 1,
        "population_root": "0a3be75bd4c5ee78a4fb77a414f8e922f94b0973360cdd390f02692808ae980f",
        "request_count": 1,
        "request_plan_hash": "b53ce4c3f70b4e17eb99912c12e2c3d1872e329849267d953d4e679d3e861421",
    },
    "turnover": {
        "population_count": 3798,
        "population_root": "6e5ccc539a4e361c798d0c16e6616d07dedd56a4e81ba5c9f99d0b073e2d12fb",
        "request_count": 3798,
        "request_plan_hash": "11e2e1de0c2925a26aa8e38b76d774054bbf561534a49e9d109185e142e6c2c2",
    },
}


def _approved_source_file_hash(
    path: str | Path, *, expected_sha256: str, role: str
) -> str:
    try:
        observed = sha256_file(Path(path))
    except OSError as exc:
        raise ValueError(
            f"baostock_reconciliation_source_file_unavailable:{role}"
        ) from exc
    if observed != expected_sha256:
        raise ValueError(
            f"baostock_reconciliation_source_file_sha256_mismatch:{role}"
        )
    return observed


def build_security_basic_plan(
    securities_path: str | Path = SECURITIES_PATH,
    *,
    include_codes: Sequence[str] | None = None,
) -> tuple[list[dict[str, Any]], list[ProviderProbeRequest]]:
    _approved_source_file_hash(
        securities_path,
        expected_sha256=BAOSTOCK_SECURITIES_SOURCE_SHA256,
        role="securities",
    )
    population, _state_requests = build_baostock_state_plan(
        securities_path, include_codes=include_codes
    )
    requests = [
        ProviderProbeRequest(
            request_id=f"baostock_stock_basic_{row['ts_code'].replace('.', '_')}",
            provider="baostock",
            endpoint="security_basic_reconciliation",
            method="BAOSTOCK",
            url=(
                "baostock://public-api.baostock.com/stock_basic"
                f"?code={row['provider_code']}"
            ),
            disposition="provider_cannot_prove",
            evidence_semantics="raw_custom_socket_response_plus_locked_parser",
            expected_terminal_states=("positive", "empty"),
            required_checks=(
                "provider_success",
                "raw_wire_captured",
                "terminal_marker_complete",
                "pagination_terminal_unambiguous",
                "row_width_matches_fields",
                "stock_basic_fields_exact",
                "stock_basic_identity_unique",
            ),
            metadata={
                "case": "stock_basic",
                "ts_code": row["ts_code"],
                "provider_code": row["provider_code"],
            },
        )
        for row in population
    ]
    return population, requests


def build_security_snapshot_plan(
    calendar_path: str | Path = CALENDAR_PATH,
) -> tuple[list[str], list[ProviderProbeRequest]]:
    """Freeze one full-provider security snapshot for every governed open day."""

    _approved_source_file_hash(
        calendar_path,
        expected_sha256=BAOSTOCK_CALENDAR_SOURCE_SHA256,
        role="calendar",
    )

    open_dates = [
        str(row.get("trade_date") or "")
        for row in _read_jsonl(Path(calendar_path))
        if row.get("is_open") is True
        and "20120101" <= str(row.get("trade_date") or "") <= "20191231"
    ]
    if len(open_dates) != len(set(open_dates)):
        raise ValueError("baostock_security_snapshot_calendar_duplicate")
    if any(not _strict_compact_date(value) for value in open_dates):
        raise ValueError("baostock_security_snapshot_calendar_date_invalid")
    governed_dates = sorted(open_dates)
    if (
        len(governed_dates) != SECURITY_SNAPSHOT_OPEN_DATE_COUNT
        or governed_dates[:1] != ["20120104"]
        or governed_dates[-1:] != ["20191231"]
        or canonical_hash(governed_dates)
        != SECURITY_SNAPSHOT_APPROVED_OPEN_DATE_ROOT
    ):
        raise ValueError(
            "baostock_security_snapshot_calendar_unexpected:"
            f"{len(governed_dates)}"
        )
    snapshot_dates = [SECURITY_SNAPSHOT_SEED_DATE, *governed_dates]
    if (
        len(snapshot_dates) != SECURITY_SNAPSHOT_REQUEST_COUNT
        or canonical_hash(snapshot_dates)
        != SECURITY_SNAPSHOT_APPROVED_POPULATION_ROOT
    ):
        raise ValueError("baostock_security_snapshot_population_root_invalid")
    requests = [
        ProviderProbeRequest(
            request_id=f"baostock_security_snapshot_{date}",
            provider="baostock",
            endpoint="security_snapshot_reconciliation",
            method="BAOSTOCK",
            url=(
                "baostock://public-api.baostock.com/all_stock"
                f"?date={date[:4]}-{date[4:6]}-{date[6:]}"
            ),
            disposition="provider_cannot_prove",
            evidence_semantics="raw_custom_socket_response_plus_locked_parser",
            expected_terminal_states=("positive",),
            required_checks=(
                "provider_success",
                "raw_wire_captured",
                "terminal_marker_complete",
                "pagination_terminal_unambiguous",
                "row_width_matches_fields",
                "all_stock_fields_exact",
                "snapshot_query_date_bound",
                "unique_provider_code",
                "all_stock_values_nonempty",
                "trade_status_domain_valid",
            ),
            metadata={
                "case": "all_stock",
                "snapshot_query_date": date,
                "provider_code_name_pit_proven": False,
                "alias_adjudicated": False,
                "usage": "provider_reconciliation_only",
            },
        )
        for date in snapshot_dates
    ]
    return snapshot_dates, requests


def build_index_daily_plan() -> tuple[list[str], list[ProviderProbeRequest]]:
    expected_fields = tuple(BAOSTOCK_FIELDS.split(","))
    request = ProviderProbeRequest(
        request_id="baostock_index_daily_000300_SH",
        provider="baostock",
        endpoint="index_daily_bars_reconciliation",
        method="BAOSTOCK",
        url=(
            "baostock://public-api.baostock.com/history"
            "?code=sh.000300&start=2012-01-01&end=2019-12-31"
            f"&fields={BAOSTOCK_FIELDS}"
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
            "history_fields_exact",
            "unique_security_day",
            "provider_code_matches_request",
        ),
        metadata={
            "case": "history_custom",
            "ts_code": "000300.SH",
            "provider_code": "sh.000300",
            "expected_fields": expected_fields,
        },
    )
    return ["000300.SH"], [request]


def build_turnover_plan(
    securities_path: str | Path = SECURITIES_PATH,
    *,
    include_codes: Sequence[str] | None = None,
) -> tuple[list[dict[str, Any]], list[ProviderProbeRequest]]:
    _approved_source_file_hash(
        securities_path,
        expected_sha256=BAOSTOCK_SECURITIES_SOURCE_SHA256,
        role="securities",
    )
    population, _state_requests = build_baostock_state_plan(
        securities_path, include_codes=include_codes
    )
    fields = ("date", "code", "turn")
    requests = [
        ProviderProbeRequest(
            request_id=f"baostock_turnover_{row['ts_code'].replace('.', '_')}",
            provider="baostock",
            endpoint="daily_turnover_reconciliation",
            method="BAOSTOCK",
            url=(
                "baostock://public-api.baostock.com/history"
                f"?code={row['provider_code']}&start=2011-12-01&end=2019-12-31"
                "&fields=date,code,turn"
            ),
            disposition="provider_cannot_prove",
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
                "provider_code_matches_request",
            ),
            metadata={
                "case": "history_custom",
                "ts_code": row["ts_code"],
                "provider_code": row["provider_code"],
                "expected_fields": fields,
            },
        )
        for row in population
    ]
    return population, requests


def build_adjustment_plan(
    securities_path: str | Path = SECURITIES_PATH,
) -> tuple[list[dict[str, Any]], list[ProviderProbeRequest]]:
    _approved_source_file_hash(
        securities_path,
        expected_sha256=BAOSTOCK_SECURITIES_SOURCE_SHA256,
        role="securities",
    )
    population, _state_requests = build_baostock_state_plan(securities_path)
    requests = [
        ProviderProbeRequest(
            request_id=f"baostock_adjustment_{row['ts_code'].replace('.', '_')}",
            provider="baostock",
            endpoint="adjust_factor_reconciliation",
            method="BAOSTOCK",
            url=(
                "baostock://public-api.baostock.com/adjust_factor"
                f"?code={row['provider_code']}&start=2012-01-01&end=2019-12-31"
            ),
            disposition="provider_cannot_prove",
            evidence_semantics="raw_custom_socket_response_plus_locked_parser",
            expected_terminal_states=("positive", "empty"),
            required_checks=(
                "provider_success",
                "raw_wire_captured",
                "terminal_marker_complete",
                "pagination_terminal_unambiguous",
                "row_width_matches_fields",
                "historical_revision_timestamp_absent",
                "provider_code_matches_request",
            ),
            metadata={
                "case": "adjust_factor",
                "ts_code": row["ts_code"],
                "provider_code": row["provider_code"],
            },
        )
        for row in population
    ]
    return population, requests


def build_hs300_snapshot_plan(
    calendar_path: str | Path = CALENDAR_PATH,
) -> tuple[list[str], list[ProviderProbeRequest]]:
    _approved_source_file_hash(
        calendar_path,
        expected_sha256=BAOSTOCK_CALENDAR_SOURCE_SHA256,
        role="calendar",
    )
    open_dates = {
        str(row.get("trade_date") or "")
        for row in _read_jsonl(Path(calendar_path))
        if row.get("is_open") is True
        and "20120101" <= str(row.get("trade_date") or "") <= "20191231"
    }
    dates = ["20111230", *sorted(open_dates)]
    if len(open_dates) != 1_945:
        raise ValueError(f"baostock_hs300_calendar_unexpected:{len(open_dates)}")
    requests = [
        ProviderProbeRequest(
            request_id=f"baostock_hs300_{date}",
            provider="baostock",
            endpoint="hs300_snapshot",
            method="BAOSTOCK",
            url=(
                "baostock://public-api.baostock.com/hs300"
                f"?date={date[:4]}-{date[4:6]}-{date[6:]}"
            ),
            disposition="provider_cannot_prove",
            evidence_semantics="raw_custom_socket_response_plus_locked_parser",
            expected_terminal_states=("positive",),
            required_checks=(
                "provider_success",
                "raw_wire_captured",
                "terminal_marker_complete",
                "pagination_terminal_unambiguous",
                "row_width_matches_fields",
                "exactly_300_unique_members",
                "snapshot_update_date_present",
            ),
            metadata={"case": "hs300", "snapshot_query_date": date},
        )
        for date in dates
    ]
    return dates, requests


def build_dividend_plan(
    securities_path: str | Path = SECURITIES_PATH,
) -> tuple[list[dict[str, Any]], list[ProviderProbeRequest]]:
    _approved_source_file_hash(
        securities_path,
        expected_sha256=BAOSTOCK_SECURITIES_SOURCE_SHA256,
        role="securities",
    )
    population, _state_requests = build_baostock_state_plan(securities_path)
    requests: list[ProviderProbeRequest] = []
    for row in population:
        for year in range(2011, 2020):
            requests.append(
                ProviderProbeRequest(
                    request_id=(
                        f"baostock_dividend_{row['ts_code'].replace('.', '_')}_{year}"
                    ),
                    provider="baostock",
                    endpoint="dividend_reconciliation",
                    method="BAOSTOCK",
                    url=(
                        "baostock://public-api.baostock.com/dividend"
                        f"?code={row['provider_code']}&year={year}"
                    ),
                    disposition="provider_cannot_prove",
                    evidence_semantics="raw_custom_socket_response_plus_locked_parser",
                    expected_terminal_states=("positive", "empty"),
                    required_checks=(
                        "provider_success",
                        "raw_wire_captured",
                        "terminal_marker_complete",
                        "pagination_terminal_unambiguous",
                        "row_width_matches_fields",
                        "historical_revision_timestamp_absent",
                        "provider_code_matches_request",
                    ),
                    metadata={
                        "case": "dividend",
                        "ts_code": row["ts_code"],
                        "provider_code": row["provider_code"],
                        "report_year": year,
                    },
                )
            )
    return population, requests


def normalize_security_snapshots(
    run_root: Path,
    requests: Sequence[ProviderProbeRequest],
    terminal: Mapping[str, Mapping[str, Any]],
) -> Sequence[NormalizedArtifact]:
    """Replay full-market provider snapshots without adjudicating code aliases."""

    _assert_security_snapshot_request_closure(requests)
    output = run_root / "normalized"
    output.mkdir(exist_ok=True)
    rows_path = output / "security_snapshots.jsonl"
    coverage_path = output / "security_snapshot_coverage.jsonl"
    conflicts_path = output / "conflicts.jsonl"
    conflicts: list[dict[str, Any]] = []
    record_count = 0
    coverage_count = 0
    package_parser_loss_request_count = 0
    package_parser_loss_row_count = 0
    package_parser_loss_cell_count = 0
    observed_query_dates: set[str] = set()
    if set(terminal) != {request.request_id for request in requests}:
        raise ValueError("baostock_security_snapshot_terminal_closure_invalid")
    with rows_path.open("wb") as rows_handle, coverage_path.open(
        "wb"
    ) as coverage_handle:
        for request in requests:
            try:
                query_date = _security_snapshot_query_date(request)
            except ValueError as exc:
                conflicts.append(
                    {
                        "request_id": request.request_id,
                        "reason": str(exc),
                    }
                )
                continue
            if query_date in observed_query_dates:
                conflicts.append(
                    {
                        "request_id": request.request_id,
                        "reason": "snapshot_query_date_duplicate",
                        "snapshot_query_date": query_date,
                    }
                )
                continue
            observed_query_dates.add(query_date)
            receipt = terminal[request.request_id]
            wrapper = read_json(
                run_root / str(receipt["raw_envelope_relative_path"])
            )
            try:
                raw_payload = base64.b64decode(
                    str(wrapper.get("raw_payload_base64") or ""), validate=True
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "baostock_security_snapshot_raw_payload_invalid"
                ) from exc
            source_payload_sha256 = hashlib.sha256(raw_payload).hexdigest()
            if (
                receipt.get("terminal_state") != "positive"
                or wrapper.get("request_id") != request.request_id
                or wrapper.get("raw_payload_sha256") != source_payload_sha256
            ):
                conflicts.append(
                    {
                        "request_id": request.request_id,
                        "reason": "snapshot_source_binding_invalid",
                    }
                )
                continue
            try:
                fields, items, parser_diagnostics = (
                    _baostock_logical_rows_with_reconciliation(raw_payload)
                )
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                conflicts.append(
                    {
                        "request_id": request.request_id,
                        "reason": "snapshot_wire_decode_invalid",
                        "detail": str(exc),
                    }
                )
                continue
            if fields != ["code", "tradeStatus", "code_name"]:
                conflicts.append(
                    {
                        "request_id": request.request_id,
                        "reason": "snapshot_schema_mismatch",
                        "fields": fields,
                    }
                )
                continue
            parser_loss_detected = bool(
                parser_diagnostics["package_parser_loss_detected"]
            )
            parser_loss_rows = int(
                parser_diagnostics["package_parser_loss_row_count"]
            )
            parser_loss_cells = int(
                parser_diagnostics["package_parser_loss_cell_count"]
            )
            if parser_loss_detected:
                package_parser_loss_request_count += 1
                package_parser_loss_row_count += parser_loss_rows
                package_parser_loss_cell_count += parser_loss_cells
            source_request_semantic_hash = canonical_hash(request.semantic())
            normalized_rows: list[dict[str, Any]] = []
            provider_codes: set[str] = set()
            row_conflict: dict[str, Any] | None = None
            for item in items:
                try:
                    provider_code, ts_code, trade_status, provider_name = (
                        _strict_security_snapshot_row(item)
                    )
                except ValueError as exc:
                    row_conflict = {
                        "request_id": request.request_id,
                        "reason": str(exc),
                        "row": list(item),
                    }
                    break
                if provider_code in provider_codes:
                    row_conflict = {
                        "request_id": request.request_id,
                        "reason": "snapshot_provider_code_duplicate",
                        "provider_code": provider_code,
                    }
                    break
                provider_codes.add(provider_code)
                normalized_rows.append(
                    {
                        "snapshot_query_date": query_date,
                        "provider_code": provider_code,
                        "ts_code": ts_code,
                        "trade_status": trade_status,
                        "provider_code_name": provider_name,
                        "provider_code_name_pit_proven": False,
                        "alias_adjudicated": False,
                        "usage": "provider_reconciliation_only",
                        "source_request_id": request.request_id,
                        "source_request_semantic_hash": (
                            source_request_semantic_hash
                        ),
                        "source_payload_sha256": source_payload_sha256,
                    }
                )
            if row_conflict is not None:
                conflicts.append(row_conflict)
                continue
            if not normalized_rows:
                conflicts.append(
                    {
                        "request_id": request.request_id,
                        "reason": "snapshot_rows_empty",
                    }
                )
                continue
            for row in sorted(
                normalized_rows, key=lambda value: str(value["provider_code"])
            ):
                _write_row(rows_handle, row)
                record_count += 1
            _write_row(
                coverage_handle,
                {
                    "snapshot_query_date": query_date,
                    "terminal_state": "positive",
                    "returned_count": len(normalized_rows),
                    "source_request_id": request.request_id,
                    "source_request_semantic_hash": source_request_semantic_hash,
                    "source_payload_sha256": source_payload_sha256,
                    "package_parser_loss_detected": parser_loss_detected,
                    "package_parser_loss_row_count": parser_loss_rows,
                    "package_parser_loss_cell_count": parser_loss_cells,
                },
            )
            coverage_count += 1
        for handle in (rows_handle, coverage_handle):
            handle.flush()
            os.fsync(handle.fileno())
    _atomic_jsonl(conflicts_path, conflicts)
    if conflicts:
        raise ValueError(
            f"baostock_security_snapshot_normalization_invalid:"
            f"{conflicts[0]['reason']}"
        )
    manifest_path = output / "normalized_manifest.json"
    manifest = {
        "schema_version": "baostock_security_snapshot_normalization_v1",
        "snapshot_count": coverage_count,
        "record_count": record_count,
        "conflict_count": 0,
        "rows_sha256": sha256_file(rows_path),
        "coverage_sha256": sha256_file(coverage_path),
        "admission_ready": False,
        "usage": "provider_reconciliation_only",
        "provider_code_name_pit_proven": False,
        "alias_adjudicated": False,
        "raw_market_data_rewritten": False,
        "authoritative_value_source": "raw_wire_response_record",
        "package_parser_usage": "reconciliation_only",
        "package_parser_semantics": "baostock_0_9_3_setData_split_join",
        "package_parser_loss_request_count": (
            package_parser_loss_request_count
        ),
        "package_parser_loss_row_count": package_parser_loss_row_count,
        "package_parser_loss_cell_count": package_parser_loss_cell_count,
        "blockers": [
            "provider_code_name_is_not_pit_evidence",
            "historical_provider_code_aliases_not_adjudicated",
            "snapshot_cannot_rewrite_or_rename_archived_market_rows",
        ],
    }
    manifest["content_hash"] = canonical_hash(manifest)
    _atomic_json(manifest_path, manifest)
    return (
        NormalizedArtifact(
            "security_snapshot_reconciliation",
            "normalized/security_snapshots.jsonl",
            record_count,
        ),
        NormalizedArtifact(
            "security_snapshot_coverage",
            "normalized/security_snapshot_coverage.jsonl",
            coverage_count,
        ),
        NormalizedArtifact("conflicts", "normalized/conflicts.jsonl", 0),
        NormalizedArtifact(
            "normalized_manifest", "normalized/normalized_manifest.json", 1
        ),
    )


def _assert_security_snapshot_request_closure(
    requests: Sequence[ProviderProbeRequest],
) -> list[str]:
    if len(requests) != SECURITY_SNAPSHOT_REQUEST_COUNT:
        raise ValueError("baostock_security_snapshot_request_count_invalid")
    dates = [_security_snapshot_query_date(request) for request in requests]
    if (
        dates[:1] != [SECURITY_SNAPSHOT_SEED_DATE]
        or dates[1:] != sorted(dates[1:])
        or len(dates) != len(set(dates))
        or len(dates[1:]) != SECURITY_SNAPSHOT_OPEN_DATE_COUNT
        or canonical_hash(dates[1:])
        != SECURITY_SNAPSHOT_APPROVED_OPEN_DATE_ROOT
        or canonical_hash(dates)
        != SECURITY_SNAPSHOT_APPROVED_POPULATION_ROOT
    ):
        raise ValueError("baostock_security_snapshot_request_closure_invalid")
    return dates


def _security_snapshot_query_date(request: ProviderProbeRequest) -> str:
    query_date = str(request.metadata.get("snapshot_query_date") or "")
    if not _strict_compact_date(query_date):
        raise ValueError("snapshot_query_date_invalid")
    parsed = urllib.parse.urlsplit(request.url)
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    expected_date = f"{query_date[:4]}-{query_date[4:6]}-{query_date[6:]}"
    if (
        request.provider != "baostock"
        or request.method.upper() != "BAOSTOCK"
        or request.endpoint != "security_snapshot_reconciliation"
        or request.metadata.get("case") != "all_stock"
        or request.metadata.get("provider_code_name_pit_proven") is not False
        or request.metadata.get("alias_adjudicated") is not False
        or request.metadata.get("usage") != "provider_reconciliation_only"
        or request.request_id != f"baostock_security_snapshot_{query_date}"
        or parsed.scheme != "baostock"
        or parsed.netloc != "public-api.baostock.com"
        or parsed.hostname != "public-api.baostock.com"
        or parsed.path != "/all_stock"
        or bool(parsed.fragment)
        or query != {"date": [expected_date]}
    ):
        raise ValueError("snapshot_query_date_binding_invalid")
    return query_date


def _strict_compact_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y%m%d")
    except (TypeError, ValueError):
        return False
    return len(value) == 8


def _strict_security_snapshot_row(
    item: Sequence[Any],
) -> tuple[str, str, str, str]:
    if len(item) != 3 or any(not str(value).strip() for value in item):
        raise ValueError("snapshot_row_value_empty_or_width_invalid")
    provider_code, trade_status, provider_name = (
        str(item[0]),
        str(item[1]),
        str(item[2]),
    )
    if not re.fullmatch(r"(?:sh|sz)\.[0-9]{6}", provider_code):
        raise ValueError("snapshot_provider_code_invalid")
    if trade_status not in {"0", "1"}:
        raise ValueError("snapshot_trade_status_invalid")
    return (
        provider_code,
        _from_baostock_code(provider_code),
        trade_status,
        provider_name,
    )


def normalize_adjustments(
    run_root: Path,
    requests: Sequence[ProviderProbeRequest],
    terminal: Mapping[str, Mapping[str, Any]],
) -> Sequence[NormalizedArtifact]:
    return _normalize_tabular(
        run_root,
        requests,
        terminal,
        role="adjustment_factor_reconciliation",
        fields=(
            "code",
            "dividOperateDate",
            "foreAdjustFactor",
            "backAdjustFactor",
            "adjustFactor",
        ),
        output_name="adjustment_factor_reconciliation.jsonl",
        blockers=("historical_adjustment_revision_timestamp_unavailable",),
    )


def normalize_security_basic(
    run_root: Path,
    requests: Sequence[ProviderProbeRequest],
    terminal: Mapping[str, Mapping[str, Any]],
) -> Sequence[NormalizedArtifact]:
    return _normalize_tabular(
        run_root,
        requests,
        terminal,
        role="security_basic_reconciliation",
        fields=("code", "code_name", "ipoDate", "outDate", "type", "status"),
        output_name="security_basic_reconciliation.jsonl",
        blockers=(
            "historical_name_change_timeline_unavailable",
            "query_returns_current_aggregate_not_pit_versions",
        ),
    )


def normalize_index_daily(
    run_root: Path,
    requests: Sequence[ProviderProbeRequest],
    terminal: Mapping[str, Mapping[str, Any]],
) -> Sequence[NormalizedArtifact]:
    return _normalize_tabular(
        run_root,
        requests,
        terminal,
        role="index_daily_bars_reconciliation",
        fields=tuple(BAOSTOCK_FIELDS.split(",")),
        output_name="index_daily_bars_reconciliation.jsonl",
        blockers=("formal_index_bar_coverage_use_not_bound",),
    )


def normalize_turnover(
    run_root: Path,
    requests: Sequence[ProviderProbeRequest],
    terminal: Mapping[str, Mapping[str, Any]],
) -> Sequence[NormalizedArtifact]:
    return _normalize_tabular(
        run_root,
        requests,
        terminal,
        role="daily_turnover_reconciliation",
        fields=("date", "code", "turn"),
        output_name="daily_turnover_reconciliation.jsonl",
        blockers=(
            "volume_ratio_unavailable",
            "historical_total_market_value_unavailable",
            "formal_daily_basic_coverage_use_not_bound",
        ),
    )


def normalize_dividends(
    run_root: Path,
    requests: Sequence[ProviderProbeRequest],
    terminal: Mapping[str, Mapping[str, Any]],
) -> Sequence[NormalizedArtifact]:
    return _normalize_tabular(
        run_root,
        requests,
        terminal,
        role="dividend_reconciliation",
        fields=(
            "code",
            "dividPreNoticeDate",
            "dividAgmPumDate",
            "dividPlanAnnounceDate",
            "dividPlanDate",
            "dividRegistDate",
            "dividOperateDate",
            "dividPayDate",
            "dividStockMarketDate",
            "dividCashPsBeforeTax",
            "dividCashPsAfterTax",
            "dividStocksPs",
            "dividCashStock",
            "dividReserveToStockPs",
        ),
        output_name="dividend_reconciliation.jsonl",
        blockers=("dividend_event_version_history_unavailable",),
    )


def normalize_hs300_snapshots(
    run_root: Path,
    requests: Sequence[ProviderProbeRequest],
    terminal: Mapping[str, Mapping[str, Any]],
) -> Sequence[NormalizedArtifact]:
    output = run_root / "normalized"
    output.mkdir(exist_ok=True)
    rows_path = output / "hs300_snapshots.jsonl"
    conflicts_path = output / "conflicts.jsonl"
    count = 0
    conflicts: list[dict[str, Any]] = []
    with rows_path.open("wb") as handle:
        for request in requests:
            wrapper = read_json(
                run_root / str(terminal[request.request_id]["raw_envelope_relative_path"])
            )
            fields, items = _baostock_logical_rows(
                base64.b64decode(wrapper["raw_payload_base64"], validate=True)
            )
            if fields != ["updateDate", "code", "code_name"] or len(items) != 300:
                conflicts.append(
                    {
                        "request_id": request.request_id,
                        "reason": "hs300_snapshot_shape_invalid",
                        "fields": fields,
                        "row_count": len(items),
                    }
                )
                continue
            query_date = str(request.metadata["snapshot_query_date"])
            members: set[str] = set()
            for item in items:
                ts_code = _from_baostock_code(str(item[1]))
                members.add(ts_code)
                _write_row(
                    handle,
                    {
                        "index_code": "000300.SH",
                        "snapshot_query_date": query_date,
                        "provider_update_date": str(item[0]).replace("-", ""),
                        "ts_code": ts_code,
                        "provider_code_name": item[2],
                        "weight": None,
                        "publication_time_proven": False,
                        "source_request_id": request.request_id,
                        "source_payload_sha256": wrapper["raw_payload_sha256"],
                    },
                )
                count += 1
            if len(members) != 300:
                conflicts.append(
                    {
                        "request_id": request.request_id,
                        "reason": "hs300_snapshot_member_duplicate",
                    }
                )
        handle.flush()
        os.fsync(handle.fileno())
    _atomic_jsonl(conflicts_path, conflicts)
    if conflicts:
        raise ValueError(f"hs300_snapshot_normalization_invalid:{conflicts[0]['reason']}")
    manifest_path = output / "normalized_manifest.json"
    manifest = {
        "schema_version": "baostock_hs300_snapshot_normalization_v1",
        "snapshot_count": len(requests),
        "record_count": count,
        "conflict_count": 0,
        "rows_sha256": sha256_file(rows_path),
        "pit_publication_proven": False,
        "weight_available": False,
        "blockers": [
            "snapshot_update_date_is_not_publication_time",
            "historical_weight_unavailable",
        ],
    }
    manifest["content_hash"] = canonical_hash(manifest)
    _atomic_json(manifest_path, manifest)
    return (
        NormalizedArtifact("hs300_snapshot_reconciliation", "normalized/hs300_snapshots.jsonl", count),
        NormalizedArtifact("conflicts", "normalized/conflicts.jsonl", 0),
        NormalizedArtifact("normalized_manifest", "normalized/normalized_manifest.json", 1),
    )


def _normalize_tabular(
    run_root: Path,
    requests: Sequence[ProviderProbeRequest],
    terminal: Mapping[str, Mapping[str, Any]],
    *,
    role: str,
    fields: Sequence[str],
    output_name: str,
    blockers: Sequence[str],
) -> Sequence[NormalizedArtifact]:
    output = run_root / "normalized"
    output.mkdir(exist_ok=True)
    rows_path = output / output_name
    coverage_path = output / "coverage.jsonl"
    conflicts_path = output / "conflicts.jsonl"
    count = 0
    coverage_count = 0
    conflicts: list[dict[str, Any]] = []
    with rows_path.open("wb") as rows_handle, coverage_path.open("wb") as coverage_handle:
        for request in requests:
            receipt = terminal[request.request_id]
            wrapper = read_json(run_root / str(receipt["raw_envelope_relative_path"]))
            observed_fields, items = _baostock_logical_rows(
                base64.b64decode(wrapper["raw_payload_base64"], validate=True)
            )
            if observed_fields != list(fields):
                conflicts.append(
                    {
                        "request_id": request.request_id,
                        "reason": "schema_mismatch",
                        "fields": observed_fields,
                    }
                )
                continue
            expected_provider_code = str(request.metadata.get("provider_code") or "")
            if expected_provider_code and "code" in observed_fields:
                code_index = observed_fields.index("code")
                mismatched_codes = sorted(
                    {
                        (
                            str(item[code_index])
                            if len(item) > code_index
                            else "<missing>"
                        )
                        for item in items
                        if len(item) <= code_index
                        or str(item[code_index]) != expected_provider_code
                    }
                )
                if not expected_provider_code or mismatched_codes:
                    conflicts.append(
                        {
                            "request_id": request.request_id,
                            "reason": "provider_code_mismatch",
                            "expected_provider_code": expected_provider_code,
                            "observed_provider_codes": mismatched_codes,
                        }
                    )
                    continue
            for item in items:
                row = dict(zip(fields, item))
                row["ts_code"] = _from_baostock_code(str(row.pop("code")))
                row["source_request_id"] = request.request_id
                row["source_payload_sha256"] = wrapper["raw_payload_sha256"]
                row["historical_revision_proven"] = False
                _write_row(rows_handle, row)
                count += 1
            _write_row(
                coverage_handle,
                {
                    "request_id": request.request_id,
                    "ts_code": request.metadata.get("ts_code"),
                    "report_year": request.metadata.get("report_year"),
                    "terminal_state": receipt["terminal_state"],
                    "returned_count": len(items),
                    "source_payload_sha256": wrapper["raw_payload_sha256"],
                },
            )
            coverage_count += 1
        for handle in (rows_handle, coverage_handle):
            handle.flush()
            os.fsync(handle.fileno())
    _atomic_jsonl(conflicts_path, conflicts)
    if conflicts:
        raise ValueError(f"baostock_reconciliation_invalid:{conflicts[0]['reason']}")
    manifest_path = output / "normalized_manifest.json"
    manifest = {
        "schema_version": "baostock_reconciliation_normalization_v1",
        "role": role,
        "record_count": count,
        "coverage_count": coverage_count,
        "rows_sha256": sha256_file(rows_path),
        "coverage_sha256": sha256_file(coverage_path),
        "admission_ready": False,
        "blockers": list(blockers),
    }
    manifest["content_hash"] = canonical_hash(manifest)
    _atomic_json(manifest_path, manifest)
    return (
        NormalizedArtifact(role, f"normalized/{output_name}", count),
        NormalizedArtifact("coverage", "normalized/coverage.jsonl", coverage_count),
        NormalizedArtifact("conflicts", "normalized/conflicts.jsonl", 0),
        NormalizedArtifact("normalized_manifest", "normalized/normalized_manifest.json", 1),
    )


def _adapter_identity(
    phase: str,
    *,
    implementation_root: str | None = None,
    wire_protocol_root: str | None = None,
) -> dict[str, str]:
    return {
        "adapter": f"baostock_{phase}_reconciliation_capture_v2",
        "authorization_policy": BAOSTOCK_AUTHORIZATION_POLICY,
        "baostock_distribution": "0.9.3",
        "baostock_client": "00.9.30",
        "calendar_source_sha256": BAOSTOCK_CALENDAR_SOURCE_SHA256,
        "compressed_trailer_semantics": BAOSTOCK_COMPRESSED_TRAILER_SEMANTICS,
        "implementation_root": implementation_root or _implementation_root(),
        "pagination_contract_root": canonical_hash(
            BAOSTOCK_PAGINATION_CONTRACT
        ),
        "securities_source_sha256": BAOSTOCK_SECURITIES_SOURCE_SHA256,
        "wire_protocol_root": wire_protocol_root or baostock_wire_protocol_root(),
    }


def _contract(
    *,
    phase: str,
    output_root: Path,
    signer: PersistentReceiptSigner,
    population_root: str,
    request_count: int,
    delay: float,
    timeout: float,
    retries: int,
    permission_context_id: str,
    securities_path: str | Path = SECURITIES_PATH,
    calendar_path: str | Path = CALENDAR_PATH,
) -> FreeProviderBackfillContract:
    _approved_source_file_hashes(
        securities_path=securities_path,
        calendar_path=calendar_path,
    )
    if phase == "security-snapshots" and (
        request_count != SECURITY_SNAPSHOT_REQUEST_COUNT
        or population_root != SECURITY_SNAPSHOT_APPROVED_POPULATION_ROOT
    ):
        raise ValueError("baostock_security_snapshot_contract_closure_invalid")
    request_start = (
        "20110101"
        if phase
        in {
            "dividends",
            "hs300-snapshots",
            "security-snapshots",
            "turnover",
        }
        else "20120101"
    )
    return FreeProviderBackfillContract(
        activity_name=f"free_domestic_baostock_{phase}_2012_2019_v1",
        provider="baostock",
        output_root=output_root,
        permission_context_id=permission_context_id,
        population_root=population_root,
        capture_public_key_sha256=_public_key_hash(signer.public_key_pem),
        capture_public_key_pem_b64=base64.b64encode(signer.public_key_pem).decode(),
        scope_start="20120101",
        scope_end="20191231",
        request_start=request_start,
        request_end="20191231",
        allowed_hosts=("public-api.baostock.com",),
        budget=BackfillResourceBudget(
            max_requests=request_count * (retries + 1),
            max_wire_exchanges=request_count * 2 * (retries + 1),
            max_response_bytes=64 * 1024 * 1024,
            max_total_response_bytes=16 * 1024 * 1024 * 1024,
            timeout_seconds=timeout,
            minimum_delay_seconds=delay,
            max_retries=retries,
        ),
        adapter_identity=_adapter_identity(phase),
        source_profile_id=BAOSTOCK_SOURCE_PROFILE_ID,
    )


def _phase_definition(phase: str) -> dict[str, Any]:
    definitions: dict[str, dict[str, Any]] = {
        "adjustments": {
            "normalizer": normalize_adjustments,
            "roles": (
                "adjustment_factor_reconciliation",
                "coverage",
                "conflicts",
                "normalized_manifest",
            ),
            "blockers": (
                "historical_adjustment_revision_timestamp_unavailable",
                "company_action_causal_chain_unproven",
            ),
        },
        "hs300-snapshots": {
            "normalizer": normalize_hs300_snapshots,
            "roles": (
                "hs300_snapshot_reconciliation",
                "conflicts",
                "normalized_manifest",
            ),
            "blockers": (
                "snapshot_update_date_is_not_publication_time",
                "historical_weight_unavailable",
            ),
        },
        "dividends": {
            "normalizer": normalize_dividends,
            "roles": (
                "dividend_reconciliation",
                "coverage",
                "conflicts",
                "normalized_manifest",
            ),
            "blockers": ("dividend_event_version_history_unavailable",),
        },
        "security-basic": {
            "normalizer": normalize_security_basic,
            "roles": (
                "security_basic_reconciliation",
                "coverage",
                "conflicts",
                "normalized_manifest",
            ),
            "blockers": (
                "historical_name_change_timeline_unavailable",
                "query_returns_current_aggregate_not_pit_versions",
            ),
        },
        "security-snapshots": {
            "normalizer": normalize_security_snapshots,
            "roles": (
                "security_snapshot_reconciliation",
                "security_snapshot_coverage",
                "conflicts",
                "normalized_manifest",
            ),
            "blockers": (
                "provider_code_name_is_not_pit_evidence",
                "historical_provider_code_aliases_not_adjudicated",
                "st_and_suspension_authoritative_absence_proof_unavailable",
            ),
        },
        "index-daily": {
            "normalizer": normalize_index_daily,
            "roles": (
                "index_daily_bars_reconciliation",
                "coverage",
                "conflicts",
                "normalized_manifest",
            ),
            "blockers": ("formal_index_bar_coverage_use_not_bound",),
        },
        "turnover": {
            "normalizer": normalize_turnover,
            "roles": (
                "daily_turnover_reconciliation",
                "coverage",
                "conflicts",
                "normalized_manifest",
            ),
            "blockers": (
                "volume_ratio_unavailable",
                "historical_total_market_value_unavailable",
                "formal_daily_basic_coverage_use_not_bound",
            ),
        },
    }
    try:
        definition = dict(definitions[phase])
        baseline = dict(_BAOSTOCK_PHASE_BASELINES[phase])
    except KeyError as exc:
        raise ValueError("baostock_reconciliation_phase_invalid") from exc
    if phase == "security-snapshots":
        baseline.update(
            {
                "population_count": SECURITY_SNAPSHOT_REQUEST_COUNT,
                "population_root": SECURITY_SNAPSHOT_APPROVED_POPULATION_ROOT,
                "request_count": SECURITY_SNAPSHOT_REQUEST_COUNT,
                "request_plan_hash": (
                    SECURITY_SNAPSHOT_APPROVED_REQUEST_PLAN_HASH
                ),
            }
        )
    definition.update(baseline)
    definition["request_start"] = (
        "20110101"
        if phase
        in {
            "dividends",
            "hs300-snapshots",
            "security-snapshots",
            "turnover",
        }
        else "20120101"
    )
    return definition


def validate_baostock_reconciliation_capture(
    path: str | Path,
    *,
    expected_phase: str,
    require_current_replay_compatible: bool = True,
) -> dict[str, Any]:
    """Validate one authorized phase and replay every normalized byte."""

    definition = _phase_definition(expected_phase)
    capture = validate_free_provider_backfill(path)
    if capture.get("publication_signature_verified") is not True:
        raise ValueError(
            "baostock_reconciliation_publication_signature_required"
        )
    if (
        capture.get("status") != "succeeded"
        or capture.get("raw_capture_replay_eligible") is not True
    ):
        raise ValueError("baostock_reconciliation_capture_incomplete")
    root = Path(str(capture["manifest_path"])).parent
    contract = read_json(root / "activity_contract.json")
    plan = read_json(root / "request_plan.json")
    expected_activity_name = (
        f"free_domestic_baostock_{expected_phase}_2012_2019_v1"
    )
    if contract.get("activity_name") != expected_activity_name:
        raise ValueError("baostock_reconciliation_phase_mismatch")
    if (
        contract.get("capture_public_key_sha256")
        != BAOSTOCK_APPROVED_CAPTURE_KEY_SHA256
    ):
        raise ValueError("baostock_reconciliation_capture_key_unauthorized")
    adapter = contract.get("adapter_identity")
    if not isinstance(adapter, Mapping):
        raise ValueError("baostock_reconciliation_adapter_identity_invalid")
    current_root = _implementation_root()
    current_replay_compatible = (
        adapter.get("implementation_root") == current_root
        and adapter.get("wire_protocol_root") == baostock_wire_protocol_root()
    )
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
    request_count = int(definition["request_count"])
    expected_budget = {
        "max_requests": request_count * 3,
        "max_wire_exchanges": request_count * 6,
        "max_response_bytes": 64 * 1024 * 1024,
        "max_total_response_bytes": 16 * 1024 * 1024 * 1024,
        "timeout_seconds": 30.0,
        "minimum_delay_seconds": 1.0,
        "max_retries": 2,
    }
    expected_scope = {
        "date_start": "20120101",
        "date_end": "20191231",
        "request_start": definition["request_start"],
        "request_end": "20191231",
    }
    expected_output_namespace = canonical_hash(
        str((SCOPE_ROOT / expected_phase.replace("-", "_")).resolve())
    )
    observed_implementation_root = str(adapter.get("implementation_root") or "")
    observed_wire_protocol_root = str(adapter.get("wire_protocol_root") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", observed_implementation_root) or not (
        re.fullmatch(r"[0-9a-f]{64}", observed_wire_protocol_root)
    ):
        raise ValueError("baostock_reconciliation_adapter_identity_invalid")
    expected_adapter = _adapter_identity(
        expected_phase,
        implementation_root=observed_implementation_root,
        wire_protocol_root=observed_wire_protocol_root,
    )
    safety = contract.get("safety")
    if (
        set(contract) != expected_contract_keys
        or contract.get("schema_version")
        != "free_provider_backfill_contract_v2"
        or contract.get("provider") != "baostock"
        or contract.get("output_namespace_id") != expected_output_namespace
        or contract.get("permission_context_id") != PERMISSION_CONTEXT
        or contract.get("population_root") != definition["population_root"]
        or contract.get("scope") != expected_scope
        or contract.get("allowed_hosts") != ["public-api.baostock.com"]
        or contract.get("budget") != expected_budget
        or dict(adapter) != expected_adapter
        or contract.get("source_profile_id") != BAOSTOCK_SOURCE_PROFILE_ID
        or contract.get("mode") != "signed_raw_provider_capture"
        or contract.get("capture_before_normalization") is not True
        or contract.get("old_lake_mutated") is not False
        or not isinstance(safety, Mapping)
        or not safety
        or any(value is not False for value in safety.values())
    ):
        raise ValueError("baostock_reconciliation_contract_closure_invalid")
    requests = plan.get("requests")
    if (
        set(plan) != {"schema_version", "request_plan_hash", "requests"}
        or plan.get("schema_version")
        != "free_provider_backfill_request_plan_v1"
        or not isinstance(requests, list)
        or len(requests) != request_count
        or plan.get("request_plan_hash") != definition["request_plan_hash"]
        or canonical_hash(requests) != definition["request_plan_hash"]
        or capture.get("request_plan_hash") != definition["request_plan_hash"]
        or capture.get("request_count") != request_count
        or any(
            row.get("provider") != "baostock"
            or urllib.parse.urlsplit(str(row.get("url") or "")).hostname
            != "public-api.baostock.com"
            for row in requests
        )
    ):
        raise ValueError("baostock_reconciliation_request_plan_closure_invalid")

    required_roles = tuple(definition["roles"])
    artifacts = capture.get("normalized_artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("baostock_reconciliation_artifact_closure_invalid")
    artifact_by_role = {
        str(artifact.get("role") or ""): artifact for artifact in artifacts
    }
    if (
        len(artifact_by_role) != len(artifacts)
        or set(artifact_by_role) != set(required_roles)
        or any(
            set(artifact)
            != {
                "role",
                "relative_path",
                "record_count",
                "sha256",
                "size_bytes",
            }
            for artifact in artifacts
        )
    ):
        raise ValueError("baostock_reconciliation_artifact_closure_invalid")
    if any(
        artifact_by_role[role].get("record_count")
        != (
            1
            if role == "normalized_manifest"
            else len(
                (
                    root
                    / str(artifact_by_role[role]["relative_path"])
                ).read_bytes().splitlines()
            )
        )
        for role in required_roles
    ):
        raise ValueError("baostock_reconciliation_artifact_closure_invalid")
    base_result = {
        "schema_version": "baostock_reconciliation_validation_v1",
        "phase": expected_phase,
        "content_hash": capture["content_hash"],
        "signed_integrity_verified": True,
        "publication_signature_verified": True,
        "approved_capture_key_verified": True,
        "operator_capture_contract_authorized": current_replay_compatible,
        "provider_origin_attested": False,
        "capture_runtime_isolation_verified": False,
        "provider_trailer_integrity_for_compressed_responses": "unverified",
        "zlib_stream_checksum_verified_for_compressed_responses": True,
        "current_replay_compatible": current_replay_compatible,
        "data_admission_eligible": False,
        "downstream_ineligible": True,
    }
    if not current_replay_compatible:
        if require_current_replay_compatible:
            raise ValueError(
                "baostock_reconciliation_current_replay_incompatible"
            )
        return base_result | {
            "phase_contract_verified": True,
            "historical_contract_closure_verified": True,
            "normalized_replay_identical": False,
            "population_count": definition["population_count"],
            "population_root": definition["population_root"],
            "request_count": request_count,
            "request_plan_hash": definition["request_plan_hash"],
            "qualification": "quarantined_historical_integrity_only",
            "blockers": [
                "current_replay_implementation_identity_mismatch",
                "operator_capture_contract_not_currently_authorized",
                "provider_origin_not_attested",
                "capture_runtime_isolation_not_attested",
            ],
        }
    replayed, replay_root = replay_normalized_artifacts(
        capture["manifest_path"],
        normalizer=definition["normalizer"],
        required_roles=required_roles,
    )
    if any(
        replayed[role]
        != (root / str(artifact_by_role[role]["relative_path"])).read_bytes()
        for role in required_roles
    ):
        raise ValueError("baostock_reconciliation_replay_mismatch")
    return base_result | {
        "phase_contract_verified": True,
        "normalized_replay_identical": True,
        "normalized_replay_root": replay_root,
        "population_count": definition["population_count"],
        "population_root": definition["population_root"],
        "request_count": request_count,
        "request_plan_hash": definition["request_plan_hash"],
        "qualification": "quarantined_reconciliation_only",
        "blockers": [
            "provider_origin_not_attested",
            "capture_runtime_isolation_not_attested",
            *definition["blockers"],
        ],
    }


def _implementation_root() -> str:
    shared_capture_source = inspect.getsourcefile(run_free_provider_backfill)
    provider_probe_source = inspect.getsourcefile(BaostockProbeTransport)
    if not shared_capture_source or not provider_probe_source:
        raise ValueError("baostock_reconciliation_source_identity_unavailable")
    return canonical_hash(
        {
            "reconciliation_module_sha256": sha256_file(Path(__file__)),
            "shared_capture_module_sha256": sha256_file(
                Path(shared_capture_source)
            ),
            "provider_probe_module_sha256": sha256_file(
                Path(provider_probe_source)
            ),
            "adapter_identity": inspect.getsource(_adapter_identity),
            "contract_builder": inspect.getsource(_contract),
            "phase_definition": inspect.getsource(_phase_definition),
            "specialized_validator": inspect.getsource(
                validate_baostock_reconciliation_capture
            ),
            "approved_source_file_hashes": inspect.getsource(
                _approved_source_file_hashes
            ),
            "governance_policy": {
                "approved_capture_key_sha256": (
                    BAOSTOCK_APPROVED_CAPTURE_KEY_SHA256
                ),
                "source_profile_id": BAOSTOCK_SOURCE_PROFILE_ID,
                "permission_context": PERMISSION_CONTEXT,
                "authorization_policy": BAOSTOCK_AUTHORIZATION_POLICY,
                "securities_source_sha256": (
                    BAOSTOCK_SECURITIES_SOURCE_SHA256
                ),
                "calendar_source_sha256": BAOSTOCK_CALENDAR_SOURCE_SHA256,
                "compressed_trailer_semantics": (
                    BAOSTOCK_COMPRESSED_TRAILER_SEMANTICS
                ),
                "pagination_contract": BAOSTOCK_PAGINATION_CONTRACT,
                "transient_transport_error_map": (
                    BAOSTOCK_TRANSIENT_TRANSPORT_ERROR_MAP
                ),
                "phase_baselines": _BAOSTOCK_PHASE_BASELINES,
            },
            "adjustment_plan": inspect.getsource(build_adjustment_plan),
            "security_basic_plan": inspect.getsource(build_security_basic_plan),
            "security_snapshot_plan": inspect.getsource(
                build_security_snapshot_plan
            ),
            "security_snapshot_contract": {
                "seed_date": SECURITY_SNAPSHOT_SEED_DATE,
                "approved_open_date_root": (
                    SECURITY_SNAPSHOT_APPROVED_OPEN_DATE_ROOT
                ),
                "approved_population_root": (
                    SECURITY_SNAPSHOT_APPROVED_POPULATION_ROOT
                ),
                "open_date_count": SECURITY_SNAPSHOT_OPEN_DATE_COUNT,
                "request_count": SECURITY_SNAPSHOT_REQUEST_COUNT,
            },
            "index_daily_plan": inspect.getsource(build_index_daily_plan),
            "turnover_plan": inspect.getsource(build_turnover_plan),
            "hs300_plan": inspect.getsource(build_hs300_snapshot_plan),
            "dividend_plan": inspect.getsource(build_dividend_plan),
            "tabular_normalizer": inspect.getsource(_normalize_tabular),
            "hs300_normalizer": inspect.getsource(normalize_hs300_snapshots),
            "security_basic_normalizer": inspect.getsource(normalize_security_basic),
            "security_snapshot_normalizer": inspect.getsource(
                normalize_security_snapshots
            ),
            "security_snapshot_query_binding": inspect.getsource(
                _security_snapshot_query_date
            ),
            "security_snapshot_request_closure": inspect.getsource(
                _assert_security_snapshot_request_closure
            ),
            "security_snapshot_row_parser": inspect.getsource(
                _strict_security_snapshot_row
            ),
            "strict_compact_date": inspect.getsource(_strict_compact_date),
            "index_daily_normalizer": inspect.getsource(normalize_index_daily),
            "turnover_normalizer": inspect.getsource(normalize_turnover),
            "provider_transport": inspect.getsource(BaostockProbeTransport),
            "recovering_transport": inspect.getsource(RecoveringBaostockTransport),
            "bounded_reconciliation_transport": inspect.getsource(
                BoundedBaostockReconciliationTransport
            ),
            "wire_decoder": inspect.getsource(_baostock_logical_rows),
            "wire_protocol_root": baostock_wire_protocol_root(),
            "provider_code_converter": inspect.getsource(_from_baostock_code),
            "read_jsonl": inspect.getsource(_read_jsonl),
            "write_row": inspect.getsource(_write_row),
            "atomic_json": inspect.getsource(_atomic_json),
            "atomic_jsonl": inspect.getsource(_atomic_jsonl),
            "baostock_distribution_record_root": baostock_distribution_record_root(),
        }
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Baostock reconciliation capture.")
    parser.add_argument(
        "--phase",
        choices=(
            "adjustments",
            "hs300-snapshots",
            "dividends",
            "security-basic",
            "security-snapshots",
            "index-daily",
            "turnover",
        ),
        required=True,
    )
    parser.add_argument("--securities-path", default=str(SECURITIES_PATH))
    parser.add_argument("--calendar-path", default=str(CALENDAR_PATH))
    parser.add_argument("--security-code", action="append")
    parser.add_argument("--permission-context-id", default=PERMISSION_CONTEXT)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--validate")
    parser.add_argument("--minimum-delay-seconds", type=float, default=1.0)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--pretty", action="store_true")
    return parser


def _approved_source_file_hashes(
    *, securities_path: str | Path, calendar_path: str | Path
) -> dict[str, str]:
    return {
        "securities": _approved_source_file_hash(
            securities_path,
            expected_sha256=BAOSTOCK_SECURITIES_SOURCE_SHA256,
            role="securities",
        ),
        "calendar": _approved_source_file_hash(
            calendar_path,
            expected_sha256=BAOSTOCK_CALENDAR_SOURCE_SHA256,
            role="calendar",
        ),
    }


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.validate:
        try:
            payload = validate_baostock_reconciliation_capture(
                args.validate, expected_phase=args.phase
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(
                _render(
                    {
                        "schema_version": (
                            "baostock_reconciliation_validation_error_v1"
                        ),
                        "phase": args.phase,
                        "status": "blocked",
                        "reason": str(exc),
                        "data_admission_eligible": False,
                        "downstream_ineligible": True,
                    },
                    pretty=args.pretty,
                )
            )
            return 2
        print(_render(payload, pretty=args.pretty))
        return 0
    if args.security_code:
        print(
            _render(
                {
                    "schema_version": "baostock_reconciliation_policy_block_v1",
                    "phase": args.phase,
                    "status": "blocked",
                    "reason": "governed_phase_subset_forbidden",
                    "data_admission_eligible": False,
                    "downstream_ineligible": True,
                },
                pretty=args.pretty,
            )
        )
        return 2
    if (
        args.permission_context_id != PERMISSION_CONTEXT
        or args.minimum_delay_seconds != 1.0
        or args.timeout_seconds != 30.0
        or args.max_retries != 2
    ):
        print(
            _render(
                {
                    "schema_version": "baostock_reconciliation_policy_block_v1",
                    "phase": args.phase,
                    "status": "blocked",
                    "reason": "governed_phase_runtime_policy_drift",
                    "data_admission_eligible": False,
                    "downstream_ineligible": True,
                },
                pretty=args.pretty,
            )
        )
        return 2
    try:
        _approved_source_file_hashes(
            securities_path=args.securities_path,
            calendar_path=args.calendar_path,
        )
    except ValueError as exc:
        print(
            _render(
                {
                    "schema_version": "baostock_reconciliation_policy_block_v1",
                    "phase": args.phase,
                    "status": "blocked",
                    "reason": str(exc),
                    "data_admission_eligible": False,
                    "downstream_ineligible": True,
                },
                pretty=args.pretty,
            )
        )
        return 2
    if args.phase == "adjustments":
        population, requests = build_adjustment_plan(args.securities_path)
        normalizer = normalize_adjustments
    elif args.phase == "hs300-snapshots":
        population, requests = build_hs300_snapshot_plan(args.calendar_path)
        normalizer = normalize_hs300_snapshots
    elif args.phase == "security-basic":
        population, requests = build_security_basic_plan(
            args.securities_path, include_codes=args.security_code
        )
        normalizer = normalize_security_basic
    elif args.phase == "security-snapshots":
        population, requests = build_security_snapshot_plan(args.calendar_path)
        normalizer = normalize_security_snapshots
    elif args.phase == "index-daily":
        population, requests = build_index_daily_plan()
        normalizer = normalize_index_daily
    elif args.phase == "turnover":
        population, requests = build_turnover_plan(
            args.securities_path, include_codes=args.security_code
        )
        normalizer = normalize_turnover
    else:
        population, requests = build_dividend_plan(args.securities_path)
        normalizer = normalize_dividends
    population_root = canonical_hash(population)
    preview = {
        "schema_version": "baostock_reconciliation_plan_preview_v1",
        "phase": args.phase,
        "population_count": len(population),
        "population_root": population_root,
        "request_count": len(requests),
        "request_plan_hash": canonical_hash([request.semantic() for request in requests]),
        "network_called": False,
    }
    definition = _phase_definition(args.phase)
    if (
        preview["population_count"] != definition["population_count"]
        or preview["population_root"] != definition["population_root"]
        or preview["request_count"] != definition["request_count"]
        or preview["request_plan_hash"] != definition["request_plan_hash"]
    ):
        print(
            _render(
                preview
                | {
                    "status": "blocked",
                    "reason": "baostock_reconciliation_plan_policy_mismatch",
                    "data_admission_eligible": False,
                    "downstream_ineligible": True,
                },
                pretty=args.pretty,
            )
        )
        return 2
    if args.plan_only and not CAPTURE_KEY.is_file():
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
    signer = PersistentReceiptSigner.load(CAPTURE_KEY)
    if _public_key_hash(signer.public_key_pem) != (
        BAOSTOCK_APPROVED_CAPTURE_KEY_SHA256
    ):
        print(
            _render(
                preview
                | {
                    "status": "blocked",
                    "reason": "baostock_reconciliation_capture_key_unauthorized",
                    "data_admission_eligible": False,
                    "downstream_ineligible": True,
                },
                pretty=args.pretty,
            )
        )
        return 2
    output = SCOPE_ROOT / args.phase.replace("-", "_")
    contract = _contract(
        phase=args.phase,
        output_root=output,
        signer=signer,
        population_root=population_root,
        request_count=len(requests),
        delay=args.minimum_delay_seconds,
        timeout=args.timeout_seconds,
        retries=args.max_retries,
        permission_context_id=args.permission_context_id,
        securities_path=args.securities_path,
        calendar_path=args.calendar_path,
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
    transport = BoundedBaostockReconciliationTransport()
    try:
        result = run_free_provider_backfill(
            contract,
            requests,
            transport=transport,
            signer=signer,
            normalizer=normalizer,
            runtime_implementation_root=_implementation_root(),
        )
    finally:
        transport.close()
    if result.get("status") == "succeeded":
        validation = validate_baostock_reconciliation_capture(
            result["manifest_path"], expected_phase=args.phase
        )
        result = result | {"specialized_validation": validation}
    print(_render(result, pretty=args.pretty))
    return 0 if result.get("status") == "succeeded" else 1


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

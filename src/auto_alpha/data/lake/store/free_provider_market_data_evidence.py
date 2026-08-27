"""Provider-neutral market-data evidence derived from signed provider captures.

This module closes a deliberately narrow seam for the first research profile:
CSI300 index daily bars.  It independently replays the signed Baostock raw
capture, projects only the provider-neutral fields approved by the profile,
checks index-day coverage and value validity, and freezes the benchmark
consumer closure.  The result is evidence for a later Source Freeze; it is
never itself a Data Admission Verdict.
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import inspect
import json
import os
import sqlite3
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from auto_alpha.data.ingestion.pipeline.ashare.free_provider_backfill import (
    _baostock_logical_rows,
    _baostock_implementation_root,
    _from_baostock_code,
    baostock_wire_protocol_root,
    normalize_baostock_state_capture,
    replay_normalized_artifacts,
    validate_free_provider_backfill,
)
from auto_alpha.data.ingestion.pipeline.ashare.free_provider_baostock_reconciliation import (
    normalize_index_daily,
    validate_baostock_reconciliation_capture,
)
from auto_alpha.data.pit.engine.security_master import (
    _identity_derivation_implementation_root,
    validate_security_identity_lifecycle_intervals,
)
from auto_alpha.platform.artifacts.storage import (
    canonical_hash,
    atomic_json,
    publish_generation,
    publish_prepared_generation,
    read_json,
    sha256_file,
    validate_generation,
)

from .admission import first_data_admission_profile


SCHEMA_VERSION = "free_provider_market_data_evidence_v2"
TRADE_CALENDAR_SCHEMA_VERSION = "free_provider_trade_calendar_evidence_v2"
DAILY_BARS_SCHEMA_VERSION = "free_provider_daily_bars_evidence_v3"
MANIFEST_NAME = "free_provider_market_data_evidence.json"
GENERATION_PREFIX = "free_provider_market_data_evidence"
DATASET = "index_daily_bars"
INDEX_CODE = "000300.SH"
CANONICAL_ROWS_NAME = "index_daily_bars.jsonl"
VALIDITY_ROWS_NAME = "index_daily_bars_validity.jsonl"
COVERAGE_GAPS_NAME = "coverage_gaps.jsonl"
INDEX_SOURCE_ROWS_NAME = "source_index_daily_bars.jsonl"
INDEX_SOURCE_CALENDAR_NAME = "source_index_trade_calendar.jsonl"
CANONICAL_FIELDS = (
    "index_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "volume",
    "amount",
)
TRADE_CALENDAR_FIELDS = (
    "exchange",
    "trade_date",
    "is_open",
    "prev_trade_date",
)
DAILY_BARS_FIELDS = (
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "volume",
    "amount",
)
TRADE_CALENDAR_ROWS_NAME = "trade_calendar.jsonl"
TRADE_CALENDAR_VALIDITY_NAME = "trade_calendar_validity.jsonl"
TRADE_CALENDAR_GAPS_NAME = "trade_calendar_coverage_gaps.jsonl"
DAILY_BARS_ROWS_NAME = "daily_bars.jsonl"
DAILY_BARS_VALIDITY_NAME = "daily_bars_validity.jsonl"
DAILY_BARS_GAPS_NAME = "daily_bars_coverage_gaps.jsonl"
DAILY_BARS_SOURCE_ROWS_NAME = "source_provider_daily_bars.jsonl"
DAILY_BARS_SOURCE_CALENDAR_NAME = "source_trade_calendar.jsonl"
DAILY_BARS_SOURCE_CONFLICTS_NAME = "source_normalizer_conflicts.jsonl"
DAILY_BARS_SOURCE_IDENTITY_INTERVALS_NAME = "source_identity_intervals.jsonl"
DAILY_BARS_SOURCE_IDENTITY_BINDING_NAME = "source_identity_binding.json"
DAILY_BARS_BATCH_ROW_LIMIT = 10_000
DAILY_BARS_SQLITE_CACHE_MIB = 64
DAILY_BARS_SQLITE_SPILL_LIMIT_BYTES = 32 * 1024 * 1024 * 1024
DAILY_BARS_RESUME_SCHEMA_VERSION = "daily_bars_sqlite_resume_v3"
DAILY_BARS_REPLAY_CHECKPOINT_SCHEMA_VERSION = (
    "daily_bars_market_replay_checkpoint_v1"
)
DAILY_BARS_OUTPUT_CHECKPOINT_SCHEMA_VERSION = "daily_bars_output_checkpoint_v2"
DAILY_BARS_OUTPUT_CHECKPOINT_NAME = "output_checkpoint.json"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
NORMALIZED_REPLAY_BLOCKER_VALUES = ("current_parser_replay_failed",)
SAFETY_FLAGS = (
    "data_admission_eligible",
    "profile_activation_authorized",
    "alpha_search_authorized",
    "holdout_activation_authorized",
    "paper_trading_authorized",
    "shadow_trading_authorized",
    "live_trading_authorized",
)
_MARKET_MANIFEST_COMMON_FIELDS = frozenset(
    {
        "schema_version",
        "dataset",
        "profile_id",
        "scope",
        "source_binding",
        "provider_neutral_projection",
        "coverage",
        "coverage_gaps_sha256",
        "coverage_gaps_size_bytes",
        "coverage_gaps_root",
        "validity",
        "validity_rows_sha256",
        "validity_rows_size_bytes",
        "validity_rows_root",
        "consumer_closure",
        "technical_evidence_status",
        "formal_data_admission_ready",
        "blockers",
        "safety",
        "content_hash",
        "generation_id",
        "manifest_path",
    }
)
_MARKET_MANIFEST_FIELDS = {
    DATASET: _MARKET_MANIFEST_COMMON_FIELDS,
    "trade_calendar": _MARKET_MANIFEST_COMMON_FIELDS | {"pit_axis"},
    "daily_bars": _MARKET_MANIFEST_COMMON_FIELDS
    | {"pit_axis", "resource_execution"},
}
_MARKET_SCOPE_FIELDS = frozenset(
    {"access_view", "date_start", "date_end", "as_of_market_date"}
)
_MARKET_PROJECTION_FIELDS = frozenset(
    {
        "dataset",
        "record_count",
        "canonical_rows_sha256",
        "canonical_rows_size_bytes",
        "canonical_rows_root",
    }
)
_INDEX_PROJECTION_FIELDS = _MARKET_PROJECTION_FIELDS | {
    "provider",
    "provider_role",
    "index_code",
}
_MARKET_VALIDITY_FIELDS = frozenset(
    {
        "valid_row_count",
        "invalid_row_count",
        "required_field_count",
        "all_required_values_valid",
    }
)
_DAILY_DISK_VALIDITY_FIELDS = _MARKET_VALIDITY_FIELDS | {
    "not_applicable_candidate_count",
    "not_applicable_authority",
    "not_applicable_authority_status",
}
_MARKET_CONSUMER_FIELDS = frozenset(
    {
        "approved_fields",
        "consumer_roles",
        "formula_input_authorized",
        "profile_contract_exact",
    }
)
_TRADE_CALENDAR_PIT_AXIS_FIELDS = frozenset(
    {
        "exchanges",
        "calendar_day_count",
        "open_trade_date_count",
        "open_trade_dates_root",
        "axis_semantics",
    }
)
_DAILY_MEMORY_PIT_AXIS_FIELDS = frozenset(
    {
        "security_count",
        "security_lifecycle_root",
        "exchange_open_dates_root",
        "axis_semantics",
    }
)
_DAILY_DISK_PIT_AXIS_FIELDS = frozenset(
    {
        "security_count",
        "identity_timeline_binding",
        "identity_timeline_axis_complete",
        "exchange_open_dates_root",
        "expected_security_day_root",
        "expected_axis_binding_root",
        "axis_semantics",
    }
)
_IDENTITY_TIMELINE_BINDING_FIELDS = frozenset(
    {
        "schema_version",
        "identity_timeline_content_hash",
        "identity_timeline_derivation_content_hash",
        "identity_timeline_derivation_implementation_root",
        "identity_timeline_rows_root",
        "identity_timeline_rows_root_semantics",
        "identity_timeline_daily_row_count",
        "identity_timeline_intervals_root",
        "identity_timeline_manifest_sha256",
        "current_state_fallback_used",
        "independent_admission_verdict_required",
    }
)
_DAILY_MEMORY_RESOURCE_FIELDS = frozenset(
    {"engine", "input_mode", "output_mode", "resume_supported"}
)
_DAILY_DISK_RESOURCE_FIELDS = frozenset(
    {
        "engine",
        "input_mode",
        "output_mode",
        "batch_row_limit",
        "sqlite_cache_limit_mib",
        "sqlite_spill_limit_bytes",
        "sqlite_mmap_bytes",
        "work_identity",
        "resume_schema_version",
        "resume_implementation_root",
        "checkpoint_granularity",
        "checkpoint_input_prefix_sha256",
        "checkpoint_projected_rows_sha256",
        "checkpoint_static_axis_sha256",
        "checkpoint_joined_state_sha256",
        "source_binding_root",
        "expected_axis_binding_root",
        "resume_supported",
    }
)
_INDEX_SOURCE_ARCHIVE_FIELDS = frozenset(
    {
        "archived_index_replay_sha256",
        "archived_index_replay_size_bytes",
        "archived_index_calendar_sha256",
        "archived_index_calendar_size_bytes",
        "independent_signed_capture_proof_archived",
        "independent_source_reference_resolution_required",
    }
)
_INDEX_SOURCE_DIAGNOSTIC_FIELDS = _INDEX_SOURCE_ARCHIVE_FIELDS | {
    "capture_generation_id",
    "capture_content_hash",
    "normalized_replay_root",
    "calendar_source_sha256",
    "operator_capture_contract_authorized",
    "provider_origin_attested",
    "capture_runtime_isolation_verified",
}
_INDEX_SOURCE_PRODUCTION_FIELDS = _INDEX_SOURCE_DIAGNOSTIC_FIELDS | {
    "capture_manifest_sha256",
    "capture_contract_id",
    "request_plan_hash",
    "publication_signature_verified",
    "published_normalized_identical",
    "calendar_source_contract_sha256",
    "calendar_source_binding_verified",
    "capture_qualification",
    "capture_qualification_blockers",
}
_MARKET_SOURCE_DIAGNOSTIC_FIELDS = frozenset(
    {
        "capture_source_profile_id",
        "capture_scope",
        "capture_generation_id",
        "capture_content_hash",
        "capture_contract_id",
        "request_plan_hash",
        "publication_signature_verified",
        "wire_replay_verified",
        "parser_replay_verified",
        "normalized_replay_root",
        "normalized_replay_blockers",
        "published_normalized_identical",
        "operator_capture_contract_authorized",
        "provider_origin_attested",
        "capture_runtime_isolation_verified",
        "capture_adapter_implementation_root",
        "current_capture_toolchain_implementation_root",
        "capture_toolchain_implementation_match",
        "normalizer_conflicts_bound",
        "normalizer_conflict_count",
        "normalizer_conflicts_root",
    }
)
_MARKET_SOURCE_PRODUCTION_FIELDS = frozenset(
    {
        "capture_generation_id",
        "capture_source_profile_id",
        "capture_scope",
        "capture_content_hash",
        "capture_manifest_sha256",
        "capture_contract_id",
        "request_plan_hash",
        "publication_signature_verified",
        "wire_replay_verified",
        "parser_replay_verified",
        "normalized_replay_root",
        "normalized_replay_blockers",
        "parser_roles",
        "capture_adapter_implementation_root",
        "current_capture_toolchain_implementation_root",
        "capture_toolchain_implementation_match",
        "market_projection_implementation_root",
        "market_projection_schema_version",
        "normalizer_conflicts_bound",
        "normalizer_conflict_count",
        "normalizer_conflicts_root",
        "operator_capture_contract_authorized",
        "provider_origin_attested",
        "capture_runtime_isolation_verified",
        "capture_qualification",
        "capture_qualification_blockers",
    }
)
_DAILY_SOURCE_ARCHIVE_FIELDS = frozenset(
    {
        "provider_daily_bars_replay_sha256",
        "provider_daily_bars_replay_size_bytes",
        "trade_calendar_replay_sha256",
        "trade_calendar_replay_size_bytes",
        "identity_timeline_binding_root",
        "identity_intervals_archive_sha256",
        "identity_intervals_archive_size_bytes",
        "identity_binding_archive_sha256",
        "identity_binding_archive_size_bytes",
        "normalizer_conflicts_size_bytes",
        "archived_normalized_replay_root",
        "independent_signed_capture_proof_archived",
        "independent_source_reference_resolution_required",
    }
)
_DAILY_SOURCE_DIAGNOSTIC_DISK_FIELDS = (
    _MARKET_SOURCE_DIAGNOSTIC_FIELDS
    | _DAILY_SOURCE_ARCHIVE_FIELDS
    | {
        "capture_manifest_sha256",
        "parser_roles",
        "market_projection_implementation_root",
        "market_projection_schema_version",
    }
)
_DAILY_SOURCE_PRODUCTION_DISK_FIELDS = (
    _MARKET_SOURCE_PRODUCTION_FIELDS
    | _DAILY_SOURCE_ARCHIVE_FIELDS
    | {
        "capture_published_daily_bars_projection",
        "daily_bars_projection_frozen_by_this_evidence",
    }
)


@dataclass(frozen=True)
class IndexDailyBarsAssessment:
    """One deterministic provider-neutral projection and its evidence bytes."""

    semantic: dict[str, Any]
    canonical_rows: bytes
    validity_rows: bytes
    coverage_gaps: bytes
    source_replay_rows: bytes
    source_calendar_rows: bytes


@dataclass(frozen=True)
class MarketDataAssessment:
    """A provider-neutral technical assessment, never an admission verdict."""

    semantic: dict[str, Any]
    canonical_rows: bytes
    validity_rows: bytes
    coverage_gaps: bytes
    source_archive: dict[str, bytes] | None = None


def assess_trade_calendar_replay(
    replayed_rows: bytes,
    *,
    profile: Mapping[str, Any],
    date_start: str,
    date_end: str,
    source_binding: Mapping[str, Any],
    exchanges: Sequence[str] = ("SSE", "SZSE"),
) -> MarketDataAssessment:
    """Recompute the complete exchange-day axis from replayed provider bytes."""

    _require_scope(date_start, date_end, "trade_calendar")
    expected_exchanges = tuple(dict.fromkeys(str(value) for value in exchanges))
    if not expected_exchanges or any(not value for value in expected_exchanges):
        raise ValueError("trade_calendar_exchanges_invalid")
    contract, profile_contract_exact = _dataset_profile_contract(
        profile,
        dataset="trade_calendar",
        granularity="exchange_span",
        approved_fields=TRADE_CALENDAR_FIELDS,
        consumer_roles=("date_axis", "scheduling_control"),
    )
    expected_days = _date_span(date_start, date_end)
    expected_keys = {
        (exchange, trade_date)
        for exchange in expected_exchanges
        for trade_date in expected_days
    }
    canonical: list[dict[str, Any]] = []
    validity: list[dict[str, Any]] = []
    observed_counts: dict[tuple[str, str], int] = {}
    for source in _read_jsonl_bytes(replayed_rows, "trade_calendar_replay"):
        raw_date = str(source.get("trade_date") or source.get("cal_date") or "")
        if _valid_date(raw_date) and not date_start <= raw_date <= date_end:
            continue
        projected, reasons = _project_calendar_row(source, expected_exchanges)
        key = (str(projected["exchange"]), str(projected["trade_date"]))
        observed_counts[key] = observed_counts.get(key, 0) + 1
        canonical.append(projected)
        validity.append(
            {
                "exchange": projected["exchange"],
                "trade_date": projected["trade_date"],
                "valid": not reasons,
                "reasons": reasons,
            }
        )
    order = sorted(
        range(len(canonical)),
        key=lambda ordinal: (
            str(canonical[ordinal]["exchange"]),
            str(canonical[ordinal]["trade_date"]),
            ordinal,
        ),
    )
    canonical = [canonical[ordinal] for ordinal in order]
    validity = [validity[ordinal] for ordinal in order]
    duplicates = sorted(key for key, count in observed_counts.items() if count > 1)
    duplicate_keys = set(duplicates)
    previous_open: dict[str, str | None] = {
        exchange: None for exchange in expected_exchanges
    }
    for row, validity_row in zip(canonical, validity, strict=True):
        key = (str(row["exchange"]), str(row["trade_date"]))
        reasons = set(validity_row["reasons"])
        if key in duplicate_keys:
            reasons.add("duplicate_exchange_day")
        exchange, trade_date = key
        if exchange in previous_open and _valid_date(trade_date):
            previous = row.get("prev_trade_date")
            if previous_open[exchange] is not None and previous != previous_open[exchange]:
                reasons.add("prev_trade_date_chain_invalid")
            if row.get("is_open") is True:
                previous_open[exchange] = trade_date
        validity_row["reasons"] = sorted(reasons)
        validity_row["valid"] = not reasons
    observed_keys = set(observed_counts)
    missing = sorted(expected_keys - observed_keys)
    extra = sorted(observed_keys - expected_keys)
    exact_cover = not (missing or extra or duplicates)
    gaps = [] if exact_cover else [
        {
            "missing_exchange_days": _pair_rows(missing, "exchange"),
            "extra_exchange_days": _pair_rows(extra, "exchange"),
            "duplicate_exchange_days": _pair_rows(duplicates, "exchange"),
        }
    ]
    invalid_count = sum(row["valid"] is not True for row in validity)
    canonical_bytes = _jsonl_bytes(canonical)
    validity_bytes = _jsonl_bytes(validity)
    gap_bytes = _jsonl_bytes(gaps)
    blockers = _governance_blockers(profile, contract, source_binding) | {
        "source_freeze_consumer_binding_pending",
        "trade_calendar_session_authority_pending",
        "trade_calendar_independent_source_reference_resolution_pending",
    }
    if source_binding.get("published_normalized_identical") is False:
        blockers.add("trade_calendar_published_normalization_replay_mismatch")
    if source_binding.get("wire_replay_verified") is not True:
        blockers.add("trade_calendar_signed_wire_replay_unverified")
    if source_binding.get("parser_replay_verified") is not True:
        blockers.add("trade_calendar_parser_replay_unverified")
    if source_binding.get("publication_signature_verified") is not True:
        blockers.add("trade_calendar_signed_publication_unverified")
    if source_binding.get("normalizer_conflicts_bound") is not True:
        blockers.add("trade_calendar_normalizer_conflicts_unbound")
    elif source_binding.get("normalizer_conflict_count") != 0:
        blockers.add("trade_calendar_normalization_conflicts_present")
    if source_binding.get("capture_toolchain_implementation_match") is not True:
        blockers.add("trade_calendar_capture_toolchain_identity_mismatch")
    if source_binding.get("capture_source_profile_id") != profile.get("profile_id"):
        blockers.add("trade_calendar_capture_profile_binding_failed")
    if not _capture_scope_contains(source_binding, date_start, date_end):
        blockers.add("trade_calendar_capture_scope_binding_failed")
    if not profile_contract_exact:
        blockers.add("trade_calendar_profile_consumer_closure_failed")
    if not exact_cover:
        blockers.add("trade_calendar_exchange_day_exact_cover_failed")
    if invalid_count:
        blockers.add("trade_calendar_required_value_validity_failed")
    technical_blockers = _technical_blockers("trade_calendar")
    open_dates = sorted(
        {
            str(row["trade_date"])
            for row in canonical
            if row.get("is_open") is True
            and row.get("exchange") in expected_exchanges
            and _valid_date(str(row.get("trade_date") or ""))
        }
    )
    semantic = _market_semantic(
        schema=TRADE_CALENDAR_SCHEMA_VERSION,
        dataset="trade_calendar",
        profile=profile,
        date_start=date_start,
        date_end=date_end,
        source_binding=source_binding,
        canonical=canonical,
        canonical_bytes=canonical_bytes,
        validity=validity,
        validity_bytes=validity_bytes,
        gaps=gaps,
        gap_bytes=gap_bytes,
        approved_fields=TRADE_CALENDAR_FIELDS,
        consumer_roles=("date_axis", "scheduling_control"),
        profile_contract_exact=profile_contract_exact,
        coverage={
            "expected_exchange_day_count": len(expected_keys),
            "observed_exchange_day_count": len(observed_keys & expected_keys),
            "missing_exchange_day_count": len(missing),
            "extra_exchange_day_count": len(extra),
            "duplicate_exchange_day_count": len(duplicates),
            "provisional_exact_cover": exact_cover,
        },
        pit_axis={
            "exchanges": list(expected_exchanges),
            "calendar_day_count": len(expected_days),
            "open_trade_date_count": len(open_dates),
            "open_trade_dates_root": canonical_hash(open_dates),
            "axis_semantics": "exchange_calendar_day_with_previous_open_chain",
        },
        blockers=blockers,
        technical_blockers=technical_blockers,
    )
    return MarketDataAssessment(semantic, canonical_bytes, validity_bytes, gap_bytes)


def assess_daily_bars_replay(
    replayed_rows: bytes,
    calendar_rows: bytes,
    lifecycle_rows: bytes,
    *,
    profile: Mapping[str, Any],
    date_start: str,
    date_end: str,
    source_binding: Mapping[str, Any],
) -> MarketDataAssessment:
    """Verify OHLCV and security-day exact cover on immutable PIT axes."""

    _require_scope(date_start, date_end, "daily_bars")
    contract, profile_contract_exact = _dataset_profile_contract(
        profile,
        dataset="daily_bars",
        granularity="security_day",
        approved_fields=DAILY_BARS_FIELDS,
        consumer_roles=("formula_input", "target", "execution", "capacity"),
    )
    lifecycles, lifecycle_invalid = _lifecycles(lifecycle_rows)
    expected_exchanges = sorted(
        {str(row["exchange"]) for row in lifecycles.values()}
    )
    open_dates, calendar_invalid = _open_dates_by_exchange(
        calendar_rows,
        date_start=date_start,
        date_end=date_end,
        expected_exchanges=expected_exchanges,
    )
    expected_by_security: dict[str, set[str]] = {}
    expected_count = 0
    for code, row in lifecycles.items():
        dates = {
            date
            for date in open_dates.get(str(row["exchange"]), set())
            if str(row["list_date"]) <= date
            and (not row.get("delist_date") or date < str(row["delist_date"]))
        }
        expected_by_security[code] = dates
        expected_count += len(dates)
    canonical: list[dict[str, str]] = []
    validity: list[dict[str, Any]] = []
    observed_by_security: dict[str, dict[str, int]] = {}
    for source in _read_jsonl_bytes(replayed_rows, "daily_bars_replay"):
        raw_date = str(source.get("trade_date") or source.get("date") or "").replace("-", "")
        if _valid_date(raw_date) and not date_start <= raw_date <= date_end:
            continue
        projected, reasons = _project_daily_bar(source)
        code, trade_date = projected["ts_code"], projected["trade_date"]
        counts = observed_by_security.setdefault(code, {})
        counts[trade_date] = counts.get(trade_date, 0) + 1
        canonical.append(projected)
        validity.append(
            {
                "ts_code": code,
                "trade_date": trade_date,
                "valid": not reasons,
                "reasons": reasons,
            }
        )
    order = sorted(
        range(len(canonical)),
        key=lambda ordinal: (
            canonical[ordinal]["ts_code"],
            canonical[ordinal]["trade_date"],
            ordinal,
        ),
    )
    canonical = [canonical[ordinal] for ordinal in order]
    validity = [validity[ordinal] for ordinal in order]
    missing_count = extra_count = duplicate_count = observed_expected_count = 0
    gaps: list[dict[str, Any]] = []
    all_codes = sorted(set(expected_by_security) | set(observed_by_security))
    for code in all_codes:
        expected = expected_by_security.get(code, set())
        counts = observed_by_security.get(code, {})
        observed = set(counts)
        missing = sorted(expected - observed)
        extra = sorted(observed - expected)
        duplicates = sorted(date for date, count in counts.items() if count > 1)
        missing_count += len(missing)
        extra_count += len(extra)
        duplicate_count += len(duplicates)
        observed_expected_count += len(observed & expected)
        if missing or extra or duplicates:
            gaps.append(
                {
                    "ts_code": code,
                    "missing_trade_dates": missing,
                    "extra_trade_dates": extra,
                    "duplicate_trade_dates": duplicates,
                }
            )
    duplicate_keys = {
        (code, date)
        for code, counts in observed_by_security.items()
        for date, count in counts.items()
        if count > 1
    }
    for row in validity:
        if (str(row["ts_code"]), str(row["trade_date"])) in duplicate_keys:
            row["reasons"] = sorted({*row["reasons"], "duplicate_security_day"})
            row["valid"] = False
    exact_cover = not (missing_count or extra_count or duplicate_count)
    invalid_count = sum(row["valid"] is not True for row in validity)
    canonical_bytes = _jsonl_bytes(canonical)
    validity_bytes = _jsonl_bytes(validity)
    gap_bytes = _jsonl_bytes(gaps)
    blockers = _governance_blockers(profile, contract, source_binding) | {
        "daily_bars_independent_source_reference_resolution_pending",
        "source_freeze_consumer_binding_pending",
        "trade_calendar_data_admission_pending",
        "securities_data_admission_pending",
        "adjustment_factors_data_admission_pending",
        "execution_control_data_admission_pending",
        "daily_bars_resource_mode_in_memory_diagnostic_only",
    }
    if source_binding.get("published_normalized_identical") is False:
        blockers.add("daily_bars_published_normalization_replay_mismatch")
    if source_binding.get("wire_replay_verified") is not True:
        blockers.add("daily_bars_signed_wire_replay_unverified")
    if source_binding.get("parser_replay_verified") is not True:
        blockers.add("daily_bars_parser_replay_unverified")
    if source_binding.get("publication_signature_verified") is not True:
        blockers.add("daily_bars_signed_publication_unverified")
    if source_binding.get("normalizer_conflicts_bound") is not True:
        blockers.add("daily_bars_normalizer_conflicts_unbound")
    elif source_binding.get("normalizer_conflict_count") != 0:
        blockers.add("daily_bars_normalization_conflicts_present")
    if source_binding.get("capture_toolchain_implementation_match") is not True:
        blockers.add("daily_bars_capture_toolchain_identity_mismatch")
    if source_binding.get("capture_source_profile_id") != profile.get("profile_id"):
        blockers.add("daily_bars_capture_profile_binding_failed")
    if not _capture_scope_contains(source_binding, date_start, date_end):
        blockers.add("daily_bars_capture_scope_binding_failed")
    if not profile_contract_exact:
        blockers.add("daily_bars_profile_consumer_closure_failed")
    if calendar_invalid:
        blockers.add("daily_bars_calendar_axis_invalid")
    if not exact_cover:
        blockers.add("daily_bars_security_day_exact_cover_failed")
    if invalid_count:
        blockers.add("daily_bars_required_value_validity_failed")
    semantic = _market_semantic(
        schema=DAILY_BARS_SCHEMA_VERSION,
        dataset="daily_bars",
        profile=profile,
        date_start=date_start,
        date_end=date_end,
        source_binding=source_binding,
        canonical=canonical,
        canonical_bytes=canonical_bytes,
        validity=validity,
        validity_bytes=validity_bytes,
        gaps=gaps,
        gap_bytes=gap_bytes,
        approved_fields=DAILY_BARS_FIELDS,
        consumer_roles=("formula_input", "target", "execution", "capacity"),
        profile_contract_exact=profile_contract_exact,
        coverage={
            "expected_security_day_count": expected_count,
            "observed_security_day_count": observed_expected_count,
            "missing_security_day_count": missing_count,
            "extra_security_day_count": extra_count,
            "duplicate_security_day_count": duplicate_count,
            "provisional_exact_cover": exact_cover,
        },
        pit_axis={
            "security_count": len(lifecycles),
            "security_lifecycle_root": canonical_hash(
                [lifecycles[code] for code in sorted(lifecycles)]
            ),
            "exchange_open_dates_root": canonical_hash(
                {
                    exchange: sorted(dates)
                    for exchange, dates in sorted(open_dates.items())
                }
            ),
            "axis_semantics": "security_lifecycle_intersection_exchange_open_day",
        },
        blockers=blockers,
        technical_blockers=_technical_blockers("daily_bars"),
    )
    return MarketDataAssessment(semantic, canonical_bytes, validity_bytes, gap_bytes)


def assess_index_daily_bars_replay(
    replayed_rows: bytes,
    calendar_rows: bytes,
    *,
    profile: Mapping[str, Any],
    date_start: str,
    date_end: str,
    source_binding: Mapping[str, Any],
) -> IndexDailyBarsAssessment:
    """Assess independently replayed rows against the first profile contract."""

    _require_date(date_start, "date_start")
    _require_date(date_end, "date_end")
    if date_start > date_end:
        raise ValueError("index_daily_bars_scope_invalid")

    contract, profile_contract_exact = _profile_contract(profile)
    archived_replay_rows = _jsonl_bytes(
        _read_jsonl_bytes(replayed_rows, "index_daily_bars_replay")
    )
    archived_calendar_rows = _jsonl_bytes(
        _read_jsonl_bytes(calendar_rows, "index_daily_bars_calendar")
    )
    source_binding = dict(source_binding) | {
        "archived_index_replay_sha256": hashlib.sha256(
            archived_replay_rows
        ).hexdigest(),
        "archived_index_replay_size_bytes": len(archived_replay_rows),
        "archived_index_calendar_sha256": hashlib.sha256(
            archived_calendar_rows
        ).hexdigest(),
        "archived_index_calendar_size_bytes": len(archived_calendar_rows),
        "independent_signed_capture_proof_archived": False,
        "independent_source_reference_resolution_required": True,
    }
    expected_dates, calendar_duplicates, calendar_invalid = _calendar_dates(
        calendar_rows,
        date_start=date_start,
        date_end=date_end,
    )
    provider_rows = _read_jsonl_bytes(replayed_rows, "index_daily_bars_replay")
    canonical: list[dict[str, str]] = []
    validity: list[dict[str, Any]] = []
    observed_dates: list[str] = []
    for source in provider_rows:
        projected, reasons = _project_row(source)
        canonical.append(projected)
        observed_dates.append(projected["trade_date"])
        validity.append(
            {
                "index_code": projected["index_code"],
                "trade_date": projected["trade_date"],
                "valid": not reasons,
                "reasons": reasons,
            }
        )

    order = sorted(
        range(len(canonical)),
        key=lambda ordinal: (
            canonical[ordinal]["trade_date"],
            canonical[ordinal]["index_code"],
            ordinal,
        ),
    )
    canonical = [canonical[ordinal] for ordinal in order]
    validity = [validity[ordinal] for ordinal in order]
    observed_counts: dict[str, int] = {}
    for trade_date in observed_dates:
        observed_counts[trade_date] = observed_counts.get(trade_date, 0) + 1
    observed_set = set(observed_counts)
    missing = sorted(expected_dates - observed_set)
    extra = sorted(observed_set - expected_dates)
    duplicates = sorted(
        trade_date for trade_date, count in observed_counts.items() if count > 1
    )
    if duplicates:
        for row in validity:
            if row["trade_date"] in duplicates:
                row["valid"] = False
                row["reasons"] = sorted(
                    {*row["reasons"], "duplicate_index_day"}
                )

    invalid_count = sum(row["valid"] is not True for row in validity)
    exact_cover = not (
        missing
        or extra
        or duplicates
        or calendar_duplicates
        or calendar_invalid
    )
    gaps = []
    if not exact_cover:
        gaps.append(
            {
                "index_code": INDEX_CODE,
                "missing_trade_dates": missing,
                "extra_trade_dates": extra,
                "duplicate_trade_dates": duplicates,
            }
        )

    canonical_bytes = _jsonl_bytes(canonical)
    validity_bytes = _jsonl_bytes(validity)
    gap_bytes = _jsonl_bytes(gaps)
    blockers: set[str] = {
        "index_daily_bars_independent_source_reference_resolution_pending",
        "source_freeze_consumer_binding_pending",
        "trade_calendar_data_admission_pending",
    }
    if profile.get("activation_status") != "active":
        blockers.add("data_admission_profile_human_approval_required")
    if not contract.get("acquisition_contracts"):
        blockers.add("provider_acquisition_contract_not_activated")
    if source_binding.get("operator_capture_contract_authorized") is not True:
        blockers.add("operator_capture_contract_not_currently_authorized")
    if source_binding.get("provider_origin_attested") is not True:
        blockers.add("provider_origin_not_attested")
    if source_binding.get("capture_runtime_isolation_verified") is not True:
        blockers.add("capture_runtime_isolation_not_attested")
    if "current_replay_implementation_identity_mismatch" in set(
        source_binding.get("capture_qualification_blockers") or ()
    ):
        blockers.add("current_replay_implementation_identity_mismatch")
    if source_binding.get("calendar_source_binding_verified", True) is not True:
        blockers.add("index_daily_bars_calendar_source_binding_failed")
    if source_binding.get("published_normalized_identical", True) is not True:
        blockers.add("index_daily_bars_published_normalization_replay_mismatch")
    if not profile_contract_exact:
        blockers.add("index_daily_bars_profile_consumer_closure_failed")
    if calendar_invalid or calendar_duplicates:
        blockers.add("index_daily_bars_calendar_axis_invalid")
    if not exact_cover:
        blockers.add("index_daily_bars_index_day_exact_cover_failed")
    if invalid_count:
        blockers.add("index_daily_bars_required_value_validity_failed")

    technical_blockers = {
        "index_daily_bars_calendar_axis_invalid",
        "index_daily_bars_calendar_source_binding_failed",
        "index_daily_bars_index_day_exact_cover_failed",
        "index_daily_bars_independent_source_reference_resolution_pending",
        "index_daily_bars_profile_consumer_closure_failed",
        "index_daily_bars_published_normalization_replay_mismatch",
        "index_daily_bars_required_value_validity_failed",
    }
    semantic = {
        "schema_version": SCHEMA_VERSION,
        "dataset": DATASET,
        "profile_id": profile.get("profile_id"),
        "scope": {
            "access_view": "research",
            "date_start": date_start,
            "date_end": date_end,
            "as_of_market_date": date_end,
        },
        "source_binding": dict(source_binding),
        "provider_neutral_projection": {
            "provider": "baostock",
            "provider_role": "index_daily_bars_reconciliation",
            "dataset": DATASET,
            "index_code": INDEX_CODE,
            "record_count": len(canonical),
            "canonical_rows_sha256": hashlib.sha256(canonical_bytes).hexdigest(),
            "canonical_rows_size_bytes": len(canonical_bytes),
            "canonical_rows_root": canonical_hash(canonical),
        },
        "coverage": {
            "expected_index_day_count": len(expected_dates),
            "observed_index_day_count": len(observed_set & expected_dates),
            "missing_index_day_count": len(missing),
            "extra_index_day_count": len(extra),
            "duplicate_index_day_count": len(duplicates),
            "provisional_exact_cover": exact_cover,
        },
        "coverage_gaps_sha256": hashlib.sha256(gap_bytes).hexdigest(),
        "coverage_gaps_size_bytes": len(gap_bytes),
        "coverage_gaps_root": canonical_hash(gaps),
        "validity": {
            "valid_row_count": len(validity) - invalid_count,
            "invalid_row_count": invalid_count,
            "required_field_count": len(CANONICAL_FIELDS),
            "all_required_values_valid": invalid_count == 0,
        },
        "validity_rows_sha256": hashlib.sha256(validity_bytes).hexdigest(),
        "validity_rows_size_bytes": len(validity_bytes),
        "validity_rows_root": canonical_hash(validity),
        "consumer_closure": {
            "approved_fields": list(CANONICAL_FIELDS),
            "consumer_roles": ["benchmark_control"],
            "formula_input_authorized": False,
            "profile_contract_exact": profile_contract_exact,
        },
        "technical_evidence_status": (
            "blocked" if blockers & technical_blockers else "verified"
        ),
        "formal_data_admission_ready": False,
        "blockers": sorted(blockers),
        "safety": {name: False for name in SAFETY_FLAGS},
    }
    return IndexDailyBarsAssessment(
        semantic=semantic,
        canonical_rows=canonical_bytes,
        validity_rows=validity_bytes,
        coverage_gaps=gap_bytes,
        source_replay_rows=archived_replay_rows,
        source_calendar_rows=archived_calendar_rows,
    )


def build_index_daily_bars_evidence(
    capture: str | Path,
    calendar: str | Path,
    output_root: str | Path,
    *,
    profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Replay one signed capture and publish immutable, fail-closed evidence."""

    capture_manifest = validate_free_provider_backfill(capture)
    capture_root = Path(str(capture_manifest["manifest_path"])).parent
    qualification = validate_baostock_reconciliation_capture(
        capture_manifest["manifest_path"],
        expected_phase="index-daily",
        require_current_replay_compatible=False,
    )
    replayed, replay_root = replay_normalized_artifacts(
        capture_manifest["manifest_path"],
        normalizer=normalize_index_daily,
        required_roles=("index_daily_bars_reconciliation",),
    )
    published_artifact = next(
        (
            row
            for row in capture_manifest.get("normalized_artifacts") or ()
            if row.get("role") == "index_daily_bars_reconciliation"
        ),
        None,
    )
    if not isinstance(published_artifact, Mapping):
        raise ValueError("index_daily_bars_published_artifact_missing")
    published_path = capture_root / str(published_artifact.get("relative_path") or "")
    if not published_path.is_file() or published_path.is_symlink():
        raise ValueError("index_daily_bars_published_artifact_invalid")
    calendar_path = Path(calendar)
    if not calendar_path.is_file() or calendar_path.is_symlink():
        raise ValueError("index_daily_bars_calendar_source_invalid")
    contract = read_json(capture_root / "activity_contract.json")
    adapter = contract.get("adapter_identity") or {}
    calendar_sha256 = sha256_file(calendar_path)
    source_binding = {
        "capture_generation_id": capture_manifest["generation_id"],
        "capture_content_hash": capture_manifest["content_hash"],
        "capture_manifest_sha256": sha256_file(capture_manifest["manifest_path"]),
        "capture_contract_id": capture_manifest["contract_id"],
        "request_plan_hash": capture_manifest["request_plan_hash"],
        "publication_signature_verified": capture_manifest.get(
            "publication_signature_verified"
        )
        is True,
        "normalized_replay_root": replay_root,
        "published_normalized_identical": (
            replayed["index_daily_bars_reconciliation"]
            == published_path.read_bytes()
        ),
        "calendar_source_sha256": calendar_sha256,
        "calendar_source_contract_sha256": adapter.get("calendar_source_sha256"),
        "calendar_source_binding_verified": (
            calendar_sha256 == adapter.get("calendar_source_sha256")
        ),
        "operator_capture_contract_authorized": qualification.get(
            "operator_capture_contract_authorized"
        )
        is True,
        "provider_origin_attested": qualification.get("provider_origin_attested")
        is True,
        "capture_runtime_isolation_verified": qualification.get(
            "capture_runtime_isolation_verified"
        )
        is True,
        "capture_qualification": qualification.get("qualification"),
        "capture_qualification_blockers": qualification.get("blockers") or [],
    }
    assessment = assess_index_daily_bars_replay(
        replayed["index_daily_bars_reconciliation"],
        calendar_path.read_bytes(),
        profile=profile or first_data_admission_profile(),
        date_start="20120101",
        date_end="20191231",
        source_binding=source_binding,
    )
    return publish_index_daily_bars_assessment(assessment, output_root)


def publish_index_daily_bars_assessment(
    assessment: IndexDailyBarsAssessment,
    output_root: str | Path,
) -> dict[str, Any]:
    """Publish one assessed projection as an immutable evidence generation."""

    return publish_generation(
        output_root,
        prefix=GENERATION_PREFIX,
        manifest_name=MANIFEST_NAME,
        semantic=assessment.semantic,
        extra_files={
            CANONICAL_ROWS_NAME: assessment.canonical_rows,
            VALIDITY_ROWS_NAME: assessment.validity_rows,
            COVERAGE_GAPS_NAME: assessment.coverage_gaps,
            INDEX_SOURCE_ROWS_NAME: assessment.source_replay_rows,
            INDEX_SOURCE_CALENDAR_NAME: assessment.source_calendar_rows,
        },
    )


def validate_index_daily_bars_evidence(path: str | Path) -> dict[str, Any]:
    """Validate immutable file closure and fail-closed evidence semantics."""

    payload = validate_generation(
        path,
        schema=SCHEMA_VERSION,
        manifest_name=MANIFEST_NAME,
    )
    root = Path(str(payload["manifest_path"])).parent
    expected_files = {
        MANIFEST_NAME,
        CANONICAL_ROWS_NAME,
        VALIDITY_ROWS_NAME,
        COVERAGE_GAPS_NAME,
        INDEX_SOURCE_ROWS_NAME,
        INDEX_SOURCE_CALENDAR_NAME,
    }
    tree_exact = _market_generation_tree_exact(root, expected_files)
    rows_path = root / CANONICAL_ROWS_NAME
    validity_path = root / VALIDITY_ROWS_NAME
    gaps_path = root / COVERAGE_GAPS_NAME
    source_rows_path = root / INDEX_SOURCE_ROWS_NAME
    source_calendar_path = root / INDEX_SOURCE_CALENDAR_NAME
    try:
        replay = _deep_replay_index_daily_bars_evidence(
            rows_path,
            validity_path,
            gaps_path,
            source_rows_path=source_rows_path,
            source_calendar_path=source_calendar_path,
            payload=payload,
        )
    except (OSError, ValueError) as exc:
        raise ValueError("index_daily_bars_evidence_invalid") from exc
    rows = replay["rows"]
    validity = replay["validity"]
    gaps = replay["gaps"]
    projection = payload.get("provider_neutral_projection") or {}
    coverage = payload.get("coverage") or {}
    validity_summary = payload.get("validity") or {}
    consumer = payload.get("consumer_closure") or {}
    source_binding = payload.get("source_binding") or {}
    safety = payload.get("safety") or {}
    raw_blockers = payload.get("blockers")
    blockers = (
        set(raw_blockers)
        if type(raw_blockers) is list
        and all(type(value) is str for value in raw_blockers)
        else set()
    )
    profile_contract_exact = consumer.get("profile_contract_exact")
    technical_blockers_present = bool(blockers & _index_technical_blockers())
    if (
        not tree_exact
        or not _market_semantic_scalar_types_valid(payload)
        or projection.get("record_count") != len(rows)
        or projection.get("canonical_rows_sha256") != sha256_file(rows_path)
        or projection.get("canonical_rows_size_bytes") != rows_path.stat().st_size
        or projection.get("canonical_rows_root") != canonical_hash(rows)
        or payload.get("validity_rows_sha256") != sha256_file(validity_path)
        or payload.get("validity_rows_size_bytes") != validity_path.stat().st_size
        or payload.get("validity_rows_root") != canonical_hash(validity)
        or payload.get("coverage_gaps_sha256") != sha256_file(gaps_path)
        or payload.get("coverage_gaps_size_bytes") != gaps_path.stat().st_size
        or payload.get("coverage_gaps_root") != canonical_hash(gaps)
        or coverage != replay["coverage"]
        or validity_summary != replay["validity_summary"]
        or validity_summary.get("valid_row_count")
        + validity_summary.get("invalid_row_count")
        != len(validity)
        or coverage.get("provisional_exact_cover") is not (not gaps)
        or "index_daily_bars_independent_source_reference_resolution_pending"
        not in blockers
        or source_binding.get("independent_signed_capture_proof_archived") is not False
        or source_binding.get("independent_source_reference_resolution_required")
        is not True
        or source_binding.get("archived_index_replay_sha256")
        != sha256_file(source_rows_path)
        or source_binding.get("archived_index_replay_size_bytes")
        != source_rows_path.stat().st_size
        or source_binding.get("archived_index_calendar_sha256")
        != sha256_file(source_calendar_path)
        or source_binding.get("archived_index_calendar_size_bytes")
        != source_calendar_path.stat().st_size
        or consumer.get("approved_fields") != list(CANONICAL_FIELDS)
        or consumer.get("consumer_roles") != ["benchmark_control"]
        or consumer.get("formula_input_authorized") is not False
        or profile_contract_exact not in {True, False}
        or (
            profile_contract_exact is False
            and "index_daily_bars_profile_consumer_closure_failed" not in blockers
        )
        or payload.get("technical_evidence_status")
        != ("blocked" if technical_blockers_present else "verified")
        or payload.get("formal_data_admission_ready") is not False
        or set(safety) != set(SAFETY_FLAGS)
        or any(value is not False for value in safety.values())
    ):
        raise ValueError("index_daily_bars_evidence_invalid")
    return payload


def _deep_replay_index_daily_bars_evidence(
    rows_path: Path,
    validity_path: Path,
    gaps_path: Path,
    *,
    source_rows_path: Path,
    source_calendar_path: Path,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Reproject index OHLC and coverage from the archived normalized inputs."""

    rows = list(_iter_canonical_jsonl_path(rows_path, "index_daily_bars"))
    validity = list(
        _iter_canonical_jsonl_path(
            validity_path, "index_daily_bars_validity"
        )
    )
    gaps = list(
        _iter_canonical_jsonl_path(gaps_path, "index_daily_bars_gaps")
    )
    source_rows = list(
        _iter_canonical_jsonl_path(
            source_rows_path, "index_daily_bars_archived_replay"
        )
    )
    source_calendar = list(
        _iter_canonical_jsonl_path(
            source_calendar_path, "index_daily_bars_archived_calendar"
        )
    )
    scope = payload.get("scope") or {}
    if (
        not isinstance(scope, Mapping)
        or type(scope.get("date_start")) is not str
        or type(scope.get("date_end")) is not str
        or not _valid_date(scope["date_start"])
        or not _valid_date(scope["date_end"])
        or scope["date_start"] > scope["date_end"]
    ):
        raise ValueError("index_daily_bars_scope_invalid")
    replayed = assess_index_daily_bars_replay(
        _jsonl_bytes(source_rows),
        _jsonl_bytes(source_calendar),
        profile=first_data_admission_profile(),
        date_start=scope["date_start"],
        date_end=scope["date_end"],
        source_binding=payload.get("source_binding") or {},
    )
    if (
        replayed.canonical_rows != rows_path.read_bytes()
        or replayed.validity_rows != validity_path.read_bytes()
        or replayed.coverage_gaps != gaps_path.read_bytes()
    ):
        raise ValueError("index_daily_bars_source_projection_replay_mismatch")
    return {
        "rows": rows,
        "validity": validity,
        "gaps": gaps,
        "coverage": replayed.semantic["coverage"],
        "validity_summary": replayed.semantic["validity"],
    }


def build_trade_calendar_evidence(
    capture: str | Path,
    output_root: str | Path,
    *,
    profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Replay a signed Baostock state capture into calendar evidence."""

    capture_manifest, capture_root, contract, replayed, replay_root, replay_blockers = (
        _replay_state_capture(capture, ("trade_calendar", "conflicts"))
    )
    published = _published_artifact_bytes(capture_manifest, capture_root, "trade_calendar")
    scope = contract.get("scope") or {}
    source_binding = _signed_capture_binding(
        capture_manifest,
        contract,
        replay_root=replay_root,
        published_normalized_identical=(published == replayed["trade_calendar"]),
        parser_roles=("trade_calendar", "conflicts"),
        replay_blockers=replay_blockers,
        normalizer_conflicts=replayed["conflicts"],
    )
    assessment = assess_trade_calendar_replay(
        replayed["trade_calendar"],
        profile=profile or first_data_admission_profile(),
        date_start=str(scope.get("date_start") or ""),
        date_end=str(scope.get("date_end") or ""),
        source_binding=source_binding,
    )
    return publish_trade_calendar_assessment(assessment, output_root)


def build_daily_bars_evidence(
    capture: str | Path,
    output_root: str | Path,
    *,
    identity_timeline_evidence: str | Path | None = None,
    profile: Mapping[str, Any] | None = None,
    spill_root: str | Path | None = None,
) -> dict[str, Any]:
    """Replay and assess millions of bars with bounded RAM and safe resume."""

    if identity_timeline_evidence is None:
        raise ValueError("daily_bars_identity_timeline_evidence_required")
    identity_timeline, identity_binding = _load_identity_timeline_evidence(
        identity_timeline_evidence
    )
    capture_manifest = validate_free_provider_backfill(capture)
    capture_root = Path(str(capture_manifest["manifest_path"])).parent
    contract = read_json(capture_root / "activity_contract.json")
    if contract.get("provider") != "baostock":
        raise ValueError("market_data_capture_provider_invalid")
    output = Path(output_root)
    _durable_directory(
        output,
        error="daily_bars_resume_output_root_symlink_forbidden",
    )
    active_profile = profile or first_data_admission_profile()
    work_binding = _daily_bars_work_binding(
        capture_manifest,
        contract,
        identity_binding,
        active_profile,
    )
    work_identity = canonical_hash(work_binding)
    work_parent = (
        Path(spill_root)
        if spill_root is not None
        else output / ".daily-bars-work"
    )
    _reject_symlink_components(
        work_parent,
        error="daily_bars_resume_spill_root_symlink_forbidden",
    )
    with _daily_bars_work_lock(work_parent, work_identity):
        return _build_daily_bars_evidence_locked(
            capture_manifest=capture_manifest,
            contract=contract,
            identity_timeline=identity_timeline,
            identity_binding=identity_binding,
            profile=active_profile,
            output=output,
            work_parent=work_parent,
            work_binding=work_binding,
            work_identity=work_identity,
        )


def _build_daily_bars_evidence_locked(
    *,
    capture_manifest: Mapping[str, Any],
    contract: Mapping[str, Any],
    identity_timeline: Mapping[str, Any],
    identity_binding: Mapping[str, Any],
    profile: Mapping[str, Any],
    output: Path,
    work_parent: Path,
    work_binding: Mapping[str, Any],
    work_identity: str,
) -> dict[str, Any]:
    """Finish one full build while the caller owns the identity lock."""

    scope = contract.get("scope") or {}
    work_directory = work_parent / work_identity
    _bind_daily_bars_work_directory(
        work_directory,
        work_binding=work_binding,
        work_identity=work_identity,
    )
    replay_paths, replay_root = _resume_market_state_replay(
        capture_manifest["manifest_path"],
        work_directory=work_directory,
        work_binding=work_binding,
        work_identity=work_identity,
    )
    source_binding = _signed_capture_binding(
        capture_manifest,
        contract,
        replay_root=replay_root,
        published_normalized_identical=None,
        parser_roles=(
            "provider_daily_bars",
            "trade_calendar",
            "conflicts",
        ),
        replay_blockers=(),
        normalizer_conflicts=replay_paths["conflicts"],
    ) | {
        "capture_published_daily_bars_projection": False,
        "daily_bars_projection_frozen_by_this_evidence": True,
    }
    resumed_output = work_directory / "outputs"
    semantic = _stream_daily_bars_assessment(
        replay_paths["provider_daily_bars"],
        replay_paths["trade_calendar"],
        identity_timeline,
        resumed_output,
        profile=profile,
        date_start=str(scope.get("date_start") or ""),
        date_end=str(scope.get("date_end") or ""),
        source_binding=source_binding,
        identity_binding=identity_binding,
        spill_root=work_directory / "assessment",
        conflicts_path=replay_paths["conflicts"],
    )
    content_hash = canonical_hash(semantic)
    generation_id = f"{GENERATION_PREFIX}_{content_hash[:24]}"
    with tempfile.TemporaryDirectory(
        prefix=".daily-bars-prepared-", dir=output
    ) as prepared_parent_name:
        prepared_parent = Path(prepared_parent_name)
        working = prepared_parent / "working"
        working.mkdir()
        for name in (
            DAILY_BARS_ROWS_NAME,
            DAILY_BARS_VALIDITY_NAME,
            DAILY_BARS_GAPS_NAME,
            DAILY_BARS_SOURCE_ROWS_NAME,
            DAILY_BARS_SOURCE_CALENDAR_NAME,
            DAILY_BARS_SOURCE_CONFLICTS_NAME,
            DAILY_BARS_SOURCE_IDENTITY_INTERVALS_NAME,
            DAILY_BARS_SOURCE_IDENTITY_BINDING_NAME,
        ):
            _copy_file_fsync(resumed_output / name, working / name)
        atomic_json(
            working / MANIFEST_NAME,
            semantic
            | {
                "content_hash": content_hash,
                "generation_id": generation_id,
            },
        )
        prepared = prepared_parent / generation_id
        os.replace(working, prepared)
        _fsync_directory(prepared_parent)
        published = publish_prepared_generation(
            output,
            prepared_directory=prepared,
            manifest_name=MANIFEST_NAME,
            validator=validate_daily_bars_evidence,
            pointer_schema=f"{GENERATION_PREFIX}_pointer_v1",
            pointer_fields={"dataset": "daily_bars"},
        )
        _fsync_directory(output)
        return published


def publish_trade_calendar_assessment(
    assessment: MarketDataAssessment,
    output_root: str | Path,
) -> dict[str, Any]:
    """Publish immutable exchange-calendar evidence."""

    return _publish_market_assessment(
        assessment,
        output_root,
        dataset="trade_calendar",
        rows_name=TRADE_CALENDAR_ROWS_NAME,
        validity_name=TRADE_CALENDAR_VALIDITY_NAME,
        gaps_name=TRADE_CALENDAR_GAPS_NAME,
    )


def publish_daily_bars_assessment(
    assessment: MarketDataAssessment,
    output_root: str | Path,
) -> dict[str, Any]:
    """Publish immutable security-day OHLCV evidence."""

    return _publish_market_assessment(
        assessment,
        output_root,
        dataset="daily_bars",
        rows_name=DAILY_BARS_ROWS_NAME,
        validity_name=DAILY_BARS_VALIDITY_NAME,
        gaps_name=DAILY_BARS_GAPS_NAME,
    )


def validate_trade_calendar_evidence(path: str | Path) -> dict[str, Any]:
    """Validate all calendar evidence bytes and fail-closed semantics."""

    return _validate_market_evidence(
        path,
        dataset="trade_calendar",
        schema=TRADE_CALENDAR_SCHEMA_VERSION,
        rows_name=TRADE_CALENDAR_ROWS_NAME,
        validity_name=TRADE_CALENDAR_VALIDITY_NAME,
        gaps_name=TRADE_CALENDAR_GAPS_NAME,
        approved_fields=TRADE_CALENDAR_FIELDS,
        consumer_roles=("date_axis", "scheduling_control"),
    )


def validate_daily_bars_evidence(path: str | Path) -> dict[str, Any]:
    """Validate all daily-bar evidence bytes and fail-closed semantics."""

    return _validate_market_evidence(
        path,
        dataset="daily_bars",
        schema=DAILY_BARS_SCHEMA_VERSION,
        rows_name=DAILY_BARS_ROWS_NAME,
        validity_name=DAILY_BARS_VALIDITY_NAME,
        gaps_name=DAILY_BARS_GAPS_NAME,
        approved_fields=DAILY_BARS_FIELDS,
        consumer_roles=("formula_input", "target", "execution", "capacity"),
    )


def _market_semantic(
    *,
    schema: str,
    dataset: str,
    profile: Mapping[str, Any],
    date_start: str,
    date_end: str,
    source_binding: Mapping[str, Any],
    canonical: Sequence[Mapping[str, Any]],
    canonical_bytes: bytes,
    validity: Sequence[Mapping[str, Any]],
    validity_bytes: bytes,
    gaps: Sequence[Mapping[str, Any]],
    gap_bytes: bytes,
    approved_fields: Sequence[str],
    consumer_roles: Sequence[str],
    profile_contract_exact: bool,
    coverage: Mapping[str, Any],
    pit_axis: Mapping[str, Any],
    blockers: set[str],
    technical_blockers: set[str],
) -> dict[str, Any]:
    invalid_count = sum(row.get("valid") is not True for row in validity)
    semantic = {
        "schema_version": schema,
        "dataset": dataset,
        "profile_id": profile.get("profile_id"),
        "scope": {
            "access_view": "research",
            "date_start": date_start,
            "date_end": date_end,
            "as_of_market_date": date_end,
        },
        "source_binding": dict(source_binding),
        "provider_neutral_projection": {
            "dataset": dataset,
            "record_count": len(canonical),
            "canonical_rows_sha256": hashlib.sha256(canonical_bytes).hexdigest(),
            "canonical_rows_size_bytes": len(canonical_bytes),
            "canonical_rows_root": hashlib.sha256(canonical_bytes).hexdigest(),
        },
        "pit_axis": dict(pit_axis),
        "coverage": dict(coverage),
        "coverage_gaps_sha256": hashlib.sha256(gap_bytes).hexdigest(),
        "coverage_gaps_size_bytes": len(gap_bytes),
        "coverage_gaps_root": hashlib.sha256(gap_bytes).hexdigest(),
        "validity": {
            "valid_row_count": len(validity) - invalid_count,
            "invalid_row_count": invalid_count,
            "required_field_count": len(approved_fields),
            "all_required_values_valid": invalid_count == 0,
        },
        "validity_rows_sha256": hashlib.sha256(validity_bytes).hexdigest(),
        "validity_rows_size_bytes": len(validity_bytes),
        "validity_rows_root": hashlib.sha256(validity_bytes).hexdigest(),
        "consumer_closure": {
            "approved_fields": list(approved_fields),
            "consumer_roles": list(consumer_roles),
            "formula_input_authorized": False,
            "profile_contract_exact": profile_contract_exact,
        },
        "technical_evidence_status": (
            "blocked" if blockers & technical_blockers else "verified"
        ),
        "formal_data_admission_ready": False,
        "blockers": sorted(blockers),
        "safety": {name: False for name in SAFETY_FLAGS},
    }
    if dataset == "daily_bars":
        semantic["resource_execution"] = {
            "engine": "in_memory_assessment",
            "input_mode": "bounded_bytes",
            "output_mode": "bounded_bytes",
            "resume_supported": False,
        }
    return semantic


def _stream_daily_bars_assessment(
    replayed_rows_path: Path,
    calendar_path: Path,
    identity_timeline: Mapping[str, Any],
    output_directory: Path,
    *,
    profile: Mapping[str, Any],
    date_start: str,
    date_end: str,
    source_binding: Mapping[str, Any],
    identity_binding: Mapping[str, Any],
    spill_root: Path,
    conflicts_path: Path | None = None,
) -> dict[str, Any]:
    """Assess a full market matrix with bounded memory and disk-backed joins."""

    _require_scope(date_start, date_end, "daily_bars")
    identity_interval_rows = [
        dict(row)
        for row in identity_timeline.get("intervals") or ()
        if isinstance(row, Mapping)
    ]
    identity_intervals_bytes = _jsonl_bytes(identity_interval_rows)
    identity_binding_bytes = (
        _canonical_json_text(identity_binding).encode("utf-8") + b"\n"
    )
    normalizer_conflicts_bytes = (
        conflicts_path.read_bytes() if conflicts_path is not None else b""
    )
    archived_replay_root = canonical_hash(
        sorted(
            [
                {
                    "role": "provider_daily_bars",
                    "sha256": sha256_file(replayed_rows_path),
                    "size_bytes": replayed_rows_path.stat().st_size,
                },
                {
                    "role": "trade_calendar",
                    "sha256": sha256_file(calendar_path),
                    "size_bytes": calendar_path.stat().st_size,
                },
                {
                    "role": "conflicts",
                    "sha256": hashlib.sha256(
                        normalizer_conflicts_bytes
                    ).hexdigest(),
                    "size_bytes": len(normalizer_conflicts_bytes),
                },
            ],
            key=lambda row: row["role"],
        )
    )
    source_binding = dict(source_binding) | {
        "provider_daily_bars_replay_sha256": sha256_file(replayed_rows_path),
        "provider_daily_bars_replay_size_bytes": replayed_rows_path.stat().st_size,
        "trade_calendar_replay_sha256": sha256_file(calendar_path),
        "trade_calendar_replay_size_bytes": calendar_path.stat().st_size,
        "identity_timeline_binding_root": canonical_hash(identity_binding),
        "identity_intervals_archive_sha256": hashlib.sha256(
            identity_intervals_bytes
        ).hexdigest(),
        "identity_intervals_archive_size_bytes": len(identity_intervals_bytes),
        "identity_binding_archive_sha256": hashlib.sha256(
            identity_binding_bytes
        ).hexdigest(),
        "identity_binding_archive_size_bytes": len(identity_binding_bytes),
        "normalizer_conflicts_size_bytes": len(normalizer_conflicts_bytes),
        "archived_normalized_replay_root": archived_replay_root,
        "independent_signed_capture_proof_archived": False,
        "independent_source_reference_resolution_required": True,
    }
    contract, profile_contract_exact = _dataset_profile_contract(
        profile,
        dataset="daily_bars",
        granularity="security_day",
        approved_fields=DAILY_BARS_FIELDS,
        consumer_roles=("formula_input", "target", "execution", "capacity"),
    )
    active_intervals = [
        dict(row)
        for row in identity_timeline.get("intervals") or ()
        if isinstance(row, Mapping)
        and row.get("active_on_trade_date") is True
    ]
    security_ids = sorted(
        {
            str(row["security_id"])
            for row in identity_timeline.get("intervals") or ()
            if isinstance(row, Mapping)
        }
    )
    expected_exchanges = sorted(
        {
            "SSE" if str(row["security_code"]).endswith(".SH") else "SZSE"
            for row in active_intervals
        }
    )
    open_dates, calendar_invalid = _open_dates_by_exchange(
        calendar_path.read_bytes(),
        date_start=date_start,
        date_end=date_end,
        expected_exchanges=expected_exchanges,
    )
    open_union = sorted(
        {trade_date for dates in open_dates.values() for trade_date in dates}
    )
    intervals_by_security: dict[str, list[Mapping[str, Any]]] = {}
    for interval in identity_timeline.get("intervals") or ():
        if isinstance(interval, Mapping):
            intervals_by_security.setdefault(
                str(interval["security_id"]), []
            ).append(interval)
    identity_axis_valid = _identity_interval_axis_valid(
        intervals_by_security,
        open_dates=open_dates,
        expected_daily_row_count=identity_binding.get(
            "identity_timeline_daily_row_count"
        ),
    )
    resume_binding = {
        "schema_version": DAILY_BARS_RESUME_SCHEMA_VERSION,
        "provider_daily_bars_sha256": sha256_file(replayed_rows_path),
        "provider_daily_bars_size_bytes": replayed_rows_path.stat().st_size,
        "trade_calendar_sha256": sha256_file(calendar_path),
        "trade_calendar_size_bytes": calendar_path.stat().st_size,
        "normalized_replay_root": source_binding.get("normalized_replay_root"),
        "capture_content_hash": source_binding.get("capture_content_hash"),
        "capture_toolchain_implementation_root": source_binding.get(
            "current_capture_toolchain_implementation_root"
        ),
        "market_projection_implementation_root": source_binding.get(
            "market_projection_implementation_root"
        ),
        "resume_implementation_root": (
            _daily_bars_resume_implementation_root()
        ),
        "batch_row_limit": DAILY_BARS_BATCH_ROW_LIMIT,
        "identity_timeline_content_hash": identity_binding.get(
            "identity_timeline_content_hash"
        ),
        "identity_timeline_derivation_implementation_root": identity_binding.get(
            "identity_timeline_derivation_implementation_root"
        ),
        "identity_timeline_rows_root": identity_binding.get(
            "identity_timeline_rows_root"
        ),
        "identity_timeline_intervals_root": identity_binding.get(
            "identity_timeline_intervals_root"
        ),
        "profile_id": profile.get("profile_id"),
        "profile_content_root": canonical_hash(profile),
        "date_start": date_start,
        "date_end": date_end,
    }
    work_identity = canonical_hash(resume_binding)
    _durable_directory(
        output_directory,
        error="daily_bars_resume_output_root_symlink_forbidden",
    )
    _durable_directory(
        spill_root,
        error="daily_bars_resume_spill_root_symlink_forbidden",
    )
    resumed = _load_daily_bars_output_checkpoint(
        output_directory,
        resume_binding=resume_binding,
        work_identity=work_identity,
    )
    if resumed is not None:
        return resumed
    database_path = spill_root / "daily_bars_assessment.sqlite3"
    database_existed = database_path.exists() or database_path.is_symlink()
    allowed_spill_names = {
        database_path.name,
        f"{database_path.name}-wal",
        f"{database_path.name}-shm",
    }
    spill_entries = list(spill_root.iterdir())
    if (
        any(entry.is_symlink() for entry in spill_entries)
        or (not database_existed and spill_entries)
        or any(entry.name not in allowed_spill_names for entry in spill_entries)
    ):
        raise ValueError("daily_bars_resume_spill_closure_invalid")
    if database_path.is_symlink():
        raise ValueError("daily_bars_resume_database_symlink_forbidden")
    connection = sqlite3.connect(database_path, isolation_level=None)
    try:
        if not database_existed:
            _fsync_directory(spill_root)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA temp_store=FILE")
        connection.execute("PRAGMA mmap_size=0")
        connection.execute(
            f"PRAGMA cache_size=-{DAILY_BARS_SQLITE_CACHE_MIB * 1024}"
        )
        page_size = _sqlite_scalar(connection, "PRAGMA page_size")
        connection.execute(
            f"PRAGMA max_page_count={DAILY_BARS_SQLITE_SPILL_LIMIT_BYTES // page_size}"
        )
        existing_tables: set[str] = set()
        if database_existed:
            integrity = connection.execute("PRAGMA quick_check").fetchone()
            if integrity != ("ok",):
                raise ValueError("daily_bars_resume_database_integrity_failed")
            existing_tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
                if not str(row[0]).startswith("sqlite_")
            }
            if existing_tables and "resume_state" not in existing_tables:
                raise ValueError("daily_bars_resume_database_schema_invalid")
        if not existing_tables:
            connection.execute("BEGIN IMMEDIATE")
            initialization = (
                """CREATE TABLE resume_state (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                schema_version TEXT NOT NULL,
                work_identity TEXT NOT NULL,
                resume_binding_json TEXT NOT NULL,
                input_offset INTEGER NOT NULL,
                next_ordinal INTEGER NOT NULL,
                input_prefix_sha256 TEXT NOT NULL,
                projected_rows_sha256 TEXT NOT NULL,
                static_axis_sha256 TEXT NOT NULL,
                joined_state_sha256 TEXT NOT NULL,
                phase TEXT NOT NULL
            )""",
                """CREATE TABLE identity_intervals (
                interval_ordinal INTEGER PRIMARY KEY,
                security_id TEXT NOT NULL,
                ts_code TEXT NOT NULL,
                exchange TEXT NOT NULL,
                trade_date_start TEXT NOT NULL,
                trade_date_end TEXT NOT NULL
            )""",
                """CREATE TABLE open_dates (
                exchange TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                PRIMARY KEY (exchange, trade_date)
            ) WITHOUT ROWID""",
                """CREATE TABLE bars (
                ordinal INTEGER PRIMARY KEY,
                ts_code TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                canonical_json TEXT NOT NULL,
                reasons_json TEXT NOT NULL,
                not_applicable_candidate TEXT
            )""",
            )
            try:
                for statement in initialization:
                    connection.execute(statement)
                connection.execute(
                    """INSERT INTO resume_state
                    VALUES (1, ?, ?, ?, 0, 0, ?, ?, ?, ?, 'ingesting')""",
                    (
                        DAILY_BARS_RESUME_SCHEMA_VERSION,
                        work_identity,
                        _canonical_json_text(resume_binding),
                        EMPTY_SHA256,
                        EMPTY_SHA256,
                        EMPTY_SHA256,
                        EMPTY_SHA256,
                    ),
                )
                connection.executemany(
                    "INSERT INTO identity_intervals VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (
                            ordinal,
                            str(interval["security_id"]),
                            str(interval["security_code"]),
                            (
                                "SSE"
                                if str(interval["security_code"]).endswith(".SH")
                                else "SZSE"
                            ),
                            str(interval["trade_date_start"]),
                            str(interval["trade_date_end"]),
                        )
                        for ordinal, interval in enumerate(active_intervals)
                    ],
                )
                connection.executemany(
                    "INSERT INTO open_dates VALUES (?, ?)",
                    [
                        (exchange, trade_date)
                        for exchange, dates in sorted(open_dates.items())
                        for trade_date in sorted(dates)
                    ],
                )
                connection.execute(
                    "UPDATE resume_state SET static_axis_sha256=? WHERE singleton=1",
                    (_sqlite_static_axis_digest(connection),),
                )
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
        state = _daily_bars_resume_state(
            connection,
            resume_binding=resume_binding,
            work_identity=work_identity,
            replayed_rows_path=replayed_rows_path,
        )
        if state["input_offset"]:
            with replayed_rows_path.open("rb") as input_handle:
                input_handle.seek(state["input_offset"] - 1)
                if input_handle.read(1) != b"\n":
                    raise ValueError("daily_bars_resume_input_offset_invalid")
        batch: list[tuple[int, str, str, str, str, str | None]] = []
        if state["phase"] == "ingesting":
            ordinal = state["next_ordinal"]
            committed_offset = state["input_offset"]
            input_prefix_hasher = _input_prefix_hasher(
                replayed_rows_path,
                committed_offset,
            )
            pending_lines = 0
            with replayed_rows_path.open("rb") as input_handle:
                input_handle.seek(committed_offset)
                while (raw_line := input_handle.readline()):
                    input_prefix_hasher.update(raw_line)
                    next_offset = input_handle.tell()
                    pending_lines += 1
                    if raw_line.strip():
                        try:
                            source = json.loads(raw_line)
                        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                            raise ValueError(
                                "daily_bars_replay_jsonl_invalid"
                            ) from exc
                        if not isinstance(source, dict):
                            raise ValueError("daily_bars_replay_row_invalid")
                        raw_date = str(
                            source.get("trade_date")
                            or source.get("date")
                            or ""
                        ).replace("-", "")
                        if not (
                            _valid_date(raw_date)
                            and not date_start <= raw_date <= date_end
                        ):
                            projected, reasons = _project_daily_bar(source)
                            not_applicable = (
                                "proven_suspension"
                                if "provider_reported_suspension_requires_admitted_control"
                                in reasons
                                else None
                            )
                            batch.append(
                                (
                                    ordinal,
                                    projected["ts_code"],
                                    projected["trade_date"],
                                    _canonical_json_text(projected),
                                    json.dumps(
                                        reasons,
                                        ensure_ascii=False,
                                        sort_keys=True,
                                        separators=(",", ":"),
                                    ),
                                    not_applicable,
                                )
                            )
                            ordinal += 1
                    if pending_lines >= DAILY_BARS_BATCH_ROW_LIMIT:
                        _commit_daily_bars_resume_batch(
                            connection,
                            batch=batch,
                            input_offset=next_offset,
                            next_ordinal=ordinal,
                            input_prefix_sha256=input_prefix_hasher.hexdigest(),
                        )
                        batch.clear()
                        pending_lines = 0
                        committed_offset = next_offset
            _commit_daily_bars_resume_batch(
                connection,
                batch=batch,
                input_offset=replayed_rows_path.stat().st_size,
                next_ordinal=ordinal,
                input_prefix_sha256=input_prefix_hasher.hexdigest(),
                phase="ingested",
            )
            batch.clear()
            state = _daily_bars_resume_state(
                connection,
                resume_binding=resume_binding,
                work_identity=work_identity,
                replayed_rows_path=replayed_rows_path,
            )
        if state["phase"] == "ingested":
            connection.execute("BEGIN IMMEDIATE")
            statements = (
                "CREATE INDEX bars_security_day ON bars (ts_code, trade_date)",
                """CREATE TABLE expected AS
                SELECT i.security_id, i.ts_code, o.trade_date
                FROM identity_intervals AS i
                JOIN open_dates AS o ON o.exchange = i.exchange
                WHERE o.trade_date >= i.trade_date_start
                  AND o.trade_date <= i.trade_date_end""",
                "CREATE UNIQUE INDEX expected_identity_security_day ON expected (security_id, ts_code, trade_date)",
                "CREATE UNIQUE INDEX expected_security_day ON expected (ts_code, trade_date)",
                """CREATE TABLE observed AS
                SELECT ts_code, trade_date, COUNT(*) AS occurrence_count
                FROM bars
                GROUP BY ts_code, trade_date""",
                "CREATE UNIQUE INDEX observed_security_day ON observed (ts_code, trade_date)",
                """CREATE TABLE duplicate_counts AS
                SELECT ts_code, trade_date, occurrence_count
                FROM observed WHERE occurrence_count > 1""",
                "CREATE UNIQUE INDEX duplicate_security_day ON duplicate_counts (ts_code, trade_date)",
                """CREATE TABLE gap_events (
                ts_code TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                kind TEXT NOT NULL
            )""",
                """INSERT INTO gap_events
                SELECT e.ts_code, e.trade_date, 'missing'
                FROM expected AS e
                LEFT JOIN observed AS o
                  ON o.ts_code = e.ts_code AND o.trade_date = e.trade_date
                WHERE o.ts_code IS NULL""",
                """INSERT INTO gap_events
                SELECT o.ts_code, o.trade_date, 'extra'
                FROM observed AS o
                LEFT JOIN expected AS e
                  ON e.ts_code = o.ts_code AND e.trade_date = o.trade_date
                WHERE e.ts_code IS NULL""",
                """INSERT INTO gap_events
                SELECT ts_code, trade_date, 'duplicate'
                FROM duplicate_counts""",
                "CREATE INDEX gap_event_order ON gap_events (ts_code, trade_date, kind)",
            )
            try:
                for statement in statements:
                    connection.execute(statement)
                connection.execute(
                    """UPDATE resume_state
                    SET phase='joined', joined_state_sha256=? WHERE singleton=1""",
                    (_sqlite_joined_state_digest(connection),),
                )
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            state = _daily_bars_resume_state(
                connection,
                resume_binding=resume_binding,
                work_identity=work_identity,
                replayed_rows_path=replayed_rows_path,
            )
        if state["phase"] != "joined":
            raise ValueError("daily_bars_resume_phase_invalid")
        expected_count = _sqlite_scalar(connection, "SELECT COUNT(*) FROM expected")
        expected_axis_root = _sqlite_expected_axis_root(connection)
        observed_expected_count = _sqlite_scalar(
            connection,
            """
            SELECT COUNT(*) FROM observed AS o
            JOIN expected AS e
              ON e.ts_code = o.ts_code AND e.trade_date = o.trade_date
            """,
        )
        missing_count = _sqlite_scalar(
            connection, "SELECT COUNT(*) FROM gap_events WHERE kind='missing'"
        )
        extra_count = _sqlite_scalar(
            connection, "SELECT COUNT(*) FROM gap_events WHERE kind='extra'"
        )
        duplicate_count = _sqlite_scalar(
            connection, "SELECT COUNT(*) FROM gap_events WHERE kind='duplicate'"
        )
        _publish_daily_bars_source_archive(
            output_directory,
            provider_rows_path=replayed_rows_path,
            calendar_path=calendar_path,
            identity_intervals_bytes=identity_intervals_bytes,
            identity_binding_bytes=identity_binding_bytes,
            normalizer_conflicts_bytes=normalizer_conflicts_bytes,
        )
        rows_path = output_directory / DAILY_BARS_ROWS_NAME
        validity_path = output_directory / DAILY_BARS_VALIDITY_NAME
        gaps_path = output_directory / DAILY_BARS_GAPS_NAME
        valid_count = invalid_count = not_applicable_count = 0
        with rows_path.open("xb") as rows_handle, validity_path.open(
            "xb"
        ) as validity_handle:
            cursor = connection.execute(
                """
                SELECT b.ts_code, b.trade_date, b.canonical_json,
                       b.reasons_json, b.not_applicable_candidate,
                       d.occurrence_count
                FROM bars AS b
                LEFT JOIN duplicate_counts AS d
                  ON d.ts_code = b.ts_code AND d.trade_date = b.trade_date
                ORDER BY b.ts_code, b.trade_date, b.ordinal
                """
            )
            for (
                code,
                trade_date,
                canonical_json,
                reasons_json,
                not_applicable,
                occurrence_count,
            ) in cursor:
                reasons = list(json.loads(str(reasons_json)))
                if occurrence_count is not None:
                    reasons.append("duplicate_security_day")
                reasons = sorted(set(str(value) for value in reasons))
                is_valid = not reasons
                valid_count += int(is_valid)
                invalid_count += int(not is_valid)
                not_applicable_count += int(not_applicable is not None)
                rows_handle.write(str(canonical_json).encode() + b"\n")
                validity_row: dict[str, Any] = {
                    "ts_code": str(code),
                    "trade_date": str(trade_date),
                    "valid": is_valid,
                    "reasons": reasons,
                }
                if not_applicable is not None:
                    validity_row["not_applicable_candidate"] = str(
                        not_applicable
                    )
                validity_handle.write(
                    _canonical_json_text(validity_row).encode() + b"\n"
                )
            _flush_file(rows_handle)
            _flush_file(validity_handle)
        with gaps_path.open("xb") as gaps_handle:
            current_code: str | None = None
            buckets = {"missing": [], "extra": [], "duplicate": []}

            def flush_gap() -> None:
                if current_code is None:
                    return
                gap = {
                    "ts_code": current_code,
                    "missing_trade_dates": buckets["missing"],
                    "extra_trade_dates": buckets["extra"],
                    "duplicate_trade_dates": buckets["duplicate"],
                }
                gaps_handle.write(_canonical_json_text(gap).encode() + b"\n")

            for code, trade_date, kind in connection.execute(
                """
                SELECT ts_code, trade_date, kind FROM gap_events
                ORDER BY ts_code, trade_date, kind
                """
            ):
                code = str(code)
                if current_code is not None and code != current_code:
                    flush_gap()
                    buckets = {"missing": [], "extra": [], "duplicate": []}
                current_code = code
                buckets[str(kind)].append(str(trade_date))
            flush_gap()
            _flush_file(gaps_handle)
    finally:
        connection.close()
    exact_cover = not (missing_count or extra_count or duplicate_count)
    blockers = _governance_blockers(profile, contract, source_binding) | {
        "source_freeze_consumer_binding_pending",
        "trade_calendar_data_admission_pending",
        "securities_data_admission_pending",
        "adjustment_factors_data_admission_pending",
        "execution_control_data_admission_pending",
        "identity_timeline_independent_admission_pending",
        "daily_bars_independent_source_reference_resolution_pending",
    }
    if source_binding.get("wire_replay_verified") is not True:
        blockers.add("daily_bars_signed_wire_replay_unverified")
    if source_binding.get("normalized_replay_root") != archived_replay_root:
        blockers.add("daily_bars_archived_source_replay_root_mismatch")
    if source_binding.get("parser_replay_verified") is not True:
        blockers.add("daily_bars_parser_replay_unverified")
    if source_binding.get("publication_signature_verified") is not True:
        blockers.add("daily_bars_signed_publication_unverified")
    if source_binding.get("normalizer_conflicts_bound") is not True:
        blockers.add("daily_bars_normalizer_conflicts_unbound")
    elif source_binding.get("normalizer_conflict_count") != 0:
        blockers.add("daily_bars_normalization_conflicts_present")
    if source_binding.get("capture_toolchain_implementation_match") is not True:
        blockers.add("daily_bars_capture_toolchain_identity_mismatch")
    if source_binding.get("capture_source_profile_id") != profile.get("profile_id"):
        blockers.add("daily_bars_capture_profile_binding_failed")
    if not _capture_scope_contains(source_binding, date_start, date_end):
        blockers.add("daily_bars_capture_scope_binding_failed")
    if not profile_contract_exact:
        blockers.add("daily_bars_profile_consumer_closure_failed")
    if calendar_invalid:
        blockers.add("daily_bars_calendar_axis_invalid")
    if not exact_cover:
        blockers.add("daily_bars_security_day_exact_cover_failed")
    if invalid_count:
        blockers.add("daily_bars_required_value_validity_failed")
    if not identity_axis_valid:
        blockers.add("daily_bars_identity_timeline_axis_invalid")
    canonical_sha = sha256_file(rows_path)
    validity_sha = sha256_file(validity_path)
    gaps_sha = sha256_file(gaps_path)
    exchange_open_dates_root = canonical_hash(
        {
            exchange: sorted(dates)
            for exchange, dates in sorted(open_dates.items())
        }
    )
    expected_axis_binding = {
        "date_start": date_start,
        "date_end": date_end,
        "expected_security_day_count": expected_count,
        "expected_security_day_root": expected_axis_root,
        "exchange_open_dates_root": exchange_open_dates_root,
        "trade_calendar_replay_sha256": source_binding[
            "trade_calendar_replay_sha256"
        ],
        "identity_timeline_binding_root": source_binding[
            "identity_timeline_binding_root"
        ],
    }
    semantic = {
        "schema_version": DAILY_BARS_SCHEMA_VERSION,
        "dataset": "daily_bars",
        "profile_id": profile.get("profile_id"),
        "scope": {
            "access_view": "research",
            "date_start": date_start,
            "date_end": date_end,
            "as_of_market_date": date_end,
        },
        "source_binding": dict(source_binding),
        "provider_neutral_projection": {
            "dataset": "daily_bars",
            "record_count": valid_count + invalid_count,
            "canonical_rows_sha256": canonical_sha,
            "canonical_rows_size_bytes": rows_path.stat().st_size,
            "canonical_rows_root": canonical_sha,
        },
        "pit_axis": {
            "security_count": len(security_ids),
            "identity_timeline_binding": dict(identity_binding),
            "identity_timeline_axis_complete": identity_axis_valid,
            "exchange_open_dates_root": exchange_open_dates_root,
            "expected_security_day_root": expected_axis_root,
            "expected_axis_binding_root": canonical_hash(
                expected_axis_binding
            ),
            "axis_semantics": (
                "governed_identity_active_interval_intersection_exchange_open_day"
            ),
        },
        "coverage": {
            "expected_security_day_count": expected_count,
            "observed_security_day_count": observed_expected_count,
            "missing_security_day_count": missing_count,
            "extra_security_day_count": extra_count,
            "duplicate_security_day_count": duplicate_count,
            "provisional_exact_cover": exact_cover,
        },
        "coverage_gaps_sha256": gaps_sha,
        "coverage_gaps_size_bytes": gaps_path.stat().st_size,
        "coverage_gaps_root": gaps_sha,
        "validity": {
            "valid_row_count": valid_count,
            "invalid_row_count": invalid_count,
            "required_field_count": len(DAILY_BARS_FIELDS),
            "all_required_values_valid": invalid_count == 0,
            "not_applicable_candidate_count": not_applicable_count,
            "not_applicable_authority": "suspensions",
            "not_applicable_authority_status": "pending_admission",
        },
        "validity_rows_sha256": validity_sha,
        "validity_rows_size_bytes": validity_path.stat().st_size,
        "validity_rows_root": validity_sha,
        "consumer_closure": {
            "approved_fields": list(DAILY_BARS_FIELDS),
            "consumer_roles": [
                "formula_input",
                "target",
                "execution",
                "capacity",
            ],
            "formula_input_authorized": False,
            "profile_contract_exact": profile_contract_exact,
        },
        "resource_execution": {
            "engine": "sqlite_disk_spill",
            "input_mode": "jsonl_stream",
            "output_mode": "jsonl_stream",
            "batch_row_limit": DAILY_BARS_BATCH_ROW_LIMIT,
            "sqlite_cache_limit_mib": DAILY_BARS_SQLITE_CACHE_MIB,
            "sqlite_spill_limit_bytes": DAILY_BARS_SQLITE_SPILL_LIMIT_BYTES,
            "sqlite_mmap_bytes": 0,
            "work_identity": work_identity,
            "resume_schema_version": DAILY_BARS_RESUME_SCHEMA_VERSION,
            "resume_implementation_root": resume_binding[
                "resume_implementation_root"
            ],
            "checkpoint_granularity": (
                "committed_input_prefix_and_projected_state"
            ),
            "checkpoint_input_prefix_sha256": state[
                "input_prefix_sha256"
            ],
            "checkpoint_projected_rows_sha256": state[
                "projected_rows_sha256"
            ],
            "checkpoint_static_axis_sha256": state[
                "static_axis_sha256"
            ],
            "checkpoint_joined_state_sha256": state[
                "joined_state_sha256"
            ],
            "source_binding_root": canonical_hash(source_binding),
            "expected_axis_binding_root": canonical_hash(
                expected_axis_binding
            ),
            "resume_supported": True,
        },
        "technical_evidence_status": (
            "blocked"
            if blockers & _technical_blockers("daily_bars")
            else "verified"
        ),
        "formal_data_admission_ready": False,
        "blockers": sorted(blockers),
        "safety": {name: False for name in SAFETY_FLAGS},
    }
    _publish_daily_bars_output_checkpoint(
        output_directory,
        semantic=semantic,
        resume_binding=resume_binding,
        work_identity=work_identity,
    )
    return semantic


def _publish_market_assessment(
    assessment: MarketDataAssessment,
    output_root: str | Path,
    *,
    dataset: str,
    rows_name: str,
    validity_name: str,
    gaps_name: str,
) -> dict[str, Any]:
    if assessment.semantic.get("dataset") != dataset:
        raise ValueError(f"{dataset}_assessment_dataset_invalid")
    extra_files = {
        rows_name: assessment.canonical_rows,
        validity_name: assessment.validity_rows,
        gaps_name: assessment.coverage_gaps,
    }
    resource = assessment.semantic.get("resource_execution") or {}
    if dataset == "daily_bars" and resource.get("engine") == "sqlite_disk_spill":
        expected_archive = {
            DAILY_BARS_SOURCE_ROWS_NAME,
            DAILY_BARS_SOURCE_CALENDAR_NAME,
            DAILY_BARS_SOURCE_CONFLICTS_NAME,
            DAILY_BARS_SOURCE_IDENTITY_INTERVALS_NAME,
            DAILY_BARS_SOURCE_IDENTITY_BINDING_NAME,
        }
        if (
            not isinstance(assessment.source_archive, dict)
            or set(assessment.source_archive) != expected_archive
        ):
            raise ValueError("daily_bars_assessment_source_archive_missing")
        extra_files.update(assessment.source_archive)
    return publish_generation(
        output_root,
        prefix=GENERATION_PREFIX,
        manifest_name=MANIFEST_NAME,
        semantic=assessment.semantic,
        extra_files=extra_files,
    )


def _validate_market_evidence(
    path: str | Path,
    *,
    dataset: str,
    schema: str,
    rows_name: str,
    validity_name: str,
    gaps_name: str,
    approved_fields: Sequence[str],
    consumer_roles: Sequence[str],
) -> dict[str, Any]:
    payload = validate_generation(path, schema=schema, manifest_name=MANIFEST_NAME)
    root = Path(str(payload["manifest_path"])).parent
    resource = payload.get("resource_execution") or {}
    expected_files = {MANIFEST_NAME, rows_name, validity_name, gaps_name}
    if dataset == "daily_bars" and resource.get("engine") == "sqlite_disk_spill":
        expected_files.update(
            {
                DAILY_BARS_SOURCE_ROWS_NAME,
                DAILY_BARS_SOURCE_CALENDAR_NAME,
                DAILY_BARS_SOURCE_CONFLICTS_NAME,
                DAILY_BARS_SOURCE_IDENTITY_INTERVALS_NAME,
                DAILY_BARS_SOURCE_IDENTITY_BINDING_NAME,
            }
        )
    tree_exact = _market_generation_tree_exact(root, expected_files)
    rows_path = root / rows_name
    validity_path = root / validity_name
    gaps_path = root / gaps_name
    try:
        replay = _deep_replay_market_evidence(
            rows_path,
            validity_path,
            gaps_path,
            dataset=dataset,
            payload=payload,
        )
        source_replay = (
            _replay_archived_daily_source_closure(
                root,
                rows_path=rows_path,
                validity_path=validity_path,
                gaps_path=gaps_path,
                payload=payload,
            )
            if dataset == "daily_bars"
            and resource.get("engine") == "sqlite_disk_spill"
            else None
        )
    except (OSError, ValueError, sqlite3.DatabaseError) as exc:
        raise ValueError(f"{dataset}_evidence_invalid") from exc
    row_count = replay["row_count"]
    validity_count = replay["validity_count"]
    observed_valid_count = replay["valid_row_count"]
    observed_invalid_count = replay["invalid_row_count"]
    gap_count = replay["gap_row_count"]
    projection = payload.get("provider_neutral_projection") or {}
    coverage = payload.get("coverage") or {}
    validity_summary = payload.get("validity") or {}
    consumer = payload.get("consumer_closure") or {}
    source_binding = payload.get("source_binding") or {}
    safety = payload.get("safety") or {}
    raw_blockers = payload.get("blockers")
    blockers = (
        set(raw_blockers)
        if type(raw_blockers) is list
        and all(type(value) is str for value in raw_blockers)
        else set()
    )
    technical_blockers_present = bool(blockers & _technical_blockers(dataset))
    profile_contract_exact = consumer.get("profile_contract_exact")
    exact_cover_blocker = (
        "trade_calendar_exchange_day_exact_cover_failed"
        if dataset == "trade_calendar"
        else "daily_bars_security_day_exact_cover_failed"
    )
    invalid = (
        payload.get("dataset") != dataset
        or not tree_exact
        or not _market_semantic_scalar_types_valid(payload)
        or not _market_source_replay_state_valid(payload)
        or projection.get("record_count") != row_count
        or projection.get("canonical_rows_sha256") != sha256_file(rows_path)
        or projection.get("canonical_rows_size_bytes") != rows_path.stat().st_size
        or projection.get("canonical_rows_root") != sha256_file(rows_path)
        or payload.get("validity_rows_sha256") != sha256_file(validity_path)
        or payload.get("validity_rows_size_bytes") != validity_path.stat().st_size
        or payload.get("validity_rows_root") != sha256_file(validity_path)
        or payload.get("coverage_gaps_sha256") != sha256_file(gaps_path)
        or payload.get("coverage_gaps_size_bytes") != gaps_path.stat().st_size
        or payload.get("coverage_gaps_root") != sha256_file(gaps_path)
        or validity_summary.get("valid_row_count")
        + validity_summary.get("invalid_row_count")
        != validity_count
        or validity_summary.get("valid_row_count") != observed_valid_count
        or validity_summary.get("invalid_row_count") != observed_invalid_count
        or validity_summary.get("all_required_values_valid")
        is not (observed_invalid_count == 0)
        or validity_summary.get("required_field_count") != len(approved_fields)
        or (
            dataset == "daily_bars"
            and "not_applicable_candidate_count" in validity_summary
            and validity_summary.get("not_applicable_candidate_count")
            != replay["not_applicable_candidate_count"]
        )
        or coverage != replay["coverage"]
        or coverage.get("provisional_exact_cover") is not (gap_count == 0)
        or (
            coverage.get("provisional_exact_cover") is False
            and exact_cover_blocker not in blockers
        )
        or (
            coverage.get("provisional_exact_cover") is True
            and exact_cover_blocker in blockers
        )
        or (
            observed_invalid_count > 0
            and f"{dataset}_required_value_validity_failed" not in blockers
        )
        or (
            observed_invalid_count == 0
            and f"{dataset}_required_value_validity_failed" in blockers
        )
        or consumer.get("approved_fields") != list(approved_fields)
        or consumer.get("consumer_roles") != list(consumer_roles)
        or consumer.get("formula_input_authorized") is not False
        or profile_contract_exact not in {True, False}
        or (
            profile_contract_exact is False
            and f"{dataset}_profile_consumer_closure_failed" not in blockers
        )
        or payload.get("technical_evidence_status")
        != ("blocked" if technical_blockers_present else "verified")
        or payload.get("formal_data_admission_ready") is not False
        or set(safety) != set(SAFETY_FLAGS)
        or any(value is not False for value in safety.values())
        or not _market_source_blockers_consistent(payload)
        or (
            dataset == "daily_bars"
            and not _daily_resource_execution_valid(resource, blockers)
        )
        or (
            dataset == "daily_bars"
            and resource.get("engine") == "sqlite_disk_spill"
            and (
                source_binding.get("market_projection_schema_version")
                != "market_state_projection_v2"
                or source_binding.get("market_projection_implementation_root")
                != _market_projection_implementation_root()
                or not _daily_disk_source_axis_binding_valid(
                    payload,
                    replay,
                )
                or source_replay != {
                    "coverage": replay["coverage"],
                    "expected_axis_root": replay["expected_axis_root"],
                    "row_count": replay["row_count"],
                }
            )
        )
    )
    if invalid:
        raise ValueError(f"{dataset}_evidence_invalid")
    return payload


def _deep_replay_market_evidence(
    rows_path: Path,
    validity_path: Path,
    gaps_path: Path,
    *,
    dataset: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Replay canonical rows, validity and coverage with disk-bounded state."""

    if dataset not in {"trade_calendar", "daily_bars"}:
        raise ValueError("market_evidence_dataset_invalid")
    canonical_fields = (
        TRADE_CALENDAR_FIELDS if dataset == "trade_calendar" else DAILY_BARS_FIELDS
    )
    key_fields = (
        ("exchange", "trade_date")
        if dataset == "trade_calendar"
        else ("ts_code", "trade_date")
    )
    duplicate_reason = (
        "duplicate_exchange_day"
        if dataset == "trade_calendar"
        else "duplicate_security_day"
    )
    pit_axis = payload.get("pit_axis") or {}
    scope = payload.get("scope") or {}
    date_start = str(scope.get("date_start") or "")
    date_end = str(scope.get("date_end") or "")
    if not _valid_date(date_start) or not _valid_date(date_end) or date_start > date_end:
        raise ValueError("market_evidence_scope_invalid")
    exchanges = tuple(pit_axis.get("exchanges") or ())
    if dataset == "trade_calendar" and (
        not exchanges
        or any(value not in {"SSE", "SZSE"} for value in exchanges)
        or list(exchanges) != list(dict.fromkeys(exchanges))
    ):
        raise ValueError("trade_calendar_axis_invalid")

    with tempfile.TemporaryDirectory(prefix="market-evidence-validation-") as temporary:
        connection = sqlite3.connect(Path(temporary) / "closure.sqlite3")
        try:
            connection.execute("PRAGMA journal_mode=OFF")
            connection.execute("PRAGMA temp_store=FILE")
            connection.execute("PRAGMA mmap_size=0")
            connection.execute(
                f"PRAGMA cache_size=-{DAILY_BARS_SQLITE_CACHE_MIB * 1024}"
            )
            connection.execute(
                """CREATE TABLE observed (
                key1 TEXT NOT NULL,
                key2 TEXT NOT NULL,
                occurrence_count INTEGER NOT NULL,
                PRIMARY KEY (key1, key2)
                ) WITHOUT ROWID"""
            )
            connection.execute(
                """CREATE TABLE gap_events (
                key1 TEXT NOT NULL,
                key2 TEXT NOT NULL,
                kind TEXT NOT NULL,
                PRIMARY KEY (key1, key2, kind)
                ) WITHOUT ROWID"""
            )
            expected_axis_root: str | None = None
            if dataset == "trade_calendar":
                connection.execute(
                    """CREATE TABLE expected (
                    key1 TEXT NOT NULL,
                    key2 TEXT NOT NULL,
                    PRIMARY KEY (key1, key2)
                    ) WITHOUT ROWID"""
                )
                connection.executemany(
                    "INSERT INTO expected VALUES (?, ?)",
                    (
                        (exchange, trade_date)
                        for exchange in exchanges
                        for trade_date in _date_span(date_start, date_end)
                    ),
                )

            validity_iterator = iter(
                _iter_canonical_jsonl_path(validity_path, f"{dataset}_validity")
            )
            previous_key: tuple[str, str] | None = None
            first_duplicate_flag = False
            group_size = 0
            previous_open: dict[str, str | None] = {
                exchange: None for exchange in exchanges
            }
            open_dates: set[str] = set()
            row_count = valid_count = invalid_count = 0
            not_applicable_count = 0

            for row in _iter_canonical_jsonl_path(rows_path, dataset):
                try:
                    validity = next(validity_iterator)
                except StopIteration as exc:
                    raise ValueError("market_validity_row_missing") from exc
                if set(row) != set(canonical_fields):
                    raise ValueError("market_canonical_schema_invalid")
                if dataset == "trade_calendar":
                    if (
                        type(row.get("exchange")) is not str
                        or type(row.get("trade_date")) is not str
                        or type(row.get("is_open")) is not bool
                        or (
                            row.get("prev_trade_date") is not None
                            and type(row.get("prev_trade_date")) is not str
                        )
                    ):
                        raise ValueError("trade_calendar_canonical_type_invalid")
                elif any(type(row.get(field)) is not str for field in canonical_fields):
                    raise ValueError("daily_bars_canonical_type_invalid")
                validity_fields = set(validity)
                required_validity = {*key_fields, "valid", "reasons"}
                allowed_validity = set(required_validity)
                if dataset == "daily_bars":
                    allowed_validity.add("not_applicable_candidate")
                if not required_validity <= validity_fields or not validity_fields <= allowed_validity:
                    raise ValueError("market_validity_schema_invalid")
                if (
                    any(type(validity.get(field)) is not str for field in key_fields)
                    or (
                        "not_applicable_candidate" in validity
                        and type(validity.get("not_applicable_candidate")) is not str
                    )
                ):
                    raise ValueError("market_validity_type_invalid")
                key = tuple(str(row[field]) for field in key_fields)
                if tuple(str(validity[field]) for field in key_fields) != key:
                    raise ValueError("market_row_validity_key_mismatch")
                if previous_key is not None and key < previous_key:
                    raise ValueError("market_canonical_order_invalid")
                reasons = validity.get("reasons")
                if (
                    not isinstance(reasons, list)
                    or any(not isinstance(value, str) or not value for value in reasons)
                    or reasons != sorted(set(reasons))
                    or type(validity.get("valid")) is not bool
                    or validity.get("valid") is not (not reasons)
                ):
                    raise ValueError("market_validity_semantics_invalid")

                if dataset == "trade_calendar":
                    projected, expected_reasons = _project_calendar_row(row, exchanges)
                    exchange, trade_date = key
                    if (
                        exchange in previous_open
                        and _valid_date(trade_date)
                        and previous_open[exchange] is not None
                        and projected.get("prev_trade_date")
                        != previous_open[exchange]
                    ):
                        expected_reasons.append("prev_trade_date_chain_invalid")
                    if projected.get("is_open") is True and exchange in previous_open:
                        previous_open[exchange] = trade_date
                        if _valid_date(trade_date):
                            open_dates.add(trade_date)
                else:
                    not_applicable = validity.get("not_applicable_candidate")
                    suspension = (
                        "provider_reported_suspension_requires_admitted_control"
                        in reasons
                    )
                    invalid_status = "provider_trade_status_invalid" in reasons
                    if "not_applicable_candidate" in validity and (
                        not_applicable != "proven_suspension" or not suspension
                    ):
                        raise ValueError("daily_bars_not_applicable_invalid")
                    inferred_status: object = 0 if suspension else "invalid" if invalid_status else 1
                    projected, expected_reasons = _project_daily_bar(
                        dict(row, provider_trade_status=inferred_status)
                    )
                    not_applicable_count += int(not_applicable is not None)
                if projected != row:
                    raise ValueError("market_canonical_projection_invalid")
                expected_reason_set = set(expected_reasons)
                observed_reason_set = set(reasons)
                if observed_reason_set - {duplicate_reason} != expected_reason_set:
                    raise ValueError("market_validity_replay_mismatch")

                has_duplicate_reason = duplicate_reason in observed_reason_set
                if key == previous_key:
                    if group_size == 1 and not first_duplicate_flag:
                        raise ValueError("market_duplicate_validity_missing")
                    if not has_duplicate_reason:
                        raise ValueError("market_duplicate_validity_missing")
                    group_size += 1
                else:
                    if group_size == 1 and first_duplicate_flag:
                        raise ValueError("market_duplicate_validity_spurious")
                    previous_key = key
                    group_size = 1
                    first_duplicate_flag = has_duplicate_reason
                connection.execute(
                    """INSERT INTO observed VALUES (?, ?, 1)
                    ON CONFLICT (key1, key2) DO UPDATE SET
                    occurrence_count=occurrence_count + 1""",
                    key,
                )
                row_count += 1
                valid_count += int(validity["valid"] is True)
                invalid_count += int(validity["valid"] is False)
            try:
                next(validity_iterator)
            except StopIteration:
                pass
            else:
                raise ValueError("market_validity_row_extra")
            if group_size == 1 and first_duplicate_flag:
                raise ValueError("market_duplicate_validity_spurious")

            gap_row_count = _replay_market_gaps(
                connection,
                gaps_path,
                dataset=dataset,
                date_start=date_start,
                date_end=date_end,
            )
            duplicate_count = _sqlite_scalar(
                connection,
                "SELECT COUNT(*) FROM observed WHERE occurrence_count > 1",
            )
            if _sqlite_scalar(
                connection,
                """SELECT COUNT(*) FROM (
                SELECT key1, key2 FROM observed WHERE occurrence_count > 1
                EXCEPT SELECT key1, key2 FROM gap_events WHERE kind='duplicate'
                )""",
            ) or _sqlite_scalar(
                connection,
                """SELECT COUNT(*) FROM (
                SELECT key1, key2 FROM gap_events WHERE kind='duplicate'
                EXCEPT SELECT key1, key2 FROM observed WHERE occurrence_count > 1
                )""",
            ):
                raise ValueError("market_duplicate_gap_mismatch")

            if dataset == "trade_calendar":
                missing_count = _sqlite_scalar(
                    connection,
                    """SELECT COUNT(*) FROM expected AS e LEFT JOIN observed AS o
                    ON o.key1=e.key1 AND o.key2=e.key2 WHERE o.key1 IS NULL""",
                )
                extra_count = _sqlite_scalar(
                    connection,
                    """SELECT COUNT(*) FROM observed AS o LEFT JOIN expected AS e
                    ON e.key1=o.key1 AND e.key2=o.key2 WHERE e.key1 IS NULL""",
                )
                observed_expected_count = _sqlite_scalar(
                    connection,
                    """SELECT COUNT(*) FROM observed AS o JOIN expected AS e
                    ON e.key1=o.key1 AND e.key2=o.key2""",
                )
                expected_count = _sqlite_scalar(connection, "SELECT COUNT(*) FROM expected")
                for kind, query in (
                    (
                        "missing",
                        "SELECT e.key1, e.key2 FROM expected AS e LEFT JOIN observed AS o ON o.key1=e.key1 AND o.key2=e.key2 WHERE o.key1 IS NULL",
                    ),
                    (
                        "extra",
                        "SELECT o.key1, o.key2 FROM observed AS o LEFT JOIN expected AS e ON e.key1=o.key1 AND e.key2=o.key2 WHERE e.key1 IS NULL",
                    ),
                ):
                    if _gap_set_mismatch(connection, kind, query):
                        raise ValueError("trade_calendar_gap_set_mismatch")
                if (
                    pit_axis.get("calendar_day_count") != len(_date_span(date_start, date_end))
                    or pit_axis.get("open_trade_date_count") != len(open_dates)
                    or pit_axis.get("open_trade_dates_root")
                    != canonical_hash(sorted(open_dates))
                ):
                    raise ValueError("trade_calendar_pit_axis_replay_mismatch")
                coverage = {
                    "expected_exchange_day_count": expected_count,
                    "observed_exchange_day_count": observed_expected_count,
                    "missing_exchange_day_count": missing_count,
                    "extra_exchange_day_count": extra_count,
                    "duplicate_exchange_day_count": duplicate_count,
                    "provisional_exact_cover": not (
                        missing_count or extra_count or duplicate_count
                    ),
                }
            else:
                missing_count = _gap_kind_count(connection, "missing")
                extra_count = _gap_kind_count(connection, "extra")
                expected_axis_root = _validation_expected_axis_root(connection)
                unique_count = _sqlite_scalar(connection, "SELECT COUNT(*) FROM observed")
                if _sqlite_scalar(
                    connection,
                    """SELECT COUNT(*) FROM gap_events AS g LEFT JOIN observed AS o
                    ON o.key1=g.key1 AND o.key2=g.key2
                    WHERE (g.kind='extra' AND o.key1 IS NULL)
                       OR (g.kind='missing' AND o.key1 IS NOT NULL)""",
                ):
                    raise ValueError("daily_bars_gap_membership_invalid")
                observed_expected_count = unique_count - extra_count
                expected_count = observed_expected_count + missing_count
                coverage = {
                    "expected_security_day_count": expected_count,
                    "observed_security_day_count": observed_expected_count,
                    "missing_security_day_count": missing_count,
                    "extra_security_day_count": extra_count,
                    "duplicate_security_day_count": duplicate_count,
                    "provisional_exact_cover": not (
                        missing_count or extra_count or duplicate_count
                    ),
                }
            if coverage["provisional_exact_cover"] is not (gap_row_count == 0):
                raise ValueError("market_gap_closure_invalid")
            return {
                "row_count": row_count,
                "validity_count": row_count,
                "valid_row_count": valid_count,
                "invalid_row_count": invalid_count,
                "not_applicable_candidate_count": not_applicable_count,
                "gap_row_count": gap_row_count,
                "coverage": coverage,
                "expected_axis_root": expected_axis_root,
            }
        finally:
            connection.close()


def _replay_archived_daily_source_closure(
    root: Path,
    *,
    rows_path: Path,
    validity_path: Path,
    gaps_path: Path,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Independently rebuild frozen bars and their expected PIT axis."""

    source_binding = payload.get("source_binding") or {}
    pit_axis = payload.get("pit_axis") or {}
    identity_binding = pit_axis.get("identity_timeline_binding") or {}
    scope = payload.get("scope") or {}
    date_start = str(scope.get("date_start") or "")
    date_end = str(scope.get("date_end") or "")
    source_rows_path = root / DAILY_BARS_SOURCE_ROWS_NAME
    source_calendar_path = root / DAILY_BARS_SOURCE_CALENDAR_NAME
    source_conflicts_path = root / DAILY_BARS_SOURCE_CONFLICTS_NAME
    source_intervals_path = root / DAILY_BARS_SOURCE_IDENTITY_INTERVALS_NAME
    source_identity_path = root / DAILY_BARS_SOURCE_IDENTITY_BINDING_NAME
    archived = (
        (
            source_rows_path,
            "provider_daily_bars_replay_sha256",
            "provider_daily_bars_replay_size_bytes",
        ),
        (
            source_calendar_path,
            "trade_calendar_replay_sha256",
            "trade_calendar_replay_size_bytes",
        ),
        (
            source_conflicts_path,
            "normalizer_conflicts_root",
            "normalizer_conflicts_size_bytes",
        ),
        (
            source_intervals_path,
            "identity_intervals_archive_sha256",
            "identity_intervals_archive_size_bytes",
        ),
        (
            source_identity_path,
            "identity_binding_archive_sha256",
            "identity_binding_archive_size_bytes",
        ),
    )
    for path, hash_field, size_field in archived:
        if (
            not path.is_file()
            or path.is_symlink()
            or source_binding.get(hash_field) != sha256_file(path)
            or source_binding.get(size_field) != path.stat().st_size
        ):
            raise ValueError("daily_bars_source_archive_binding_invalid")
    conflict_count = sum(
        1
        for _ in _iter_canonical_jsonl_path(
            source_conflicts_path, "daily_bars_archived_normalizer_conflicts"
        )
    )
    if (
        source_binding.get("normalizer_conflicts_bound") is not True
        or type(source_binding.get("normalizer_conflict_count")) is not int
        or source_binding.get("normalizer_conflict_count") != conflict_count
    ):
        raise ValueError("daily_bars_source_conflict_count_invalid")
    replay_artifacts = [
        {
            "role": role,
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for role, path in (
            ("provider_daily_bars", source_rows_path),
            ("trade_calendar", source_calendar_path),
            ("conflicts", source_conflicts_path),
        )
    ]
    if source_binding.get("archived_normalized_replay_root") != canonical_hash(
        sorted(replay_artifacts, key=lambda row: row["role"])
    ):
        raise ValueError("daily_bars_source_replay_root_invalid")
    identity_rows = list(
        _iter_canonical_jsonl_path(
            source_identity_path,
            "daily_bars_source_identity_binding",
        )
    )
    intervals = list(
        _iter_canonical_jsonl_path(
            source_intervals_path,
            "daily_bars_source_identity_intervals",
        )
    )
    if (
        identity_rows != [dict(identity_binding)]
        or source_binding.get("identity_timeline_binding_root")
        != canonical_hash(identity_binding)
        or canonical_hash(intervals)
        != identity_binding.get("identity_timeline_intervals_root")
    ):
        raise ValueError("daily_bars_source_identity_archive_invalid")
    active_intervals = [
        row for row in intervals if row.get("active_on_trade_date") is True
    ]
    exchanges = sorted(
        {
            "SSE" if str(row.get("security_code") or "").endswith(".SH") else "SZSE"
            for row in active_intervals
        }
    )
    if not active_intervals or any(exchange not in {"SSE", "SZSE"} for exchange in exchanges):
        raise ValueError("daily_bars_source_identity_axis_invalid")
    open_dates, calendar_invalid = _open_dates_by_exchange(
        source_calendar_path.read_bytes(),
        date_start=date_start,
        date_end=date_end,
        expected_exchanges=exchanges,
    )
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for interval in intervals:
        grouped.setdefault(str(interval.get("security_id") or ""), []).append(interval)
    if calendar_invalid or not _identity_interval_axis_valid(
        grouped,
        open_dates=open_dates,
        expected_daily_row_count=identity_binding.get(
            "identity_timeline_daily_row_count"
        ),
    ):
        raise ValueError("daily_bars_source_axis_invalid")

    with tempfile.TemporaryDirectory(prefix="daily-source-replay-") as temporary:
        connection = sqlite3.connect(Path(temporary) / "closure.sqlite3")
        try:
            connection.executescript(
                """
                PRAGMA journal_mode=OFF;
                PRAGMA temp_store=FILE;
                PRAGMA mmap_size=0;
                CREATE TABLE source_multiset (
                    canonical_json TEXT NOT NULL,
                    reasons_json TEXT NOT NULL,
                    not_applicable TEXT NOT NULL,
                    occurrence_count INTEGER NOT NULL,
                    PRIMARY KEY (canonical_json, reasons_json, not_applicable)
                ) WITHOUT ROWID;
                CREATE TABLE frozen_multiset (
                    canonical_json TEXT NOT NULL,
                    reasons_json TEXT NOT NULL,
                    not_applicable TEXT NOT NULL,
                    occurrence_count INTEGER NOT NULL,
                    PRIMARY KEY (canonical_json, reasons_json, not_applicable)
                ) WITHOUT ROWID;
                CREATE TABLE observed (
                    key1 TEXT NOT NULL,
                    key2 TEXT NOT NULL,
                    occurrence_count INTEGER NOT NULL,
                    PRIMARY KEY (key1, key2)
                ) WITHOUT ROWID;
                CREATE TABLE expected (
                    key1 TEXT NOT NULL,
                    key2 TEXT NOT NULL,
                    PRIMARY KEY (key1, key2)
                ) WITHOUT ROWID;
                CREATE TABLE gap_events (
                    key1 TEXT NOT NULL,
                    key2 TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    PRIMARY KEY (key1, key2, kind)
                ) WITHOUT ROWID;
                """
            )
            source_count = 0
            for source in _iter_canonical_jsonl_path(
                source_rows_path,
                "daily_bars_archived_provider_rows",
            ):
                raw_date = str(
                    source.get("trade_date") or source.get("date") or ""
                ).replace("-", "")
                if _valid_date(raw_date) and not date_start <= raw_date <= date_end:
                    continue
                projected, reasons = _project_daily_bar(source)
                not_applicable = (
                    "proven_suspension"
                    if "provider_reported_suspension_requires_admitted_control"
                    in reasons
                    else ""
                )
                values = (
                    _canonical_json_text(projected),
                    _canonical_json_text({"reasons": reasons}),
                    not_applicable,
                )
                connection.execute(
                    """INSERT INTO source_multiset VALUES (?, ?, ?, 1)
                    ON CONFLICT DO UPDATE SET occurrence_count=occurrence_count+1""",
                    values,
                )
                connection.execute(
                    """INSERT INTO observed VALUES (?, ?, 1)
                    ON CONFLICT DO UPDATE SET occurrence_count=occurrence_count+1""",
                    (projected["ts_code"], projected["trade_date"]),
                )
                source_count += 1
            validity_iterator = iter(
                _iter_canonical_jsonl_path(
                    validity_path,
                    "daily_bars_archived_frozen_validity",
                )
            )
            frozen_count = 0
            for row in _iter_canonical_jsonl_path(
                rows_path,
                "daily_bars_archived_frozen_rows",
            ):
                try:
                    validity = next(validity_iterator)
                except StopIteration as exc:
                    raise ValueError("daily_bars_source_frozen_validity_missing") from exc
                reasons = [
                    value
                    for value in validity.get("reasons") or ()
                    if value != "duplicate_security_day"
                ]
                values = (
                    _canonical_json_text(row),
                    _canonical_json_text({"reasons": reasons}),
                    str(validity.get("not_applicable_candidate") or ""),
                )
                connection.execute(
                    """INSERT INTO frozen_multiset VALUES (?, ?, ?, 1)
                    ON CONFLICT DO UPDATE SET occurrence_count=occurrence_count+1""",
                    values,
                )
                frozen_count += 1
            try:
                next(validity_iterator)
            except StopIteration:
                pass
            else:
                raise ValueError("daily_bars_source_frozen_validity_extra")
            mismatch_query = """
                SELECT canonical_json, reasons_json, not_applicable, occurrence_count
                FROM source_multiset
                EXCEPT
                SELECT canonical_json, reasons_json, not_applicable, occurrence_count
                FROM frozen_multiset
            """
            reverse_query = """
                SELECT canonical_json, reasons_json, not_applicable, occurrence_count
                FROM frozen_multiset
                EXCEPT
                SELECT canonical_json, reasons_json, not_applicable, occurrence_count
                FROM source_multiset
            """
            if (
                source_count != frozen_count
                or _sqlite_scalar(
                    connection, f"SELECT COUNT(*) FROM ({mismatch_query})"
                )
                or _sqlite_scalar(
                    connection, f"SELECT COUNT(*) FROM ({reverse_query})"
                )
            ):
                raise ValueError("daily_bars_source_projection_replay_mismatch")
            for interval in active_intervals:
                code = str(interval.get("security_code") or "")
                exchange = "SSE" if code.endswith(".SH") else "SZSE"
                start = str(interval.get("trade_date_start") or "")
                end = str(interval.get("trade_date_end") or "")
                try:
                    connection.executemany(
                        "INSERT INTO expected VALUES (?, ?)",
                        (
                            (code, trade_date)
                            for trade_date in sorted(open_dates.get(exchange, set()))
                            if start <= trade_date <= end
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise ValueError("daily_bars_source_expected_axis_overlap") from exc
            _replay_market_gaps(
                connection,
                gaps_path,
                dataset="daily_bars",
                date_start=date_start,
                date_end=date_end,
            )
            missing_query = """SELECT e.key1, e.key2 FROM expected AS e
                LEFT JOIN observed AS o ON o.key1=e.key1 AND o.key2=e.key2
                WHERE o.key1 IS NULL"""
            extra_query = """SELECT o.key1, o.key2 FROM observed AS o
                LEFT JOIN expected AS e ON e.key1=o.key1 AND e.key2=o.key2
                WHERE e.key1 IS NULL"""
            duplicate_query = """SELECT key1, key2 FROM observed
                WHERE occurrence_count > 1"""
            for kind, query in (
                ("missing", missing_query),
                ("extra", extra_query),
                ("duplicate", duplicate_query),
            ):
                if _gap_set_mismatch(connection, kind, query):
                    raise ValueError("daily_bars_source_gap_replay_mismatch")
            missing_count = _sqlite_scalar(
                connection, f"SELECT COUNT(*) FROM ({missing_query})"
            )
            extra_count = _sqlite_scalar(
                connection, f"SELECT COUNT(*) FROM ({extra_query})"
            )
            duplicate_count = _sqlite_scalar(
                connection, f"SELECT COUNT(*) FROM ({duplicate_query})"
            )
            expected_count = _sqlite_scalar(connection, "SELECT COUNT(*) FROM expected")
            observed_expected_count = expected_count - missing_count
            return {
                "coverage": {
                    "expected_security_day_count": expected_count,
                    "observed_security_day_count": observed_expected_count,
                    "missing_security_day_count": missing_count,
                    "extra_security_day_count": extra_count,
                    "duplicate_security_day_count": duplicate_count,
                    "provisional_exact_cover": not (
                        missing_count or extra_count or duplicate_count
                    ),
                },
                "expected_axis_root": _sqlite_expected_validation_table_root(
                    connection
                ),
                "row_count": source_count,
            }
        finally:
            connection.close()


def _sqlite_expected_validation_table_root(connection: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    for code, trade_date in connection.execute(
        "SELECT key1, key2 FROM expected ORDER BY key1, key2"
    ):
        digest.update(
            _canonical_json_text(
                {"trade_date": str(trade_date), "ts_code": str(code)}
            ).encode("utf-8")
            + b"\n"
        )
    return digest.hexdigest()


def _replay_market_gaps(
    connection: sqlite3.Connection,
    path: Path,
    *,
    dataset: str,
    date_start: str,
    date_end: str,
) -> int:
    gap_row_count = 0
    previous_code: str | None = None
    for gap in _iter_canonical_jsonl_path(path, f"{dataset}_gaps"):
        gap_row_count += 1
        if dataset == "trade_calendar":
            if gap_row_count != 1 or set(gap) != {
                "missing_exchange_days",
                "extra_exchange_days",
                "duplicate_exchange_days",
            }:
                raise ValueError("trade_calendar_gap_schema_invalid")
            groups = (
                ("missing", gap["missing_exchange_days"]),
                ("extra", gap["extra_exchange_days"]),
                ("duplicate", gap["duplicate_exchange_days"]),
            )
            for kind, rows in groups:
                if not isinstance(rows, list) or rows != sorted(
                    rows,
                    key=lambda row: (
                        str(row.get("exchange") or "") if isinstance(row, Mapping) else "",
                        str(row.get("trade_date") or "") if isinstance(row, Mapping) else "",
                    ),
                ):
                    raise ValueError("trade_calendar_gap_order_invalid")
                for row in rows:
                    if (
                        not isinstance(row, Mapping)
                        or set(row) != {"exchange", "trade_date"}
                        or type(row.get("exchange")) is not str
                        or type(row.get("trade_date")) is not str
                    ):
                        raise ValueError("trade_calendar_gap_entry_invalid")
                    _insert_gap_event(
                        connection,
                        str(row["exchange"]),
                        str(row["trade_date"]),
                        kind,
                    )
        else:
            if set(gap) != {
                "ts_code",
                "missing_trade_dates",
                "extra_trade_dates",
                "duplicate_trade_dates",
            }:
                raise ValueError("daily_bars_gap_schema_invalid")
            if type(gap.get("ts_code")) is not str:
                raise ValueError("daily_bars_gap_code_invalid")
            code = gap["ts_code"]
            if previous_code is not None and code <= previous_code:
                raise ValueError("daily_bars_gap_order_invalid")
            previous_code = code
            event_count = 0
            for kind, field in (
                ("missing", "missing_trade_dates"),
                ("extra", "extra_trade_dates"),
                ("duplicate", "duplicate_trade_dates"),
            ):
                dates = gap[field]
                if (
                    not isinstance(dates, list)
                    or dates != sorted(set(dates))
                    or any(not isinstance(value, str) for value in dates)
                    or (
                        kind == "missing"
                        and (
                            not code.endswith((".SH", ".SZ"))
                            or any(not _valid_date(value) for value in dates)
                        )
                    )
                ):
                    raise ValueError("daily_bars_gap_dates_invalid")
                for trade_date in dates:
                    _insert_gap_event(connection, code, trade_date, kind)
                    event_count += 1
            if not event_count:
                raise ValueError("daily_bars_empty_gap_row_invalid")
    return gap_row_count


def _insert_gap_event(
    connection: sqlite3.Connection,
    key1: str,
    key2: str,
    kind: str,
) -> None:
    if kind not in {"missing", "extra", "duplicate"}:
        raise ValueError("market_gap_entry_invalid")
    try:
        connection.execute(
            "INSERT INTO gap_events VALUES (?, ?, ?)", (key1, key2, kind)
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError("market_gap_entry_duplicate") from exc


def _gap_kind_count(connection: sqlite3.Connection, kind: str) -> int:
    row = connection.execute(
        "SELECT COUNT(*) FROM gap_events WHERE kind=?", (kind,)
    ).fetchone()
    if row is None or type(row[0]) is not int:
        raise ValueError("market_gap_count_invalid")
    return int(row[0])


def _gap_set_mismatch(
    connection: sqlite3.Connection,
    kind: str,
    actual_query: str,
) -> bool:
    return bool(
        _sqlite_scalar(
            connection,
            f"SELECT COUNT(*) FROM ({actual_query} EXCEPT SELECT key1, key2 FROM gap_events WHERE kind='{kind}')",
        )
        or _sqlite_scalar(
            connection,
            f"SELECT COUNT(*) FROM (SELECT key1, key2 FROM gap_events WHERE kind='{kind}' EXCEPT {actual_query})",
        )
    )


def _daily_resource_execution_valid(
    resource: Mapping[str, Any], blockers: set[str]
) -> bool:
    if resource.get("engine") == "in_memory_assessment":
        return bool(
            resource
            == {
                "engine": "in_memory_assessment",
                "input_mode": "bounded_bytes",
                "output_mode": "bounded_bytes",
                "resume_supported": False,
            }
            and "daily_bars_resource_mode_in_memory_diagnostic_only" in blockers
        )
    return bool(
        resource.get("engine") == "sqlite_disk_spill"
        and resource.get("input_mode") == "jsonl_stream"
        and resource.get("output_mode") == "jsonl_stream"
        and resource.get("batch_row_limit") == DAILY_BARS_BATCH_ROW_LIMIT
        and resource.get("sqlite_cache_limit_mib")
        == DAILY_BARS_SQLITE_CACHE_MIB
        and resource.get("sqlite_spill_limit_bytes")
        == DAILY_BARS_SQLITE_SPILL_LIMIT_BYTES
        and resource.get("sqlite_mmap_bytes") == 0
        and _sha256_text(resource.get("work_identity"))
        and resource.get("resume_schema_version")
        == DAILY_BARS_RESUME_SCHEMA_VERSION
        and resource.get("resume_implementation_root")
        == _daily_bars_resume_implementation_root()
        and resource.get("checkpoint_granularity")
        == "committed_input_prefix_and_projected_state"
        and _sha256_text(resource.get("checkpoint_input_prefix_sha256"))
        and _sha256_text(resource.get("checkpoint_projected_rows_sha256"))
        and _sha256_text(resource.get("checkpoint_static_axis_sha256"))
        and _sha256_text(resource.get("checkpoint_joined_state_sha256"))
        and _sha256_text(resource.get("source_binding_root"))
        and _sha256_text(resource.get("expected_axis_binding_root"))
        and resource.get("resume_supported") is True
        and "daily_bars_streaming_resume_not_implemented" not in blockers
        and "daily_bars_resource_mode_in_memory_diagnostic_only" not in blockers
    )


def _daily_disk_source_axis_binding_valid(
    payload: Mapping[str, Any], replay: Mapping[str, Any]
) -> bool:
    """Bind production disk evidence to current code and immutable PIT inputs."""

    source = payload.get("source_binding") or {}
    pit_axis = payload.get("pit_axis") or {}
    identity = pit_axis.get("identity_timeline_binding") or {}
    coverage = payload.get("coverage") or {}
    scope = payload.get("scope") or {}
    resource = payload.get("resource_execution") or {}
    sha_fields = (
        "capture_content_hash",
        "capture_manifest_sha256",
        "capture_contract_id",
        "request_plan_hash",
        "normalized_replay_root",
        "archived_normalized_replay_root",
        "capture_adapter_implementation_root",
        "current_capture_toolchain_implementation_root",
        "market_projection_implementation_root",
        "normalizer_conflicts_root",
        "provider_daily_bars_replay_sha256",
        "trade_calendar_replay_sha256",
        "identity_timeline_binding_root",
        "identity_intervals_archive_sha256",
        "identity_binding_archive_sha256",
    )
    identity_sha_fields = (
        "identity_timeline_content_hash",
        "identity_timeline_derivation_content_hash",
        "identity_timeline_derivation_implementation_root",
        "identity_timeline_rows_root",
        "identity_timeline_intervals_root",
        "identity_timeline_manifest_sha256",
    )
    if (
        not isinstance(source, Mapping)
        or not isinstance(pit_axis, Mapping)
        or not isinstance(identity, Mapping)
        or not isinstance(coverage, Mapping)
        or not all(_sha256_text(source.get(field)) for field in sha_fields)
        or not all(_sha256_text(identity.get(field)) for field in identity_sha_fields)
        or source.get("capture_source_profile_id") != payload.get("profile_id")
        or not _capture_scope_contains(
            source,
            str(scope.get("date_start") or ""),
            str(scope.get("date_end") or ""),
        )
        or source.get("parser_roles")
        != ["provider_daily_bars", "trade_calendar", "conflicts"]
        or source.get("market_projection_schema_version")
        != "market_state_projection_v2"
        or source.get("archived_normalized_replay_root")
        != source.get("normalized_replay_root")
        or source.get("market_projection_implementation_root")
        != _market_projection_implementation_root()
        or source.get("current_capture_toolchain_implementation_root")
        != _baostock_implementation_root()
        or source.get("capture_toolchain_implementation_match")
        is not (
            source.get("capture_adapter_implementation_root")
            == source.get("current_capture_toolchain_implementation_root")
        )
        or type(source.get("publication_signature_verified")) is not bool
        or type(source.get("wire_replay_verified")) is not bool
        or type(source.get("parser_replay_verified")) is not bool
        or source.get("normalizer_conflicts_bound") is not True
        or type(source.get("normalizer_conflict_count")) is not int
        or int(source["normalizer_conflict_count"]) < 0
        or type(source.get("provider_daily_bars_replay_size_bytes")) is not int
        or int(source["provider_daily_bars_replay_size_bytes"]) < 0
        or type(source.get("trade_calendar_replay_size_bytes")) is not int
        or int(source["trade_calendar_replay_size_bytes"]) < 0
        or type(source.get("identity_intervals_archive_size_bytes")) is not int
        or int(source["identity_intervals_archive_size_bytes"]) < 0
        or type(source.get("identity_binding_archive_size_bytes")) is not int
        or int(source["identity_binding_archive_size_bytes"]) < 0
        or source.get("independent_signed_capture_proof_archived") is not False
        or source.get("independent_source_reference_resolution_required")
        is not True
        or source.get("identity_timeline_binding_root")
        != canonical_hash(identity)
        or identity.get("identity_timeline_derivation_implementation_root")
        != _identity_derivation_implementation_root()
        or identity.get("identity_timeline_rows_root_semantics")
        != "sha256_canonical_jsonl_trade_date_security_id_v1"
        or identity.get("current_state_fallback_used") is not False
        or identity.get("independent_admission_verdict_required") is not True
        or pit_axis.get("expected_security_day_root")
        != replay.get("expected_axis_root")
        or pit_axis.get("expected_security_day_root") is None
        or coverage.get("expected_security_day_count")
        != replay.get("coverage", {}).get("expected_security_day_count")
    ):
        return False
    expected_axis_binding = {
        "date_start": scope.get("date_start"),
        "date_end": scope.get("date_end"),
        "expected_security_day_count": coverage.get(
            "expected_security_day_count"
        ),
        "expected_security_day_root": pit_axis.get(
            "expected_security_day_root"
        ),
        "exchange_open_dates_root": pit_axis.get(
            "exchange_open_dates_root"
        ),
        "trade_calendar_replay_sha256": source.get(
            "trade_calendar_replay_sha256"
        ),
        "identity_timeline_binding_root": source.get(
            "identity_timeline_binding_root"
        ),
    }
    return bool(
        _sha256_text(pit_axis.get("exchange_open_dates_root"))
        and pit_axis.get("expected_axis_binding_root")
        == canonical_hash(expected_axis_binding)
        and resource.get("expected_axis_binding_root")
        == canonical_hash(expected_axis_binding)
        and resource.get("source_binding_root") == canonical_hash(source)
        and resource.get("checkpoint_input_prefix_sha256")
        == source.get("provider_daily_bars_replay_sha256")
    )


def _market_source_blockers_consistent(payload: Mapping[str, Any]) -> bool:
    """Recompute every source-state blocker in both directions."""

    dataset = str(payload.get("dataset") or "")
    if dataset not in {"trade_calendar", "daily_bars"}:
        return False
    source = payload.get("source_binding") or {}
    scope = payload.get("scope") or {}
    blockers = set(payload.get("blockers") or ())
    prefix = dataset
    is_disk_daily = dataset == "daily_bars" and (
        payload.get("resource_execution") or {}
    ).get("engine") == "sqlite_disk_spill"

    def exact(condition: bool, blocker: str) -> bool:
        return (blocker in blockers) is condition

    checks = (
        exact(
            source.get("operator_capture_contract_authorized") is not True,
            "operator_capture_contract_not_currently_authorized",
        ),
        exact(
            source.get("provider_origin_attested") is not True,
            "provider_origin_not_attested",
        ),
        exact(
            source.get("capture_runtime_isolation_verified") is not True,
            "capture_runtime_isolation_not_attested",
        ),
        exact(
            source.get("publication_signature_verified") is not True,
            "capture_publication_signature_unverified",
        ),
        exact(
            source.get("publication_signature_verified") is not True,
            f"{prefix}_signed_publication_unverified",
        ),
        exact(
            source.get("wire_replay_verified") is not True,
            f"{prefix}_signed_wire_replay_unverified",
        ),
        exact(
            source.get("parser_replay_verified") is not True,
            f"{prefix}_parser_replay_unverified",
        ),
        exact(
            source.get("normalizer_conflicts_bound") is not True,
            f"{prefix}_normalizer_conflicts_unbound",
        ),
        exact(
            source.get("normalizer_conflicts_bound") is True
            and source.get("normalizer_conflict_count") != 0,
            f"{prefix}_normalization_conflicts_present",
        ),
        exact(
            source.get("capture_toolchain_implementation_match") is not True,
            f"{prefix}_capture_toolchain_identity_mismatch",
        ),
        exact(
            is_disk_daily
            and source.get("archived_normalized_replay_root")
            != source.get("normalized_replay_root"),
            f"{prefix}_archived_source_replay_root_mismatch",
        ),
        exact(
            source.get("capture_source_profile_id") != payload.get("profile_id"),
            f"{prefix}_capture_profile_binding_failed",
        ),
        exact(
            not _capture_scope_contains(
                source,
                str(scope.get("date_start") or ""),
                str(scope.get("date_end") or ""),
            ),
            f"{prefix}_capture_scope_binding_failed",
        ),
        exact(
            "current_replay_implementation_identity_mismatch"
            in set(source.get("capture_qualification_blockers") or ()),
            "current_replay_implementation_identity_mismatch",
        ),
    )
    normalized = source.get("published_normalized_identical")
    if normalized is not None:
        checks += (
            exact(
                normalized is not True,
                f"{prefix}_published_normalization_replay_mismatch",
            ),
        )
    checks += (
        exact(
            True,
            f"{prefix}_independent_source_reference_resolution_pending",
        ),
    )
    return all(checks)


def _market_source_replay_state_valid(payload: Mapping[str, Any]) -> bool:
    """Validate the bounded replay state and its two verification booleans."""

    dataset = payload.get("dataset")
    source = payload.get("source_binding")
    if dataset not in {"trade_calendar", "daily_bars"} or not isinstance(
        source, Mapping
    ):
        return False
    replay_blockers = source.get("normalized_replay_blockers")
    if (
        type(replay_blockers) is not list
        or any(
            type(value) is not str
            or value not in NORMALIZED_REPLAY_BLOCKER_VALUES
            for value in replay_blockers
        )
        or replay_blockers != sorted(set(replay_blockers))
    ):
        return False
    expected_verified = not replay_blockers
    exact_boolean_fields = (
        "capture_runtime_isolation_verified",
        "capture_toolchain_implementation_match",
        "normalizer_conflicts_bound",
        "operator_capture_contract_authorized",
        "parser_replay_verified",
        "provider_origin_attested",
        "publication_signature_verified",
        "wire_replay_verified",
    )
    if (
        any(type(source.get(field)) is not bool for field in exact_boolean_fields)
        or source.get("wire_replay_verified") is not expected_verified
        or source.get("parser_replay_verified") is not expected_verified
        or type(source.get("normalizer_conflict_count")) is not int
        or source["normalizer_conflict_count"] < 0
        or not _sha256_text(source.get("normalizer_conflicts_root"))
        or not _sha256_text(source.get("normalized_replay_root"))
        or not isinstance(source.get("capture_scope"), Mapping)
        or any(
            type(source["capture_scope"].get(field)) is not str
            for field in ("date_start", "date_end")
        )
    ):
        return False
    published = source.get("published_normalized_identical")
    resource = payload.get("resource_execution") or {}
    disk_daily = dataset == "daily_bars" and resource.get("engine") == "sqlite_disk_spill"
    return bool(
        (type(published) is bool)
        or (disk_daily and published is None)
    )


def _technical_blockers(dataset: str) -> set[str]:
    if dataset == "trade_calendar":
        return {
            "trade_calendar_exchange_day_exact_cover_failed",
            "trade_calendar_independent_source_reference_resolution_pending",
            "trade_calendar_capture_profile_binding_failed",
            "trade_calendar_capture_scope_binding_failed",
            "trade_calendar_capture_toolchain_identity_mismatch",
            "trade_calendar_profile_consumer_closure_failed",
            "trade_calendar_parser_replay_unverified",
            "trade_calendar_normalization_conflicts_present",
            "trade_calendar_normalizer_conflicts_unbound",
            "trade_calendar_published_normalization_replay_mismatch",
            "trade_calendar_required_value_validity_failed",
            "trade_calendar_signed_wire_replay_unverified",
            "trade_calendar_signed_publication_unverified",
        }
    if dataset == "daily_bars":
        return {
            "daily_bars_calendar_axis_invalid",
            "daily_bars_archived_source_replay_root_mismatch",
            "daily_bars_capture_profile_binding_failed",
            "daily_bars_capture_scope_binding_failed",
            "daily_bars_capture_toolchain_identity_mismatch",
            "daily_bars_lifecycle_axis_invalid",
            "daily_bars_identity_timeline_axis_invalid",
            "daily_bars_independent_source_reference_resolution_pending",
            "daily_bars_profile_consumer_closure_failed",
            "daily_bars_parser_replay_unverified",
            "daily_bars_normalization_conflicts_present",
            "daily_bars_normalizer_conflicts_unbound",
            "daily_bars_published_normalization_replay_mismatch",
            "daily_bars_required_value_validity_failed",
            "daily_bars_security_day_exact_cover_failed",
            "daily_bars_signed_wire_replay_unverified",
            "daily_bars_signed_publication_unverified",
        }
    raise ValueError("market_data_evidence_dataset_invalid")


def _index_technical_blockers() -> set[str]:
    return {
        "index_daily_bars_calendar_axis_invalid",
        "index_daily_bars_calendar_source_binding_failed",
        "index_daily_bars_index_day_exact_cover_failed",
        "index_daily_bars_independent_source_reference_resolution_pending",
        "index_daily_bars_profile_consumer_closure_failed",
        "index_daily_bars_published_normalization_replay_mismatch",
        "index_daily_bars_required_value_validity_failed",
    }


def _market_semantic_scalar_types_valid(payload: Mapping[str, Any]) -> bool:
    """Reject Python's bool-as-int aliasing across governed semantic counts."""

    boolean_fields = {
        *SAFETY_FLAGS,
        "all_required_values_valid",
        "capture_runtime_isolation_verified",
        "calendar_source_binding_verified",
        "capture_toolchain_implementation_match",
        "current_state_fallback_used",
        "daily_bars_projection_frozen_by_this_evidence",
        "derivation_complete",
        "formula_input_authorized",
        "formal_data_admission_ready",
        "identity_timeline_axis_complete",
        "independent_admission_verdict_required",
        "independent_signed_capture_proof_archived",
        "independent_source_reference_resolution_required",
        "normalizer_conflicts_bound",
        "operator_capture_contract_authorized",
        "parser_replay_verified",
        "profile_contract_exact",
        "published_normalized_identical",
        "provider_origin_attested",
        "provisional_exact_cover",
        "publication_signature_verified",
        "resume_supported",
        "valid",
        "wire_replay_verified",
    }

    def visit(value: object, key: str | None = None) -> bool:
        if key is not None and (
            key.endswith("_count") or key.endswith("_size_bytes")
        ):
            return type(value) is int and value >= 0
        if key in boolean_fields:
            return type(value) is bool
        if isinstance(value, Mapping):
            return all(type(child_key) is str and visit(child, child_key) for child_key, child in value.items())
        if isinstance(value, list):
            return all(visit(child) for child in value)
        return True

    dataset = payload.get("dataset")
    expected_manifest_fields = _MARKET_MANIFEST_FIELDS.get(dataset)
    projection = payload.get("provider_neutral_projection")
    coverage = payload.get("coverage")
    validity = payload.get("validity")
    consumer = payload.get("consumer_closure")
    scope = payload.get("scope")
    coverage_fields = {
        DATASET: {
            "expected_index_day_count",
            "observed_index_day_count",
            "missing_index_day_count",
            "extra_index_day_count",
            "duplicate_index_day_count",
            "provisional_exact_cover",
        },
        "trade_calendar": {
            "expected_exchange_day_count",
            "observed_exchange_day_count",
            "missing_exchange_day_count",
            "extra_exchange_day_count",
            "duplicate_exchange_day_count",
            "provisional_exact_cover",
        },
        "daily_bars": {
            "expected_security_day_count",
            "observed_security_day_count",
            "missing_security_day_count",
            "extra_security_day_count",
            "duplicate_security_day_count",
            "provisional_exact_cover",
        },
    }
    required_validity_fields = {
        "valid_row_count",
        "invalid_row_count",
        "required_field_count",
        "all_required_values_valid",
    }
    allowed_validity_fields = required_validity_fields | {
        "not_applicable_candidate_count",
        "not_applicable_authority",
        "not_applicable_authority_status",
    }
    return bool(
        isinstance(payload, Mapping)
        and type(dataset) is str
        and dataset in coverage_fields
        and expected_manifest_fields is not None
        and set(payload) == expected_manifest_fields
        and type(payload.get("profile_id")) is str
        and type(payload.get("technical_evidence_status")) is str
        and isinstance(scope, Mapping)
        and isinstance(projection, Mapping)
        and isinstance(coverage, Mapping)
        and set(coverage) == coverage_fields[dataset]
        and isinstance(validity, Mapping)
        and required_validity_fields <= set(validity) <= allowed_validity_fields
        and isinstance(consumer, Mapping)
        and type(projection.get("dataset")) is str
        and type(projection.get("record_count")) is int
        and projection["record_count"] >= 0
        and type(consumer.get("formula_input_authorized")) is bool
        and type(consumer.get("profile_contract_exact")) is bool
        and type(payload.get("blockers")) is list
        and all(type(value) is str and value for value in payload["blockers"])
        and payload["blockers"] == sorted(set(payload["blockers"]))
        and all(
            type(scope.get(field)) is str
            for field in ("access_view", "date_start", "date_end", "as_of_market_date")
        )
        and _market_nested_schema_valid(payload)
        and visit(payload)
    )


def _market_nested_schema_valid(payload: Mapping[str, Any]) -> bool:
    """Require one closed manifest shape for each market evidence mode."""

    dataset = payload.get("dataset")
    scope = payload.get("scope")
    projection = payload.get("provider_neutral_projection")
    coverage = payload.get("coverage")
    validity = payload.get("validity")
    consumer = payload.get("consumer_closure")
    safety = payload.get("safety")
    source = payload.get("source_binding")
    if not all(
        isinstance(value, Mapping)
        for value in (
            scope,
            projection,
            coverage,
            validity,
            consumer,
            safety,
            source,
        )
    ):
        return False
    if (
        set(scope) != _MARKET_SCOPE_FIELDS
        or set(consumer) != _MARKET_CONSUMER_FIELDS
        or set(safety) != set(SAFETY_FLAGS)
        or set(projection)
        != (
            _INDEX_PROJECTION_FIELDS
            if dataset == DATASET
            else _MARKET_PROJECTION_FIELDS
        )
    ):
        return False
    resource = payload.get("resource_execution")
    disk_daily = bool(
        dataset == "daily_bars"
        and isinstance(resource, Mapping)
        and resource.get("engine") == "sqlite_disk_spill"
    )
    if set(validity) != (
        _DAILY_DISK_VALIDITY_FIELDS
        if disk_daily
        else _MARKET_VALIDITY_FIELDS
    ):
        return False
    if not _market_source_binding_schema_valid(
        str(dataset), source, disk_daily=disk_daily
    ):
        return False
    if dataset == DATASET:
        return "pit_axis" not in payload and "resource_execution" not in payload
    pit_axis = payload.get("pit_axis")
    if not isinstance(pit_axis, Mapping):
        return False
    if dataset == "trade_calendar":
        return bool(
            set(pit_axis) == _TRADE_CALENDAR_PIT_AXIS_FIELDS
            and "resource_execution" not in payload
        )
    if dataset != "daily_bars" or not isinstance(resource, Mapping):
        return False
    expected_pit_fields = (
        _DAILY_DISK_PIT_AXIS_FIELDS
        if disk_daily
        else _DAILY_MEMORY_PIT_AXIS_FIELDS
    )
    expected_resource_fields = (
        _DAILY_DISK_RESOURCE_FIELDS
        if disk_daily
        else _DAILY_MEMORY_RESOURCE_FIELDS
    )
    if (
        set(pit_axis) != expected_pit_fields
        or set(resource) != expected_resource_fields
    ):
        return False
    if not disk_daily:
        return True
    identity = pit_axis.get("identity_timeline_binding")
    return bool(
        isinstance(identity, Mapping)
        and set(identity) == _IDENTITY_TIMELINE_BINDING_FIELDS
    )


def _market_source_binding_schema_valid(
    dataset: str,
    source: Mapping[str, Any],
    *,
    disk_daily: bool,
) -> bool:
    """Close the diagnostic, signed-capture and disk-archive source variants."""

    observed = set(source)
    if dataset == DATASET:
        if observed not in {
            _INDEX_SOURCE_DIAGNOSTIC_FIELDS,
            _INDEX_SOURCE_PRODUCTION_FIELDS,
        }:
            return False
        required_hashes = {
            "capture_content_hash",
            "normalized_replay_root",
            "calendar_source_sha256",
            "archived_index_replay_sha256",
            "archived_index_calendar_sha256",
        }
        if observed == _INDEX_SOURCE_PRODUCTION_FIELDS:
            required_hashes |= {
                "capture_manifest_sha256",
                "capture_contract_id",
                "request_plan_hash",
            }
            calendar_contract = source.get("calendar_source_contract_sha256")
            if calendar_contract is not None and not _sha256_text(
                calendar_contract
            ):
                return False
            if (
                type(source.get("publication_signature_verified")) is not bool
                or type(source.get("published_normalized_identical")) is not bool
                or type(source.get("calendar_source_binding_verified")) is not bool
                or not _string_list(source.get("capture_qualification_blockers"))
                or source.get("capture_qualification") is not None
                and type(source.get("capture_qualification")) is not str
            ):
                return False
        return bool(
            all(_sha256_text(source.get(field)) for field in required_hashes)
            and type(source.get("capture_generation_id")) is str
            and bool(source["capture_generation_id"])
            and type(source.get("operator_capture_contract_authorized")) is bool
            and type(source.get("provider_origin_attested")) is bool
            and type(source.get("capture_runtime_isolation_verified")) is bool
            and type(source.get("independent_signed_capture_proof_archived"))
            is bool
            and type(
                source.get("independent_source_reference_resolution_required")
            )
            is bool
            and type(source.get("archived_index_replay_size_bytes")) is int
            and source["archived_index_replay_size_bytes"] >= 0
            and type(source.get("archived_index_calendar_size_bytes")) is int
            and source["archived_index_calendar_size_bytes"] >= 0
        )

    if dataset not in {"trade_calendar", "daily_bars"}:
        return False
    production_with_published = _MARKET_SOURCE_PRODUCTION_FIELDS | {
        "published_normalized_identical"
    }
    if disk_daily:
        variants = {
            _DAILY_SOURCE_DIAGNOSTIC_DISK_FIELDS,
            _DAILY_SOURCE_PRODUCTION_DISK_FIELDS,
        }
    else:
        variants = {
            _MARKET_SOURCE_DIAGNOSTIC_FIELDS,
            production_with_published,
        }
    if observed not in variants:
        return False
    scope = source.get("capture_scope")
    scope_fields = set(scope) if isinstance(scope, Mapping) else set()
    allowed_scope_fields = {
        frozenset({"date_start", "date_end"}),
        frozenset({"date_start", "date_end", "request_start", "request_end"}),
    }
    if (
        not isinstance(scope, Mapping)
        or frozenset(scope_fields) not in allowed_scope_fields
        or any(type(scope.get(field)) is not str for field in scope_fields)
    ):
        return False
    required_hashes = {
        "capture_content_hash",
        "capture_contract_id",
        "request_plan_hash",
        "normalized_replay_root",
        "capture_adapter_implementation_root",
        "current_capture_toolchain_implementation_root",
        "normalizer_conflicts_root",
    }
    if disk_daily:
        required_hashes |= {
            "capture_manifest_sha256",
            "market_projection_implementation_root",
            "provider_daily_bars_replay_sha256",
            "trade_calendar_replay_sha256",
            "identity_timeline_binding_root",
            "identity_intervals_archive_sha256",
            "identity_binding_archive_sha256",
            "archived_normalized_replay_root",
        }
    if not all(_sha256_text(source.get(field)) for field in required_hashes):
        return False
    if (
        type(source.get("capture_generation_id")) is not str
        or not source["capture_generation_id"]
        or type(source.get("capture_source_profile_id")) is not str
        or not source["capture_source_profile_id"]
        or not _string_list(source.get("normalized_replay_blockers"))
    ):
        return False
    if "parser_roles" in source and not _string_list(source.get("parser_roles")):
        return False
    if "capture_qualification_blockers" in source and not _string_list(
        source.get("capture_qualification_blockers")
    ):
        return False
    if "capture_qualification" in source and (
        source.get("capture_qualification") is not None
        and type(source.get("capture_qualification")) is not str
    ):
        return False
    return True


def _string_list(value: object) -> bool:
    return bool(
        type(value) is list
        and all(type(item) is str and item for item in value)
        and len(value) == len(set(value))
    )


def _dataset_profile_contract(
    profile: Mapping[str, Any],
    *,
    dataset: str,
    granularity: str,
    approved_fields: Sequence[str],
    consumer_roles: Sequence[str],
) -> tuple[Mapping[str, Any], bool]:
    matches = [
        row
        for row in profile.get("datasets") or ()
        if isinstance(row, Mapping) and row.get("dataset") == dataset
    ]
    contract: Mapping[str, Any] = matches[0] if len(matches) == 1 else {}
    exact = bool(
        len(matches) == 1
        and contract.get("role") == "base-required"
        and contract.get("coverage_granularity") == granularity
        and len(contract.get("approved_fields") or ()) == len(approved_fields)
        and set(contract.get("approved_fields") or ()) == set(approved_fields)
        and len(contract.get("consumer_roles") or ()) == len(consumer_roles)
        and set(contract.get("consumer_roles") or ()) == set(consumer_roles)
        and contract.get("evidence_grade") == "governed_receipts"
    )
    return contract, exact


def _governance_blockers(
    profile: Mapping[str, Any],
    contract: Mapping[str, Any],
    source_binding: Mapping[str, Any],
) -> set[str]:
    blockers: set[str] = set()
    if profile.get("activation_status") != "active":
        blockers.add("data_admission_profile_human_approval_required")
    if not contract.get("acquisition_contracts"):
        blockers.add("provider_acquisition_contract_not_activated")
    if source_binding.get("operator_capture_contract_authorized") is not True:
        blockers.add("operator_capture_contract_not_currently_authorized")
    if source_binding.get("provider_origin_attested") is not True:
        blockers.add("provider_origin_not_attested")
    if source_binding.get("capture_runtime_isolation_verified") is not True:
        blockers.add("capture_runtime_isolation_not_attested")
    if source_binding.get("publication_signature_verified") is not True:
        blockers.add("capture_publication_signature_unverified")
    if "current_replay_implementation_identity_mismatch" in set(
        source_binding.get("capture_qualification_blockers") or ()
    ):
        blockers.add("current_replay_implementation_identity_mismatch")
    return blockers


def _project_calendar_row(
    source: Mapping[str, Any], expected_exchanges: Sequence[str]
) -> tuple[dict[str, Any], list[str]]:
    exchange = str(source.get("exchange") or "")
    trade_date = str(source.get("trade_date") or source.get("cal_date") or "")
    raw_is_open = source.get("is_open")
    is_open = _strict_bool(raw_is_open)
    raw_previous = source.get("prev_trade_date")
    previous = None if raw_previous in {None, ""} else str(raw_previous)
    projected = {
        "exchange": exchange,
        "trade_date": trade_date,
        "is_open": is_open,
        "prev_trade_date": previous,
    }
    reasons: list[str] = []
    if exchange not in expected_exchanges:
        reasons.append("exchange_invalid")
    if not _valid_date(trade_date):
        reasons.append("trade_date_invalid")
    if is_open is None:
        reasons.append("is_open_invalid")
    if previous is None:
        reasons.append("pre_span_previous_open_seed_missing")
    elif not _valid_date(previous):
        reasons.append("prev_trade_date_invalid")
    elif previous is not None and _valid_date(trade_date) and previous >= trade_date:
        reasons.append("prev_trade_date_not_before_trade_date")
    return projected, sorted(set(reasons))


def _project_daily_bar(
    source: Mapping[str, Any],
) -> tuple[dict[str, str], list[str]]:
    trade_date = str(source.get("trade_date") or source.get("date") or "").replace("-", "")
    code = str(source.get("ts_code") or "")
    projected = {
        "ts_code": code,
        "trade_date": trade_date,
        "open": str(source.get("open") or ""),
        "high": str(source.get("high") or ""),
        "low": str(source.get("low") or ""),
        "close": str(source.get("close") or ""),
        "pre_close": str(source.get("pre_close") or source.get("preclose") or ""),
        "volume": str(source.get("volume") or ""),
        "amount": str(source.get("amount") or ""),
    }
    reasons: list[str] = []
    if not code.endswith((".SH", ".SZ")):
        reasons.append("ts_code_invalid")
    if not _valid_date(trade_date):
        reasons.append("trade_date_invalid")
    raw_status = source.get("provider_trade_status", source.get("tradestatus"))
    status = str(raw_status) if raw_status is not None else ""
    if status not in {"0", "1"}:
        reasons.append("provider_trade_status_invalid")
    price_fields = ("open", "high", "low", "close")
    numeric_fields = (
        ("pre_close", "volume", "amount")
        if status == "0"
        else (*price_fields, "pre_close", "volume", "amount")
    )
    decimals: dict[str, Decimal] = {}
    for field in numeric_fields:
        try:
            value = Decimal(projected[field])
        except (InvalidOperation, ValueError):
            reasons.append(f"{field}_not_numeric")
            continue
        if not value.is_finite():
            reasons.append(f"{field}_not_finite")
            continue
        decimals[field] = value
    for field in (*price_fields, "pre_close"):
        if field in decimals and decimals[field] <= 0:
            reasons.append(f"{field}_not_positive")
    if "volume" in decimals and decimals["volume"] < 0:
        reasons.append("volume_negative")
    if "amount" in decimals and decimals["amount"] < 0:
        reasons.append("amount_negative")
    if status == "1" and all(field in decimals for field in price_fields):
        if not (
            decimals["low"]
            <= min(decimals["open"], decimals["close"])
            <= max(decimals["open"], decimals["close"])
            <= decimals["high"]
        ):
            reasons.append("ohlc_order_invalid")
    if status == "0":
        if any(projected[field] for field in price_fields):
            reasons.append("suspended_ohlc_must_be_empty")
        reasons.append("provider_reported_suspension_requires_admitted_control")
    return projected, sorted(set(reasons))


def _open_dates_by_exchange(
    payload: bytes,
    *,
    date_start: str,
    date_end: str,
    expected_exchanges: Sequence[str],
) -> tuple[dict[str, set[str]], bool]:
    open_dates: dict[str, set[str]] = {}
    observed: set[tuple[str, str]] = set()
    invalid = False
    for row in _read_jsonl_bytes(payload, "trade_calendar"):
        exchange = str(row.get("exchange") or "")
        trade_date = str(row.get("trade_date") or row.get("cal_date") or "")
        if _valid_date(trade_date) and not date_start <= trade_date <= date_end:
            continue
        is_open = _strict_bool(row.get("is_open"))
        key = (exchange, trade_date)
        if (
            exchange not in set(expected_exchanges)
            or not _valid_date(trade_date)
            or is_open is None
            or key in observed
        ):
            invalid = True
            continue
        observed.add(key)
        if is_open:
            open_dates.setdefault(exchange, set()).add(trade_date)
    expected_keys = {
        (exchange, trade_date)
        for exchange in expected_exchanges
        for trade_date in _date_span(date_start, date_end)
    }
    if not open_dates or observed != expected_keys:
        invalid = True
    return open_dates, invalid


def _lifecycles(payload: bytes) -> tuple[dict[str, dict[str, Any]], bool]:
    rows: dict[str, dict[str, Any]] = {}
    invalid = False
    for source in _read_jsonl_bytes(payload, "security_lifecycles"):
        code = str(source.get("ts_code") or source.get("security_id") or "")
        inferred_exchange = (
            "SSE" if code.endswith(".SH") else "SZSE" if code.endswith(".SZ") else ""
        )
        exchange = str(source.get("exchange") or inferred_exchange)
        list_date = str(source.get("list_date") or "")
        raw_delist = source.get("delist_date")
        delist_date = None if raw_delist in {None, "", "99999999"} else str(raw_delist)
        if (
            code in rows
            or not code.endswith((".SH", ".SZ"))
            or exchange not in {"SSE", "SZSE"}
            or not _valid_date(list_date)
            or (delist_date is not None and not _valid_date(delist_date))
            or (delist_date is not None and delist_date < list_date)
        ):
            invalid = True
            continue
        rows[code] = {
            "ts_code": code,
            "exchange": exchange,
            "list_date": list_date,
            "delist_date": delist_date,
        }
    if not rows:
        invalid = True
    return rows, invalid


def _daily_bars_work_binding(
    capture: Mapping[str, Any],
    contract: Mapping[str, Any],
    identity_binding: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind resumable work to validated raw, PIT and executable identities."""

    return {
        "schema_version": DAILY_BARS_RESUME_SCHEMA_VERSION,
        "capture_content_hash": capture.get("content_hash"),
        "capture_catalog_sha256": capture.get("capture_catalog_sha256"),
        "capture_manifest_sha256": sha256_file(str(capture["manifest_path"])),
        "capture_contract_id": capture.get("contract_id"),
        "capture_request_plan_hash": capture.get("request_plan_hash"),
        "capture_toolchain_implementation_root": _baostock_implementation_root(),
        "market_projection_implementation_root": (
            _market_projection_implementation_root()
        ),
        "resume_implementation_root": (
            _daily_bars_resume_implementation_root()
        ),
        "identity_timeline_content_hash": identity_binding.get(
            "identity_timeline_content_hash"
        ),
        "identity_timeline_derivation_implementation_root": identity_binding.get(
            "identity_timeline_derivation_implementation_root"
        ),
        "identity_timeline_rows_root": identity_binding.get(
            "identity_timeline_rows_root"
        ),
        "identity_timeline_intervals_root": identity_binding.get(
            "identity_timeline_intervals_root"
        ),
        "identity_timeline_manifest_sha256": identity_binding.get(
            "identity_timeline_manifest_sha256"
        ),
        "profile_id": profile.get("profile_id"),
        "profile_content_root": canonical_hash(profile),
        "scope": dict(contract.get("scope") or {}),
    }


def _reject_symlink_components(path: Path, *, error: str) -> None:
    """Reject every existing symlink component without resolving through it."""

    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError(error)


def _fsync_directory(path: Path) -> None:
    """Persist directory entries without following a concurrently replaced symlink."""

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError("daily_bars_directory_fsync_path_invalid") from exc
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _durable_directory(path: Path, *, error: str) -> None:
    _reject_symlink_components(path, error=error)
    existed = path.exists()
    path.mkdir(parents=True, exist_ok=True)
    _reject_symlink_components(path, error=error)
    if not path.is_dir():
        raise ValueError(error)
    if not existed:
        _fsync_directory(path)
        _fsync_directory(path.parent)


@contextmanager
def _daily_bars_work_lock(work_parent: Path, work_identity: str) -> Iterator[None]:
    """Serialize one immutable work identity without waiting or lock stealing."""

    if not _sha256_text(work_identity):
        raise ValueError("daily_bars_resume_work_identity_invalid")
    _durable_directory(
        work_parent,
        error="daily_bars_resume_work_parent_symlink_forbidden",
    )
    lock_path = work_parent / f".{work_identity}.lock"
    _reject_symlink_components(
        lock_path,
        error="daily_bars_resume_lock_symlink_forbidden",
    )
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise ValueError("daily_bars_resume_lock_path_invalid") from exc
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("daily_bars_resume_work_identity_locked") from exc
        os.ftruncate(descriptor, 0)
        os.write(descriptor, (work_identity + "\n").encode("ascii"))
        os.fsync(descriptor)
        _fsync_directory(work_parent)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _bind_daily_bars_work_directory(
    work_directory: Path,
    *,
    work_binding: Mapping[str, Any],
    work_identity: str,
) -> None:
    """Create or verify the immutable identity of one resumable work root."""

    _durable_directory(
        work_directory,
        error="daily_bars_resume_work_root_symlink_forbidden",
    )
    binding_path = work_directory / "work_binding.json"
    expected = {
        "schema_version": DAILY_BARS_RESUME_SCHEMA_VERSION,
        "work_identity": work_identity,
        "work_binding": dict(work_binding),
    }
    expected["content_hash"] = canonical_hash(expected)
    if binding_path.exists() or binding_path.is_symlink():
        if binding_path.is_symlink() or read_json(binding_path) != expected:
            raise ValueError("daily_bars_resume_work_binding_drift")
    else:
        if any(work_directory.iterdir()):
            raise ValueError("daily_bars_resume_work_root_not_empty")
        _write_bytes_exclusive_fsync(
            binding_path,
            _canonical_json_text(expected).encode("utf-8") + b"\n",
        )
        _fsync_directory(work_directory)


def _resume_market_state_replay(
    capture: str | Path,
    *,
    work_directory: Path,
    work_binding: Mapping[str, Any],
    work_identity: str,
) -> tuple[dict[str, Path], str]:
    """Reuse only a complete content-bound replay, otherwise publish one."""

    replay_directory = work_directory / "replay"
    _reject_symlink_components(
        replay_directory,
        error="daily_bars_resume_replay_root_symlink_forbidden",
    )
    if replay_directory.exists() or replay_directory.is_symlink():
        return _validate_market_state_replay_checkpoint(
            replay_directory,
            work_binding=work_binding,
            work_identity=work_identity,
        )
    with tempfile.TemporaryDirectory(
        prefix=f".{work_identity}.replay-",
        dir=work_directory.parent,
    ) as staging_name:
        staging = Path(staging_name)
        paths, replay_root = _replay_market_state_capture_to_directory(
            capture,
            output_directory=staging,
        )
        artifacts = [
            {
                "role": role,
                "relative_path": path.relative_to(staging).as_posix(),
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
            for role, path in sorted(paths.items())
        ]
        checkpoint = {
            "schema_version": DAILY_BARS_REPLAY_CHECKPOINT_SCHEMA_VERSION,
            "work_identity": work_identity,
            "work_binding_hash": canonical_hash(work_binding),
            "normalized_replay_root": replay_root,
            "artifacts": artifacts,
        }
        checkpoint["content_hash"] = canonical_hash(checkpoint)
        atomic_json(staging / "replay_checkpoint.json", checkpoint)
        _fsync_directory(staging)
        os.replace(staging, replay_directory)
        _fsync_directory(work_directory)
    return _validate_market_state_replay_checkpoint(
        replay_directory,
        work_binding=work_binding,
        work_identity=work_identity,
    )


def _validate_market_state_replay_checkpoint(
    replay_directory: Path,
    *,
    work_binding: Mapping[str, Any],
    work_identity: str,
) -> tuple[dict[str, Path], str]:
    if replay_directory.is_symlink() or not replay_directory.is_dir():
        raise ValueError("daily_bars_resume_replay_root_invalid")
    checkpoint_path = replay_directory / "replay_checkpoint.json"
    if not checkpoint_path.is_file() or checkpoint_path.is_symlink():
        raise ValueError("daily_bars_resume_replay_checkpoint_missing")
    checkpoint = read_json(checkpoint_path)
    semantic = {
        key: value for key, value in checkpoint.items() if key != "content_hash"
    }
    rows = checkpoint.get("artifacts")
    if (
        checkpoint.get("schema_version")
        != DAILY_BARS_REPLAY_CHECKPOINT_SCHEMA_VERSION
        or checkpoint.get("content_hash") != canonical_hash(semantic)
        or checkpoint.get("work_identity") != work_identity
        or checkpoint.get("work_binding_hash") != canonical_hash(work_binding)
        or not isinstance(rows, list)
    ):
        raise ValueError("daily_bars_resume_replay_checkpoint_invalid")
    paths: dict[str, Path] = {}
    expected_files = {"replay_checkpoint.json"}
    artifacts: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("daily_bars_resume_replay_artifact_invalid")
        role = str(row.get("role") or "")
        relative = Path(str(row.get("relative_path") or ""))
        path = replay_directory / relative
        if (
            role in paths
            or role
            not in {"provider_daily_bars", "trade_calendar", "conflicts"}
            or relative.is_absolute()
            or ".." in relative.parts
            or not path.is_file()
            or path.is_symlink()
            or row.get("sha256") != sha256_file(path)
            or row.get("size_bytes") != path.stat().st_size
        ):
            raise ValueError("daily_bars_resume_replay_artifact_invalid")
        paths[role] = path
        expected_files.add(relative.as_posix())
        artifacts.append(
            {
                "role": role,
                "sha256": row["sha256"],
                "size_bytes": row["size_bytes"],
            }
        )
    observed_files = {
        path.relative_to(replay_directory).as_posix()
        for path in replay_directory.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if (
        set(paths) != {"provider_daily_bars", "trade_calendar", "conflicts"}
        or observed_files != expected_files
        or any(path.is_symlink() for path in replay_directory.rglob("*"))
        or checkpoint.get("normalized_replay_root")
        != canonical_hash(sorted(artifacts, key=lambda row: row["role"]))
    ):
        raise ValueError("daily_bars_resume_replay_closure_invalid")
    return paths, str(checkpoint["normalized_replay_root"])


def _replay_market_state_capture_to_directory(
    capture: str | Path,
    *,
    output_directory: str | Path,
) -> tuple[dict[str, Path], str]:
    """Stream signed state raw bytes into market-only projection files."""

    validated = validate_free_provider_backfill(capture)
    root = Path(str(validated["manifest_path"])).parent
    output = Path(output_directory)
    if output.is_symlink() or not output.is_dir() or any(output.iterdir()):
        raise ValueError("market_data_replay_output_directory_invalid")
    plan = read_json(root / "request_plan.json")
    request_rows = plan.get("requests") or ()
    request_ids = {
        str(row.get("request_id") or "")
        for row in request_rows
        if isinstance(row, Mapping)
    }
    terminal = {
        str(row["request_id"]): row
        for row in _iter_jsonl_path(
            root / "capture_journal.jsonl", "capture_journal"
        )
        if row.get("event_type") == "capture_attempt_terminal"
    }
    if not request_ids or set(terminal) != request_ids:
        raise ValueError("market_data_replay_terminal_closure_invalid")
    normalized = output / "normalized"
    normalized.mkdir()
    paths = {
        "provider_daily_bars": normalized / "provider_daily_bars.jsonl",
        "trade_calendar": normalized / "trade_calendar.jsonl",
        "conflicts": normalized / "conflicts.jsonl",
    }
    handles = {role: path.open("wb") for role, path in paths.items()}

    def write(role: str, row: Mapping[str, Any]) -> None:
        handles[role].write(_canonical_json_text(row).encode() + b"\n")

    history_fields = [
        "date",
        "code",
        "open",
        "high",
        "low",
        "close",
        "preclose",
        "volume",
        "amount",
        "tradestatus",
        "isST",
    ]
    try:
        for request in request_rows:
            if not isinstance(request, Mapping):
                raise ValueError("market_data_replay_request_invalid")
            request_id = str(request.get("request_id") or "")
            event = terminal[request_id]
            relative = Path(
                str(event.get("raw_envelope_relative_path") or "")
            )
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("market_data_replay_raw_path_invalid")
            wrapper = read_json(root / relative)
            raw_payload = base64.b64decode(
                str(wrapper.get("raw_payload_base64") or ""), validate=True
            )
            fields, items = _baostock_logical_rows(raw_payload)
            metadata = request.get("metadata") or {}
            if not isinstance(metadata, Mapping):
                raise ValueError("market_data_replay_metadata_invalid")
            source_hash = str(wrapper.get("raw_payload_sha256") or "")
            if metadata.get("case") == "trade_calendar":
                if fields != ["calendar_date", "is_trading_day"]:
                    write(
                        "conflicts",
                        {
                            "request_id": request_id,
                            "reason": "calendar_schema_mismatch",
                            "fields": fields,
                        },
                    )
                    continue
                previous_open: str | None = None
                for item in items:
                    trade_date = str(item[0]).replace("-", "")
                    is_open = str(item[1]) == "1"
                    for exchange in ("SSE", "SZSE"):
                        write(
                            "trade_calendar",
                            {
                                "exchange": exchange,
                                "trade_date": trade_date,
                                "is_open": is_open,
                                "prev_trade_date": previous_open,
                                "source_request_id": request_id,
                                "source_payload_sha256": source_hash,
                            },
                        )
                    if is_open:
                        previous_open = trade_date
                continue
            if metadata.get("case") != "history" or fields != history_fields:
                write(
                    "conflicts",
                    {
                        "request_id": request_id,
                        "reason": "history_schema_mismatch",
                        "fields": fields,
                    },
                )
                continue
            code = str(metadata.get("ts_code") or "")
            seen_dates: set[str] = set()
            if not items:
                write(
                    "conflicts",
                    {
                        "request_id": request_id,
                        "reason": "provider_empty_for_identity_population",
                        "ts_code": code,
                    },
                )
            for item in items:
                trade_date = str(item[0]).replace("-", "")
                observed_code = _from_baostock_code(str(item[1]))
                status = str(item[9])
                is_st = str(item[10])
                reason = (
                    "provider_code_mismatch"
                    if observed_code != code
                    else "duplicate_trade_date"
                    if trade_date in seen_dates
                    else "state_value_invalid"
                    if status not in {"0", "1"} or is_st not in {"0", "1"}
                    else None
                )
                if reason is not None:
                    write(
                        "conflicts",
                        {
                            "request_id": request_id,
                            "reason": reason,
                            "expected_ts_code": code,
                            "observed_ts_code": observed_code,
                            "trade_date": trade_date,
                        },
                    )
                    continue
                seen_dates.add(trade_date)
                write(
                    "provider_daily_bars",
                    {
                        "ts_code": code,
                        "trade_date": trade_date,
                        "open": str(item[2]),
                        "high": str(item[3]),
                        "low": str(item[4]),
                        "close": str(item[5]),
                        "pre_close": str(item[6]),
                        "volume": str(item[7]),
                        "amount": str(item[8]),
                        "provider_trade_status": int(status),
                        "source_request_id": request_id,
                        "source_payload_sha256": source_hash,
                    },
                )
    finally:
        for handle in handles.values():
            _flush_file(handle)
            handle.close()
    replay_root = canonical_hash(
        [
            {
                "role": role,
                "sha256": sha256_file(paths[role]),
                "size_bytes": paths[role].stat().st_size,
            }
            for role in sorted(paths)
        ]
    )
    return paths, replay_root


def _market_projection_implementation_root() -> str:
    return canonical_hash(
        {
            "schema_version": "market_state_projection_v2",
            "module_sha256": sha256_file(Path(__file__)),
            "replay": inspect.getsource(
                _replay_market_state_capture_to_directory
            ),
            "row_projection": inspect.getsource(_project_daily_bar),
            "wire_protocol_root": baostock_wire_protocol_root(),
        }
    )


def _replay_state_capture(
    capture: str | Path,
    required_roles: Sequence[str],
) -> tuple[
    dict[str, Any],
    Path,
    dict[str, Any],
    dict[str, bytes],
    str,
    tuple[str, ...],
]:
    capture_manifest = validate_free_provider_backfill(capture)
    capture_root = Path(str(capture_manifest["manifest_path"])).parent
    contract = read_json(capture_root / "activity_contract.json")
    if contract.get("provider") != "baostock":
        raise ValueError("market_data_capture_provider_invalid")
    replay_blockers: tuple[str, ...] = ()
    try:
        replayed, replay_root = replay_normalized_artifacts(
            capture_manifest["manifest_path"],
            normalizer=normalize_baostock_state_capture,
            required_roles=required_roles,
        )
    except ValueError:
        try:
            replayed = {
                role: _published_artifact_bytes(
                    capture_manifest, capture_root, role
                )
                for role in required_roles
            }
        except ValueError as missing:
            raise ValueError(
                "market_data_capture_current_parser_replay_failed"
            ) from missing
        replay_root = canonical_hash(
            [
                {
                    "role": role,
                    "published_fallback": True,
                    "sha256": hashlib.sha256(replayed[role]).hexdigest(),
                    "size_bytes": len(replayed[role]),
                }
                for role in sorted(replayed)
            ]
        )
        replay_blockers = ("current_parser_replay_failed",)
    return (
        capture_manifest,
        capture_root,
        contract,
        replayed,
        replay_root,
        replay_blockers,
    )


def _published_artifact_bytes(
    capture: Mapping[str, Any], root: Path, role: str
) -> bytes:
    matches = [
        row
        for row in capture.get("normalized_artifacts") or ()
        if isinstance(row, Mapping) and row.get("role") == role
    ]
    if len(matches) != 1:
        raise ValueError(f"market_data_capture_{role}_artifact_missing")
    relative = Path(str(matches[0].get("relative_path") or ""))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"market_data_capture_{role}_artifact_path_invalid")
    path = root / relative
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"market_data_capture_{role}_artifact_invalid")
    return path.read_bytes()


def _signed_capture_binding(
    capture: Mapping[str, Any],
    contract: Mapping[str, Any],
    *,
    replay_root: str,
    published_normalized_identical: bool | None,
    parser_roles: Sequence[str],
    replay_blockers: Sequence[str],
    normalizer_conflicts: bytes | Path,
) -> dict[str, Any]:
    replay_blocker_list = list(replay_blockers)
    if (
        any(
            type(value) is not str
            or value not in NORMALIZED_REPLAY_BLOCKER_VALUES
            for value in replay_blocker_list
        )
        or replay_blocker_list != sorted(set(replay_blocker_list))
    ):
        raise ValueError("market_normalized_replay_blockers_invalid")
    adapter = contract.get("adapter_identity") or {}
    if isinstance(normalizer_conflicts, bytes):
        conflict_rows = _read_jsonl_bytes(
            normalizer_conflicts, "normalizer_conflicts"
        )
        if _jsonl_bytes(conflict_rows) != normalizer_conflicts:
            raise ValueError("normalizer_conflicts_canonical_jsonl_invalid")
        conflict_count = len(conflict_rows)
        conflict_root = hashlib.sha256(normalizer_conflicts).hexdigest()
    else:
        conflict_count = sum(
            1
            for _ in _iter_canonical_jsonl_path(
                normalizer_conflicts, "normalizer_conflicts"
            )
        )
        conflict_root = sha256_file(normalizer_conflicts)
    current_toolchain_root = _baostock_implementation_root()
    captured_toolchain_root = adapter.get("implementation_root")
    binding = {
        "capture_generation_id": capture.get("generation_id"),
        "capture_source_profile_id": contract.get("source_profile_id"),
        "capture_scope": dict(contract.get("scope") or {}),
        "capture_content_hash": capture.get("content_hash"),
        "capture_manifest_sha256": sha256_file(str(capture["manifest_path"])),
        "capture_contract_id": capture.get("contract_id"),
        "request_plan_hash": capture.get("request_plan_hash"),
        "publication_signature_verified": capture.get("publication_signature_verified") is True,
        "wire_replay_verified": not replay_blocker_list,
        "parser_replay_verified": not replay_blocker_list,
        "normalized_replay_root": replay_root,
        "normalized_replay_blockers": replay_blocker_list,
        "parser_roles": list(parser_roles),
        "published_normalized_identical": published_normalized_identical,
        "capture_adapter_implementation_root": adapter.get("implementation_root"),
        "current_capture_toolchain_implementation_root": (
            current_toolchain_root
        ),
        "capture_toolchain_implementation_match": (
            captured_toolchain_root == current_toolchain_root
        ),
        "market_projection_implementation_root": (
            _market_projection_implementation_root()
        ),
        "market_projection_schema_version": "market_state_projection_v2",
        "normalizer_conflicts_bound": True,
        "normalizer_conflict_count": conflict_count,
        "normalizer_conflicts_root": conflict_root,
        "operator_capture_contract_authorized": False,
        "provider_origin_attested": False,
        "capture_runtime_isolation_verified": False,
        "capture_qualification": "technical_wire_replay_only",
        "capture_qualification_blockers": [
            "operator_capture_contract_not_currently_authorized",
            "provider_origin_not_attested",
            "capture_runtime_isolation_not_attested",
        ],
    }
    if published_normalized_identical is None:
        binding.pop("published_normalized_identical")
    return binding


def _load_identity_timeline_evidence(
    path: str | Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        payload = validate_security_identity_lifecycle_intervals(path)
    except (OSError, ValueError) as exc:
        raise ValueError(
            "daily_bars_identity_timeline_evidence_invalid"
        ) from exc
    intervals = payload.get("intervals")
    if (
        not isinstance(intervals, list)
        or payload.get("derivation_schema_version")
        != "pit_security_identity_lifecycle_timeline_v2"
        or not _sha256_text(payload.get("derivation_content_hash"))
        or not _sha256_text(payload.get("rows_root"))
        or payload.get("rows_root_semantics")
        != "sha256_canonical_jsonl_trade_date_security_id_v1"
        or type(payload.get("daily_row_count")) is not int
        or int(payload["daily_row_count"]) <= 0
        or payload.get("derivation_complete") is not True
        or payload.get("identity_coverage_complete") is not True
        or payload.get("blockers") != []
        or payload.get("survivorship_backfill_used") is not False
        or payload.get("current_state_backfill_used") is not False
    ):
        raise ValueError("daily_bars_identity_timeline_evidence_invalid")
    active_by_code: dict[str, list[tuple[str, str]]] = {}
    for interval in intervals:
        if not isinstance(interval, Mapping):
            raise ValueError("daily_bars_identity_timeline_interval_invalid")
        code = str(interval.get("security_code") or "")
        start = str(interval.get("trade_date_start") or "")
        end = str(interval.get("trade_date_end") or "")
        if (
            not str(interval.get("security_id") or "")
            or not code.endswith((".SH", ".SZ"))
            or not _valid_date(start)
            or not _valid_date(end)
            or start > end
            or interval.get("identity_resolved") is not True
            or interval.get("identity_unique") is not True
            or type(interval.get("active_on_trade_date")) is not bool
        ):
            raise ValueError("daily_bars_identity_timeline_interval_invalid")
        if interval.get("active_on_trade_date") is True:
            active_by_code.setdefault(code, []).append((start, end))
    for ranges in active_by_code.values():
        previous_end: str | None = None
        for start, end in sorted(ranges):
            if previous_end is not None and start <= previous_end:
                raise ValueError(
                    "daily_bars_identity_timeline_active_interval_overlap"
                )
            previous_end = end
    manifest_path = Path(str(payload["manifest_path"]))
    return payload, {
        "schema_version": payload["schema_version"],
        "identity_timeline_content_hash": payload["content_hash"],
        "identity_timeline_derivation_content_hash": payload[
            "derivation_content_hash"
        ],
        "identity_timeline_derivation_implementation_root": payload[
            "derivation_implementation_root"
        ],
        "identity_timeline_rows_root": payload["rows_root"],
        "identity_timeline_rows_root_semantics": payload[
            "rows_root_semantics"
        ],
        "identity_timeline_daily_row_count": payload["daily_row_count"],
        "identity_timeline_intervals_root": payload["intervals_root"],
        "identity_timeline_manifest_sha256": sha256_file(manifest_path),
        "current_state_fallback_used": False,
        "independent_admission_verdict_required": payload.get(
            "independent_admission_verdict_required"
        )
        is True,
    }


def _identity_interval_axis_valid(
    intervals_by_security: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    open_dates: Mapping[str, set[str]],
    expected_daily_row_count: Any,
) -> bool:
    """Prove one and only one identity state for every bound trade date."""

    trade_dates = sorted(
        {value for values in open_dates.values() for value in values}
    )
    if (
        not intervals_by_security
        or not trade_dates
        or type(expected_daily_row_count) is not int
        or expected_daily_row_count
        != len(intervals_by_security) * len(trade_dates)
    ):
        return False
    for security_id, raw_intervals in intervals_by_security.items():
        intervals = sorted(
            (dict(row) for row in raw_intervals),
            key=lambda row: (
                str(row.get("trade_date_start") or ""),
                str(row.get("trade_date_end") or ""),
                str(row.get("security_code") or ""),
            ),
        )
        if not security_id or not intervals:
            return False
        for trade_date in trade_dates:
            matches = [
                row
                for row in intervals
                if str(row.get("trade_date_start") or "")
                <= trade_date
                <= str(row.get("trade_date_end") or "")
            ]
            if len(matches) != 1:
                return False
            state = matches[0]
            code = str(state.get("security_code") or "")
            exchange = (
                "SSE"
                if code.endswith(".SH")
                else "SZSE"
                if code.endswith(".SZ")
                else ""
            )
            if (
                str(state.get("security_id") or "") != security_id
                or state.get("identity_resolved") is not True
                or state.get("identity_unique") is not True
                or not exchange
                or (
                    state.get("active_on_trade_date") is True
                    and trade_date not in open_dates.get(exchange, set())
                )
                or type(state.get("active_on_trade_date")) is not bool
            ):
                return False
    return True


def _date_span(start: str, end: str) -> list[str]:
    current = datetime.strptime(start, "%Y%m%d")
    terminal = datetime.strptime(end, "%Y%m%d")
    rows: list[str] = []
    while current <= terminal:
        rows.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return rows


def _pair_rows(pairs: Sequence[tuple[str, str]], subject: str) -> list[dict[str, str]]:
    return [{subject: left, "trade_date": right} for left, right in pairs]


def _strict_bool(value: object) -> bool | None:
    if value in {True, 1, "1"}:
        return True
    if value in {False, 0, "0"}:
        return False
    return None


def _capture_scope_contains(
    source_binding: Mapping[str, Any], date_start: str, date_end: str
) -> bool:
    scope = source_binding.get("capture_scope") or {}
    return bool(
        isinstance(scope, Mapping)
        and _valid_date(str(scope.get("date_start") or ""))
        and _valid_date(str(scope.get("date_end") or ""))
        and str(scope["date_start"]) <= date_start
        and str(scope["date_end"]) >= date_end
    )


def _require_scope(date_start: str, date_end: str, dataset: str) -> None:
    if not _valid_date(date_start) or not _valid_date(date_end) or date_start > date_end:
        raise ValueError(f"{dataset}_scope_invalid")


def _profile_contract(
    profile: Mapping[str, Any],
) -> tuple[Mapping[str, Any], bool]:
    matches = [
        row
        for row in profile.get("datasets") or ()
        if isinstance(row, Mapping) and row.get("dataset") == DATASET
    ]
    contract: Mapping[str, Any] = matches[0] if len(matches) == 1 else {}
    exact = bool(
        len(matches) == 1
        and contract.get("role") == "base-required"
        and contract.get("coverage_granularity") == "index_day"
        and len(contract.get("approved_fields") or ()) == len(CANONICAL_FIELDS)
        and set(contract.get("approved_fields") or ()) == set(CANONICAL_FIELDS)
        and contract.get("consumer_roles") == ["benchmark_control"]
        and contract.get("evidence_grade") == "governed_receipts"
    )
    return contract, exact


def _calendar_dates(
    payload: bytes,
    *,
    date_start: str,
    date_end: str,
) -> tuple[set[str], list[str], bool]:
    rows = _read_jsonl_bytes(payload, "trade_calendar")
    counts: dict[str, int] = {}
    invalid = False
    for row in rows:
        date = str(row.get("trade_date") or row.get("cal_date") or "")
        is_open = row.get("is_open")
        if not _valid_date(date) or is_open not in {True, False, 0, 1, "0", "1"}:
            invalid = True
            continue
        if date_start <= date <= date_end:
            counts[date] = counts.get(date, 0) + 1
    duplicates = sorted(date for date, count in counts.items() if count > 1)
    expected = {
        str(row.get("trade_date") or row.get("cal_date") or "")
        for row in rows
        if date_start
        <= str(row.get("trade_date") or row.get("cal_date") or "")
        <= date_end
        and row.get("is_open") in {True, 1, "1"}
    }
    return expected, duplicates, invalid


def _project_row(source: Mapping[str, Any]) -> tuple[dict[str, str], list[str]]:
    date = str(source.get("date") or "").replace("-", "")
    code = str(source.get("ts_code") or "")
    projected = {
        "index_code": code,
        "trade_date": date,
        "open": str(source.get("open") or ""),
        "high": str(source.get("high") or ""),
        "low": str(source.get("low") or ""),
        "close": str(source.get("close") or ""),
        "pre_close": str(source.get("preclose") or ""),
        "volume": str(source.get("volume") or ""),
        "amount": str(source.get("amount") or ""),
    }
    reasons: list[str] = []
    if code != INDEX_CODE:
        reasons.append("index_code_invalid")
    if not _valid_date(date):
        reasons.append("trade_date_invalid")
    decimals: dict[str, Decimal] = {}
    for field in ("open", "high", "low", "close", "pre_close", "volume", "amount"):
        value = projected[field]
        try:
            decimal = Decimal(value)
        except (InvalidOperation, ValueError):
            reasons.append(f"{field}_not_numeric")
            continue
        if not decimal.is_finite():
            reasons.append(f"{field}_not_finite")
            continue
        decimals[field] = decimal
    for field in ("open", "high", "low", "close", "pre_close"):
        if field in decimals and decimals[field] <= 0:
            reasons.append(f"{field}_not_positive")
    if "volume" in decimals and decimals["volume"] < 0:
        reasons.append("volume_negative")
    if "amount" in decimals and decimals["amount"] < 0:
        reasons.append("amount_negative")
    if all(field in decimals for field in ("open", "high", "low", "close")):
        if not (
            decimals["low"]
            <= min(decimals["open"], decimals["close"])
            <= max(decimals["open"], decimals["close"])
            <= decimals["high"]
        ):
            reasons.append("ohlc_order_invalid")
    if str(source.get("tradestatus") or "") != "1":
        reasons.append("provider_trade_status_not_open")
    return projected, sorted(set(reasons))


def _read_jsonl_bytes(payload: bytes, role: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{role}_utf8_invalid") from exc
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{role}_jsonl_invalid") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{role}_row_invalid")
        rows.append(row)
    return rows


def _jsonl_file_summary(path: Path, role: str) -> tuple[int, int, int]:
    count = valid_count = invalid_count = 0
    with path.open("rb") as handle:
        for raw_line in handle:
            if not raw_line.strip():
                continue
            try:
                row = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"{role}_jsonl_invalid") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{role}_row_invalid")
            count += 1
            if row.get("valid") is True:
                valid_count += 1
            elif row.get("valid") is False:
                invalid_count += 1
    return count, valid_count, invalid_count


def _iter_jsonl_path(path: Path, role: str) -> Iterator[dict[str, Any]]:
    with path.open("rb") as handle:
        for raw_line in handle:
            if not raw_line.strip():
                continue
            try:
                row = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"{role}_jsonl_invalid") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{role}_row_invalid")
            yield row


def _iter_canonical_jsonl_path(
    path: Path, role: str
) -> Iterator[dict[str, Any]]:
    """Yield exact canonical JSONL, rejecting blanks and alternate encodings."""

    if not path.is_file() or path.is_symlink():
        raise ValueError(f"{role}_path_invalid")
    with path.open("rb") as handle:
        for raw_line in handle:
            if not raw_line.strip() or not raw_line.endswith(b"\n"):
                raise ValueError(f"{role}_canonical_jsonl_invalid")
            try:
                row = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"{role}_jsonl_invalid") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{role}_row_invalid")
            expected = _canonical_json_text(row).encode("utf-8") + b"\n"
            if raw_line != expected:
                raise ValueError(f"{role}_canonical_encoding_invalid")
            yield row


def _market_generation_tree_exact(
    root: Path, expected_files: set[str]
) -> bool:
    """Accept exactly the expected root-level regular files and no other inode."""

    try:
        if root.is_symlink() or not root.is_dir():
            return False
        observed: set[str] = set()
        for entry in root.iterdir():
            mode = entry.lstat().st_mode
            if not stat.S_ISREG(mode) or entry.name not in expected_files:
                return False
            observed.add(entry.name)
    except OSError:
        return False
    return observed == expected_files


def _canonical_json_text(row: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(row),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sqlite_scalar(connection: sqlite3.Connection, query: str) -> int:
    row = connection.execute(query).fetchone()
    if row is None or not isinstance(row[0], int):
        raise ValueError("daily_bars_sqlite_scalar_invalid")
    return int(row[0])


def _sqlite_expected_axis_root(connection: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    for code, trade_date in connection.execute(
        """SELECT ts_code, trade_date FROM expected
           ORDER BY ts_code, trade_date"""
    ):
        digest.update(
            _canonical_json_text(
                {
                    "trade_date": str(trade_date),
                    "ts_code": str(code),
                }
            ).encode("utf-8")
            + b"\n"
        )
    return digest.hexdigest()


def _validation_expected_axis_root(connection: sqlite3.Connection) -> str:
    """Rebuild the expected daily axis from observed rows and exact gap events."""

    digest = hashlib.sha256()
    query = """
        SELECT key1, key2 FROM observed
        EXCEPT SELECT key1, key2 FROM gap_events WHERE kind='extra'
        UNION
        SELECT key1, key2 FROM gap_events WHERE kind='missing'
        ORDER BY key1, key2
    """
    for code, trade_date in connection.execute(query):
        digest.update(
            _canonical_json_text(
                {"trade_date": str(trade_date), "ts_code": str(code)}
            ).encode("utf-8")
            + b"\n"
        )
    return digest.hexdigest()


def _input_prefix_hasher(path: Path, offset: int) -> Any:
    """Hash exactly the committed raw prefix and leave a hasher resumable in RAM."""

    if (
        type(offset) is not int
        or offset < 0
        or not path.is_file()
        or path.is_symlink()
        or offset > path.stat().st_size
    ):
        raise ValueError("daily_bars_resume_input_prefix_invalid")
    digest = hashlib.sha256()
    remaining = offset
    with path.open("rb") as handle:
        while remaining:
            chunk = handle.read(min(1024 * 1024, remaining))
            if not chunk:
                raise ValueError("daily_bars_resume_input_prefix_truncated")
            digest.update(chunk)
            remaining -= len(chunk)
    return digest


def _extend_projected_rows_digest(
    previous_digest: str,
    rows: Sequence[tuple[int, str, str, str, str, str | None]],
) -> str:
    """Extend a deterministic chain over every committed projected value."""

    if not _sha256_text(previous_digest):
        raise ValueError("daily_bars_resume_projected_digest_invalid")
    digest = previous_digest
    for ordinal, code, trade_date, canonical_json, reasons_json, not_applicable in rows:
        record = _canonical_json_text(
            {
                "canonical_json": canonical_json,
                "not_applicable_candidate": not_applicable,
                "ordinal": ordinal,
                "reasons_json": reasons_json,
                "trade_date": trade_date,
                "ts_code": code,
            }
        ).encode("utf-8")
        digest = hashlib.sha256(bytes.fromhex(digest) + record + b"\n").hexdigest()
    return digest


def _replay_projected_rows_digest(connection: sqlite3.Connection) -> str:
    digest = EMPTY_SHA256
    expected_ordinal = 0
    for raw in connection.execute(
        """SELECT ordinal, ts_code, trade_date, canonical_json,
                  reasons_json, not_applicable_candidate
           FROM bars ORDER BY ordinal"""
    ):
        row = (
            int(raw[0]),
            str(raw[1]),
            str(raw[2]),
            str(raw[3]),
            str(raw[4]),
            None if raw[5] is None else str(raw[5]),
        )
        if row[0] != expected_ordinal:
            raise ValueError("daily_bars_resume_projected_ordinal_invalid")
        digest = _extend_projected_rows_digest(digest, (row,))
        expected_ordinal += 1
    return digest


def _sqlite_static_axis_digest(connection: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    for row in connection.execute(
        """SELECT interval_ordinal, security_id, ts_code, exchange,
                  trade_date_start, trade_date_end
           FROM identity_intervals ORDER BY interval_ordinal"""
    ):
        digest.update(
            _canonical_json_text(
                {
                    "exchange": str(row[3]),
                    "interval_ordinal": int(row[0]),
                    "security_id": str(row[1]),
                    "trade_date_end": str(row[5]),
                    "trade_date_start": str(row[4]),
                    "ts_code": str(row[2]),
                }
            ).encode("utf-8")
            + b"\n"
        )
    digest.update(b"--open-dates--\n")
    for exchange, trade_date in connection.execute(
        "SELECT exchange, trade_date FROM open_dates ORDER BY exchange, trade_date"
    ):
        digest.update(
            _canonical_json_text(
                {"exchange": str(exchange), "trade_date": str(trade_date)}
            ).encode("utf-8")
            + b"\n"
        )
    return digest.hexdigest()


def _sqlite_joined_state_digest(connection: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    queries = (
        (
            "expected",
            """SELECT security_id, ts_code, trade_date FROM expected
            ORDER BY security_id, ts_code, trade_date""",
        ),
        (
            "observed",
            """SELECT ts_code, trade_date, occurrence_count FROM observed
            ORDER BY ts_code, trade_date""",
        ),
        (
            "duplicates",
            """SELECT ts_code, trade_date, occurrence_count FROM duplicate_counts
            ORDER BY ts_code, trade_date""",
        ),
        (
            "gaps",
            """SELECT ts_code, trade_date, kind FROM gap_events
            ORDER BY ts_code, trade_date, kind""",
        ),
    )
    for role, query in queries:
        digest.update(f"--{role}--\n".encode("ascii"))
        for row in connection.execute(query):
            digest.update(
                _canonical_json_text(
                    {"values": [value for value in row]}
                ).encode("utf-8")
                + b"\n"
            )
    return digest.hexdigest()


def _daily_bars_resume_state(
    connection: sqlite3.Connection,
    *,
    resume_binding: Mapping[str, Any],
    work_identity: str,
    replayed_rows_path: Path,
) -> dict[str, Any]:
    try:
        row = connection.execute(
            """SELECT schema_version, work_identity, resume_binding_json,
                      input_offset, next_ordinal, input_prefix_sha256,
                      projected_rows_sha256, static_axis_sha256,
                      joined_state_sha256, phase
               FROM resume_state WHERE singleton=1"""
        ).fetchone()
        tables = {
            str(item[0])
            for item in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            if not str(item[0]).startswith("sqlite_")
        }
    except sqlite3.DatabaseError as exc:
        raise ValueError("daily_bars_resume_database_schema_invalid") from exc
    if row is None or len(row) != 10:
        raise ValueError("daily_bars_resume_state_missing")
    (
        schema,
        observed_identity,
        binding_json,
        offset,
        ordinal,
        input_prefix_sha256,
        projected_rows_sha256,
        static_axis_sha256,
        joined_state_sha256,
        phase,
    ) = row
    base_tables = {"resume_state", "identity_intervals", "open_dates", "bars"}
    joined_tables = base_tables | {
        "expected",
        "observed",
        "duplicate_counts",
        "gap_events",
    }
    expected_tables = joined_tables if phase == "joined" else base_tables
    if (
        schema != DAILY_BARS_RESUME_SCHEMA_VERSION
        or observed_identity != work_identity
        or binding_json != _canonical_json_text(resume_binding)
        or type(offset) is not int
        or type(ordinal) is not int
        or offset < 0
        or offset > int(resume_binding["provider_daily_bars_size_bytes"])
        or ordinal < 0
        or not _sha256_text(input_prefix_sha256)
        or not _sha256_text(projected_rows_sha256)
        or not _sha256_text(static_axis_sha256)
        or not _sha256_text(joined_state_sha256)
        or phase not in {"ingesting", "ingested", "joined"}
        or tables != expected_tables
        or _sqlite_scalar(connection, "SELECT COUNT(*) FROM bars") != ordinal
        or _input_prefix_hasher(replayed_rows_path, offset).hexdigest()
        != input_prefix_sha256
        or _replay_projected_rows_digest(connection)
        != projected_rows_sha256
        or _sqlite_static_axis_digest(connection) != static_axis_sha256
        or (
            phase == "joined"
            and _sqlite_joined_state_digest(connection) != joined_state_sha256
        )
        or (phase != "joined" and joined_state_sha256 != EMPTY_SHA256)
        or (
            phase in {"ingested", "joined"}
            and offset != int(resume_binding["provider_daily_bars_size_bytes"])
        )
    ):
        raise ValueError("daily_bars_resume_state_drift")
    return {
        "input_offset": offset,
        "next_ordinal": ordinal,
        "input_prefix_sha256": input_prefix_sha256,
        "projected_rows_sha256": projected_rows_sha256,
        "static_axis_sha256": static_axis_sha256,
        "joined_state_sha256": joined_state_sha256,
        "phase": phase,
    }


def _commit_daily_bars_resume_batch(
    connection: sqlite3.Connection,
    *,
    batch: Sequence[tuple[int, str, str, str, str, str | None]],
    input_offset: int,
    next_ordinal: int,
    input_prefix_sha256: str,
    phase: str = "ingesting",
) -> None:
    if phase not in {"ingesting", "ingested"}:
        raise ValueError("daily_bars_resume_commit_phase_invalid")
    connection.execute("BEGIN IMMEDIATE")
    try:
        previous = connection.execute(
            """SELECT input_offset, next_ordinal, input_prefix_sha256,
                      projected_rows_sha256, phase
               FROM resume_state WHERE singleton=1"""
        ).fetchone()
        if (
            previous is None
            or previous[4] != "ingesting"
            or type(input_offset) is not int
            or type(next_ordinal) is not int
            or input_offset < int(previous[0])
            or next_ordinal != int(previous[1]) + len(batch)
            or not _sha256_text(input_prefix_sha256)
        ):
            raise ValueError("daily_bars_resume_commit_state_invalid")
        projected_rows_sha256 = _extend_projected_rows_digest(
            str(previous[3]),
            batch,
        )
        if batch:
            connection.executemany(
                "INSERT INTO bars VALUES (?, ?, ?, ?, ?, ?)", batch
            )
        connection.execute(
            """UPDATE resume_state
               SET input_offset=?, next_ordinal=?, input_prefix_sha256=?,
                   projected_rows_sha256=?, phase=?
               WHERE singleton=1 AND phase='ingesting'""",
            (
                input_offset,
                next_ordinal,
                input_prefix_sha256,
                projected_rows_sha256,
                phase,
            ),
        )
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


def _load_daily_bars_output_checkpoint(
    output_directory: Path,
    *,
    resume_binding: Mapping[str, Any],
    work_identity: str,
) -> dict[str, Any] | None:
    _reject_symlink_components(
        output_directory,
        error="daily_bars_resume_output_root_symlink_forbidden",
    )
    if not output_directory.exists() and not output_directory.is_symlink():
        return None
    if output_directory.is_symlink() or not output_directory.is_dir():
        raise ValueError("daily_bars_resume_output_root_invalid")
    checkpoint_path = output_directory / DAILY_BARS_OUTPUT_CHECKPOINT_NAME
    if not checkpoint_path.exists() and not checkpoint_path.is_symlink():
        if any(output_directory.iterdir()):
            raise ValueError("daily_bars_resume_output_not_empty")
        return None
    if not checkpoint_path.is_file() or checkpoint_path.is_symlink():
        raise ValueError("daily_bars_resume_output_checkpoint_invalid")
    checkpoint = read_json(checkpoint_path)
    checkpoint_semantic = {
        key: value for key, value in checkpoint.items() if key != "content_hash"
    }
    artifacts = checkpoint.get("artifacts")
    semantic = checkpoint.get("semantic")
    if (
        checkpoint.get("schema_version")
        != DAILY_BARS_OUTPUT_CHECKPOINT_SCHEMA_VERSION
        or checkpoint.get("content_hash") != canonical_hash(checkpoint_semantic)
        or checkpoint.get("work_identity") != work_identity
        or checkpoint.get("resume_binding_hash")
        != canonical_hash(resume_binding)
        or not isinstance(artifacts, list)
        or not isinstance(semantic, dict)
        or (semantic.get("resource_execution") or {}).get("work_identity")
        != work_identity
        or (semantic.get("resource_execution") or {}).get(
            "resume_implementation_root"
        )
        != resume_binding.get("resume_implementation_root")
    ):
        raise ValueError("daily_bars_resume_output_checkpoint_invalid")
    expected_names = {
        DAILY_BARS_ROWS_NAME,
        DAILY_BARS_VALIDITY_NAME,
        DAILY_BARS_GAPS_NAME,
        DAILY_BARS_SOURCE_ROWS_NAME,
        DAILY_BARS_SOURCE_CALENDAR_NAME,
        DAILY_BARS_SOURCE_CONFLICTS_NAME,
        DAILY_BARS_SOURCE_IDENTITY_INTERVALS_NAME,
        DAILY_BARS_SOURCE_IDENTITY_BINDING_NAME,
    }
    observed_names: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, Mapping):
            raise ValueError("daily_bars_resume_output_artifact_invalid")
        name = str(artifact.get("name") or "")
        path = output_directory / name
        if (
            name in observed_names
            or name not in expected_names
            or not path.is_file()
            or path.is_symlink()
            or artifact.get("sha256") != sha256_file(path)
            or artifact.get("size_bytes") != path.stat().st_size
        ):
            raise ValueError("daily_bars_resume_output_artifact_invalid")
        observed_names.add(name)
    observed_files = {
        path.relative_to(output_directory).as_posix()
        for path in output_directory.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if (
        observed_names != expected_names
        or observed_files != expected_names | {DAILY_BARS_OUTPUT_CHECKPOINT_NAME}
        or any(path.is_symlink() for path in output_directory.rglob("*"))
    ):
        raise ValueError("daily_bars_resume_output_closure_invalid")
    try:
        replay = _deep_replay_market_evidence(
            output_directory / DAILY_BARS_ROWS_NAME,
            output_directory / DAILY_BARS_VALIDITY_NAME,
            output_directory / DAILY_BARS_GAPS_NAME,
            dataset="daily_bars",
            payload=semantic,
        )
        source_replay = _replay_archived_daily_source_closure(
            output_directory,
            rows_path=output_directory / DAILY_BARS_ROWS_NAME,
            validity_path=output_directory / DAILY_BARS_VALIDITY_NAME,
            gaps_path=output_directory / DAILY_BARS_GAPS_NAME,
            payload=semantic,
        )
    except (OSError, ValueError, sqlite3.DatabaseError) as exc:
        raise ValueError("daily_bars_resume_output_semantic_invalid") from exc
    projection = semantic.get("provider_neutral_projection") or {}
    validity = semantic.get("validity") or {}
    blockers = set(semantic.get("blockers") or ())
    if (
        projection.get("record_count") != replay["row_count"]
        or semantic.get("coverage") != replay["coverage"]
        or validity.get("valid_row_count") != replay["valid_row_count"]
        or validity.get("invalid_row_count") != replay["invalid_row_count"]
        or source_replay
        != {
            "coverage": replay["coverage"],
            "expected_axis_root": replay["expected_axis_root"],
            "row_count": replay["row_count"],
        }
        or not _daily_resource_execution_valid(
            semantic.get("resource_execution") or {}, blockers
        )
        or not _market_source_blockers_consistent(semantic)
    ):
        raise ValueError("daily_bars_resume_output_semantic_invalid")
    return dict(semantic)


def _publish_daily_bars_output_checkpoint(
    output_directory: Path,
    *,
    semantic: Mapping[str, Any],
    resume_binding: Mapping[str, Any],
    work_identity: str,
) -> None:
    artifacts = []
    for name in (
        DAILY_BARS_ROWS_NAME,
        DAILY_BARS_VALIDITY_NAME,
        DAILY_BARS_GAPS_NAME,
        DAILY_BARS_SOURCE_ROWS_NAME,
        DAILY_BARS_SOURCE_CALENDAR_NAME,
        DAILY_BARS_SOURCE_CONFLICTS_NAME,
        DAILY_BARS_SOURCE_IDENTITY_INTERVALS_NAME,
        DAILY_BARS_SOURCE_IDENTITY_BINDING_NAME,
    ):
        path = output_directory / name
        if not path.is_file() or path.is_symlink():
            raise ValueError("daily_bars_resume_output_artifact_invalid")
        artifacts.append(
            {
                "name": name,
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
        )
    checkpoint = {
        "schema_version": DAILY_BARS_OUTPUT_CHECKPOINT_SCHEMA_VERSION,
        "work_identity": work_identity,
        "resume_binding_hash": canonical_hash(resume_binding),
        "artifacts": artifacts,
        "semantic": dict(semantic),
    }
    checkpoint["content_hash"] = canonical_hash(checkpoint)
    _write_bytes_exclusive_fsync(
        output_directory / DAILY_BARS_OUTPUT_CHECKPOINT_NAME,
        _canonical_json_text(checkpoint).encode("utf-8") + b"\n",
    )
    _fsync_directory(output_directory)
    if _load_daily_bars_output_checkpoint(
        output_directory,
        resume_binding=resume_binding,
        work_identity=work_identity,
    ) != dict(semantic):
        raise ValueError("daily_bars_resume_output_checkpoint_replay_mismatch")


def _daily_bars_resume_implementation_root() -> str:
    return canonical_hash(
        {
            "schema_version": DAILY_BARS_RESUME_SCHEMA_VERSION,
            "module_sha256": sha256_file(Path(__file__)),
            "batch_row_limit": DAILY_BARS_BATCH_ROW_LIMIT,
            "sqlite_cache_mib": DAILY_BARS_SQLITE_CACHE_MIB,
            "sqlite_spill_limit_bytes": DAILY_BARS_SQLITE_SPILL_LIMIT_BYTES,
            "stream": inspect.getsource(_stream_daily_bars_assessment),
            "state": inspect.getsource(_daily_bars_resume_state),
            "commit": inspect.getsource(_commit_daily_bars_resume_batch),
            "load_output": inspect.getsource(
                _load_daily_bars_output_checkpoint
            ),
            "publish_output": inspect.getsource(
                _publish_daily_bars_output_checkpoint
            ),
        }
    )


def _publish_daily_bars_source_archive(
    output_directory: Path,
    *,
    provider_rows_path: Path,
    calendar_path: Path,
    identity_intervals_bytes: bytes,
    identity_binding_bytes: bytes,
    normalizer_conflicts_bytes: bytes,
) -> None:
    """Create the self-contained replay closure in an exactly empty directory."""

    _reject_symlink_components(
        output_directory,
        error="daily_bars_resume_output_root_symlink_forbidden",
    )
    if not output_directory.is_dir() or any(output_directory.iterdir()):
        raise ValueError("daily_bars_resume_output_not_empty")
    _copy_file_fsync(
        provider_rows_path,
        output_directory / DAILY_BARS_SOURCE_ROWS_NAME,
    )
    _copy_file_fsync(
        calendar_path,
        output_directory / DAILY_BARS_SOURCE_CALENDAR_NAME,
    )
    _write_bytes_exclusive_fsync(
        output_directory / DAILY_BARS_SOURCE_CONFLICTS_NAME,
        normalizer_conflicts_bytes,
    )
    _write_bytes_exclusive_fsync(
        output_directory / DAILY_BARS_SOURCE_IDENTITY_INTERVALS_NAME,
        identity_intervals_bytes,
    )
    _write_bytes_exclusive_fsync(
        output_directory / DAILY_BARS_SOURCE_IDENTITY_BINDING_NAME,
        identity_binding_bytes,
    )
    _fsync_directory(output_directory)


def _write_bytes_exclusive_fsync(path: Path, payload: bytes) -> None:
    _reject_symlink_components(path, error="daily_bars_output_path_invalid")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise ValueError("daily_bars_output_path_invalid") from exc
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(payload)
            _flush_file(handle)
    finally:
        os.close(descriptor)


def _copy_file_fsync(source: Path, destination: Path) -> None:
    _reject_symlink_components(
        destination,
        error="daily_bars_prepared_copy_path_invalid",
    )
    if not source.is_file() or source.is_symlink() or destination.is_symlink():
        raise ValueError("daily_bars_prepared_copy_path_invalid")
    with source.open("rb") as source_handle, destination.open("xb") as target:
        for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
            target.write(chunk)
        _flush_file(target)


def _flush_file(handle: Any) -> None:
    handle.flush()
    os.fsync(handle.fileno())


def _jsonl_bytes(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(
        json.dumps(
            dict(row),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        + b"\n"
        for row in rows
    )


def _valid_date(value: str) -> bool:
    try:
        return datetime.strptime(value, "%Y%m%d").strftime("%Y%m%d") == value
    except ValueError:
        return False


def _sha256_text(value: object) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)


def _require_date(value: str, role: str) -> None:
    if not _valid_date(value):
        raise ValueError(f"index_daily_bars_{role}_invalid")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build provider-neutral A-share market-data evidence."
    )
    parser.add_argument(
        "--dataset",
        choices=("trade_calendar", "daily_bars", "index_daily_bars"),
        default="index_daily_bars",
    )
    parser.add_argument("--capture")
    parser.add_argument("--calendar")
    parser.add_argument("--identity-timeline-evidence")
    parser.add_argument("--output-root")
    parser.add_argument("--validate")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.validate:
        validators = {
            "trade_calendar": validate_trade_calendar_evidence,
            "daily_bars": validate_daily_bars_evidence,
            "index_daily_bars": validate_index_daily_bars_evidence,
        }
        result = validators[args.dataset](args.validate)
    else:
        if not args.capture or not args.output_root:
            raise SystemExit("--capture and --output-root are required")
        if args.dataset == "trade_calendar":
            result = build_trade_calendar_evidence(args.capture, args.output_root)
        elif args.dataset == "daily_bars":
            if not args.identity_timeline_evidence:
                raise SystemExit(
                    "--identity-timeline-evidence is required for daily_bars"
                )
            result = build_daily_bars_evidence(
                args.capture,
                args.output_root,
                identity_timeline_evidence=args.identity_timeline_evidence,
            )
        else:
            if not args.calendar:
                raise SystemExit(
                    "--calendar is required for index_daily_bars"
                )
            result = build_index_daily_bars_evidence(
                args.capture,
                args.calendar,
                args.output_root,
            )
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
            sort_keys=True,
        )
    )
    return 0 if result.get("formal_data_admission_ready") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())

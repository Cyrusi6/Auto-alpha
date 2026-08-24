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
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Sequence

from auto_alpha.data.ingestion.pipeline.ashare.free_provider_backfill import (
    replay_normalized_artifacts,
    validate_free_provider_backfill,
)
from auto_alpha.data.ingestion.pipeline.ashare.free_provider_baostock_reconciliation import (
    normalize_index_daily,
    validate_baostock_reconciliation_capture,
)
from auto_alpha.platform.artifacts.storage import (
    canonical_hash,
    publish_generation,
    read_json,
    sha256_file,
    validate_generation,
)

from .admission import first_data_admission_profile


SCHEMA_VERSION = "free_provider_market_data_evidence_v1"
MANIFEST_NAME = "free_provider_market_data_evidence.json"
GENERATION_PREFIX = "free_provider_market_data_evidence"
DATASET = "index_daily_bars"
INDEX_CODE = "000300.SH"
CANONICAL_ROWS_NAME = "index_daily_bars.jsonl"
VALIDITY_ROWS_NAME = "index_daily_bars_validity.jsonl"
COVERAGE_GAPS_NAME = "coverage_gaps.jsonl"
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
SAFETY_FLAGS = (
    "data_admission_eligible",
    "profile_activation_authorized",
    "alpha_search_authorized",
    "holdout_activation_authorized",
    "paper_trading_authorized",
    "shadow_trading_authorized",
    "live_trading_authorized",
)


@dataclass(frozen=True)
class IndexDailyBarsAssessment:
    """One deterministic provider-neutral projection and its evidence bytes."""

    semantic: dict[str, Any]
    canonical_rows: bytes
    validity_rows: bytes
    coverage_gaps: bytes


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
    }
    observed_files = {
        item.relative_to(root).as_posix()
        for item in root.rglob("*")
        if item.is_file() and not item.is_symlink()
    }
    rows_path = root / CANONICAL_ROWS_NAME
    validity_path = root / VALIDITY_ROWS_NAME
    gaps_path = root / COVERAGE_GAPS_NAME
    rows = _read_jsonl_bytes(rows_path.read_bytes(), "index_daily_bars")
    validity = _read_jsonl_bytes(
        validity_path.read_bytes(), "index_daily_bars_validity"
    )
    gaps = _read_jsonl_bytes(gaps_path.read_bytes(), "index_daily_bars_gaps")
    projection = payload.get("provider_neutral_projection") or {}
    coverage = payload.get("coverage") or {}
    validity_summary = payload.get("validity") or {}
    consumer = payload.get("consumer_closure") or {}
    safety = payload.get("safety") or {}
    blockers = set(payload.get("blockers") or ())
    profile_contract_exact = consumer.get("profile_contract_exact")
    technical_blockers_present = bool(
        blockers
        & {
            "index_daily_bars_calendar_axis_invalid",
            "index_daily_bars_calendar_source_binding_failed",
            "index_daily_bars_index_day_exact_cover_failed",
            "index_daily_bars_profile_consumer_closure_failed",
            "index_daily_bars_published_normalization_replay_mismatch",
            "index_daily_bars_required_value_validity_failed",
        }
    )
    if (
        observed_files != expected_files
        or any(item.is_symlink() for item in root.rglob("*"))
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
        or validity_summary.get("valid_row_count")
        + validity_summary.get("invalid_row_count")
        != len(validity)
        or coverage.get("provisional_exact_cover") is not (not gaps)
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


def _require_date(value: str, role: str) -> None:
    if not _valid_date(value):
        raise ValueError(f"index_daily_bars_{role}_invalid")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build provider-neutral CSI300 index-bar evidence."
    )
    parser.add_argument("--capture")
    parser.add_argument("--calendar")
    parser.add_argument("--output-root")
    parser.add_argument("--validate")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.validate:
        result = validate_index_daily_bars_evidence(args.validate)
    else:
        if not args.capture or not args.calendar or not args.output_root:
            raise SystemExit("--capture, --calendar and --output-root are required")
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

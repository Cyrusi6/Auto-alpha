"""Independent row-level coverage audit for signed free-provider captures."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from auto_alpha.data.ingestion.pipeline.ashare.free_provider_backfill import (
    _baostock_logical_rows,
    _safe_output_root,
    validate_free_provider_backfill,
)
from auto_alpha.data.ingestion.pipeline.ashare.run_provider_probe import BAOSTOCK_FIELDS
from auto_alpha.platform.artifacts.storage import (
    canonical_hash,
    publish_generation,
    read_json,
    sha256_file,
    validate_generation,
)


SCHEMA_VERSION = "free_provider_state_coverage_use_v2"
MANIFEST_NAME = "free_provider_state_coverage_use.json"
GENERATION_PREFIX = "free_provider_state_coverage_use"


def audit_baostock_state_coverage(
    capture: str | Path,
    output_root: str | Path,
) -> dict[str, Any]:
    """Recompute lifecycle-day exact cover from normalized provider state rows."""

    capture_manifest = validate_free_provider_backfill(capture)
    if capture_manifest.get("status") != "succeeded":
        raise ValueError("state_coverage_capture_blocked")
    capture_root = Path(str(capture_manifest["manifest_path"])).parent
    plan = read_json(capture_root / "request_plan.json")
    population = {
        str(row["metadata"]["ts_code"]): {
            "list_date": str(row["metadata"].get("list_date") or ""),
            "delist_date": (
                str(row["metadata"]["delist_date"])
                if row["metadata"].get("delist_date")
                else None
            ),
        }
        for row in plan["requests"]
        if (row.get("metadata") or {}).get("case") == "history"
    }
    if not population:
        raise ValueError("state_coverage_population_missing")
    terminal = _terminal_by_request(capture_root / "capture_journal.jsonl")
    calendars, calendar_projection_root = _calendar_from_signed_raw(
        capture_root,
        plan["requests"],
        terminal,
    )
    if set(calendars) != {"SSE", "SZSE"} or calendars["SSE"] != calendars["SZSE"]:
        raise ValueError("state_coverage_exchange_calendar_mismatch")
    open_dates = tuple(
        date
        for date, is_open in sorted(calendars["SSE"].items())
        if is_open and "20120101" <= date <= "20191231"
    )
    seed_dates = tuple(
        date
        for date, is_open in sorted(calendars["SSE"].items())
        if is_open and date < "20120101"
    )
    if len(open_dates) != 1_945 or not seed_dates:
        raise ValueError("state_coverage_calendar_geometry_invalid")

    output = _safe_output_root(output_root)
    gaps: list[dict[str, Any]] = []
    audited_codes: set[str] = set()
    expected_total = 0
    observed_total = 0
    missing_total = 0
    extra_total = 0
    st_positive_total = 0
    suspended_total = 0
    seed_required = 0
    seed_covered = 0
    state_projection: list[dict[str, Any]] = []
    for ts_code, observed_rows, projection in _state_rows_from_signed_raw(
        capture_root,
        plan["requests"],
        terminal,
    ):
        state_projection.append(projection)
        lifecycle = population.get(ts_code)
        if lifecycle is None:
            extras = sorted(row["trade_date"] for row in observed_rows)
            extra_total += len(extras)
            gaps.append(
                {
                    "ts_code": ts_code,
                    "reason": "security_outside_population",
                    "extra_date_ranges": _date_ranges(extras),
                }
            )
            continue
        audited_codes.add(ts_code)
        observed_dates = {row["trade_date"] for row in observed_rows}
        if len(observed_dates) != len(observed_rows):
            raise ValueError(f"state_coverage_duplicate_security_day:{ts_code}")
        expected = _expected_dates(open_dates, lifecycle)
        expected_total += len(expected)
        observed_research = {
            date for date in observed_dates if "20120101" <= date <= "20191231"
        }
        observed_total += len(observed_research & expected)
        missing = sorted(expected - observed_research)
        extra = sorted(observed_research - expected)
        missing_total += len(missing)
        extra_total += len(extra)
        st_positive_total += sum(
            row["provider_is_st"] == 1 and row["trade_date"] in expected
            for row in observed_rows
        )
        suspended_total += sum(
            row["provider_trade_status"] == 0 and row["trade_date"] in expected
            for row in observed_rows
        )
        if lifecycle["list_date"] < "20120101":
            seed_required += 1
            if any(
                row["trade_date"] in seed_dates
                for row in observed_rows
            ):
                seed_covered += 1
            else:
                gaps.append({"ts_code": ts_code, "reason": "pre_span_seed_missing"})
        if missing or extra:
            gaps.append(
                {
                    "ts_code": ts_code,
                    "reason": "security_day_exact_cover_mismatch",
                    "expected_count": len(expected),
                    "observed_in_scope_count": len(observed_research),
                    "missing_count": len(missing),
                    "missing_date_ranges": _date_ranges(missing),
                    "extra_count": len(extra),
                    "extra_date_ranges": _date_ranges(extra),
                }
            )

    for ts_code in sorted(set(population) - audited_codes):
        expected = _expected_dates(open_dates, population[ts_code])
        expected_total += len(expected)
        missing_total += len(expected)
        if population[ts_code]["list_date"] < "20120101":
            seed_required += 1
        gaps.append(
            {
                "ts_code": ts_code,
                "reason": "provider_security_response_empty",
                "missing_count": len(expected),
                "missing_date_ranges": _date_ranges(sorted(expected)),
            }
        )

    gap_bytes = b"".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        + b"\n"
        for row in gaps
    )
    metrics = {
        "population_count": len(population),
        "open_date_count": len(open_dates),
        "expected_security_day_count": expected_total,
        "observed_exact_security_day_count": observed_total,
        "missing_security_day_count": missing_total,
        "extra_security_day_count": extra_total,
        "st_positive_security_day_count": st_positive_total,
        "suspended_security_day_count": suspended_total,
        "pre_span_seed_required_count": seed_required,
        "pre_span_seed_covered_count": seed_covered,
        "gap_security_count": len({row["ts_code"] for row in gaps}),
    }
    exact_cover = (
        missing_total == 0
        and extra_total == 0
        and seed_required == seed_covered
        and observed_total == expected_total
    )
    blockers = [
        *([] if exact_cover else ["provider_state_security_day_exact_cover_failed"]),
        "coverage_use_not_yet_bound_to_data_admission_receipts",
        "st_subtype_unknown",
        "suspension_timing_unknown",
        "human_profile_activation_not_bound",
    ]
    semantic = {
        "schema_version": SCHEMA_VERSION,
        "status": "coverage_exact" if exact_cover else "blocked_gaps",
        "input_capture_generation_id": capture_manifest["generation_id"],
        "input_capture_content_hash": capture_manifest["content_hash"],
        "input_capture_manifest_sha256": sha256_file(capture_manifest["manifest_path"]),
        "state_projection_root": canonical_hash(state_projection),
        "calendar_projection_root": calendar_projection_root,
        "request_plan_hash": capture_manifest["request_plan_hash"],
        "coverage_gaps_sha256": hashlib.sha256(gap_bytes).hexdigest(),
        "coverage_gaps_size_bytes": len(gap_bytes),
        "coverage_gap_record_count": len(gaps),
        "coverage_gap_root": canonical_hash(gaps),
        "metrics": metrics,
        "exact_security_day_cover": exact_cover,
        "formal_data_admission_ready": False,
        "blockers": blockers,
        "safety": {
            "data_admission_eligible": False,
            "profile_activation_authorized": False,
            "alpha_search_authorized": False,
            "holdout_activation_authorized": False,
            "paper_trading_authorized": False,
            "shadow_trading_authorized": False,
            "live_trading_authorized": False,
        },
    }
    return publish_generation(
        output,
        prefix=GENERATION_PREFIX,
        manifest_name=MANIFEST_NAME,
        semantic=semantic,
        extra_files={"coverage_gaps.jsonl": gap_bytes},
    )


def validate_state_coverage_use(path: str | Path) -> dict[str, Any]:
    payload = validate_generation(
        path,
        schema=SCHEMA_VERSION,
        manifest_name=MANIFEST_NAME,
    )
    root = Path(str(payload["manifest_path"])).parent
    gap_path = root / "coverage_gaps.jsonl"
    if not gap_path.is_file():
        raise ValueError("state_coverage_gap_evidence_missing")
    gaps = [line for line in gap_path.read_text(encoding="utf-8").splitlines() if line]
    gap_bytes = gap_path.read_bytes()
    gap_rows = [json.loads(line) for line in gaps]
    expected_gap_security_count = int(payload["metrics"]["gap_security_count"])
    observed_gap_codes = {
        str(row.get("ts_code") or "") for row in gap_rows
    }
    safety = payload.get("safety") or {}
    if (
        payload.get("coverage_gaps_sha256")
        != hashlib.sha256(gap_bytes).hexdigest()
        or payload.get("coverage_gaps_size_bytes") != len(gap_bytes)
        or payload.get("coverage_gap_record_count") != len(gap_rows)
        or payload.get("coverage_gap_root") != canonical_hash(gap_rows)
        or len(observed_gap_codes) != expected_gap_security_count
        or not _gap_rows_valid(gap_rows)
        or payload.get("formal_data_admission_ready") is not False
        or any(value is not False for value in safety.values())
        or set(safety)
        != {
            "data_admission_eligible",
            "profile_activation_authorized",
            "alpha_search_authorized",
            "holdout_activation_authorized",
            "paper_trading_authorized",
            "shadow_trading_authorized",
            "live_trading_authorized",
        }
        or (payload.get("exact_security_day_cover") is True and gap_rows)
        or (
            payload.get("status") == "coverage_exact"
            and payload.get("exact_security_day_cover") is not True
        )
    ):
        raise ValueError("state_coverage_gap_evidence_invalid")
    return payload


def _gap_rows_valid(rows: Sequence[Mapping[str, Any]]) -> bool:
    allowed_reasons = {
        "pre_span_seed_missing",
        "provider_security_response_empty",
        "security_day_exact_cover_mismatch",
        "security_outside_population",
    }
    for row in rows:
        if not str(row.get("ts_code") or "") or row.get("reason") not in allowed_reasons:
            return False
        for key in ("missing_date_ranges", "extra_date_ranges"):
            if key not in row:
                continue
            ranges = row.get(key)
            if not isinstance(ranges, list):
                return False
            for value in ranges:
                if (
                    not isinstance(value, Mapping)
                    or not _valid_date(str(value.get("date_start") or ""))
                    or not _valid_date(str(value.get("date_end") or ""))
                    or str(value["date_start"]) > str(value["date_end"])
                    or not isinstance(value.get("count"), int)
                    or isinstance(value.get("count"), bool)
                    or int(value["count"]) <= 0
                ):
                    return False
    return True


def _valid_date(value: str) -> bool:
    try:
        return datetime.strptime(value, "%Y%m%d").strftime("%Y%m%d") == value
    except ValueError:
        return False


def _terminal_by_request(path: Path) -> dict[str, dict[str, Any]]:
    terminal: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            event = json.loads(line)
            if event.get("event_type") == "capture_attempt_terminal":
                terminal[str(event["request_id"])] = event
    return terminal


def _archived_rows(
    root: Path,
    request: Mapping[str, Any],
    terminal: Mapping[str, Mapping[str, Any]],
) -> tuple[list[str], list[list[str]], dict[str, Any]]:
    request_id = str(request["request_id"])
    receipt = terminal.get(request_id)
    if receipt is None or receipt.get("terminal_state") not in {"positive", "empty"}:
        raise ValueError(f"state_coverage_terminal_missing:{request_id}")
    relative = Path(str(receipt.get("raw_envelope_relative_path") or ""))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("state_coverage_raw_path_invalid")
    wrapper = read_json(root / relative)
    raw_payload = base64.b64decode(
        str(wrapper.get("raw_payload_base64") or ""), validate=True
    )
    fields, rows = _baostock_logical_rows(raw_payload)
    projection = {
        "request_id": request_id,
        "raw_envelope_sha256": receipt.get("raw_envelope_sha256"),
        "raw_payload_sha256": wrapper.get("raw_payload_sha256"),
        "logical_root": canonical_hash({"fields": fields, "rows": rows}),
        "row_count": len(rows),
    }
    return fields, rows, projection


def _calendar_from_signed_raw(
    root: Path,
    requests: Sequence[Mapping[str, Any]],
    terminal: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, dict[str, bool]], str]:
    calendar_requests = [
        request
        for request in requests
        if (request.get("metadata") or {}).get("case") == "trade_calendar"
    ]
    if len(calendar_requests) != 1:
        raise ValueError("state_coverage_calendar_request_invalid")
    fields, items, projection = _archived_rows(
        root, calendar_requests[0], terminal
    )
    if fields != ["calendar_date", "is_trading_day"]:
        raise ValueError("state_coverage_calendar_schema_invalid")
    shared: dict[str, bool] = {}
    for item in items:
        if len(item) != 2 or str(item[1]) not in {"0", "1"}:
            raise ValueError("state_coverage_calendar_row_invalid")
        trade_date = str(item[0]).replace("-", "")
        if not _valid_date(trade_date) or trade_date in shared:
            raise ValueError("state_coverage_calendar_duplicate_or_date_invalid")
        shared[trade_date] = str(item[1]) == "1"
    return {"SSE": dict(shared), "SZSE": dict(shared)}, canonical_hash(projection)


def _state_rows_from_signed_raw(
    root: Path,
    requests: Sequence[Mapping[str, Any]],
    terminal: Mapping[str, Mapping[str, Any]],
) -> Iterator[tuple[str, list[dict[str, Any]], dict[str, Any]]]:
    previous_code = ""
    for request in requests:
        metadata = request.get("metadata") or {}
        if metadata.get("case") != "history":
            continue
        ts_code = str(metadata.get("ts_code") or "")
        if not ts_code or (previous_code and ts_code <= previous_code):
            raise ValueError("state_coverage_plan_population_not_sorted_unique")
        previous_code = ts_code
        fields, items, projection = _archived_rows(root, request, terminal)
        if fields != BAOSTOCK_FIELDS.split(","):
            raise ValueError(f"state_coverage_history_schema_invalid:{ts_code}")
        symbol, suffix = ts_code.split(".", 1)
        expected_provider_code = f"{'sh' if suffix == 'SH' else 'sz'}.{symbol}"
        observed: list[dict[str, Any]] = []
        seen_dates: set[str] = set()
        for item in items:
            if (
                len(item) != len(fields)
                or str(item[1]) != expected_provider_code
                or str(item[9]) not in {"0", "1"}
                or str(item[10]) not in {"0", "1"}
            ):
                raise ValueError(f"state_coverage_provider_row_invalid:{ts_code}")
            trade_date = str(item[0]).replace("-", "")
            if not _valid_date(trade_date) or trade_date in seen_dates:
                raise ValueError(f"state_coverage_security_day_duplicate:{ts_code}")
            seen_dates.add(trade_date)
            observed.append(
                {
                    "ts_code": ts_code,
                    "trade_date": trade_date,
                    "provider_trade_status": int(item[9]),
                    "provider_is_st": int(item[10]),
                }
            )
        yield ts_code, observed, projection


def _expected_dates(
    open_dates: Sequence[str], lifecycle: Mapping[str, str | None]
) -> set[str]:
    list_date = str(lifecycle["list_date"] or "")
    delist_date = str(lifecycle.get("delist_date") or "99999999")
    return {
        date
        for date in open_dates
        if date >= max("20120101", list_date) and date <= delist_date
    }


def _date_ranges(dates: Sequence[str]) -> list[dict[str, Any]]:
    if not dates:
        return []
    rows: list[dict[str, Any]] = []
    start = previous = dates[0]
    count = 1
    for value in dates[1:]:
        if _next_day(previous) == value:
            previous = value
            count += 1
            continue
        rows.append({"date_start": start, "date_end": previous, "count": count})
        start = previous = value
        count = 1
    rows.append({"date_start": start, "date_end": previous, "count": count})
    return rows


def _next_day(value: str) -> str:
    return (datetime.strptime(value, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit signed provider state exact cover.")
    parser.add_argument("--capture")
    parser.add_argument("--output-root")
    parser.add_argument("--validate")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.validate:
        payload = validate_state_coverage_use(args.validate)
    else:
        if not args.capture or not args.output_root:
            raise SystemExit("--capture and --output-root are required")
        payload = audit_baostock_state_coverage(args.capture, args.output_root)
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
            sort_keys=True,
        )
    )
    return 0 if payload.get("status") == "coverage_exact" else 1


if __name__ == "__main__":
    raise SystemExit(main())

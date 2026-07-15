"""Full-domain valuation closure preflight for Task 055-B."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from .evidence import SecurityDateState, canonical_hash, validate_evidence_overlay
from .valuation import ValuationState, validate_valuation_overlay


PREFLIGHT_SCHEMA = "task055b_valuation_closure_preflight_v1"
BLOCKING_EVIDENCE_STATES = {
    SecurityDateState.TRADED_SOURCE_CONFLICT.value,
    SecurityDateState.CALENDAR_OR_MEMBERSHIP_ERROR.value,
    SecurityDateState.RAW_BAR_REQUIRED_FIELD_INVALID.value,
    SecurityDateState.SOURCE_NORMALIZATION_ZERO_FILL.value,
    SecurityDateState.CORPORATE_ACTION_VALUATION_UNPROVEN.value,
    SecurityDateState.DATA_SOURCE_GAP.value,
    SecurityDateState.CONFLICT.value,
}


class PreflightError(RuntimeError):
    """Raised when closure evidence or valuation lineage is invalid."""


def run_valuation_closure_preflight(
    *,
    evidence_overlay: str | Path,
    valuation_overlay: str | Path,
    output_path: str | Path | None = None,
    max_continuity_error_cny: float = 0.01,
) -> dict[str, Any]:
    evidence = validate_evidence_overlay(evidence_overlay)
    valuation = validate_valuation_overlay(valuation_overlay, evidence_overlay=evidence_overlay)
    records = evidence["records"]
    marks = valuation["marks"]
    marks_by_key = {
        (str(row["ts_code"]), str(row["trade_date"]), str(row["reporting_point"])): row
        for row in marks
    }
    blockers: list[dict[str, Any]] = []
    unresolved = 0
    conflict = 0
    unknown_mark_notional = 0.0
    data_source_gap_carry = 0
    stale_mark_fill = 0
    required_mark_count = 0
    covered_mark_count = 0
    continuity_errors: list[float] = []
    signal_target_blockers = 0

    for row in records:
        state = str(row["state"])
        key = (str(row["ts_code"]), str(row["trade_date"]))
        if state in {SecurityDateState.TRADED_SOURCE_CONFLICT.value, SecurityDateState.CONFLICT.value}:
            conflict += 1
        if (row.get("signal_used") or row.get("target_used")) and state in BLOCKING_EVIDENCE_STATES:
            signal_target_blockers += 1
        if row.get("valuation_required"):
            for point in ("open", "close"):
                required_mark_count += 1
                mark = marks_by_key.get((*key, point))
                if mark is None or mark.get("mark_price") is None or mark.get("mark_method") == ValuationState.UNRESOLVED.value:
                    unresolved += 1
                    blockers.append({"code": "valuation_mark_unresolved", "ts_code": key[0], "trade_date": key[1], "reporting_point": point, "evidence_state": state})
                else:
                    covered_mark_count += 1
        for point in ("open", "close"):
            mark = marks_by_key.get((*key, point))
            if mark is None:
                continue
            continuity_errors.append(float(mark.get("continuity_error_cny", 0.0)))
            if mark.get("holdings_shares", 0) and mark.get("mark_price") is None:
                unknown_mark_notional += 1.0
            if state == SecurityDateState.DATA_SOURCE_GAP.value and str(mark.get("mark_method", "")).startswith("STALE_"):
                data_source_gap_carry += 1
            if str(mark.get("mark_method", "")).startswith("STALE_") and mark.get("execution_allowed"):
                stale_mark_fill += 1
            if not row.get("membership") and mark.get("execution_allowed") and not mark.get("sell_allowed"):
                blockers.append({"code": "membership_coupled_sell", "ts_code": key[0], "trade_date": key[1], "reporting_point": point})

    max_error = max(continuity_errors, default=0.0)
    if conflict:
        blockers.append({"code": "source_conflict", "count": conflict})
    if unknown_mark_notional:
        blockers.append({"code": "unknown_mark_notional", "count": unknown_mark_notional})
    if data_source_gap_carry:
        blockers.append({"code": "data_source_gap_carry", "count": data_source_gap_carry})
    if stale_mark_fill:
        blockers.append({"code": "stale_mark_execution_allowed", "count": stale_mark_fill})
    if max_error > max_continuity_error_cny:
        blockers.append({"code": "corporate_action_continuity_error", "max_error_cny": max_error})
    closure_ready = not blockers and unresolved == 0 and covered_mark_count == required_mark_count
    factor_replay_ready = signal_target_blockers == 0
    future_research_ready = not any(str(row["state"]) in BLOCKING_EVIDENCE_STATES for row in records)
    report = {
        "schema_version": PREFLIGHT_SCHEMA,
        "status": "passed" if closure_ready else "blocked",
        "evidence_content_hash": evidence["content_hash"],
        "valuation_content_hash": valuation["content_hash"],
        "readiness": {
            "factor_replay_ready": factor_replay_ready,
            "continuous_portfolio_valuation_ready": closure_ready,
            "future_research_data_ready": future_research_ready,
        },
        "metrics": {
            "valuation_domain_cells": sum(bool(row.get("valuation_required")) for row in records),
            "required_reporting_points": required_mark_count,
            "covered_reporting_points": covered_mark_count,
            "mark_coverage": covered_mark_count / required_mark_count if required_mark_count else 1.0,
            "unresolved": unresolved,
            "conflict": conflict,
            "unknown_mark_notional": unknown_mark_notional,
            "data_source_gap_carry": data_source_gap_carry,
            "stale_mark_fill": stale_mark_fill,
            "max_corporate_action_continuity_error_cny": max_error,
            "signal_target_blockers": signal_target_blockers,
        },
        "blockers": blockers,
        "policy": {"max_continuity_error_cny": float(max_continuity_error_cny)},
    }
    report["content_hash"] = canonical_hash(report)
    if output_path is not None:
        destination = Path(output_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, destination)
    return report


def validate_preflight_report(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != PREFLIGHT_SCHEMA:
        raise PreflightError("preflight_schema_invalid")
    content_hash = payload.pop("content_hash", None)
    if canonical_hash(payload) != content_hash:
        raise PreflightError("preflight_content_hash_mismatch")
    payload["content_hash"] = content_hash
    return payload

"""Independent fee and valuation-mark verifier for Task 055-B."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from task_055_a.artifacts import MONEY_TOLERANCE_CNY, canonical_hash, sha256_file
from task_055_a.verifier import SimulationVerificationError, verify_simulation_run

from .fees import validate_fee_schedule, verify_fill_fees

MARK_EVIDENCE_SCHEMA = "task055b_security_date_mark_evidence_v1"
MARK_METHODS = {
    "OFFICIAL_OPEN",
    "OFFICIAL_CLOSE",
    "STALE_OFFICIAL_NON_TRADING",
    "STALE_VENDOR_DAILY_NON_TRADING_MODELED",
    "LIFECYCLE_SETTLEMENT",
}
NO_TRADE_STATES = {"OFFICIAL_NON_TRADING", "VENDOR_DAILY_NON_TRADING_MODELED", "LIFECYCLE_TERMINATED"}
BLOCKED_MARK_STATES = {
    "TRADED_SOURCE_CONFLICT", "CALENDAR_OR_MEMBERSHIP_ERROR", "RAW_BAR_REQUIRED_FIELD_INVALID",
    "SOURCE_NORMALIZATION_ZERO_FILL", "CORPORATE_ACTION_VALUATION_UNPROVEN", "DATA_SOURCE_GAP", "CONFLICT",
}
REPORTING_POINTS = {"open", "close"}


class Task055BVerificationError(RuntimeError):
    """Raised when fee or mark evidence cannot independently close."""


def make_official_mark_rows(
    *, dates: Sequence[str], assets: Sequence[str], open_prices: Any, close_prices: Any,
    raw_quote_evidence: Mapping[tuple[str, str], Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Create explicit official marks for tests/builders from governed raw quote evidence."""
    opens = np.asarray(open_prices, dtype=float)
    closes = np.asarray(close_prices, dtype=float)
    expected = (len(dates), len(assets))
    if opens.shape != expected or closes.shape != expected:
        raise Task055BVerificationError("official_mark_axes_mismatch")
    rows: list[dict[str, Any]] = []
    for date_index, date in enumerate(dates):
        for asset_index, asset in enumerate(assets):
            evidence = dict(raw_quote_evidence.get((str(date), str(asset))) or {})
            if not evidence:
                raise Task055BVerificationError(f"raw_quote_evidence_missing:{asset}:{date}")
            for point, values, method in (("open", opens, "OFFICIAL_OPEN"), ("close", closes, "OFFICIAL_CLOSE")):
                row = {
                    "schema_version": MARK_EVIDENCE_SCHEMA,
                    "date": str(date),
                    "asset": str(asset),
                    "reporting_point": point,
                    "mark_price": float(values[date_index, asset_index]),
                    "mark_method": method,
                    "mark_source_date": str(date),
                    "stale_age_trade_days": 0,
                    "market_session_state": "TRADED_PRIMARY_BAR",
                    "execution_allowed": point == "open",
                    "corporate_action_transform": {"type": "none", "price_multiplier": 1.0},
                    "stale_mark_notional": 0.0,
                    "stale_mark_nav_ratio": 0.0,
                    "evidence": evidence,
                }
                row["evidence_hash"] = canonical_hash(evidence)
                rows.append(row)
    return rows


def build_mark_matrices(
    rows: Sequence[Mapping[str, Any]], *, dates: Sequence[str], assets: Sequence[str],
    raw_open: Any, raw_close: Any,
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Rebuild valuation matrices solely from explicit evidence rows."""
    date_index = {str(date): index for index, date in enumerate(dates)}
    asset_index = {str(asset): index for index, asset in enumerate(assets)}
    expected_shape = (len(dates), len(assets))
    opens = np.asarray(raw_open, dtype=float)
    closes = np.asarray(raw_close, dtype=float)
    if opens.shape != expected_shape or closes.shape != expected_shape:
        raise Task055BVerificationError("raw_quote_axes_mismatch")
    mark_open = np.full(expected_shape, np.nan, dtype=float)
    mark_close = np.full(expected_shape, np.nan, dtype=float)
    by_key: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    issues: list[str] = []
    for row in rows:
        date = str(row.get("date") or "")
        asset = str(row.get("asset") or "")
        point = str(row.get("reporting_point") or "")
        key = (date, asset, point)
        if key in by_key:
            issues.append(f"duplicate_mark:{asset}:{date}:{point}")
            continue
        by_key[key] = row
        if row.get("schema_version") != MARK_EVIDENCE_SCHEMA:
            issues.append(f"mark_schema_invalid:{asset}:{date}:{point}")
            continue
        if date not in date_index or asset not in asset_index or point not in REPORTING_POINTS:
            issues.append(f"mark_axis_invalid:{asset}:{date}:{point}")
            continue
        method = str(row.get("mark_method") or "")
        state = str(row.get("market_session_state") or "")
        evidence = row.get("evidence")
        if not isinstance(evidence, Mapping) or canonical_hash(dict(evidence)) != row.get("evidence_hash"):
            issues.append(f"mark_evidence_hash_invalid:{asset}:{date}:{point}")
        if method not in MARK_METHODS:
            issues.append(f"mark_method_invalid:{asset}:{date}:{point}")
            continue
        if state in BLOCKED_MARK_STATES:
            issues.append(f"blocked_mark_state_used:{asset}:{date}:{point}:{state}")
        try:
            price = float(row.get("mark_price"))
            stale_age = int(row.get("stale_age_trade_days"))
            stale_notional = float(row.get("stale_mark_notional"))
            stale_ratio = float(row.get("stale_mark_nav_ratio"))
        except (TypeError, ValueError):
            issues.append(f"mark_numeric_invalid:{asset}:{date}:{point}")
            continue
        if not np.isfinite(price) or price <= 0 or stale_age < 0 or stale_notional < 0 or stale_ratio < 0:
            issues.append(f"mark_value_invalid:{asset}:{date}:{point}")
            continue
        row_index = date_index[date]
        column_index = asset_index[asset]
        raw = opens[row_index, column_index] if point == "open" else closes[row_index, column_index]
        if method in {"OFFICIAL_OPEN", "OFFICIAL_CLOSE"}:
            expected_method = "OFFICIAL_OPEN" if point == "open" else "OFFICIAL_CLOSE"
            if method != expected_method or str(row.get("mark_source_date")) != date or stale_age != 0:
                issues.append(f"official_mark_contract_invalid:{asset}:{date}:{point}")
            if not np.isfinite(raw) or raw <= 0 or abs(price - raw) > MONEY_TOLERANCE_CNY:
                issues.append(f"official_mark_quote_mismatch:{asset}:{date}:{point}")
        elif method in {"STALE_OFFICIAL_NON_TRADING", "STALE_VENDOR_DAILY_NON_TRADING_MODELED"}:
            source_date = str(row.get("mark_source_date") or "")
            if state not in NO_TRADE_STATES or source_date not in date_index or date_index[source_date] >= row_index:
                issues.append(f"stale_mark_provenance_invalid:{asset}:{date}:{point}")
            if method == "STALE_VENDOR_DAILY_NON_TRADING_MODELED" and state != "VENDOR_DAILY_NON_TRADING_MODELED":
                issues.append(f"modeled_stale_state_invalid:{asset}:{date}:{point}")
            if bool(row.get("execution_allowed")):
                issues.append(f"stale_mark_execution_allowed:{asset}:{date}:{point}")
            transform = row.get("corporate_action_transform")
            if not isinstance(transform, Mapping) or not np.isfinite(float(transform.get("price_multiplier", np.nan))):
                issues.append(f"stale_mark_transform_invalid:{asset}:{date}:{point}")
            else:
                source = by_key.get((source_date, asset, point))
                if source is None:
                    issues.append(f"stale_mark_source_missing:{asset}:{date}:{point}")
                else:
                    expected_price = float(source["mark_price"]) * float(transform["price_multiplier"])
                    if abs(price - expected_price) > MONEY_TOLERANCE_CNY:
                        issues.append(f"stale_mark_transform_mismatch:{asset}:{date}:{point}")
        else:
            if state != "LIFECYCLE_TERMINATED" or bool(row.get("execution_allowed")):
                issues.append(f"lifecycle_settlement_contract_invalid:{asset}:{date}:{point}")
        target = mark_open if point == "open" else mark_close
        target[row_index, column_index] = price
    for date in dates:
        for asset in assets:
            for point in REPORTING_POINTS:
                if (str(date), str(asset), point) not in by_key:
                    issues.append(f"mark_missing:{asset}:{date}:{point}")
    return mark_open, mark_close, sorted(set(issues))


def verify_task055b_simulation_run(
    run_path: str | Path, *, expected_fee_schedule: str | Path | None = None,
) -> dict[str, Any]:
    """Verify Task 055-A accounting plus Task 055-B marks and fee schedule."""
    verified = verify_simulation_run(run_path)
    generation = Path(verified["root"])
    fee_path = generation / "fee_schedule_manifest.json"
    if not fee_path.is_file():
        raise Task055BVerificationError("embedded_fee_schedule_missing")
    schedule = validate_fee_schedule(fee_path)
    if expected_fee_schedule is not None:
        expected = validate_fee_schedule(expected_fee_schedule)
        if expected["content_hash"] != schedule["content_hash"]:
            raise Task055BVerificationError("embedded_fee_schedule_lineage_mismatch")
    spec = json.loads((generation / "spec.json").read_text(encoding="utf-8"))
    fills = _read_jsonl(generation / "fills.jsonl")
    axes = json.loads((generation / "axes.json").read_text(encoding="utf-8"))
    dated_fills = []
    for fill in fills:
        row = dict(fill)
        execution_index = int(row.get("execution_index", -1))
        if execution_index < 0 or execution_index >= len(axes["dates"]):
            raise Task055BVerificationError(f"fill_execution_index_invalid:{row.get('fill_id')}")
        row["date"] = str(axes["dates"][execution_index])
        dated_fills.append(row)
    policy = spec.get("policy") or {}
    issues = verify_fill_fees(
        dated_fills,
        schedule,
        modeled_cost_multiplier=float(policy.get("modeled_cost_multiplier", 1.0)),
        zero_all_costs=bool(policy.get("zero_all_costs", False)),
    )
    if issues:
        raise Task055BVerificationError(";".join(issues[:10]))
    marks = _read_jsonl(generation / "valuation_marks.jsonl")
    with np.load(generation / "verification_view.npz", allow_pickle=False) as view:
        mark_open, mark_close, mark_issues = build_mark_matrices(
            marks,
            dates=axes["dates"],
            assets=axes["assets"],
            raw_open=view["open"],
            raw_close=view["close"],
        )
    if mark_issues:
        raise Task055BVerificationError(";".join(mark_issues[:10]))
    payload = {
        "schema_version": "task055b_fee_mark_verification_v1",
        "status": "verified",
        "run_content_hash": verified["content_hash"],
        "run_truth_hash": verified["truth_hash"],
        "fee_schedule_content_hash": schedule["content_hash"],
        "fee_schedule_manifest_sha256": sha256_file(fee_path),
        "mark_evidence_root": canonical_hash(marks),
        "valuation_open_hash": canonical_hash(mark_open.tolist()),
        "valuation_close_hash": canonical_hash(mark_close.tolist()),
        "fee_fill_count": len(fills),
        "mark_count": len(marks),
        "threat_model": "independent_recomputation_from_embedded_tamper_evident_artifacts",
    }
    payload["verification_hash"] = canonical_hash(payload)
    return payload


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

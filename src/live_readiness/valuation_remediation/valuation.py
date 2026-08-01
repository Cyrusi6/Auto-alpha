"""Evidence-driven valuation marks for the Task 055-B ledger simulator."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

from .evidence import (
    SecurityDateState,
    canonical_hash,
    sha256_file,
    validate_evidence_overlay,
)


VALUATION_SCHEMA = "task055b_valuation_evidence_overlay_v2"
VALUATION_POINTER_SCHEMA = "task055b_valuation_evidence_pointer_v1"
VALUATION_MANIFEST_NAME = "valuation_evidence_manifest.json"


class ValuationError(RuntimeError):
    """Raised when a mark cannot be derived from governed evidence."""


class MarketSessionState(str, Enum):
    TRADED = "TRADED"
    OFFICIAL_NON_TRADING = "OFFICIAL_NON_TRADING"
    MODELED_NON_TRADING = "MODELED_NON_TRADING"
    LIFECYCLE_TERMINATED = "LIFECYCLE_TERMINATED"
    SOURCE_CONFLICT = "SOURCE_CONFLICT"
    UNKNOWN = "UNKNOWN"


class ExecutionOpenState(str, Enum):
    ALLOWED = "ALLOWED"
    PROHIBITED_NON_TRADING = "PROHIBITED_NON_TRADING"
    PROHIBITED_TERMINATED = "PROHIBITED_TERMINATED"
    BLOCKED_UNKNOWN = "BLOCKED_UNKNOWN"


class ValuationState(str, Enum):
    OFFICIAL_OPEN = "OFFICIAL_OPEN"
    OFFICIAL_CLOSE = "OFFICIAL_CLOSE"
    STALE_OFFICIAL_NON_TRADING = "STALE_OFFICIAL_NON_TRADING"
    STALE_VENDOR_DAILY_NON_TRADING_MODELED = "STALE_VENDOR_DAILY_NON_TRADING_MODELED"
    LIFECYCLE_SETTLEMENT = "LIFECYCLE_SETTLEMENT"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True)
class ValuationMark:
    ts_code: str
    trade_date: str
    reporting_point: str
    mark_price: float | None
    mark_method: ValuationState
    mark_source_date: str | None
    stale_age_trade_days: int
    market_session_state: MarketSessionState
    execution_open_state: ExecutionOpenState
    execution_allowed: bool
    buy_allowed: bool
    sell_allowed: bool
    corporate_action_transform: Mapping[str, Any] | None
    evidence_hash: str
    evidence_state: str
    valuation_required: bool
    holdings_shares: int
    stale_mark_notional: float
    stale_mark_nav_ratio: float | None
    continuity_error_cny: float
    blocker: str | None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["mark_method"] = self.mark_method.value
        payload["market_session_state"] = self.market_session_state.value
        payload["execution_open_state"] = self.execution_open_state.value
        return payload


def build_valuation_marks(
    evidence_records: Iterable[Mapping[str, Any]],
    *,
    initial_authoritative_marks: Mapping[str, Mapping[str, Any]] | None = None,
    holdings_by_key: Mapping[str, int] | None = None,
    nav_by_date: Mapping[str, float] | None = None,
) -> list[ValuationMark]:
    """Build open/close marks without using membership as a sell/valuation gate."""

    prior: dict[str, dict[str, Any]] = {
        str(asset): {
            "price": float(value["price"]),
            "source_date": str(value["source_date"]),
            "stale_age": int(value.get("stale_age_trade_days", 0)),
        }
        for asset, value in dict(initial_authoritative_marks or {}).items()
    }
    holdings = {str(key): int(value) for key, value in dict(holdings_by_key or {}).items()}
    navs = {str(key): float(value) for key, value in dict(nav_by_date or {}).items()}
    records = sorted((dict(row) for row in evidence_records), key=lambda row: (str(row["trade_date"]), str(row["ts_code"])))
    marks: list[ValuationMark] = []
    for row in records:
        asset = str(row["ts_code"])
        date = str(row["trade_date"])
        state = SecurityDateState(str(row["state"]))
        shares = holdings.get(f"{asset}|{date}", 0)
        evidence_hash = canonical_hash(row)
        membership = bool(row.get("membership")) and bool(row.get("membership_known"))
        action = _validated_action(row.get("corporate_action"))
        transformed_prior, transform, continuity_error = _apply_action_to_prior(prior.get(asset), action, shares)
        if transformed_prior is not None:
            prior[asset] = transformed_prior
        market_state, execution_state = _session_states(state)
        traded = state in {SecurityDateState.TRADED_PRIMARY_BAR, SecurityDateState.TRADED_CORROBORATED_BAR}
        execution_allowed = traded
        buy_allowed = execution_allowed and membership and bool(row.get("active")) and bool(row.get("listed"))
        sell_allowed = execution_allowed and bool(row.get("listed"))
        bar = _governed_bar(row, state)
        for reporting_point in ("open", "close"):
            mark_price: float | None = None
            source_date: str | None = None
            stale_age = 0
            blocker: str | None = None
            if traded and bar is not None:
                mark_price = float(bar[reporting_point])
                source_date = date
                method = ValuationState.OFFICIAL_OPEN if reporting_point == "open" else ValuationState.OFFICIAL_CLOSE
            elif state in {SecurityDateState.OFFICIAL_NON_TRADING, SecurityDateState.VENDOR_DAILY_NON_TRADING_MODELED}:
                previous = prior.get(asset)
                if previous is None:
                    method = ValuationState.UNRESOLVED
                    blocker = "no_prior_authoritative_mark_for_non_trading_day"
                else:
                    mark_price = float(previous["price"])
                    source_date = str(previous["source_date"])
                    stale_age = int(previous.get("stale_age", 0)) + 1
                    method = (
                        ValuationState.STALE_OFFICIAL_NON_TRADING
                        if state == SecurityDateState.OFFICIAL_NON_TRADING
                        else ValuationState.STALE_VENDOR_DAILY_NON_TRADING_MODELED
                    )
            elif state == SecurityDateState.LIFECYCLE_TERMINATED:
                settlement = _termination_value(row.get("lifecycle_event"))
                if settlement is None:
                    method = ValuationState.UNRESOLVED
                    blocker = "lifecycle_termination_without_settlement_evidence"
                else:
                    mark_price = settlement
                    source_date = date
                    method = ValuationState.LIFECYCLE_SETTLEMENT
            else:
                method = ValuationState.UNRESOLVED
                blocker = f"security_date_state_not_valuation_authorized:{state.value}"
            stale_notional = float(mark_price * shares) if mark_price is not None and method in {
                ValuationState.STALE_OFFICIAL_NON_TRADING,
                ValuationState.STALE_VENDOR_DAILY_NON_TRADING_MODELED,
            } else 0.0
            nav = navs.get(date)
            stale_ratio = stale_notional / nav if nav and nav > 0 else (0.0 if stale_notional == 0 else None)
            marks.append(ValuationMark(
                ts_code=asset,
                trade_date=date,
                reporting_point=reporting_point,
                mark_price=mark_price,
                mark_method=method,
                mark_source_date=source_date,
                stale_age_trade_days=stale_age,
                market_session_state=market_state,
                execution_open_state=execution_state,
                execution_allowed=execution_allowed,
                buy_allowed=buy_allowed,
                sell_allowed=sell_allowed,
                corporate_action_transform=transform,
                evidence_hash=evidence_hash,
                evidence_state=state.value,
                valuation_required=bool(row.get("valuation_required")),
                holdings_shares=shares,
                stale_mark_notional=stale_notional,
                stale_mark_nav_ratio=stale_ratio,
                continuity_error_cny=continuity_error,
                blocker=blocker,
            ))
        close_mark = marks[-1]
        if close_mark.mark_price is not None:
            if close_mark.mark_method == ValuationState.OFFICIAL_CLOSE:
                prior[asset] = {"price": close_mark.mark_price, "source_date": date, "stale_age": 0}
            elif close_mark.mark_method in {
                ValuationState.STALE_OFFICIAL_NON_TRADING,
                ValuationState.STALE_VENDOR_DAILY_NON_TRADING_MODELED,
            }:
                prior[asset] = {
                    "price": close_mark.mark_price,
                    "source_date": close_mark.mark_source_date,
                    "stale_age": close_mark.stale_age_trade_days,
                }
    return marks


def publish_valuation_overlay(
    output_root: str | Path,
    *,
    evidence_overlay: str | Path,
    initial_authoritative_marks: Mapping[str, Mapping[str, Any]] | None = None,
    holdings_by_key: Mapping[str, int] | None = None,
    nav_by_date: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    evidence = validate_evidence_overlay(evidence_overlay)
    inputs = {
        "evidence_content_hash": evidence["content_hash"],
        "initial_authoritative_marks": dict(initial_authoritative_marks or {}),
        "holdings_by_key": dict(holdings_by_key or {}),
        "nav_by_date": dict(nav_by_date or {}),
    }
    marks = build_valuation_marks(
        evidence["records"],
        initial_authoritative_marks=inputs["initial_authoritative_marks"],
        holdings_by_key=inputs["holdings_by_key"],
        nav_by_date=inputs["nav_by_date"],
    )
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".task055b_valuation.", dir=root))
    try:
        marks_path = staging / "valuation_marks.jsonl"
        _write_jsonl(marks_path, [mark.to_dict() for mark in marks])
        inputs_path = staging / "valuation_inputs.json"
        _write_json(inputs_path, inputs)
        partitions = {
            marks_path.name: {"sha256": sha256_file(marks_path), "bytes": marks_path.stat().st_size},
            inputs_path.name: {"sha256": sha256_file(inputs_path), "bytes": inputs_path.stat().st_size},
        }
        method_counts = {state.value: 0 for state in ValuationState}
        for mark in marks:
            method_counts[mark.mark_method.value] += 1
        semantic = {
            "schema_version": VALUATION_SCHEMA,
            "evidence_content_hash": evidence["content_hash"],
            "record_count": len(marks),
            "mark_key_hash": canonical_hash([(m.ts_code, m.trade_date, m.reporting_point) for m in marks]),
            "method_counts": method_counts,
            "inputs_hash": canonical_hash(inputs),
            "partitions": partitions,
        }
        content_hash = canonical_hash(semantic)
        generation_id = f"valuation_evidence_{content_hash[:24]}"
        manifest = semantic | {"content_hash": content_hash, "generation_id": generation_id, "status": "published"}
        _write_json(staging / VALUATION_MANIFEST_NAME, manifest)
        target = root / "generations" / generation_id
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(staging)
        else:
            os.replace(staging, target)
        _atomic_write_json(root / "current.json", {
            "schema_version": VALUATION_POINTER_SCHEMA,
            "generation_id": generation_id,
            "content_hash": content_hash,
            "manifest": f"generations/{generation_id}/{VALUATION_MANIFEST_NAME}",
        })
        return manifest | {"root": str(target), "manifest_path": str(target / VALUATION_MANIFEST_NAME)}
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def validate_valuation_overlay(path: str | Path, *, evidence_overlay: str | Path) -> dict[str, Any]:
    generation, manifest = _resolve_generation(path)
    if manifest.get("schema_version") != VALUATION_SCHEMA or manifest.get("status") != "published":
        raise ValuationError("valuation_manifest_invalid")
    for name, entry in dict(manifest.get("partitions", {})).items():
        artifact = generation / name
        if not artifact.is_file() or sha256_file(artifact) != entry.get("sha256"):
            raise ValuationError(f"valuation_partition_mismatch:{name}")
    inputs = _read_json(generation / "valuation_inputs.json")
    evidence = validate_evidence_overlay(evidence_overlay)
    if inputs.get("evidence_content_hash") != evidence.get("content_hash"):
        raise ValuationError("valuation_evidence_lineage_mismatch")
    rebuilt = [mark.to_dict() for mark in build_valuation_marks(
        evidence["records"],
        initial_authoritative_marks=inputs.get("initial_authoritative_marks"),
        holdings_by_key=inputs.get("holdings_by_key"),
        nav_by_date=inputs.get("nav_by_date"),
    )]
    stored = _read_jsonl(generation / "valuation_marks.jsonl")
    if rebuilt != stored:
        raise ValuationError("valuation_marks_recomputation_mismatch")
    semantic = {key: manifest[key] for key in (
        "schema_version", "evidence_content_hash", "record_count", "mark_key_hash",
        "method_counts", "inputs_hash", "partitions",
    )}
    if canonical_hash(inputs) != manifest.get("inputs_hash") or canonical_hash(semantic) != manifest.get("content_hash"):
        raise ValuationError("valuation_content_hash_mismatch")
    return manifest | {"root": str(generation), "manifest_path": str(generation / VALUATION_MANIFEST_NAME), "marks": stored}


def _session_states(state: SecurityDateState) -> tuple[MarketSessionState, ExecutionOpenState]:
    if state in {SecurityDateState.TRADED_PRIMARY_BAR, SecurityDateState.TRADED_CORROBORATED_BAR}:
        return MarketSessionState.TRADED, ExecutionOpenState.ALLOWED
    if state == SecurityDateState.OFFICIAL_NON_TRADING:
        return MarketSessionState.OFFICIAL_NON_TRADING, ExecutionOpenState.PROHIBITED_NON_TRADING
    if state == SecurityDateState.VENDOR_DAILY_NON_TRADING_MODELED:
        return MarketSessionState.MODELED_NON_TRADING, ExecutionOpenState.PROHIBITED_NON_TRADING
    if state == SecurityDateState.LIFECYCLE_TERMINATED:
        return MarketSessionState.LIFECYCLE_TERMINATED, ExecutionOpenState.PROHIBITED_TERMINATED
    if state in {SecurityDateState.TRADED_SOURCE_CONFLICT, SecurityDateState.CONFLICT}:
        return MarketSessionState.SOURCE_CONFLICT, ExecutionOpenState.BLOCKED_UNKNOWN
    return MarketSessionState.UNKNOWN, ExecutionOpenState.BLOCKED_UNKNOWN


def _governed_bar(row: Mapping[str, Any], state: SecurityDateState) -> Mapping[str, Any] | None:
    if state == SecurityDateState.TRADED_PRIMARY_BAR:
        return row.get("primary_bar") if isinstance(row.get("primary_bar"), Mapping) else None
    if state == SecurityDateState.TRADED_CORROBORATED_BAR:
        return row.get("primary_bar") if isinstance(row.get("primary_bar"), Mapping) else None
    return None


def _validated_action(value: Any) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping) or not value:
        return None
    if not value.get("valuation_transform_proven") or not value.get("source_sha256"):
        return None
    ratio = float(value.get("share_ratio", 1.0))
    dividend = float(value.get("cash_dividend_per_old_share", 0.0))
    if ratio <= 0 or not _finite(ratio) or dividend < 0 or not _finite(dividend):
        return None
    return dict(value)


def _apply_action_to_prior(
    previous: Mapping[str, Any] | None,
    action: Mapping[str, Any] | None,
    old_shares: int,
) -> tuple[dict[str, Any] | None, Mapping[str, Any] | None, float]:
    if previous is None or action is None:
        return (dict(previous) if previous is not None else None), None, 0.0
    ratio = float(action.get("share_ratio", 1.0))
    dividend = float(action.get("cash_dividend_per_old_share", 0.0))
    old_price = float(previous["price"])
    if old_price < dividend:
        return dict(previous), dict(action), float("inf")
    new_price = (old_price - dividend) / ratio
    old_value = old_shares * old_price
    new_value = old_shares * ratio * new_price + old_shares * dividend
    transformed = dict(previous)
    transformed["price"] = new_price
    transformed["stale_age"] = int(previous.get("stale_age", 0))
    transform = {
        "share_ratio": ratio,
        "cash_dividend_per_old_share": dividend,
        "source_sha256": action["source_sha256"],
        "old_mark_price": old_price,
        "transformed_mark_price": new_price,
    }
    return transformed, transform, abs(new_value - old_value)


def _termination_value(value: Any) -> float | None:
    if not isinstance(value, Mapping) or not value.get("source_sha256"):
        return None
    try:
        settlement = float(value["settlement_value_per_share"])
    except (KeyError, TypeError, ValueError):
        return None
    return settlement if settlement >= 0 and _finite(settlement) else None


def _finite(value: float) -> bool:
    return value == value and value not in (float("inf"), float("-inf"))


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":"), default=str) + "\n")


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    _write_json(temporary, payload)
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _resolve_generation(path: str | Path) -> tuple[Path, dict[str, Any]]:
    candidate = Path(path)
    if candidate.is_file():
        return candidate.parent, _read_json(candidate)
    pointer = candidate / "current.json"
    if pointer.is_file():
        payload = _read_json(pointer)
        manifest_path = candidate / str(payload.get("manifest"))
        manifest = _read_json(manifest_path)
        if payload.get("content_hash") != manifest.get("content_hash"):
            raise ValuationError("valuation_pointer_drift")
        return manifest_path.parent, manifest
    manifest_path = candidate / VALUATION_MANIFEST_NAME
    if manifest_path.is_file():
        return candidate, _read_json(manifest_path)
    raise ValuationError("valuation_overlay_not_found")

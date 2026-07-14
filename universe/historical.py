"""Historical PIT index snapshots, lifecycle, status, and market-validity masks."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact


class HistoricalUniverseBlocker(RuntimeError):
    pass


@dataclass(frozen=True)
class SnapshotPolicy:
    index_code: str
    expected_member_count: int = 300
    member_count_tolerance: int = 0
    min_weight_sum: float = 99.5
    max_weight_sum: float = 100.5
    max_staleness_calendar_days: int = 45
    policy_version: str = "historical_index_snapshot_v1"
    canonical_source_proof: str | None = None

    def fingerprint(self) -> str:
        return _hash_json(asdict(self))


@dataclass(frozen=True)
class HistoricalUniverseResult:
    output_dir: str
    proof_manifest_path: str
    stock_axis_hash: str
    date_axis_hash: str
    snapshot_source_hash: str
    snapshot_count: int
    union_member_count: int
    usable_start_date: str | None
    usable_end_date: str | None
    historical_constituent_proof: bool
    blockers: tuple[str, ...]


def build_historical_index_universe(
    index_members_path: str | Path,
    trade_calendar_path: str | Path,
    output_root: str | Path,
    policy: SnapshotPolicy,
) -> HistoricalUniverseResult:
    source_path = Path(index_members_path)
    calendar_path = Path(trade_calendar_path)
    canonical = _canonical_index_code(policy.index_code, policy.canonical_source_proof)
    trading_dates = _load_trading_dates(calendar_path)
    snapshots: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_aliases: set[str] = set()
    with source_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            row_code = str(row.get("index_code") or "")
            if row_code != canonical:
                continue
            seen_aliases.add(row_code)
            snapshots[str(row.get("trade_date") or "")].append(row)
    if not snapshots:
        raise HistoricalUniverseBlocker(f"no snapshots for index_code={canonical}")
    source_hash = _sha256(source_path)
    complete: dict[str, list[dict[str, Any]]] = {}
    rejected: dict[str, list[str]] = {}
    snapshot_rows: list[dict[str, Any]] = []
    for snapshot_date, rows in sorted(snapshots.items()):
        reasons = _snapshot_reasons(snapshot_date, rows, trading_dates, policy)
        if reasons:
            rejected[snapshot_date] = reasons
            continue
        ordered = sorted(rows, key=lambda item: str(item["ts_code"]))
        complete[snapshot_date] = ordered
        snapshot_rows.extend(
            {"index_code": canonical, "snapshot_date": snapshot_date, "ts_code": str(row["ts_code"]), "weight": float(row["weight"])}
            for row in ordered
        )
    if not complete:
        raise HistoricalUniverseBlocker("no complete index snapshots")
    union = sorted({str(row["ts_code"]) for rows in complete.values() for row in rows})
    dates = sorted(trading_dates)
    membership, weights, known, source_dates = _map_snapshots_to_daily(complete, union, dates, policy.max_staleness_calendar_days)
    usable_indices = np.flatnonzero(known)
    usable_start = dates[int(usable_indices[0])] if usable_indices.size else None
    usable_end = dates[int(usable_indices[-1])] if usable_indices.size else None
    missing_months = _missing_months(min(complete), max(complete), {value[:6] for value in complete})
    blockers = []
    if rejected:
        blockers.append("incomplete_snapshot_rows_present")
    if missing_months:
        blockers.append("snapshot_months_missing")
    transitions = _transition_stats(membership, known)
    generation_payload = {
        "source_hash": source_hash,
        "calendar_hash": _sha256(calendar_path),
        "policy": asdict(policy),
        "stock_axis_hash": _hash_list(union),
        "date_axis_hash": _hash_list(dates),
        "code_semantic_hash": _sha256(Path(__file__)),
    }
    generation = _hash_json(generation_payload)[:20]
    target = Path(output_root) / f"historical_index_{canonical.replace('.', '_').lower()}_{generation}"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
        try:
            write_jsonl_artifact(
                tmp / "historical_union_members.jsonl",
                ({"ts_code": code} for code in union),
                "historical_union_members",
                "universe.historical",
            )
            write_jsonl_artifact(
                tmp / "index_snapshots.jsonl",
                snapshot_rows,
                "historical_index_snapshots",
                "universe.historical",
            )
            _atomic_npy(tmp / "index_membership.npy", membership.astype(np.bool_))
            _atomic_npy(tmp / "index_weight.npy", weights.astype(np.float32))
            _atomic_npy(tmp / "membership_known.npy", known.astype(np.bool_))
            _atomic_npy(tmp / "snapshot_source_date.npy", np.asarray(source_dates, dtype="S8"))
            (tmp / "ts_codes.json").write_text(json.dumps(union, indent=2), encoding="utf-8")
            (tmp / "trade_dates.json").write_text(json.dumps(dates, indent=2), encoding="utf-8")
            proof = {
                "artifact_type": "historical_index_snapshot_proof",
                "policy": asdict(policy),
                "index_code": canonical,
                "source_index_codes": sorted(seen_aliases),
                "snapshot_source_hash": source_hash,
                "trade_calendar_hash": _sha256(calendar_path),
                "snapshot_count": len(complete),
                "snapshot_range": [min(complete), max(complete)],
                "month_coverage_count": len({value[:6] for value in complete}),
                "missing_months": missing_months,
                "max_snapshot_gap_days": _max_gap_days(sorted(complete)),
                "member_count_distribution": _distribution([len(rows) for rows in complete.values()]),
                "weight_sum_distribution": _distribution([sum(float(row["weight"]) for row in rows) for rows in complete.values()]),
                "transition_count": transitions,
                "usable_period": [usable_start, usable_end],
                "union_member_count": len(union),
                "current_member_count": int(membership[:, usable_indices[-1]].sum()) if usable_indices.size else 0,
                "removed_member_leakage": 0,
                "rejected_snapshots": rejected,
                "stock_axis_hash": _hash_list(union),
                "date_axis_hash": _hash_list(dates),
                "partition_sha256": {},
                "constituent_publication_timing_unknown": True,
                "blockers": blockers + ["constituent_publication_timing_unknown"],
                "historical_constituent_proof": not missing_months,
                "universe_mode": "daily_pit_constituents" if not missing_months else "blocked",
                "alpha_discovery_data_ready": False,
                "input_fingerprint": _hash_json(generation_payload),
            }
            for filename in ["historical_union_members.jsonl", "index_snapshots.jsonl", "index_membership.npy", "index_weight.npy", "membership_known.npy", "snapshot_source_date.npy"]:
                proof["partition_sha256"][filename] = _sha256(tmp / filename)
            write_json_artifact(
                tmp / "snapshot_proof_manifest.json",
                proof,
                "historical_index_snapshot_proof",
                "universe.historical",
            )
            os.replace(tmp, target)
        except Exception:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
    proof_path = target / "snapshot_proof_manifest.json"
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    return HistoricalUniverseResult(
        output_dir=str(target), proof_manifest_path=str(proof_path), stock_axis_hash=proof["stock_axis_hash"],
        date_axis_hash=proof["date_axis_hash"], snapshot_source_hash=source_hash, snapshot_count=int(proof["snapshot_count"]),
        union_member_count=int(proof["union_member_count"]), usable_start_date=proof["usable_period"][0],
        usable_end_date=proof["usable_period"][1], historical_constituent_proof=bool(proof["historical_constituent_proof"]),
        blockers=tuple(proof["blockers"]),
    )


def build_lifecycle_mask(securities: Iterable[dict[str, Any]], ts_codes: list[str], trade_dates: list[str]) -> np.ndarray:
    by_code = {str(row.get("ts_code")): row for row in securities}
    mask = np.zeros((len(ts_codes), len(trade_dates)), dtype=np.bool_)
    for stock_index, code in enumerate(ts_codes):
        row = by_code.get(code)
        if not row:
            continue
        listed = str(row.get("list_date") or "")
        delisted = str(row.get("delist_date") or "99999999")
        if not listed:
            continue
        mask[stock_index] = np.asarray([listed <= date < delisted for date in trade_dates], dtype=np.bool_)
    return mask


def build_st_masks(name_changes: Iterable[dict[str, Any]], ts_codes: list[str], trade_dates: list[str]) -> tuple[np.ndarray, np.ndarray]:
    index = {code: idx for idx, code in enumerate(ts_codes)}
    st = np.zeros((len(ts_codes), len(trade_dates)), dtype=np.bool_)
    known = np.zeros_like(st)
    for row in name_changes:
        stock_index = index.get(str(row.get("ts_code") or ""))
        start = str(row.get("start_date") or "")
        announcement = str(row.get("ann_date") or "")
        if stock_index is None or not start or not announcement:
            continue
        effective = max(start, announcement)
        end = str(row.get("end_date") or "99999999")
        is_st = "ST" in str(row.get("name") or "").upper() or "ST" in str(row.get("change_reason") or "").upper()
        for date_index, date in enumerate(trade_dates):
            if effective <= date < end:
                known[stock_index, date_index] = True
                st[stock_index, date_index] = is_st
    return st, known


def normalize_suspensions(records: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    normalized: list[dict[str, Any]] = []
    blockers: list[str] = []
    for row in records:
        code = str(row.get("ts_code") or "")
        if row.get("trade_date"):
            date = str(row["trade_date"])
            normalized.append({"ts_code": code, "start_date": date, "end_date": date, "source_schema": "daily", "timing": row.get("suspend_timing"), "type": row.get("suspend_type")})
        elif row.get("suspend_date"):
            normalized.append({"ts_code": code, "start_date": str(row["suspend_date"]), "end_date": str(row.get("resume_date") or row["suspend_date"]), "source_schema": "legacy_interval", "timing": None, "type": row.get("reason_type")})
        else:
            blockers.append(f"suspension_date_unknown:{code}")
    return normalized, blockers


def align_daily_fields(records: Iterable[dict[str, Any]], ts_codes: list[str], trade_dates: list[str], fields: list[str]) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    stock_index = {code: idx for idx, code in enumerate(ts_codes)}
    date_index = {date: idx for idx, date in enumerate(trade_dates)}
    values = {field: np.full((len(ts_codes), len(trade_dates)), np.nan, dtype=np.float32) for field in fields}
    validity = {field: np.zeros((len(ts_codes), len(trade_dates)), dtype=np.bool_) for field in fields}
    for row in records:
        i = stock_index.get(str(row.get("ts_code") or "")); j = date_index.get(str(row.get("trade_date") or ""))
        if i is None or j is None:
            continue
        for field in fields:
            value = row.get(field)
            if value is None:
                continue
            try: numeric = float(value)
            except (TypeError, ValueError): continue
            if np.isfinite(numeric):
                values[field][i, j] = numeric
                validity[field][i, j] = True
    return values, validity


def target_available_mask(price_validity: np.ndarray, horizon: int = 1) -> np.ndarray:
    result = np.zeros_like(price_validity, dtype=np.bool_)
    if horizon > 0 and price_validity.shape[1] > horizon:
        result[:, :-horizon] = price_validity[:, :-horizon] & price_validity[:, horizon:]
    return result


def _snapshot_reasons(date: str, rows: list[dict[str, Any]], trading_dates: set[str], policy: SnapshotPolicy) -> list[str]:
    reasons: list[str] = []
    if date not in trading_dates: reasons.append("snapshot_not_trading_day")
    members = [str(row.get("ts_code") or "") for row in rows]
    if len(set(members)) != len(members): reasons.append("duplicate_member")
    expected_min = policy.expected_member_count - policy.member_count_tolerance
    expected_max = policy.expected_member_count + policy.member_count_tolerance
    if not expected_min <= len(set(members)) <= expected_max: reasons.append("member_count_out_of_policy")
    weights = []
    for row in rows:
        try: value = float(row.get("weight"))
        except (TypeError, ValueError): value = -1.0
        if not str(row.get("ts_code") or "") or value < 0: reasons.append("invalid_member_or_weight")
        weights.append(value)
    total = sum(weights)
    if not policy.min_weight_sum <= total <= policy.max_weight_sum: reasons.append("weight_sum_out_of_policy")
    return sorted(set(reasons))


def _map_snapshots_to_daily(snapshots: dict[str, list[dict[str, Any]]], union: list[str], dates: list[str], max_staleness: int):
    membership = np.zeros((len(union), len(dates)), dtype=np.bool_); weights = np.zeros_like(membership, dtype=np.float32)
    known = np.zeros(len(dates), dtype=np.bool_); source_dates = [""] * len(dates); stock_index = {code: idx for idx, code in enumerate(union)}
    snapshot_dates = sorted(snapshots); pointer = -1
    for j, date in enumerate(dates):
        while pointer + 1 < len(snapshot_dates) and snapshot_dates[pointer + 1] <= date: pointer += 1
        if pointer < 0: continue
        source = snapshot_dates[pointer]; age = (_date(date) - _date(source)).days
        if age > max_staleness: continue
        source_dates[j] = source; known[j] = True
        for row in snapshots[source]:
            i = stock_index[str(row["ts_code"])]; membership[i, j] = True; weights[i, j] = float(row["weight"]) / 100.0
    return membership, weights, known, source_dates


def _canonical_index_code(value: str, proof: str | None) -> str:
    aliases = {"000300.SH": "000300.SH", "399300.SZ": "000300.SH"}
    if value not in aliases: raise HistoricalUniverseBlocker(f"unsupported index alias: {value}")
    if value != aliases[value] and not proof: raise HistoricalUniverseBlocker("index alias requires canonical source proof")
    return aliases[value]


def _load_trading_dates(path: Path) -> set[str]:
    result=set()
    with path.open() as handle:
        for line in handle:
            row=json.loads(line)
            if bool(row.get("is_open", True)) and row.get("trade_date"): result.add(str(row["trade_date"]))
    return result


def _missing_months(start: str, end: str, observed: set[str]) -> list[str]:
    current=datetime.strptime(start[:6], "%Y%m"); finish=datetime.strptime(end[:6], "%Y%m"); missing=[]
    while current <= finish:
        key=current.strftime("%Y%m")
        if key not in observed: missing.append(key)
        current=datetime(current.year + (current.month == 12), 1 if current.month == 12 else current.month + 1, 1)
    return missing


def _transition_stats(membership: np.ndarray, known: np.ndarray) -> dict[str, int]:
    entered=exited=0; previous=None
    for idx in np.flatnonzero(known):
        current=membership[:, idx]
        if previous is not None:
            entered += int((current & ~previous).sum()); exited += int((previous & ~current).sum())
        previous=current
    return {"entered": entered, "exited": exited}


def _distribution(values: list[float]) -> dict[str, float]:
    array=np.asarray(values,dtype=np.float64)
    return {"min":float(array.min()),"median":float(np.median(array)),"max":float(array.max())}


def _max_gap_days(dates: list[str]) -> int:
    return max(((_date(right)-_date(left)).days for left,right in zip(dates,dates[1:])),default=0)


def _date(value: str) -> datetime: return datetime.strptime(value, "%Y%m%d")
def _hash_list(values: list[str]) -> str: return hashlib.sha256("\n".join(values).encode()).hexdigest()
def _hash_json(value: Any) -> str: return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
def _sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b""): h.update(chunk)
    return h.hexdigest()
def _atomic_npy(path: Path, value: np.ndarray) -> None:
    temp=path.with_suffix(path.suffix+".tmp")
    with temp.open("wb") as handle: np.save(handle,value,allow_pickle=False)
    os.replace(temp,path)

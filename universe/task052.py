"""Task 052-A governed historical universe proof generation."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact


DETERMINISTIC_CREATED_AT = "1970-01-01T00:00:00Z"
UNIVERSE_SEMANTIC_CONTRACT = {
    "task": "052-A",
    "axis": "union_of_all_accepted_historical_members",
    "snapshot_effective_rule": "next_trade_day",
    "snapshot_replacement": "full",
    "unknown_before_first_effective_snapshot": True,
    "removed_member_leakage_check": "recomputed_from_effective_snapshot_transitions",
    "source_lineage": "manifest_hash_pinned",
    "version": 1,
}


class Task052UniverseProofError(RuntimeError):
    """Raised when historical membership cannot satisfy the strict proof."""


@dataclass(frozen=True)
class Task052UniversePolicy:
    index_code: str = "000300.SH"
    expected_member_count: int = 300
    min_weight_sum: float = 99.5
    max_weight_sum: float = 100.5
    max_staleness_calendar_days: int = 45
    membership_lag_trade_days: int = 1
    require_zero_rejected_snapshots: bool = True
    require_complete_month_coverage: bool = True
    policy_version: str = "task_052a_historical_universe_v1"

    def __post_init__(self) -> None:
        if self.index_code != "000300.SH":
            raise ValueError("Task 052-A accepts only canonical CSI300 index_code=000300.SH")
        if self.expected_member_count <= 0:
            raise ValueError("expected_member_count must be positive")
        if self.membership_lag_trade_days != 1:
            raise ValueError("Task 052-A requires exactly one trade-day membership lag")

    @property
    def semantic_hash(self) -> str:
        return _hash_json({"policy": asdict(self), "contract": UNIVERSE_SEMANTIC_CONTRACT})


@dataclass(frozen=True)
class Task052UniverseProofResult:
    generation_id: str
    generation_dir: str
    proof_manifest_path: str
    semantic_hash: str
    content_hash: str
    snapshot_count: int
    union_member_count: int
    removed_member_leakage: int


class Task052HistoricalUniverseProofBuilder:
    """Build a deterministic, lagged and source-lineage-pinned CSI300 proof."""

    def __init__(self, policy: Task052UniversePolicy | None = None):
        self.policy = policy or Task052UniversePolicy()

    def build(
        self,
        index_members_path: str | Path,
        trade_calendar_path: str | Path,
        source_lineage_manifest_path: str | Path,
        output_root: str | Path,
    ) -> Task052UniverseProofResult:
        members_path = Path(index_members_path)
        calendar_path = Path(trade_calendar_path)
        lineage_path = Path(source_lineage_manifest_path)
        member_hash = _sha256(members_path)
        calendar_hash = _sha256(calendar_path)
        lineage_payload = _read_json(lineage_path)
        lineage_hash = _sha256(lineage_path)
        lineage_evidence = _verify_source_lineage(lineage_payload, member_hash, calendar_hash)

        trade_dates = _load_trade_dates(calendar_path)
        trade_date_set = set(trade_dates)
        snapshots: dict[str, list[dict[str, Any]]] = defaultdict(list)
        foreign_index_rows = 0
        for row in _read_jsonl(members_path):
            if str(row.get("index_code") or "") != self.policy.index_code:
                foreign_index_rows += 1
                continue
            snapshots[str(row.get("trade_date") or "")].append(row)
        if not snapshots:
            raise Task052UniverseProofError("no canonical CSI300 snapshots found")

        accepted: dict[str, list[dict[str, Any]]] = {}
        rejected: dict[str, list[str]] = {}
        for snapshot_date, rows in sorted(snapshots.items()):
            reasons = _snapshot_rejection_reasons(rows, snapshot_date, trade_date_set, self.policy)
            if reasons:
                rejected[snapshot_date] = reasons
            else:
                accepted[snapshot_date] = sorted(rows, key=lambda item: str(item["ts_code"]))
        missing_months = _missing_months(sorted(accepted))
        blockers: list[str] = []
        if rejected and self.policy.require_zero_rejected_snapshots:
            blockers.append("rejected_snapshot_count_nonzero")
        if missing_months and self.policy.require_complete_month_coverage:
            blockers.append("natural_month_coverage_incomplete")
        if blockers:
            raise Task052UniverseProofError(
                json.dumps(
                    {"blockers": blockers, "rejected_snapshots": rejected, "missing_months": missing_months},
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )

        union = sorted({str(row["ts_code"]) for rows in accepted.values() for row in rows})
        membership, weights, known, source_dates, effective_dates = _build_lagged_daily_membership(
            accepted,
            union,
            trade_dates,
            self.policy.max_staleness_calendar_days,
        )
        leakage = _removed_member_leakage(accepted, union, trade_dates, membership, effective_dates)
        if leakage["count"]:
            raise Task052UniverseProofError(f"removed-member leakage detected: {leakage['count']}")

        stock_axis_hash = _hash_lines(union)
        date_axis_hash = _hash_lines(trade_dates)
        source_payload = {
            "index_members_sha256": member_hash,
            "trade_calendar_sha256": calendar_hash,
            "source_lineage_manifest_sha256": lineage_hash,
            "source_lineage_evidence": lineage_evidence,
        }
        generation_inputs = {
            "semantic_hash": self.policy.semantic_hash,
            "policy": asdict(self.policy),
            "source": source_payload,
            "stock_axis_hash": stock_axis_hash,
            "date_axis_hash": date_axis_hash,
        }
        content_hash = _hash_json(generation_inputs)
        generation_id = f"universe_052a_{content_hash[:24]}"
        target = Path(output_root) / generation_id
        if not target.exists():
            self._write_generation(
                target=target,
                accepted=accepted,
                rejected=rejected,
                union=union,
                trade_dates=trade_dates,
                membership=membership,
                weights=weights,
                known=known,
                source_dates=source_dates,
                effective_dates=effective_dates,
                missing_months=missing_months,
                foreign_index_rows=foreign_index_rows,
                leakage=leakage,
                generation_inputs=generation_inputs,
                content_hash=content_hash,
            )
        _validate_existing_generation(target, content_hash, self.policy.semantic_hash)
        return Task052UniverseProofResult(
            generation_id=generation_id,
            generation_dir=str(target),
            proof_manifest_path=str(target / "task_052a_universe_proof_manifest.json"),
            semantic_hash=self.policy.semantic_hash,
            content_hash=content_hash,
            snapshot_count=len(accepted),
            union_member_count=len(union),
            removed_member_leakage=int(leakage["count"]),
        )

    def _write_generation(
        self,
        *,
        target: Path,
        accepted: dict[str, list[dict[str, Any]]],
        rejected: dict[str, list[str]],
        union: list[str],
        trade_dates: list[str],
        membership: np.ndarray,
        weights: np.ndarray,
        known: np.ndarray,
        source_dates: list[str],
        effective_dates: dict[str, str],
        missing_months: list[str],
        foreign_index_rows: int,
        leakage: dict[str, Any],
        generation_inputs: dict[str, Any],
        content_hash: str,
    ) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
        try:
            snapshot_rows = [
                {
                    "index_code": self.policy.index_code,
                    "snapshot_date": snapshot_date,
                    "effective_trade_date": effective_dates[snapshot_date],
                    "ts_code": str(row["ts_code"]),
                    "weight": float(row["weight"]),
                }
                for snapshot_date, rows in sorted(accepted.items())
                for row in rows
            ]
            write_jsonl_artifact(
                temporary / "accepted_index_snapshots.jsonl",
                snapshot_rows,
                "task_052a_accepted_index_snapshots",
                "universe.task052",
                created_at=DETERMINISTIC_CREATED_AT,
            )
            _write_json(temporary / "ts_codes.json", union)
            _write_json(temporary / "trade_dates.json", trade_dates)
            _write_npy(temporary / "index_membership.npy", membership.astype(np.bool_))
            _write_npy(temporary / "index_weight.npy", weights.astype(np.float32))
            _write_npy(temporary / "membership_known.npy", known.astype(np.bool_))
            _write_npy(temporary / "snapshot_source_date.npy", np.asarray(source_dates, dtype="S8"))
            partitions = {
                path.name: _sha256(path)
                for path in sorted(temporary.iterdir())
                if path.is_file()
            }
            proof = {
                "generation_id": target.name,
                "content_hash": content_hash,
                "semantic_hash": self.policy.semantic_hash,
                "semantic_contract": UNIVERSE_SEMANTIC_CONTRACT,
                "index_code": self.policy.index_code,
                "policy": asdict(self.policy),
                "snapshot_count": len(accepted),
                "rejected_snapshot_count": len(rejected),
                "rejected_snapshots": rejected,
                "all_snapshot_member_counts_exact": all(len(rows) == self.policy.expected_member_count for rows in accepted.values()),
                "member_count": self.policy.expected_member_count,
                "weight_sum_range": [
                    min(sum(float(row["weight"]) for row in rows) for rows in accepted.values()),
                    max(sum(float(row["weight"]) for row in rows) for rows in accepted.values()),
                ],
                "weight_policy_passed": True,
                "natural_month_coverage": {
                    "observed_month_count": len({date[:6] for date in accepted}),
                    "missing_months": missing_months,
                    "complete": not missing_months,
                },
                "source_lineage": generation_inputs["source"],
                "foreign_index_row_count": foreign_index_rows,
                "membership_lag_trade_days": self.policy.membership_lag_trade_days,
                "snapshot_effective_dates": dict(sorted(effective_dates.items())),
                "union_member_count": len(union),
                "stock_axis_hash": generation_inputs["stock_axis_hash"],
                "date_axis_hash": generation_inputs["date_axis_hash"],
                "removed_member_leakage": leakage,
                "partition_sha256": partitions,
                "proof_checks": {
                    "rejected_snapshots_zero": len(rejected) == 0,
                    "member_count_exact": True,
                    "weights_within_policy": True,
                    "natural_month_coverage_complete": not missing_months,
                    "source_lineage_verified": True,
                    "removed_member_leakage_zero": leakage["count"] == 0,
                    "membership_lag_one_trade_day": True,
                },
                "historical_constituent_proof": True,
                "blockers": [],
            }
            write_json_artifact(
                temporary / "task_052a_universe_proof_manifest.json",
                proof,
                "task_052a_universe_proof_manifest",
                "universe.task052",
                created_at=DETERMINISTIC_CREATED_AT,
            )
            os.replace(temporary, target)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise


def _build_lagged_daily_membership(
    snapshots: dict[str, list[dict[str, Any]]],
    union: list[str],
    trade_dates: list[str],
    max_staleness_calendar_days: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str], dict[str, str]]:
    date_index = {date: index for index, date in enumerate(trade_dates)}
    effective_dates: dict[str, str] = {}
    for snapshot_date in sorted(snapshots):
        snapshot_index = date_index[snapshot_date]
        if snapshot_index + 1 >= len(trade_dates):
            raise Task052UniverseProofError(f"snapshot has no next trade day for conservative lag: {snapshot_date}")
        effective_dates[snapshot_date] = trade_dates[snapshot_index + 1]
    stock_index = {code: index for index, code in enumerate(union)}
    membership = np.zeros((len(union), len(trade_dates)), dtype=np.bool_)
    weights = np.zeros((len(union), len(trade_dates)), dtype=np.float32)
    known = np.zeros(len(trade_dates), dtype=np.bool_)
    source_dates = [""] * len(trade_dates)
    ordered_snapshots = sorted(snapshots)
    pointer = -1
    for date_position, trade_date in enumerate(trade_dates):
        while pointer + 1 < len(ordered_snapshots) and effective_dates[ordered_snapshots[pointer + 1]] <= trade_date:
            pointer += 1
        if pointer < 0:
            continue
        source_date = ordered_snapshots[pointer]
        if (_parse_date(trade_date) - _parse_date(source_date)).days > max_staleness_calendar_days:
            continue
        known[date_position] = True
        source_dates[date_position] = source_date
        for row in snapshots[source_date]:
            stock_position = stock_index[str(row["ts_code"])]
            membership[stock_position, date_position] = True
            weights[stock_position, date_position] = float(row["weight"]) / 100.0
    return membership, weights, known, source_dates, effective_dates


def _removed_member_leakage(
    snapshots: dict[str, list[dict[str, Any]]],
    union: list[str],
    trade_dates: list[str],
    membership: np.ndarray,
    effective_dates: dict[str, str],
) -> dict[str, Any]:
    stock_index = {code: index for index, code in enumerate(union)}
    ordered = sorted(snapshots)
    examples: list[dict[str, str]] = []
    count = 0
    pointer = -1
    ever_members: set[str] = set()
    for date_position, trade_date in enumerate(trade_dates):
        while pointer + 1 < len(ordered) and effective_dates[ordered[pointer + 1]] <= trade_date:
            pointer += 1
            ever_members.update(str(row["ts_code"]) for row in snapshots[ordered[pointer]])
        if pointer < 0:
            continue
        source_date = ordered[pointer]
        expected = {str(row["ts_code"]) for row in snapshots[source_date]}
        removed = sorted(ever_members - expected)
        for code in removed:
            if membership[stock_index[code], date_position]:
                count += 1
                if len(examples) < 20:
                    examples.append(
                        {
                            "ts_code": code,
                            "trade_date": trade_date,
                            "effective_snapshot": source_date,
                        }
                    )
    return {"count": count, "examples": examples, "method": "daily_expected_membership_recomputation"}


def _snapshot_rejection_reasons(
    rows: list[dict[str, Any]],
    snapshot_date: str,
    trade_dates: set[str],
    policy: Task052UniversePolicy,
) -> list[str]:
    reasons: list[str] = []
    if snapshot_date not in trade_dates:
        reasons.append("snapshot_not_open_trade_day")
    codes = [str(row.get("ts_code") or "") for row in rows]
    if len(rows) != policy.expected_member_count or len(set(codes)) != policy.expected_member_count:
        reasons.append("member_count_not_exact")
    if any(not code for code in codes):
        reasons.append("blank_member_code")
    weights: list[float] = []
    for row in rows:
        try:
            weight = float(row.get("weight"))
        except (TypeError, ValueError):
            weight = float("nan")
        if not np.isfinite(weight) or weight < 0:
            reasons.append("invalid_weight")
        weights.append(weight)
    weight_sum = float(np.sum(weights))
    if not policy.min_weight_sum <= weight_sum <= policy.max_weight_sum:
        reasons.append("weight_sum_out_of_policy")
    return sorted(set(reasons))


def _verify_source_lineage(payload: dict[str, Any], member_hash: str, calendar_hash: str) -> dict[str, Any]:
    strings = set(_walk_strings(payload))
    missing = []
    if member_hash not in strings:
        missing.append("index_members_sha256")
    if calendar_hash not in strings:
        missing.append("trade_calendar_sha256")
    if missing:
        raise Task052UniverseProofError(f"source lineage manifest does not pin: {','.join(missing)}")
    return {"index_members_hash_pinned": True, "trade_calendar_hash_pinned": True}


def _walk_strings(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)
    elif value is not None:
        yield str(value)


def _missing_months(snapshot_dates: list[str]) -> list[str]:
    if not snapshot_dates:
        return []
    observed = {date[:6] for date in snapshot_dates}
    current = datetime.strptime(snapshot_dates[0][:6], "%Y%m")
    finish = datetime.strptime(snapshot_dates[-1][:6], "%Y%m")
    missing: list[str] = []
    while current <= finish:
        month = current.strftime("%Y%m")
        if month not in observed:
            missing.append(month)
        current = datetime(current.year + (current.month == 12), 1 if current.month == 12 else current.month + 1, 1)
    return missing


def _load_trade_dates(path: Path) -> list[str]:
    dates = sorted(
        {
            str(row["trade_date"])
            for row in _read_jsonl(path)
            if row.get("trade_date") and bool(row.get("is_open", True))
        }
    )
    if not dates:
        raise Task052UniverseProofError("trade calendar has no open dates")
    return dates


def _validate_existing_generation(target: Path, content_hash: str, semantic_hash: str) -> None:
    manifest_path = target / "task_052a_universe_proof_manifest.json"
    if not manifest_path.exists():
        raise Task052UniverseProofError(f"existing generation is incomplete: {target}")
    manifest = _read_json(manifest_path)
    if manifest.get("content_hash") != content_hash or manifest.get("semantic_hash") != semantic_hash:
        raise Task052UniverseProofError(f"content-address collision or mutation: {target}")
    for filename, expected_hash in manifest.get("partition_sha256", {}).items():
        path = target / filename
        if not path.exists() or _sha256(path) != expected_hash:
            raise Task052UniverseProofError(f"immutable universe generation drift: {filename}")


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise Task052UniverseProofError(f"expected JSON object: {path}")
    return payload


def _read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise Task052UniverseProofError(f"expected JSON object at {path}:{line_number}")
            yield payload


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_npy(path: Path, value: np.ndarray) -> None:
    with path.open("wb") as handle:
        np.save(handle, value, allow_pickle=False)


def _parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y%m%d")


def _hash_lines(values: list[str]) -> str:
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()


def _hash_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

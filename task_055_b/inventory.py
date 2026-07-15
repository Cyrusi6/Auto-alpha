"""Offline, append-only Task 055-B security-date gap inventory."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

from task_055_a.bundle import validate_simulation_bundle
from task_055_a.observation import validate_observation_boundary_seal

from .contracts import (
    CHILD_LEDGER_SCHEMA,
    BAR_FIELD_ALIASES,
    INVENTORY_SCHEMA,
    MATRIX_ALIASES,
    MAX_REPAIR_DATE,
    POINTER_SCHEMA,
    PRICE_FIELDS,
    SECURITY_DATE_STATES,
    UNRESOLVED_STATES,
    VALIDATOR_VERSION,
    GapInventoryConfig,
    ReadinessSplit,
)

_BLOCKER_PATTERN = re.compile(r"(?:valuation_[a-z_]+_blocked:)?(?P<date>\d{8}):(?P<date_index>\d+):(?P<detail>.*)")
_BLOCKER_ASSET_PATTERN = re.compile(r"\b(?P<ts_code>\d{6}\.(?:SH|SZ|BJ))\b")
_DATE_FIELDS = ("trade_date", "date", "suspend_date", "start_date", "end_date", "ann_date", "ex_date")
_CODE_FIELDS = ("ts_code", "symbol", "security", "asset")
_JSON_SUFFIXES = {".json", ".jsonl", ".ndjson"}


class GapInventoryError(RuntimeError):
    """Raised when inventory inputs or immutable artifacts are invalid."""


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_gap_inventory(config: GapInventoryConfig) -> dict[str, Any]:
    """Build and atomically publish a complete offline gap inventory.

    This function never performs network I/O. It validates the original Task
    055-A observation seal, scans immutable matrix/bundle/run artifacts, and
    records any post-seal evidence only as retrospective historical repair.
    """

    seal = validate_observation_boundary_seal(config.observation_seal, rescan=True)
    if str((seal.get("observation") or {}).get("max_observed_endpoint") or "") > config.max_repair_date:
        raise GapInventoryError("observation_seal_exceeds_repair_boundary")
    bundle = validate_simulation_bundle(config.simulation_bundle_manifest, require_ready=True)
    matrix = _load_matrix(config.strict_matrix_root, config.required_bar_fields)
    if matrix["trade_dates"][-1] > config.max_repair_date:
        raise GapInventoryError("strict_matrix_exceeds_repair_boundary")
    benchmark_sessions = _validate_bundle_axes(bundle, config.simulation_bundle_manifest, matrix)

    first_blockers = _load_first_blockers(config.blocked_run_roots, matrix["ts_codes"])
    affected_runs = _affected_runs(first_blockers)
    evidence = _scan_evidence(config.evidence_roots, max_date=config.max_repair_date)
    cells = _inventory_cells(matrix, first_blockers, affected_runs, evidence, config.probes, benchmark_sessions)
    episodes = _build_episodes(cells, matrix["date_index"])
    readiness = _readiness(cells)
    acquired_at = config.acquired_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    source_inputs = _source_inputs(config, seal, bundle, matrix, first_blockers, evidence)
    return _publish(
        config=config,
        acquired_at=acquired_at,
        seal=seal,
        source_inputs=source_inputs,
        cells=cells,
        episodes=episodes,
        first_blockers=first_blockers,
        evidence=evidence,
        readiness=readiness,
    )


def validate_gap_inventory(manifest_path: str | Path) -> dict[str, Any]:
    path = Path(manifest_path)
    manifest = _read_json(path)
    if manifest.get("schema_version") != INVENTORY_SCHEMA:
        raise GapInventoryError("inventory_schema_invalid")
    semantic = {key: value for key, value in manifest.items() if key not in {"generation_id", "content_hash"}}
    if canonical_hash(semantic) != manifest.get("content_hash"):
        raise GapInventoryError("inventory_content_hash_mismatch")
    expected = f"security_date_inventory_{manifest['content_hash'][:24]}"
    if manifest.get("generation_id") != expected or path.parent.name != expected:
        raise GapInventoryError("inventory_generation_identity_mismatch")
    partitions = manifest.get("partitions") or {}
    for name, entry in partitions.items():
        artifact = _safe_relative(path.parent, entry.get("path"))
        if not artifact.is_file() or sha256_file(artifact) != entry.get("sha256"):
            raise GapInventoryError(f"inventory_partition_mismatch:{name}")
        if artifact.stat().st_size != entry.get("size_bytes"):
            raise GapInventoryError(f"inventory_partition_size_mismatch:{name}")
    cells = _read_jsonl(path.parent / partitions["cells"]["path"])
    episodes = _read_jsonl(path.parent / partitions["episodes"]["path"])
    blockers = _read_jsonl(path.parent / partitions["first_blockers"]["path"])
    ledger = _read_json(path.parent / partitions["child_ledger"]["path"])
    if ledger.get("schema_version") != CHILD_LEDGER_SCHEMA:
        raise GapInventoryError("child_ledger_schema_invalid")
    if canonical_hash({k: v for k, v in ledger.items() if k != "ledger_hash"}) != ledger.get("ledger_hash"):
        raise GapInventoryError("child_ledger_hash_mismatch")
    if len(cells) != manifest.get("cell_count") or len(episodes) != manifest.get("episode_count"):
        raise GapInventoryError("inventory_count_mismatch")
    if len(blockers) != manifest.get("first_blocker_count"):
        raise GapInventoryError("first_blocker_count_mismatch")
    if any(row.get("state") not in SECURITY_DATE_STATES for row in cells):
        raise GapInventoryError("inventory_state_invalid")
    return manifest | {"manifest_path": str(path), "cells": cells, "episodes": episodes, "first_blockers": blockers}


def _load_matrix(root: Path, required_fields: Iterable[str]) -> dict[str, Any]:
    if not root.is_dir():
        raise GapInventoryError("strict_matrix_root_missing")
    ts_codes = _read_axis(root, "ts_codes.json")
    dates = _read_axis(root, "trade_dates.json")
    shape = (len(ts_codes), len(dates))
    arrays: dict[str, np.ndarray | None] = {}
    for logical, aliases in MATRIX_ALIASES.items():
        arrays[logical] = _load_optional_array(root, aliases, shape, allow_date_axis=logical == "snapshot_source_date")
    fields: dict[str, np.ndarray] = {}
    validity: dict[str, np.ndarray] = {}
    manifest = _load_matrix_manifest(root)
    _validate_matrix_partitions(root, manifest["payload"])
    for field in required_fields:
        field_names = BAR_FIELD_ALIASES.get(field, (field,))
        values = _load_required_array(root, tuple(f"{name}.npy" for name in field_names), shape)
        fields[field] = values
        validity[field] = _load_required_array(
            root,
            tuple(
                candidate
                for name in field_names
                for candidate in (f"{name}_validity.npy", f"{name}_valid.npy", f"raw_{name}_validity.npy")
            ),
            shape,
        ).astype(bool, copy=False)
    return {
        "root": root,
        "ts_codes": ts_codes,
        "trade_dates": dates,
        "date_index": {date: index for index, date in enumerate(dates)},
        "shape": shape,
        "arrays": arrays,
        "fields": fields,
        "validity": validity,
        "manifest": manifest,
    }


def _inventory_cells(
    matrix: Mapping[str, Any],
    first_blockers: list[dict[str, Any]],
    affected_runs: Mapping[tuple[str, str], list[dict[str, str]]],
    evidence: list[dict[str, Any]],
    probes: Iterable[tuple[str, str]],
    benchmark_sessions: set[str],
) -> list[dict[str, Any]]:
    arrays = matrix["arrays"]
    shape = matrix["shape"]
    active = _bool_or_default(arrays.get("active"), shape, False)
    listed = _bool_or_default(arrays.get("listed"), shape, False)
    membership = _bool_or_default(arrays.get("membership"), shape, False)
    membership_known = _bool_or_default(arrays.get("membership_known"), shape, False)
    bar_observed = _bool_or_default(arrays.get("bar_observed"), shape, False)
    unexplained = _bool_or_default(arrays.get("unexplained_data_gap"), shape, False)
    suspension = _bool_or_default(arrays.get("suspension_event_present"), shape, False)
    signal = _bool_or_default(arrays.get("signal_eligible_at_close"), shape, False)
    target = _bool_or_default(arrays.get("target_available"), shape, False)

    row_present = np.zeros(shape, dtype=bool)
    invalid_required = np.zeros(shape, dtype=bool)
    zero_fill = np.zeros(shape, dtype=bool)
    invalid_fields: dict[tuple[int, int], list[str]] = defaultdict(list)
    for field, values in matrix["fields"].items():
        valid = matrix["validity"][field]
        finite = np.isfinite(values)
        field_ok = valid & finite
        if field in PRICE_FIELDS:
            field_ok &= values > 0
        row_present |= valid | finite
        bad = ~field_ok
        invalid_required |= bad
        zero_fill |= bad & finite & (values == 0)
        for stock_index, date_index in zip(*np.nonzero(bad & (active & listed & membership))):
            invalid_fields[(int(stock_index), int(date_index))].append(field)

    sellable = _bool_or_default(arrays.get("sellable_at_open"), shape, False)
    execution_known = _bool_or_default(arrays.get("open_execution_known"), shape, False)
    execution_value = _bool_or_default(arrays.get("open_execution_value"), shape, False)
    lifecycle_member = active & listed & membership
    closure_domain = _valuation_closure_domain(
        lifecycle_member,
        membership,
        sellable & execution_known & execution_value,
    )
    candidate = (
        unexplained
        | (lifecycle_member & ~bar_observed)
        | (row_present & invalid_required & (lifecycle_member | closure_domain))
        | (suspension & ~bar_observed)
        | (closure_domain & ~bar_observed)
    )
    blocker_keys = {(row["ts_code"], row["trade_date"]) for row in first_blockers if row.get("ts_code")}
    probe_keys = set(probes)
    stock_index = {code: i for i, code in enumerate(matrix["ts_codes"])}
    date_index = matrix["date_index"]
    for code, date in blocker_keys:
        if code in stock_index and date in date_index:
            candidate[stock_index[code], date_index[date]] = True

    evidence_by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in evidence:
        if row.get("ts_code") and row.get("trade_date"):
            evidence_by_key[(row["ts_code"], row["trade_date"])].append(row)

    rows: list[dict[str, Any]] = []
    for stock_i, date_i in zip(*np.nonzero(candidate)):
        code = matrix["ts_codes"][int(stock_i)]
        date = matrix["trade_dates"][int(date_i)]
        key = (code, date)
        state = "SOURCE_NORMALIZATION_ZERO_FILL" if zero_fill[stock_i, date_i] else (
            "RAW_BAR_REQUIRED_FIELD_INVALID" if row_present[stock_i, date_i] and invalid_required[stock_i, date_i] else "DATA_SOURCE_GAP"
        )
        reasons = []
        if unexplained[stock_i, date_i]: reasons.append("matrix_unexplained_data_gap")
        if lifecycle_member[stock_i, date_i] and not bar_observed[stock_i, date_i]: reasons.append("active_listed_member_without_observed_bar")
        if suspension[stock_i, date_i] and not bar_observed[stock_i, date_i]: reasons.append("suspension_event_with_missing_bar")
        if closure_domain[stock_i, date_i] and not bar_observed[stock_i, date_i]: reasons.append("valuation_closure_missing_bar")
        if invalid_fields.get((int(stock_i), int(date_i))): reasons.append("required_bar_field_invalid")
        support = evidence_by_key.get(key, [])
        previous_bar, next_bar = _neighbor_bars(matrix, int(stock_i), int(date_i), bar_observed)
        rows.append({
            "key": f"{code}|{date}",
            "ts_code": code,
            "trade_date": date,
            "trade_calendar_session": True,
            "benchmark_session": date in benchmark_sessions,
            "lifecycle": {"listed": bool(listed[stock_i, date_i]), "active": bool(active[stock_i, date_i])},
            "membership": bool(membership[stock_i, date_i]),
            "membership_known": bool(membership_known[stock_i, date_i]),
            "snapshot_source_date": _date_axis_value(arrays.get("snapshot_source_date"), stock_i, date_i),
            "bar_observed": bool(bar_observed[stock_i, date_i]),
            "raw_bar": {field: _finite_or_none(values[stock_i, date_i]) for field, values in matrix["fields"].items()},
            "raw_field_validity": {field: bool(valid[stock_i, date_i]) for field, valid in matrix["validity"].items()},
            "previous_legal_bar": previous_bar,
            "next_legal_bar": next_bar,
            "invalid_required_fields": sorted(invalid_fields.get((int(stock_i), int(date_i)), [])),
            "adj_factor": _optional_value(arrays.get("adj_factor"), stock_i, date_i),
            "daily_basic_validity": _optional_bool(arrays.get("daily_basic_validity"), stock_i, date_i),
            "limit_validity": _optional_bool(arrays.get("limit_validity"), stock_i, date_i),
            "suspension_event_present": bool(suspension[stock_i, date_i]),
            "st_effective": _optional_bool(arrays.get("st_effective"), stock_i, date_i),
            "st_status_known": _optional_bool(arrays.get("st_status_known"), stock_i, date_i),
            "corporate_action_validity": _optional_bool(arrays.get("corporate_action_validity"), stock_i, date_i),
            "valuation_closure_domain": bool(closure_domain[stock_i, date_i]),
            "signal_eligible": bool(signal[stock_i, date_i]),
            "target_available": bool(target[stock_i, date_i]),
            "first_blocker_censored_observation": key in blocker_keys,
            "regression_probe": key in probe_keys,
            "affected_runs": affected_runs.get(key, []),
            "supporting_evidence": [item["evidence_id"] for item in support],
            "supporting_evidence_details": [item["evidence_summary"] for item in support],
            "conflicting_evidence": [],
            "reasons": reasons,
            "state": state,
            "terminal_status": "unresolved" if state in UNRESOLVED_STATES else "classified",
        })
    return sorted(rows, key=lambda row: (row["ts_code"], row["trade_date"]))


def _valuation_closure_domain(
    lifecycle_member: np.ndarray,
    membership: np.ndarray,
    executable_sell: np.ndarray,
) -> np.ndarray:
    """Keep removed holdings valued only through their first legal exit."""

    domain = lifecycle_member.copy()
    for stock_index in range(membership.shape[0]):
        removals = np.flatnonzero(membership[stock_index, :-1] & ~membership[stock_index, 1:]) + 1
        for removal_index in removals:
            later_exit = np.flatnonzero(executable_sell[stock_index, removal_index:])
            if later_exit.size:
                exit_index = removal_index + int(later_exit[0])
                domain[stock_index, removal_index : exit_index + 1] = True
            else:
                domain[stock_index, removal_index:] = True
    return domain


def _build_episodes(cells: list[dict[str, Any]], date_index: Mapping[str, int]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in cells:
        grouped[row["ts_code"]].append(row)
    episodes: list[dict[str, Any]] = []
    for code, rows in sorted(grouped.items()):
        ordered = sorted(rows, key=lambda row: date_index[row["trade_date"]])
        current: list[dict[str, Any]] = []
        for row in ordered:
            if current and date_index[row["trade_date"]] != date_index[current[-1]["trade_date"]] + 1:
                episodes.append(_episode(code, current))
                current = []
            current.append(row)
        if current:
            episodes.append(_episode(code, current))
    return episodes


def _episode(code: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    states = sorted({row["state"] for row in rows})
    return {
        "episode_id": canonical_hash({"ts_code": code, "dates": [row["trade_date"] for row in rows]})[:24],
        "ts_code": code,
        "start_date": rows[0]["trade_date"],
        "end_date": rows[-1]["trade_date"],
        "trade_date_count": len(rows),
        "states": states,
        "terminal_status": "unresolved" if any(row["terminal_status"] == "unresolved" for row in rows) else "classified",
        "first_blocker_count": sum(bool(row["first_blocker_censored_observation"]) for row in rows),
        "regression_probe_count": sum(bool(row["regression_probe"]) for row in rows),
        "affected_run_count": len({(run["factor_id"], run["scenario"]) for row in rows for run in row["affected_runs"]}),
        "cell_keys": [row["key"] for row in rows],
    }


def _readiness(cells: list[dict[str, Any]]) -> ReadinessSplit:
    unresolved = [row for row in cells if row["terminal_status"] == "unresolved"]
    factor_impacts = [row for row in unresolved if row["signal_eligible"] or row["target_available"]]
    closure = [row for row in unresolved if row["valuation_closure_domain"]]
    blockers = []
    if factor_impacts: blockers.append(f"factor_replay_gap_cells:{len(factor_impacts)}")
    if closure: blockers.append(f"continuous_valuation_gap_cells:{len(closure)}")
    if unresolved: blockers.append(f"future_research_gap_cells:{len(unresolved)}")
    return ReadinessSplit(not factor_impacts, not closure, not unresolved, tuple(blockers))


def _load_first_blockers(roots: Iterable[Path], ts_codes: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in roots:
        candidates = [root] if root.is_file() else sorted(root.rglob("manifest.json")) if root.exists() else []
        for manifest_path in candidates:
            try:
                manifest = _read_json(manifest_path)
            except (OSError, json.JSONDecodeError):
                continue
            if manifest.get("schema_version") != "task055a_blocked_simulation_run_v1":
                continue
            blocker_path = manifest_path.parent / "blocker.json"
            spec_path = manifest_path.parent / "spec.json"
            if not blocker_path.is_file() or not spec_path.is_file():
                raise GapInventoryError("blocked_run_partition_missing")
            if (manifest.get("partitions") or {}).get("blocker.json", {}).get("sha256") != sha256_file(blocker_path):
                raise GapInventoryError("blocked_run_blocker_sha_mismatch")
            blocker = _read_json(blocker_path)
            spec = _read_json(spec_path)
            match = _BLOCKER_PATTERN.search(str(blocker.get("detail") or ""))
            date = match.group("date") if match else None
            date_axis_index = int(match.group("date_index")) if match else None
            asset_match = _BLOCKER_ASSET_PATTERN.search(str(blocker.get("detail") or ""))
            code = asset_match.group("ts_code") if asset_match else None
            if code is not None and code not in ts_codes:
                raise GapInventoryError("blocked_run_asset_not_on_matrix_axis")
            identity = manifest.get("content_hash") or sha256_file(manifest_path)
            if identity in seen:
                continue
            seen.add(identity)
            rows.append({
                "run_artifact_hash": identity,
                "factor_id": str(spec.get("factor_id") or spec.get("candidate_id") or ""),
                "scenario": str(spec.get("scenario") or spec.get("scenario_id") or ""),
                "blocker_code": blocker.get("code"),
                "blocker_detail": blocker.get("detail"),
                "trade_date": date,
                "date_axis_index": date_axis_index,
                "ts_code": code,
                "censoring_semantics": "first_failure_only_not_complete_gap_inventory",
            })
    return sorted(rows, key=lambda row: (row.get("factor_id") or "", row.get("scenario") or "", row.get("run_artifact_hash") or ""))


def _affected_runs(first_blockers: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, str]]]:
    result: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in first_blockers:
        if row.get("ts_code") and row.get("trade_date"):
            result[(row["ts_code"], row["trade_date"])].append({
                "factor_id": row["factor_id"], "scenario": row["scenario"], "run_artifact_hash": row["run_artifact_hash"]
            })
    return result


def _scan_evidence(roots: Iterable[Path], *, max_date: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for root in roots:
        candidates = [root] if root.is_file() else sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in _JSON_SUFFIXES) if root.exists() else []
        for path in candidates:
            file_hash = sha256_file(path)
            for line_no, payload in _iter_json_records(path):
                code = _first(payload, _CODE_FIELDS)
                date = _first(payload, _DATE_FIELDS)
                if date and (not _valid_date(date) or date > max_date):
                    continue
                result.append({
                    "evidence_id": canonical_hash({"sha256": file_hash, "line": line_no, "payload": payload}),
                    "source_file_sha256": file_hash,
                    "source_kind": _source_kind(path, payload),
                    "ts_code": code,
                    "trade_date": date,
                    "line_number": line_no,
                    "request_hash": payload.get("request_hash") or payload.get("request_fingerprint"),
                    "response_hash": payload.get("response_hash") or payload.get("content_sha256"),
                    "coverage_status": payload.get("status") or payload.get("coverage_status"),
                    "query_geometry": payload.get("query_geometry") or _query_geometry(payload),
                    "acquired_at": payload.get("acquired_at"),
                    "first_seen": payload.get("first_seen"),
                    "source_revision": payload.get("source_revision") or payload.get("provider_api_version"),
                    "retrospective_historical_repair": True,
                    "evidence_summary": _evidence_summary(payload),
                    "payload_hash": canonical_hash(payload),
                })
    return sorted(result, key=lambda row: (row.get("ts_code") or "", row.get("trade_date") or "", row["evidence_id"]))


def _publish(**kwargs: Any) -> dict[str, Any]:
    config: GapInventoryConfig = kwargs["config"]
    root = config.output_root
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".task055b_inventory.", dir=root))
    try:
        _write_jsonl(staging / "security_date_cells.jsonl", kwargs["cells"])
        _write_jsonl(staging / "episodes.jsonl", kwargs["episodes"])
        _write_jsonl(staging / "first_blockers.jsonl", kwargs["first_blockers"])
        _write_jsonl(staging / "evidence_index.jsonl", kwargs["evidence"])
        _write_json(staging / "readiness.json", kwargs["readiness"].to_dict())
        ledger = {
            "schema_version": CHILD_LEDGER_SCHEMA,
            "parent_observation_seal_sha256": sha256_file(config.observation_seal),
            "parent_observation_content_hash": kwargs["seal"].get("content_hash"),
            "acquired_at": kwargs["acquired_at"],
            "first_seen": kwargs["acquired_at"],
            "source_revision": config.source_revision,
            "classification": "seal_post_acquisition_retrospective_historical_repair",
            "max_allowed_market_date": config.max_repair_date,
            "prospective_holdout_boundary_unchanged": True,
            "network_requests_performed": 0,
            "source_inputs": kwargs["source_inputs"],
        }
        ledger["ledger_hash"] = canonical_hash(ledger)
        _write_json(staging / "historical_repair_child_ledger.json", ledger)
        partitions = {
            "cells": _entry(staging, "security_date_cells.jsonl"),
            "episodes": _entry(staging, "episodes.jsonl"),
            "first_blockers": _entry(staging, "first_blockers.jsonl"),
            "evidence": _entry(staging, "evidence_index.jsonl"),
            "readiness": _entry(staging, "readiness.json"),
            "child_ledger": _entry(staging, "historical_repair_child_ledger.json"),
        }
        state_counts: dict[str, int] = defaultdict(int)
        for row in kwargs["cells"]: state_counts[row["state"]] += 1
        semantic = {
            "schema_version": INVENTORY_SCHEMA,
            "validator_version": VALIDATOR_VERSION,
            "status": "blocked" if any(row["terminal_status"] == "unresolved" for row in kwargs["cells"]) else "classified",
            "parent_observation_seal_sha256": sha256_file(config.observation_seal),
            "matrix_source_sha256": kwargs["source_inputs"]["strict_matrix_manifest_sha256"],
            "simulation_bundle_sha256": sha256_file(config.simulation_bundle_manifest),
            "max_repair_date": config.max_repair_date,
            "cell_count": len(kwargs["cells"]),
            "episode_count": len(kwargs["episodes"]),
            "first_blocker_count": len(kwargs["first_blockers"]),
            "first_blocker_semantics": "censored_first_failure_samples_not_inventory_total",
            "evidence_record_count": len(kwargs["evidence"]),
            "state_counts": dict(sorted(state_counts.items())),
            "probe_results": [
                next(({"ts_code": code, "trade_date": date, "present": True, "state": row["state"], "terminal_status": row["terminal_status"]} for row in kwargs["cells"] if row["ts_code"] == code and row["trade_date"] == date), {"ts_code": code, "trade_date": date, "present": False, "state": None, "terminal_status": "not_on_axis_or_not_gap"})
                for code, date in config.probes
            ],
            "readiness": kwargs["readiness"].to_dict(),
            "partitions": partitions,
        }
        content_hash = canonical_hash(semantic)
        generation_id = f"security_date_inventory_{content_hash[:24]}"
        manifest = semantic | {"content_hash": content_hash, "generation_id": generation_id}
        _write_json(staging / "inventory_manifest.json", manifest)
        target = root / "generations" / generation_id
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            existing = validate_gap_inventory(target / "inventory_manifest.json")
            if existing["content_hash"] != content_hash:
                raise GapInventoryError("immutable_inventory_generation_conflict")
            shutil.rmtree(staging)
        else:
            os.replace(staging, target)
        _atomic_json(root / "current.json", {"schema_version": POINTER_SCHEMA, "generation_id": generation_id, "content_hash": content_hash, "manifest": f"generations/{generation_id}/inventory_manifest.json"})
        return validate_gap_inventory(target / "inventory_manifest.json")
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _source_inputs(config: GapInventoryConfig, seal: Mapping[str, Any], bundle: Mapping[str, Any], matrix: Mapping[str, Any], blockers: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    manifest_path = matrix["manifest"]["path"]
    return {
        "observation_seal_content_hash": seal.get("content_hash"),
        "strict_matrix_manifest_sha256": sha256_file(manifest_path),
        "strict_matrix_axis_hash": canonical_hash({"ts_codes": matrix["ts_codes"], "trade_dates": matrix["trade_dates"]}),
        "simulation_bundle_content_hash": bundle.get("content_hash"),
        "blocked_run_root_hash": canonical_hash([row["run_artifact_hash"] for row in blockers]),
        "evidence_root_hash": canonical_hash([row["evidence_id"] for row in evidence]),
    }


def _validate_bundle_axes(bundle: Mapping[str, Any], manifest_path: Path, matrix: Mapping[str, Any]) -> set[str]:
    root = manifest_path.parent
    artifacts = bundle.get("artifacts") or {}
    stocks = _read_json(root / artifacts["ts_codes"]["path"])
    execution_dates = _read_json(root / artifacts["execution_trade_dates"]["path"])
    if stocks != matrix["ts_codes"]:
        raise GapInventoryError("bundle_matrix_stock_axis_mismatch")
    if matrix["trade_dates"][: len(execution_dates)] != execution_dates:
        raise GapInventoryError("bundle_matrix_execution_axis_mismatch")
    benchmark_entry = artifacts.get("benchmark_index_bars") or {}
    benchmark_path = root / str(benchmark_entry.get("path") or "")
    if not benchmark_path.is_file():
        return set()
    return {
        str(row.get("trade_date") or row.get("date") or "")
        for row in _read_jsonl(benchmark_path)
        if row.get("trade_date") or row.get("date")
    }


def _validate_matrix_partitions(root: Path, manifest: Mapping[str, Any]) -> None:
    partitions = manifest.get("partition_sha256") or {}
    if not isinstance(partitions, Mapping):
        raise GapInventoryError("strict_matrix_partition_registry_invalid")
    for name, expected in partitions.items():
        path = root / str(name)
        if not path.is_file() or sha256_file(path) != expected:
            raise GapInventoryError(f"strict_matrix_partition_mismatch:{name}")


def _neighbor_bars(matrix: Mapping[str, Any], stock_index: int, date_index: int, observed: np.ndarray) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    def find(step: int) -> dict[str, Any] | None:
        index = date_index + step
        while 0 <= index < observed.shape[1]:
            if observed[stock_index, index] and all(matrix["validity"][field][stock_index, index] for field in PRICE_FIELDS):
                return {
                    "trade_date": matrix["trade_dates"][index],
                    "values": {field: _finite_or_none(values[stock_index, index]) for field, values in matrix["fields"].items()},
                }
            index += step
        return None
    return find(-1), find(1)


def _load_matrix_manifest(root: Path) -> dict[str, Any]:
    for name in ("task_052a_strict_matrix_manifest.json", "task_053a_strict_matrix_manifest.json", "matrix_manifest.json", "metadata.json"):
        path = root / name
        if path.is_file():
            return {"path": path, "payload": _read_json(path)}
    raise GapInventoryError("strict_matrix_manifest_missing")


def _load_required_array(root: Path, names: Iterable[str], shape: tuple[int, int]) -> np.ndarray:
    array = _load_optional_array(root, names, shape)
    if array is None:
        raise GapInventoryError(f"strict_matrix_required_array_missing:{next(iter(names))}")
    return array


def _load_optional_array(root: Path, names: Iterable[str], shape: tuple[int, int], *, allow_date_axis: bool = False) -> np.ndarray | None:
    for name in names:
        path = root / name
        if path.is_file():
            array = np.load(path, mmap_mode="r", allow_pickle=False)
            if tuple(array.shape) != shape and not (allow_date_axis and tuple(array.shape) == (shape[1],)):
                raise GapInventoryError(f"strict_matrix_array_shape_mismatch:{name}")
            return array
    return None


def _read_axis(root: Path, name: str) -> list[str]:
    values = [str(value) for value in _read_json(root / name)]
    if not values or values != sorted(values) or len(values) != len(set(values)):
        raise GapInventoryError(f"strict_matrix_axis_invalid:{name}")
    return values


def _bool_or_default(value: np.ndarray | None, shape: tuple[int, int], default: bool) -> np.ndarray:
    return np.full(shape, default, dtype=bool) if value is None else np.asarray(value, dtype=bool)


def _optional_bool(value: np.ndarray | None, stock: int, date: int) -> bool | None:
    return None if value is None else bool(value[stock, date])


def _optional_value(value: np.ndarray | None, stock: int, date: int) -> Any:
    if value is None: return None
    return _finite_or_none(value[stock, date])


def _date_axis_value(value: np.ndarray | None, stock: int, date: int) -> Any:
    if value is None: return None
    item = value[date] if value.ndim == 1 else value[stock, date]
    return str(item) if str(item) not in {"", "0", "None"} else None


def _finite_or_none(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if np.isfinite(numeric) else None


def _iter_json_records(path: Path):
    if path.suffix.lower() in {".jsonl", ".ndjson"}:
        with path.open(encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, 1):
                if line.strip():
                    payload = json.loads(line)
                    if isinstance(payload, Mapping): yield line_no, dict(payload)
        return
    payload = _read_json(path)
    rows = payload if isinstance(payload, list) else payload.get("items") or payload.get("data") or payload.get("records") or [payload]
    for line_no, row in enumerate(rows, 1):
        if isinstance(row, Mapping): yield line_no, dict(row)


def _source_kind(path: Path, payload: Mapping[str, Any]) -> str:
    text = f"{path.name} {payload.get('dataset','')} {payload.get('api_name','')}".lower()
    for kind in ("suspension", "daily_basic", "limit", "adj_factor", "stock_st", "namechange", "corporate_action", "daily"):
        if kind in text: return kind
    return "unknown_evidence"


def _query_geometry(payload: Mapping[str, Any]) -> str | None:
    if payload.get("trade_date"): return "exact_trade_date_cross_section"
    if payload.get("ts_code") and (payload.get("start_date") or payload.get("end_date")): return "security_episode_window"
    return None


def _evidence_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "dataset", "api_name", "endpoint", "ts_code", "trade_date", "start_date", "end_date",
        "suspend_type", "suspend_timing", "timing_parse_status", "canonical_interval", "status",
        "coverage_status", "row_count", "item_count", "response_fields", "request_hash",
        "request_fingerprint", "response_hash", "content_sha256", "contract_hash", "query_geometry",
        "acquired_at", "first_seen", "source_revision", "provider_api_version",
    }
    return {key: payload[key] for key in sorted(allowed & payload.keys())}


def _first(payload: Mapping[str, Any], fields: Iterable[str]) -> str | None:
    for field in fields:
        value = str(payload.get(field) or "").strip()
        if value: return value
    return None


def _valid_date(value: str) -> bool:
    try: datetime.strptime(value, "%Y%m%d")
    except ValueError: return False
    return True


def _entry(root: Path, name: str) -> dict[str, Any]:
    path = root / name
    return {"path": name, "sha256": sha256_file(path), "size_bytes": path.stat().st_size}


def _safe_relative(root: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise GapInventoryError("inventory_partition_path_invalid")
    path = (root / relative).resolve()
    if root.resolve() not in path.parents:
        raise GapInventoryError("inventory_partition_escape")
    return path


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows: handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    _write_json(temporary, payload)
    os.replace(temporary, path)

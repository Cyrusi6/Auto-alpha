"""Independent semantic verification for Task 055-G truth and causal evidence."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from data_pipeline.ashare.request_normalization import stable_json_hash
from task_055_a.bundle import (
    DERIVED_EXECUTION_MASKS,
    EXECUTION_CUTOFF,
    EXECUTION_MASKS,
    EXECUTION_METADATA,
    RAW_FIELDS,
    SIGNAL_CUTOFF,
    SIGNAL_MASKS,
    SIMULATION_BUNDLE_SCHEMA,
)
from task_055_a.policy import PREREGISTERED_SCENARIOS
from task_055_a.run import SCENARIO_NAMES, prepare_simulation_inputs
from task_055_a.simulator import EventLedgerSimulator, SimulationDataBlocker
from task_055_f.contracts import (
    EXPLICIT_FULL_DAY_TIMINGS,
    MATRIX_DAILY_FIELDS,
    MAX_STALE_AGE_TRADE_DAYS,
    MODELED_STALE_METHOD,
    OFFICIAL_CLOSE_METHOD,
    OFFICIAL_OPEN_METHOD,
    SUSPEND_FIELDS,
    TRUTH_STATES,
)

from .access import AccessBroker, canonical_hash, sha256_file
from .bundle import load_audited_simulation_bundle
from .contracts import CAUSAL_SCHEMA, MAX_DATE, SEMANTIC_VERIFICATION_SCHEMA, SIMULATION_END, SIMULATION_START, TRUTH_SCHEMA
from .fees import FeeScheduleCalculator, validate_fee_schedule_v2
from .lineage import resolve_and_validate_parent_lineage
from .network_state import PLAN_SCHEMA


class SemanticVerificationError(RuntimeError):
    pass


def verify_task055g_semantics(
    *,
    governed_root: str | Path,
    access_plan: str | Path,
    producer_truth_manifest: str | Path,
    output_root: str | Path,
    causal_manifest: str | Path | None = None,
    fee_schedule_manifest: str | Path | None = None,
    allow_synthetic_fee_schedule: bool = False,
) -> dict[str, Any]:
    broker = AccessBroker(governed_root, access_plan)
    parents = resolve_and_validate_parent_lineage(
        governed_root=governed_root,
        access_plan=access_plan,
        broker=broker,
    )
    independent = rebuild_truth_rows(parents=parents, broker=broker)
    producer = _load_producer_truth(producer_truth_manifest)
    truth_summary = compare_truth_rows(independent, producer)
    causal_summary = None
    if causal_manifest is not None:
        if fee_schedule_manifest is None:
            raise SemanticVerificationError("causal_fee_schedule_required")
        causal_summary = verify_causal_reexecution(
            causal_manifest=causal_manifest,
            fee_schedule_manifest=fee_schedule_manifest,
            producer_truth=producer,
            independent_truth=independent,
            parents=parents,
            broker=broker,
            allow_synthetic_fee_schedule=allow_synthetic_fee_schedule,
        )
    ledger = broker.publish_ledger(Path(output_root) / "independent_read_ledger")
    semantic = {
        "schema_version": SEMANTIC_VERIFICATION_SCHEMA,
        "status": "passed",
        "parent_lineage_content_hash": parents["content_hash"],
        "access_plan_content_hash": broker.plan["content_hash"],
        "producer_truth_content_hash": producer["content_hash"],
        "truth": truth_summary,
        "causal": causal_summary,
        "read_ledger_content_hash": ledger["content_hash"],
        "read_ledger_manifest": _relative_to_output(ledger["manifest_path"], output_root),
        "max_read_date": ledger.get("max_read_date"),
        "prospective_holdout_accessed": ledger.get("prospective_holdout_accessed"),
    }
    if semantic["prospective_holdout_accessed"] is not False:
        raise SemanticVerificationError("independent_verifier_future_access_detected")
    return _publish(Path(output_root), semantic)


def validate_semantic_verification(path: str | Path) -> dict[str, Any]:
    manifest_path = _resolve_manifest(path, "semantic_verification.json")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != SEMANTIC_VERIFICATION_SCHEMA or payload.get("status") != "passed":
        raise SemanticVerificationError("semantic_verification_schema_or_status_invalid")
    semantic = {key: value for key, value in payload.items() if key not in {"content_hash", "generation_id"}}
    if canonical_hash(semantic) != payload.get("content_hash"):
        raise SemanticVerificationError("semantic_verification_content_hash_mismatch")
    if payload.get("prospective_holdout_accessed") is not False:
        raise SemanticVerificationError("semantic_verification_future_access_invalid")
    return payload | {"manifest_path": str(manifest_path)}


def rebuild_truth_rows(*, parents: Mapping[str, Any], broker: AccessBroker) -> dict[str, Any]:
    inventory, cells = _load_inventory(parents, broker)
    matrix = _load_matrix_truth(parents, broker, cells)
    daily = _load_daily_evidence(parents, broker)
    suspension = _load_suspension_evidence(parents, broker, cells)
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for cell in cells:
        key = (str(cell.get("ts_code") or ""), str(cell.get("trade_date") or ""))
        if not key[0] or not re.fullmatch(r"\d{8}", key[1]) or key[1] > MAX_DATE or key in seen:
            raise SemanticVerificationError("independent_inventory_key_invalid")
        seen.add(key)
        rows.append(
            _classify_cell(
                cell=cell,
                matrix_bar=matrix["rows"].get(key),
                events=suspension["events"].get(key, []),
                coverage=suspension["coverage"].get(key, []),
                daily_evidence=daily.get(key, []),
            )
        )
    rows.sort(key=lambda row: (row["ts_code"], row["trade_date"]))
    if len(rows) != int(inventory.get("cell_count") or -1):
        raise SemanticVerificationError("independent_truth_key_conservation_failed")
    return {
        "records": rows,
        "record_count": len(rows),
        "key_root": canonical_hash(sorted(seen)),
        "rows_root": canonical_hash(rows),
        "state_counts": dict(sorted(Counter(row["state"] for row in rows).items())),
        "suspend_type_counts": dict(sorted(Counter(row["suspend_type"] for row in rows).items())),
        "matrix": matrix,
    }


def compare_truth_rows(independent: Mapping[str, Any], producer: Mapping[str, Any]) -> dict[str, Any]:
    rebuilt = list(independent.get("records") or ())
    stored = list(producer.get("records") or ())
    if len(rebuilt) != len(stored):
        raise SemanticVerificationError("producer_truth_record_count_mismatch")
    for expected, actual in zip(rebuilt, stored, strict=True):
        if canonical_hash(expected) != canonical_hash(actual):
            key = f"{expected.get('ts_code')}:{expected.get('trade_date')}"
            raise SemanticVerificationError(f"producer_truth_exact_row_mismatch:{key}")
    rows_root = canonical_hash(rebuilt)
    if rows_root != canonical_hash(stored):
        raise SemanticVerificationError("producer_truth_rows_root_mismatch")
    checks = {
        "record_count": independent.get("record_count"),
        "key_root": independent.get("key_root"),
        "state_counts": independent.get("state_counts"),
        "suspend_type_counts": independent.get("suspend_type_counts"),
    }
    for key, value in checks.items():
        if producer.get(key) != value:
            raise SemanticVerificationError(f"producer_truth_{key}_mismatch")
    return checks | {"exact_rows_root": rows_root, "exact_rows_match": True}


def verify_causal_reexecution(
    *,
    causal_manifest: str | Path,
    fee_schedule_manifest: str | Path,
    producer_truth: Mapping[str, Any],
    independent_truth: Mapping[str, Any],
    parents: Mapping[str, Any],
    broker: AccessBroker,
    allow_synthetic_fee_schedule: bool = False,
) -> dict[str, Any]:
    producer = _load_causal_artifact(causal_manifest)
    fee = validate_fee_schedule_v2(
        fee_schedule_manifest,
        allow_synthetic_test_fixture=allow_synthetic_fee_schedule,
    )
    bundle = _load_bundle(parents, broker)
    prepared = prepare_simulation_inputs(bundle)
    dates = list(prepared["market"]["dates"])
    assets = list(prepared["market"]["assets"])
    if dates[0] != SIMULATION_START or dates[-1] != SIMULATION_END:
        raise SemanticVerificationError("independent_simulation_axis_mismatch")
    if fee.get("simulation_start") != dates[0] or fee.get("simulation_end") != dates[-1]:
        raise SemanticVerificationError("independent_fee_axis_mismatch")
    matrix = _load_matrix_marks(parents, broker, assets, dates)
    truth = {"content_hash": producer_truth["content_hash"], "records": independent_truth["records"]}
    surface = _build_valuation_surface(
        truth=truth,
        assets=assets,
        dates=dates,
        matrix=matrix,
        corporate_actions=prepared["corporate_actions"],
    )
    calculator = FeeScheduleCalculator(
        fee_schedule_manifest,
        allow_synthetic_test_fixture=allow_synthetic_fee_schedule,
    )
    rebuilt = trace_causal_runs(bundle=bundle, prepared=prepared, surface=surface, calculator=calculator)
    _compare_causal(producer, rebuilt)
    lineage = producer.get("lineage") or {}
    expected_lineage = {
        "truth_v2_content_hash": producer_truth["content_hash"],
        "matrix_content_hash": matrix["manifest"].get("content_hash"),
        "simulation_bundle_content_hash": bundle["manifest"].get("content_hash"),
        "fee_schedule_content_hash": fee.get("content_hash"),
    }
    for key, value in expected_lineage.items():
        if lineage.get(key) != value:
            raise SemanticVerificationError(f"causal_lineage_mismatch:{key}")
    return {
        "content_hash": producer["content_hash"],
        "run_count": len(rebuilt["run_rows"]),
        "terminal_counts": rebuilt["terminal_counts"],
        "run_rows_root": rebuilt["run_rows_root"],
        "held_mark_root": rebuilt["held_mark_root"],
        "frontier_root": rebuilt["missing_key_root"],
        "frontier_count": rebuilt["round_one_frontier_count"],
        "authorized_modeled_held_mark_count": rebuilt["authorized_modeled_held_mark_count"],
        "reexecuted_event_ledger_simulations": len(rebuilt["run_rows"]),
    }


def trace_causal_runs(
    *,
    bundle: Mapping[str, Any],
    prepared: Mapping[str, Any],
    surface: Mapping[str, Any],
    calculator: Any,
) -> dict[str, Any]:
    dates = list(prepared["market"]["dates"])
    assets = list(prepared["market"]["assets"])
    signal_count = int(prepared["signal_count"])
    exact_ids = list(bundle["manifest"].get("exact20_ids") or ())
    if len(exact_ids) != 20 or len(set(exact_ids)) != 20:
        raise SemanticVerificationError("independent_exact20_identity_invalid")
    market = dict(prepared["market"])
    market["valuation_open"] = surface["values"]["open"]
    market["valuation_close"] = surface["values"]["close"]
    for point in ("open", "close"):
        for name in ("method", "source_date", "stale_age", "evidence_id"):
            market[f"valuation_{point}_{name}"] = surface["metadata"][point][name]
    run_rows: list[dict[str, Any]] = []
    held_rows: list[dict[str, Any]] = []
    terminal_counts: Counter[str] = Counter()
    missing_keys: set[tuple[str, str]] = set()
    modeled_held_count = 0
    for factor_id in exact_ids:
        values = np.asarray(prepared["factor_values"][factor_id])
        validity = np.asarray(prepared["factor_validity"][factor_id], dtype=bool)
        scores = np.full((len(dates), len(assets)), np.nan, dtype=float)
        selection = np.zeros((len(dates), len(assets)), dtype=bool)
        scores[:signal_count] = values.T
        selection[:signal_count] = validity.T & prepared["signal_common"]
        for scenario in SCENARIO_NAMES:
            run_marks: list[dict[str, Any]] = []

            def observer(_index: int, _date: str, _point: str, rows: Sequence[Mapping[str, Any]]) -> None:
                for raw in rows:
                    row = {"factor_id": factor_id, "scenario": scenario, **dict(raw)}
                    row["row_hash"] = canonical_hash(row)
                    run_marks.append(row)

            terminal = "causal_trace_completed"
            blocker = None
            try:
                EventLedgerSimulator(
                    PREREGISTERED_SCENARIOS[scenario],
                    fee_calculator=calculator,
                    require_external_fee_schedule=True,
                    require_explicit_valuation_marks=True,
                ).run(
                    market,
                    scores,
                    masks={"select": selection, "buy": prepared["buy"], "sell": prepared["sell"]},
                    corporate_actions=prepared["corporate_actions"],
                    diagnostic_mark_observer_v2=observer,
                )
            except SimulationDataBlocker as exc:
                terminal = "causal_valuation_blocked"
                blocker = _parse_blocker(str(exc), surface["blockers"])
                if blocker.get("ts_code") and blocker.get("trade_date"):
                    missing_keys.add((str(blocker["ts_code"]), str(blocker["trade_date"])))
            except (ValueError, RuntimeError) as exc:
                terminal = "causal_infrastructure_blocked"
                blocker = {"detail": str(exc)}
            held_rows.extend(run_marks)
            modeled_held_count += sum(row["method"] == MODELED_STALE_METHOD for row in run_marks)
            terminal_counts[terminal] += 1
            run = {
                "factor_id": factor_id,
                "scenario": scenario,
                "terminal_state": terminal,
                "held_mark_count_before_terminal": len(run_marks),
                "held_mark_root": canonical_hash(run_marks),
                "blocker": blocker,
            }
            run["row_hash"] = canonical_hash(run)
            run_rows.append(run)
    expected_pairs = sorted((factor_id, scenario) for factor_id in exact_ids for scenario in SCENARIO_NAMES)
    actual_pairs = [(row["factor_id"], row["scenario"]) for row in run_rows]
    if sorted(actual_pairs) != expected_pairs or len(actual_pairs) != len(set(actual_pairs)):
        raise SemanticVerificationError("independent_causal_cartesian_invalid")
    held_rows.sort(
        key=lambda row: (
            row["factor_id"],
            row["scenario"],
            row["trade_date"],
            row["reporting_point"],
            row["ts_code"],
        )
    )
    frontier = sorted(missing_keys)
    return {
        "exact20_ids": exact_ids,
        "run_rows": run_rows,
        "held_rows": held_rows,
        "terminal_counts": dict(sorted(terminal_counts.items())),
        "round_one_frontier": frontier,
        "round_one_frontier_count": len(frontier),
        "held_mark_count": len(held_rows),
        "authorized_modeled_held_mark_count": modeled_held_count,
        "run_rows_root": canonical_hash(run_rows),
        "held_mark_root": canonical_hash(held_rows),
        "missing_key_root": canonical_hash(frontier),
    }


def _load_inventory(parents: Mapping[str, Any], broker: AccessBroker) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = broker.read_json(parents["inventory_manifest"], principal="task055g_independent_verifier")
    if manifest.get("schema_version") != "task055b_security_date_gap_inventory_v1":
        raise SemanticVerificationError("independent_inventory_schema_invalid")
    cells = broker.read_jsonl(parents["inventory_cells"], principal="task055g_independent_verifier")
    entry = (manifest.get("partitions") or {}).get("cells") or {}
    if broker.rows[-1].get("actual_sha256") != entry.get("sha256"):
        raise SemanticVerificationError("independent_inventory_partition_mismatch")
    if len(cells) != manifest.get("cell_count"):
        raise SemanticVerificationError("independent_inventory_count_mismatch")
    return manifest, cells


def _load_matrix_truth(
    parents: Mapping[str, Any], broker: AccessBroker, cells: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    root = Path(str(parents["matrix_root"]))
    manifest = broker.read_json(parents["matrix_manifest"], principal="task055g_independent_verifier")
    codes = broker.read_json(str(root / "ts_codes.json"), principal="task055g_independent_verifier")
    dates = broker.read_json(str(root / "trade_dates.json"), principal="task055g_independent_verifier")
    code_index = {str(code): index for index, code in enumerate(codes)}
    date_index = {str(date): index for index, date in enumerate(dates)}
    arrays: dict[str, Any] = {}
    for field in MATRIX_DAILY_FIELDS:
        for name in (f"{field}.npy", f"{field}_validity.npy"):
            arrays[name] = broker.load_npy(
                str(root / name),
                component="task055g_independent_verifier",
                dataset=f"strict_matrix_partition:{name}",
            )
            if broker.rows[-1].get("actual_sha256") != (manifest.get("partition_sha256") or {}).get(name):
                raise SemanticVerificationError(f"independent_matrix_partition_mismatch:{name}")
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for cell in cells:
        code, date = str(cell.get("ts_code") or ""), str(cell.get("trade_date") or "")
        if code not in code_index or date not in date_index:
            rows[(code, date)] = {"axis_present": False, "complete": False}
            continue
        asset, day = code_index[code], date_index[date]
        values = {field: float(arrays[f"{field}.npy"][asset, day]) for field in MATRIX_DAILY_FIELDS}
        validity = {
            field: bool(arrays[f"{field}_validity.npy"][asset, day]) for field in MATRIX_DAILY_FIELDS
        }
        rows[(code, date)] = {
            "axis_present": True,
            "complete": all(validity.values()) and _valid_bar(values),
            "values": values,
            "validity": validity,
            "row_hash": canonical_hash({"code": code, "date": date, "values": values, "validity": validity}),
        }
    return {"manifest": manifest, "codes": codes, "dates": dates, "rows": rows}


def _load_daily_evidence(
    parents: Mapping[str, Any], broker: AccessBroker
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    manifest = broker.read_json(
        parents["task055e_provenance_manifest"], principal="task055g_independent_verifier"
    )
    entry = (manifest.get("partitions") or {}).get("row_provenance") or {}
    root = Path(str(parents["task055e_provenance_manifest"])).parent
    provenance = broker.read_jsonl(
        str(root / str(entry.get("path") or "")), principal="task055g_independent_verifier"
    )
    if broker.rows[-1].get("actual_sha256") != entry.get("sha256"):
        raise SemanticVerificationError("independent_provenance_partition_mismatch")
    result: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    opened: dict[str, Mapping[str, Any]] = {}
    opened_sha: dict[str, str] = {}
    for row in provenance:
        if row.get("api") != "daily" or row.get("record_kind") == "normalized_row":
            continue
        relative = str(row.get("source_relative_path") or "")
        if not relative:
            raise SemanticVerificationError("independent_daily_source_path_missing")
        if relative not in opened:
            opened[relative] = broker.read_json(relative, principal="task055g_independent_verifier")
            opened_sha[relative] = str(broker.rows[-1].get("actual_sha256") or "")
        if opened_sha[relative] != row.get("source_file_sha256"):
            raise SemanticVerificationError("independent_daily_source_sha_mismatch")
        proof = _daily_proof(row, opened[relative])
        result[(str(row["ts_code"]), str(row["trade_date"]))].append(proof)
    for key, rows in result.items():
        unique = {str(row["proof_hash"]): row for row in rows}
        result[key] = [unique[value] for value in sorted(unique)]
        if len({row["outcome"] for row in result[key]}) != 1:
            raise SemanticVerificationError("independent_daily_evidence_conflict")
    return result


def _load_suspension_evidence(
    parents: Mapping[str, Any],
    broker: AccessBroker,
    cells: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    rows = broker.read_jsonl(parents["suspension_coverage_ledger"], principal="task055g_independent_verifier")
    target_dates: dict[str, set[str]] = defaultdict(set)
    for cell in cells:
        target_dates[str(cell["ts_code"])].add(str(cell["trade_date"]))
    events: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    coverage: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    opened: set[str] = set()
    cache_root = Path(str(parents["suspension_cache_root"]))
    for ledger in rows:
        code = str(ledger.get("ts_code") or "")
        relevant = target_dates.get(code)
        if not relevant:
            continue
        if ledger.get("status") != "success" or ledger.get("api_name") != "suspend_d":
            raise SemanticVerificationError("independent_suspension_ledger_invalid")
        for slice_row in ledger.get("slices") or ():
            start, end = str(slice_row.get("start_date") or ""), str(slice_row.get("end_date") or "")
            intersecting = sorted(date for date in relevant if start <= date <= end)
            if not intersecting:
                continue
            relative = str(cache_root / str(slice_row.get("cache_key") or ""))
            if relative in opened:
                raise SemanticVerificationError("independent_suspension_slice_reused")
            opened.add(relative)
            payload = broker.read_json(relative, principal="task055g_independent_verifier")
            _validate_suspend_envelope(payload, broker.rows[-1], ledger, slice_row)
            by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for record in payload.get("records") or ():
                by_date[str(record.get("trade_date") or "")].append(dict(record))
            for date in intersecting:
                matches = by_date.get(date, [])
                proof = {
                    "api": "suspend_d",
                    "source_kind": str(payload.get("schema_version") or ""),
                    "proof_quality": "validated_historical_governed_envelope",
                    "outcome": "matching_row" if matches else "no_matching_row",
                    "request_fingerprint": payload.get("request_fingerprint"),
                    "source_sha256": str(slice_row.get("cache_sha256") or ""),
                    "source_relative_path": relative,
                    "request_start": start,
                    "request_end": end,
                    "provider_endpoint": str(slice_row.get("endpoint") or ledger.get("endpoint") or ""),
                    "provider_api_version": str(
                        slice_row.get("provider_api_version") or ledger.get("provider_api_version") or ""
                    ),
                }
                proof["proof_hash"] = canonical_hash(proof)
                coverage[(code, date)].append(proof)
                for record in matches:
                    event = {
                        "ts_code": code,
                        "trade_date": date,
                        "suspend_type": str(record.get("suspend_type") or ""),
                        "suspend_timing": record.get("suspend_timing"),
                        "row_hash": canonical_hash(record),
                        "source_proof_hash": proof["proof_hash"],
                    }
                    event["evidence_hash"] = canonical_hash(event)
                    events[(code, date)].append(event)
    return {"events": events, "coverage": coverage}


def _daily_proof(row: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    records = list(payload.get("records") or ())
    key = (str(row.get("ts_code") or ""), str(row.get("trade_date") or ""))
    source_kind = str(row.get("source_kind") or "")
    if row.get("raw_envelope_validated"):
        response = payload.get("response") or {}
        request = payload.get("request") or {}
        if response.get("code") != 0 or response.get("complete") is not True:
            raise SemanticVerificationError("independent_daily_response_invalid")
        if response.get("item_count") != len(records) or response.get("records_sha256") != stable_json_hash(records):
            raise SemanticVerificationError("independent_daily_response_integrity_invalid")
        request_range = row.get("request_range") or {}
        start, end = str(request_range.get("start_date") or ""), str(request_range.get("end_date") or "")
        if request.get("api_name") != "daily" or request.get("params") is None or not start <= key[1] <= end:
            raise SemanticVerificationError("independent_daily_request_geometry_invalid")
        quality = "validated_formal_envelope"
    else:
        metadata = payload.get("metadata") or {}
        if metadata.get("api_name") != "daily" or int(metadata.get("records") or -1) != len(records):
            raise SemanticVerificationError("independent_legacy_daily_metadata_invalid")
        quality = "legacy_composite_untrusted"
    matching = [
        record
        for record in records
        if (str(record.get("ts_code") or ""), str(record.get("trade_date") or "")) == key
    ]
    expected = row.get("record_kind") in {"formal_cache_positive_row", "legacy_cache_positive_row"}
    if expected != bool(matching):
        raise SemanticVerificationError("independent_daily_outcome_mismatch")
    proof = {
        "api": "daily",
        "source_kind": source_kind,
        "proof_quality": quality,
        "outcome": "matching_row" if matching else "no_matching_row",
        "request_fingerprint": row.get("request_fingerprint"),
        "source_sha256": row.get("source_file_sha256"),
        "source_relative_path": row.get("source_relative_path"),
    }
    proof["proof_hash"] = canonical_hash(proof)
    return proof


def _validate_suspend_envelope(
    payload: Mapping[str, Any],
    access_row: Mapping[str, Any],
    ledger: Mapping[str, Any],
    slice_row: Mapping[str, Any],
) -> None:
    if access_row.get("actual_sha256") != slice_row.get("cache_sha256"):
        raise SemanticVerificationError("independent_suspend_cache_sha_mismatch")
    if payload.get("schema_version") not in {"tushare_cache_envelope.v2", "tushare_cache_envelope.v3"}:
        raise SemanticVerificationError("independent_suspend_cache_schema_invalid")
    if payload.get("request") != slice_row.get("normalized_request"):
        raise SemanticVerificationError("independent_suspend_request_mismatch")
    if payload.get("request_fingerprint") != slice_row.get("request_fingerprint"):
        raise SemanticVerificationError("independent_suspend_fingerprint_mismatch")
    if payload.get("code_semantic_hash") != ledger.get("code_semantic_hash"):
        raise SemanticVerificationError("independent_suspend_code_hash_mismatch")
    response = payload.get("response") or {}
    records = list(payload.get("records") or ())
    if response.get("code") != 0 or response.get("complete") is not True:
        raise SemanticVerificationError("independent_suspend_response_invalid")
    if response.get("item_count") != len(records) or response.get("records_sha256") != stable_json_hash(records):
        raise SemanticVerificationError("independent_suspend_integrity_invalid")
    if len(records) >= 1000:
        raise SemanticVerificationError("independent_suspend_cap_ambiguous")
    request = payload.get("request") or {}
    if tuple(request.get("fields") or ()) != SUSPEND_FIELDS:
        raise SemanticVerificationError("independent_suspend_fields_invalid")
    params = request.get("params") or {}
    code = str(params.get("ts_code") or "")
    start, end = str(params.get("start_date") or ""), str(params.get("end_date") or "")
    seen: set[tuple[Any, ...]] = set()
    for record in records:
        key = (
            str(record.get("ts_code") or ""),
            str(record.get("trade_date") or ""),
            str(record.get("suspend_type") or ""),
            record.get("suspend_timing"),
        )
        if key in seen:
            raise SemanticVerificationError("independent_suspend_primary_key_duplicate")
        seen.add(key)
        if key[0] != code or not start <= key[1] <= end or key[1] > MAX_DATE or key[2] not in {"S", "R"}:
            raise SemanticVerificationError("independent_suspend_geometry_invalid")


def _classify_cell(
    *,
    cell: Mapping[str, Any],
    matrix_bar: Mapping[str, Any] | None,
    events: list[dict[str, Any]],
    coverage: list[dict[str, Any]],
    daily_evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    code, date = str(cell["ts_code"]), str(cell["trade_date"])
    complete_bar = bool(matrix_bar and matrix_bar.get("complete"))
    s_rows = [row for row in events if row.get("suspend_type") == "S"]
    r_rows = [row for row in events if row.get("suspend_type") == "R"]
    timing = _aggregate_timing([_timing_status(row.get("suspend_timing")) for row in s_rows])
    coverage_status = "complete" if coverage else "missing"
    listed = bool((cell.get("lifecycle") or {}).get("listed", False))
    active = bool((cell.get("lifecycle") or {}).get("active", False))
    modeled = False
    if cell.get("corporate_action_validity") is False:
        state, reason = "LIFECYCLE_OR_CORPORATE_ACTION_CONFLICT", "corporate_action_evidence_conflict"
    elif complete_bar and (not listed or not active):
        state, reason = "MATRIX_SOURCE_CONFLICT", "complete_daily_bar_conflicts_with_lifecycle_or_inventory"
    elif complete_bar and not bool(cell.get("bar_observed")):
        state, reason = "MATRIX_SOURCE_CONFLICT", "matrix_complete_bar_conflicts_with_inventory_absence"
    elif complete_bar and s_rows:
        state, reason = "MATRIX_SOURCE_CONFLICT", "complete_daily_bar_conflicts_with_suspend_event"
    elif complete_bar:
        state = "TRADED_PRIMARY_BAR"
        reason = (
            "strict_matrix_contains_complete_finite_positive_resume_bar"
            if r_rows
            else "strict_matrix_contains_complete_finite_positive_bar"
        )
    elif not listed or not active:
        state, reason = "LIFECYCLE_TERMINATED", "outside_verified_active_lifecycle_requires_settlement_evidence"
    elif s_rows and r_rows:
        state, reason = "SUSPENSION_EVENT_CONFLICT", "same_date_contains_suspend_and_resume"
    elif len(s_rows) > 1 and len({(row.get("suspend_timing"), row.get("row_hash")) for row in s_rows}) > 1:
        state, reason = "SUSPENSION_EVENT_CONFLICT", "multiple_nonidentical_suspend_rows"
    elif r_rows:
        state, reason = "RESUME_EVENT_WITHOUT_SUSPENSION_EVIDENCE", "resume_is_not_positive_suspension_evidence"
    elif s_rows and coverage_status != "complete":
        state, reason = "DATA_SOURCE_GAP", "positive_suspend_row_without_complete_source_coverage"
    elif s_rows and timing in {"raw_null", "explicit_full_day"}:
        state = "VENDOR_DAILY_NON_TRADING_MODELED_CANDIDATE"
        reason = (
            "exact_positive_s_raw_null_conservative_modeled_candidate"
            if timing == "raw_null"
            else "exact_positive_s_explicit_full_day_modeled_candidate"
        )
        modeled = True
    elif s_rows and timing == "explicit_intraday":
        state, reason = "SUSPENSION_INTRADAY_UNSUPPORTED", "intraday_suspend_timing_cannot_authorize_daily_stale_mark"
    elif s_rows:
        state, reason = "SUSPENSION_TIMING_UNPARSED", "blank_or_unparsed_suspend_timing"
    else:
        state, reason = "DATA_SOURCE_GAP", "no_complete_bar_and_no_exact_positive_suspend_event"
    if state not in TRUTH_STATES:
        raise SemanticVerificationError(f"independent_truth_state_invalid:{state}")
    payload = {
        "ts_code": code,
        "trade_date": date,
        "state": state,
        "reason_code": reason,
        "daily_bar_status": "present_complete" if complete_bar else "absent_or_invalid",
        "matrix_bar": matrix_bar,
        "inventory_bar_observed": bool(cell.get("bar_observed")),
        "suspend_type": "S+R" if s_rows and r_rows else "S" if s_rows else "R" if r_rows else "none",
        "suspend_timing_status": timing,
        "suspension_events": sorted(
            events,
            key=lambda row: (row["suspend_type"], str(row.get("suspend_timing")), row["row_hash"]),
        ),
        "suspension_source_coverage": coverage_status,
        "suspension_response_evidence": sorted(coverage, key=lambda row: row["proof_hash"]),
        "daily_response_evidence": sorted(daily_evidence, key=lambda row: row["proof_hash"]),
        "listed": listed,
        "active": active,
        "membership": bool(cell.get("membership", False)),
        "membership_known": bool(cell.get("membership_known", False)),
        "corporate_action_validity": cell.get("corporate_action_validity"),
        "valuation_domain_intersection": bool(cell.get("valuation_closure_domain", False)),
        "regression_probe": bool(cell.get("regression_probe", False)),
        "modeled_stale_candidate": modeled,
        "stale_mark_authorized": False,
        "stale_mark_authorization_note": "truth_v2_never_authorizes_price_without_prior_close_and_stale_policy",
    }
    payload["evidence_hash"] = canonical_hash(payload)
    return payload


def _load_bundle(parents: Mapping[str, Any], broker: AccessBroker) -> dict[str, Any]:
    return load_audited_simulation_bundle(
        manifest_path=str(parents["simulation_bundle"]),
        broker=broker,
    )


def _load_matrix_marks(
    parents: Mapping[str, Any], broker: AccessBroker, assets: list[str], dates: list[str]
) -> dict[str, Any]:
    root = Path(str(parents["matrix_root"]))
    manifest = broker.read_json(parents["matrix_manifest"], principal="task055g_independent_verifier")
    matrix_assets = broker.read_json(str(root / "ts_codes.json"), principal="task055g_independent_verifier")
    matrix_dates = broker.read_json(str(root / "trade_dates.json"), principal="task055g_independent_verifier")
    asset_index = {str(value): index for index, value in enumerate(matrix_assets)}
    date_index = {str(value): index for index, value in enumerate(matrix_dates)}
    if any(asset not in asset_index for asset in assets) or any(date not in date_index for date in dates):
        raise SemanticVerificationError("independent_simulation_axis_not_in_matrix")
    arrays = {}
    for name in ("open.npy", "open_validity.npy", "close.npy", "close_validity.npy"):
        arrays[name] = broker.load_npy(
            str(root / name),
            component="task055g_independent_verifier",
            dataset=f"strict_matrix_partition:{name}",
        )
        if broker.rows[-1].get("actual_sha256") != (manifest.get("partition_sha256") or {}).get(name):
            raise SemanticVerificationError(f"independent_matrix_mark_partition_mismatch:{name}")
    asset_positions = np.asarray([asset_index[asset] for asset in assets], dtype=np.int64)
    date_positions = np.asarray([date_index[date] for date in dates], dtype=np.int64)
    return {
        "manifest": manifest,
        "open": np.asarray(arrays["open.npy"][np.ix_(asset_positions, date_positions)].T, dtype=float),
        "open_valid": np.asarray(
            arrays["open_validity.npy"][np.ix_(asset_positions, date_positions)].T, dtype=bool
        ),
        "close": np.asarray(arrays["close.npy"][np.ix_(asset_positions, date_positions)].T, dtype=float),
        "close_valid": np.asarray(
            arrays["close_validity.npy"][np.ix_(asset_positions, date_positions)].T, dtype=bool
        ),
    }


def _build_valuation_surface(
    *,
    truth: Mapping[str, Any],
    assets: list[str],
    dates: list[str],
    matrix: Mapping[str, Any],
    corporate_actions: list[Mapping[str, Any]],
) -> dict[str, Any]:
    shape = (len(dates), len(assets))
    values = {point: np.full(shape, np.nan, dtype=float) for point in ("open", "close")}
    metadata = {
        point: {
            "method": np.full(shape, "", dtype=object),
            "source_date": np.full(shape, "", dtype=object),
            "stale_age": np.full(shape, -1, dtype=np.int32),
            "evidence_id": np.full(shape, "", dtype=object),
        }
        for point in ("open", "close")
    }
    blockers: dict[tuple[str, str, str], str] = {}
    truth_by_key = {(str(row["ts_code"]), str(row["trade_date"])): row for row in truth["records"]}
    date_index = {date: index for index, date in enumerate(dates)}
    action_dates: dict[str, set[int]] = defaultdict(set)
    for action in corporate_actions:
        asset = str(action.get("asset", action.get("ts_code", "")))
        raw = action.get("effective_index", action.get("ex_index", action.get("ex_date")))
        index = date_index.get(str(raw), int(raw) if str(raw).isdigit() else -1)
        if 0 <= index < len(dates):
            action_dates[asset].add(index)
    last_close = np.full(len(assets), -1, dtype=np.int32)
    for day, date in enumerate(dates):
        for asset_index, asset in enumerate(assets):
            open_value = float(matrix["open"][day, asset_index])
            close_value = float(matrix["close"][day, asset_index])
            open_valid = bool(matrix["open_valid"][day, asset_index]) and math.isfinite(open_value) and open_value > 0
            close_valid = bool(matrix["close_valid"][day, asset_index]) and math.isfinite(close_value) and close_value > 0
            if open_valid:
                values["open"][day, asset_index] = open_value
                _set_mark(
                    metadata["open"], day, asset_index, OFFICIAL_OPEN_METHOD, date, 0,
                    canonical_hash((asset, date, "open", open_value)),
                )
            if close_valid:
                values["close"][day, asset_index] = close_value
                _set_mark(
                    metadata["close"], day, asset_index, OFFICIAL_CLOSE_METHOD, date, 0,
                    canonical_hash((asset, date, "close", close_value)),
                )
            if not open_valid or not close_valid:
                row = truth_by_key.get((asset, date))
                if row is None:
                    reason = "no_truth_v2_row_for_missing_matrix_mark"
                elif row.get("state") == "MATRIX_SOURCE_CONFLICT":
                    reason = "matrix_source_conflict"
                elif not row.get("modeled_stale_candidate"):
                    reason = str(row.get("reason_code") or "truth_v2_not_modeled_candidate")
                else:
                    source = int(last_close[asset_index])
                    age = day - source if source >= 0 else -1
                    if source < 0:
                        reason = "no_prior_finite_positive_close"
                    elif age > MAX_STALE_AGE_TRADE_DAYS:
                        reason = "stale_age_gt_250"
                    elif any(source < action <= day for action in action_dates.get(asset, ())):
                        reason = "corporate_action_between_anchor_and_mark"
                    else:
                        stale_value = float(matrix["close"][source, asset_index])
                        source_date = dates[source]
                        evidence_id = str(row["evidence_hash"])
                        if not open_valid:
                            values["open"][day, asset_index] = stale_value
                            _set_mark(
                                metadata["open"], day, asset_index, MODELED_STALE_METHOD,
                                source_date, age, evidence_id,
                            )
                        if not close_valid:
                            values["close"][day, asset_index] = stale_value
                            _set_mark(
                                metadata["close"], day, asset_index, MODELED_STALE_METHOD,
                                source_date, age, evidence_id,
                            )
                        reason = ""
                if reason:
                    if not open_valid:
                        blockers[(asset, date, "open_pretrade")] = reason
                    if not close_valid:
                        blockers[(asset, date, "close")] = reason
            if close_valid:
                last_close[asset_index] = day
    return {"values": values, "metadata": metadata, "blockers": blockers}


def _compare_causal(producer: Mapping[str, Any], rebuilt: Mapping[str, Any]) -> None:
    expected_pairs = sorted(
        (factor_id, scenario)
        for factor_id in producer.get("exact20_ids") or ()
        for scenario in SCENARIO_NAMES
    )
    actual_pairs = sorted((row["factor_id"], row["scenario"]) for row in rebuilt["run_rows"])
    if len(expected_pairs) != 100 or actual_pairs != expected_pairs:
        raise SemanticVerificationError("producer_causal_exact20_cartesian_mismatch")
    stored_by_pair = {
        (row["factor_id"], row["scenario"]): row for row in producer.get("run_rows") or ()
    }
    if len(stored_by_pair) != 100:
        raise SemanticVerificationError("producer_causal_duplicate_or_missing_pair")
    for row in rebuilt["run_rows"]:
        pair = (row["factor_id"], row["scenario"])
        if stored_by_pair.get(pair) != row:
            raise SemanticVerificationError(f"producer_causal_run_semantics_mismatch:{pair[0]}:{pair[1]}")
    stored_held = list(producer.get("held_marks") or ())
    if stored_held != rebuilt["held_rows"]:
        raise SemanticVerificationError("producer_causal_held_mark_rows_mismatch")
    checks = {
        "run_count": len(rebuilt["run_rows"]),
        "run_rows_root": rebuilt["run_rows_root"],
        "held_mark_root": rebuilt["held_mark_root"],
        "missing_key_root": rebuilt["missing_key_root"],
        "terminal_counts": rebuilt["terminal_counts"],
        "round_one_frontier_count": rebuilt["round_one_frontier_count"],
        "held_mark_count": rebuilt["held_mark_count"],
        "authorized_modeled_held_mark_count": rebuilt["authorized_modeled_held_mark_count"],
    }
    for key, value in checks.items():
        if producer.get(key) != value:
            raise SemanticVerificationError(f"producer_causal_{key}_mismatch")


def _load_producer_truth(path: str | Path) -> dict[str, Any]:
    manifest_path = _resolve_manifest(path, "truth_v2_manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    semantic = {key: value for key, value in manifest.items() if key not in {"content_hash", "generation_id"}}
    if (
        manifest.get("schema_version") != TRUTH_SCHEMA
        or manifest.get("status") != "published"
        or canonical_hash(semantic) != manifest.get("content_hash")
    ):
        raise SemanticVerificationError("producer_truth_manifest_invalid")
    entry = (manifest.get("partitions") or {}).get("rows") or {}
    rows_path = manifest_path.parent / str(entry.get("path") or "")
    if (
        not rows_path.is_file()
        or rows_path.is_symlink()
        or sha256_file(rows_path) != entry.get("sha256")
        or rows_path.stat().st_size != entry.get("size_bytes")
    ):
        raise SemanticVerificationError("producer_truth_partition_invalid")
    rows = [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines() if line]
    keys = [(row.get("ts_code"), row.get("trade_date")) for row in rows]
    if len(rows) != manifest.get("record_count") or len(keys) != len(set(keys)):
        raise SemanticVerificationError("producer_truth_record_identity_invalid")
    return manifest | {"manifest_path": str(manifest_path), "records": rows, "rows_root": canonical_hash(rows)}


def _load_causal_artifact(path: str | Path) -> dict[str, Any]:
    manifest_path = _resolve_manifest(path, "causal_frontier_manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    semantic = {key: value for key, value in manifest.items() if key not in {"content_hash", "generation_id"}}
    if (
        manifest.get("schema_version") != CAUSAL_SCHEMA
        or manifest.get("status") != "published"
        or canonical_hash(semantic) != manifest.get("content_hash")
    ):
        raise SemanticVerificationError("producer_causal_manifest_invalid")
    root = manifest_path.parent
    partitions = manifest.get("partitions") or {}
    loaded: dict[str, Any] = {}
    for name in ("run_rows", "held_marks", "network_plan"):
        entry = partitions.get(name) or {}
        source = root / str(entry.get("path") or "")
        if (
            not source.is_file()
            or source.is_symlink()
            or sha256_file(source) != entry.get("sha256")
            or source.stat().st_size != entry.get("size_bytes")
        ):
            raise SemanticVerificationError(f"producer_causal_partition_invalid:{name}")
        if name == "network_plan":
            loaded[name] = json.loads(source.read_text(encoding="utf-8"))
        else:
            loaded[name] = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line]
    if canonical_hash(loaded["run_rows"]) != manifest.get("run_rows_root"):
        raise SemanticVerificationError("producer_causal_run_rows_root_invalid")
    if canonical_hash(loaded["held_marks"]) != manifest.get("held_mark_root"):
        raise SemanticVerificationError("producer_causal_held_root_invalid")
    if (
        loaded["network_plan"].get("schema_version") != PLAN_SCHEMA
        or loaded["network_plan"].get("frontier_root") != manifest.get("missing_key_root")
    ):
        raise SemanticVerificationError("producer_causal_network_plan_invalid")
    return manifest | {
        "manifest_path": str(manifest_path),
        "run_rows": loaded["run_rows"],
        "held_marks": loaded["held_marks"],
        "network_plan": loaded["network_plan"],
    }


def _artifact_entry(artifacts: Mapping[str, Any], name: str) -> dict[str, Any]:
    entry = dict(artifacts.get(name) or {})
    if not entry.get("path") or not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256") or "")):
        raise SemanticVerificationError(f"independent_bundle_artifact_missing:{name}")
    return entry


def _verify_bundle_read(entry: Mapping[str, Any], access_row: Mapping[str, Any], name: str) -> None:
    if access_row.get("actual_sha256") != entry.get("sha256"):
        raise SemanticVerificationError(f"independent_bundle_artifact_sha_mismatch:{name}")
    expected_size = entry.get("size_bytes")
    if expected_size is not None and int(expected_size) != int(access_row.get("size_bytes") or -1):
        raise SemanticVerificationError(f"independent_bundle_artifact_size_mismatch:{name}")


def _timing_status(value: Any) -> str:
    if value is None:
        return "raw_null"
    text = str(value).strip()
    if not text:
        return "blank"
    normalized = text.upper().replace(" ", "")
    if normalized in {item.upper().replace(" ", "") for item in EXPLICIT_FULL_DAY_TIMINGS}:
        return "explicit_full_day"
    if all(re.fullmatch(r"\d{2}:\d{2}-\d{2}:\d{2}", item.strip()) for item in text.split(",")):
        return "explicit_intraday"
    return "unparsed"


def _aggregate_timing(values: list[str]) -> str:
    if not values:
        return "none"
    return values[0] if len(set(values)) == 1 else "conflicting"


def _valid_bar(values: Mapping[str, Any]) -> bool:
    try:
        parsed = {field: float(values[field]) for field in MATRIX_DAILY_FIELDS}
    except (KeyError, TypeError, ValueError):
        return False
    if any(not math.isfinite(value) for value in parsed.values()):
        return False
    if any(parsed[field] <= 0 for field in ("open", "high", "low", "close", "pre_close")):
        return False
    if parsed["volume"] < 0 or parsed["amount"] < 0:
        return False
    return parsed["high"] >= max(parsed["open"], parsed["low"], parsed["close"]) and parsed["low"] <= min(
        parsed["open"], parsed["high"], parsed["close"]
    )


def _set_mark(
    metadata: Mapping[str, np.ndarray],
    day: int,
    asset: int,
    method: str,
    source_date: str,
    stale_age: int,
    evidence_id: str,
) -> None:
    metadata["method"][day, asset] = method
    metadata["source_date"][day, asset] = source_date
    metadata["stale_age"][day, asset] = stale_age
    metadata["evidence_id"][day, asset] = evidence_id


def _parse_blocker(detail: str, blockers: Mapping[tuple[str, str, str], str]) -> dict[str, Any]:
    parts = detail.split(":")
    if parts and parts[0] == "explicit_valuation_mark_blocked" and len(parts) >= 5:
        date, asset, point = parts[1], parts[2], parts[3]
        return {
            "code": "held_position_mark_unavailable",
            "ts_code": asset,
            "trade_date": date,
            "reporting_point": point,
            "reason": blockers.get((asset, date, point), parts[4]),
            "detail": detail,
        }
    if detail.startswith("valuation_"):
        return {"code": "legacy_valuation_blocker", "detail": detail}
    return {"code": "simulation_data_blocker", "detail": detail}


def _resolve_manifest(path: str | Path, filename: str) -> Path:
    value = Path(path)
    if value.is_file():
        return value
    pointer = value / "current.json"
    if pointer.is_file():
        payload = json.loads(pointer.read_text(encoding="utf-8"))
        candidate = value / str(payload.get("manifest") or "")
        if candidate.is_file():
            return candidate
    candidate = value / filename
    if candidate.is_file():
        return candidate
    raise SemanticVerificationError(f"semantic_manifest_missing:{filename}")


def _publish(root: Path, semantic: Mapping[str, Any]) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    content_hash = canonical_hash(semantic)
    generation_id = f"semantic_verification_{content_hash[:24]}"
    manifest = dict(semantic) | {"content_hash": content_hash, "generation_id": generation_id}
    staging = Path(tempfile.mkdtemp(prefix=".task055g.verify.", dir=root))
    try:
        (staging / "semantic_verification.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        target = root / "generations" / generation_id
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            existing = target / "semantic_verification.json"
            if not existing.is_file() or existing.read_text(encoding="utf-8") != (
                json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
            ):
                raise SemanticVerificationError("semantic_verification_content_address_collision")
            shutil.rmtree(staging)
        else:
            os.replace(staging, target)
        return manifest | {"manifest_path": str(target / "semantic_verification.json")}
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _relative_to_output(path: str | Path, output_root: str | Path) -> str:
    resolved = Path(path).resolve()
    root = Path(output_root).resolve()
    if root not in resolved.parents:
        raise SemanticVerificationError("semantic_output_artifact_escape")
    return str(resolved.relative_to(root))

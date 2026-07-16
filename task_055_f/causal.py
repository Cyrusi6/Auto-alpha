"""Fee-aware causal held-position tracing and exact frontier sealing."""

from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from artifact_schema.writer import write_artifact_sidecar
from task_055_a.bundle import load_simulation_bundle, validate_simulation_bundle
from task_055_a.policy import PREREGISTERED_SCENARIOS
from task_055_a.run import SCENARIO_NAMES, prepare_simulation_inputs
from task_055_a.simulator import EventLedgerSimulator, SimulationDataBlocker

from .contracts import (
    CAUSAL_SCHEMA,
    DAILY_FIELDS,
    MAX_DATE,
    MAX_LOGICAL_REQUESTS,
    MAX_PHYSICAL_ATTEMPTS,
    MAX_STALE_AGE_TRADE_DAYS,
    MAX_UNIQUE_SECURITY_DATES,
    MODELED_STALE_METHOD,
    NETWORK_PLAN_SCHEMA,
    OFFICIAL_CLOSE_METHOD,
    OFFICIAL_OPEN_METHOD,
    SIMULATION_END,
    SIMULATION_START,
)
from .fees import FeeScheduleCalculator, validate_fee_schedule_v2
from .transport import evidence_use_identity, transport_identity
from .read_ledger import AuditedReader, canonical_hash, sha256_file
from .truth_v2 import validate_truth_v2
from .valuation import publish_valuation_projection, validate_valuation_projection


class CausalTraceError(RuntimeError):
    pass


def build_causal_frontier(
    *,
    truth_v2_manifest: str | Path,
    matrix_root: str | Path,
    simulation_bundle_manifest: str | Path,
    fee_schedule_manifest: str | Path,
    output_root: str | Path,
    reader: AuditedReader,
    builder_code_hash: str,
) -> dict[str, Any]:
    truth = validate_truth_v2(truth_v2_manifest)
    fee = validate_fee_schedule_v2(fee_schedule_manifest)
    bundle_manifest = _audit_bundle(reader, simulation_bundle_manifest)
    validated_bundle = validate_simulation_bundle(simulation_bundle_manifest, require_ready=True)
    if validated_bundle.get("content_hash") != bundle_manifest.get("content_hash"):
        raise CausalTraceError("simulation_bundle_validation_hash_mismatch")
    bundle = load_simulation_bundle(simulation_bundle_manifest)
    prepared = prepare_simulation_inputs(bundle)
    dates = list(prepared["market"]["dates"])
    assets = list(prepared["market"]["assets"])
    if dates[0] != SIMULATION_START or dates[-1] != SIMULATION_END:
        raise CausalTraceError(f"simulation_axis_mismatch:{dates[0]}:{dates[-1]}")
    if fee["simulation_start"] != dates[0] or fee["simulation_end"] != dates[-1]:
        raise CausalTraceError("fee_schedule_simulation_axis_mismatch")
    matrix = _load_matrix_marks(reader, matrix_root, assets, dates)
    surface = _build_valuation_surface(
        truth=truth,
        assets=assets,
        dates=dates,
        matrix=matrix,
        corporate_actions=prepared["corporate_actions"],
    )
    projection = publish_valuation_projection(
        output_root=Path(output_root).parent / "valuation_projection",
        dates=dates,
        assets=assets,
        surface=surface,
        truth_v2_content_hash=str(truth["content_hash"]),
        matrix_content_hash=str(matrix["manifest"].get("content_hash")),
        builder_code_hash=builder_code_hash,
    )
    calculator = FeeScheduleCalculator(fee_schedule_manifest)
    trace = _trace_runs(bundle, prepared, surface, calculator)
    plan = _seal_round_one_plan(
        trace=trace,
        truth=truth,
        matrix_content_hash=str(matrix["manifest"].get("content_hash")),
        bundle_content_hash=str(bundle_manifest.get("content_hash")),
        fee_content_hash=str(fee.get("content_hash")),
        builder_code_hash=builder_code_hash,
    )
    return _publish(
        output_root=Path(output_root),
        trace=trace,
        plan=plan,
        lineage={
            "truth_v2_content_hash": truth["content_hash"],
            "matrix_content_hash": matrix["manifest"].get("content_hash"),
            "simulation_bundle_content_hash": bundle_manifest.get("content_hash"),
            "fee_schedule_content_hash": fee.get("content_hash"),
            "builder_code_hash": builder_code_hash,
        },
        valuation_projection=projection,
    )


def validate_causal_frontier(path: str | Path) -> dict[str, Any]:
    manifest_path = _resolve_manifest(path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != CAUSAL_SCHEMA or manifest.get("status") != "published":
        raise CausalTraceError("causal_manifest_invalid")
    root = manifest_path.parent
    for entry in (manifest.get("partitions") or {}).values():
        artifact = root / str(entry.get("path"))
        if not artifact.is_file() or sha256_file(artifact) != entry.get("sha256"):
            raise CausalTraceError("causal_partition_mismatch")
    run_rows = _read_jsonl(root / manifest["partitions"]["run_rows"]["path"])
    held_rows = _read_jsonl(root / manifest["partitions"]["held_marks"]["path"])
    plan = json.loads((root / manifest["partitions"]["network_plan"]["path"]).read_text(encoding="utf-8"))
    task_output_root = manifest_path.parents[3]
    projection_path = task_output_root / str(manifest["valuation_projection"]["manifest_relative"])
    projection = validate_valuation_projection(projection_path)
    if projection.get("content_hash") != manifest["valuation_projection"].get("content_hash"):
        raise CausalTraceError("causal_valuation_projection_hash_mismatch")
    pairs = [(row["factor_id"], row["scenario"]) for row in run_rows]
    expected_ids = manifest.get("exact20_ids") or []
    expected_pairs = sorted((factor_id, scenario) for factor_id in expected_ids for scenario in SCENARIO_NAMES)
    if sorted(pairs) != expected_pairs or len(pairs) != len(set(pairs)):
        raise CausalTraceError("causal_exact20_scenario_cartesian_invalid")
    if canonical_hash(run_rows) != manifest.get("run_rows_root"):
        raise CausalTraceError("causal_run_rows_root_mismatch")
    if canonical_hash(held_rows) != manifest.get("held_mark_root"):
        raise CausalTraceError("causal_held_mark_root_mismatch")
    if plan.get("schema_version") != NETWORK_PLAN_SCHEMA or plan.get("frontier_root") != manifest.get("missing_key_root"):
        raise CausalTraceError("causal_network_plan_invalid")
    semantic = {key: value for key, value in manifest.items() if key not in {"content_hash", "generation_id"}}
    if canonical_hash(semantic) != manifest.get("content_hash"):
        raise CausalTraceError("causal_content_hash_mismatch")
    return manifest | {
        "manifest_path": str(manifest_path),
        "run_rows": run_rows,
        "held_marks": held_rows,
        "network_plan": plan,
        "valuation_projection": projection,
    }


def _audit_bundle(reader: AuditedReader, manifest_path: str | Path) -> dict[str, Any]:
    manifest = reader.read_json(
        manifest_path,
        component="causal_trace",
        dataset="simulation_bundle_manifest",
    )
    root = Path(manifest_path).resolve().parent
    for name, entry in sorted((manifest.get("artifacts") or {}).items()):
        relative = str(entry.get("path") or "")
        if not relative:
            continue
        artifact = root / relative
        if not artifact.is_file():
            raise CausalTraceError(f"simulation_bundle_artifact_missing:{name}")
        reader.record_binary(
            artifact,
            component="causal_trace",
            dataset=f"simulation_bundle:{entry.get('role') or name}",
            declared_start=SIMULATION_START,
            declared_end=SIMULATION_END,
        )
        if reader.rows[-1]["sha256"] != entry.get("sha256"):
            raise CausalTraceError(f"simulation_bundle_artifact_sha_mismatch:{name}")
    return manifest


def _load_matrix_marks(
    reader: AuditedReader,
    matrix_root: str | Path,
    assets: list[str],
    dates: list[str],
) -> dict[str, Any]:
    root = Path(matrix_root).resolve()
    manifest = reader.read_json(
        root / "task_052a_strict_matrix_manifest.json",
        component="causal_trace",
        dataset="strict_matrix_manifest",
    )
    matrix_assets = reader.read_json(root / "ts_codes.json", component="causal_trace", dataset="matrix_stock_axis")
    matrix_dates = reader.read_json(root / "trade_dates.json", component="causal_trace", dataset="matrix_date_axis")
    asset_index = {str(value): index for index, value in enumerate(matrix_assets)}
    date_index = {str(value): index for index, value in enumerate(matrix_dates)}
    if any(asset not in asset_index for asset in assets) or any(date not in date_index for date in dates):
        raise CausalTraceError("simulation_axis_not_contained_in_matrix")
    partitions = manifest.get("partition_sha256") or {}
    arrays = {}
    for name in ("open.npy", "open_validity.npy", "close.npy", "close_validity.npy"):
        path = root / name
        if hasattr(reader, "load_npy"):
            arrays[name] = reader.load_npy(
                path,
                component="causal_trace",
                dataset=f"matrix_partition:{name}",
            )
            if reader.rows[-1]["sha256"] != partitions.get(name):
                raise CausalTraceError(f"matrix_partition_sha_mismatch:{name}")
        else:
            reader.record_binary(
                path,
                component="causal_trace",
                dataset=f"matrix_partition:{name}",
                declared_start=str(matrix_dates[0]),
                declared_end=str(matrix_dates[-1]),
            )
            if reader.rows[-1]["sha256"] != partitions.get(name):
                raise CausalTraceError(f"matrix_partition_sha_mismatch:{name}")
            arrays[name] = np.load(path, mmap_mode="r", allow_pickle=False)
    asset_positions = np.asarray([asset_index[asset] for asset in assets], dtype=np.int64)
    date_positions = np.asarray([date_index[date] for date in dates], dtype=np.int64)
    return {
        "manifest": manifest,
        "matrix_dates": matrix_dates,
        "asset_positions": asset_positions,
        "date_positions": date_positions,
        "open": np.asarray(arrays["open.npy"][np.ix_(asset_positions, date_positions)].T, dtype=float),
        "open_valid": np.asarray(arrays["open_validity.npy"][np.ix_(asset_positions, date_positions)].T, dtype=bool),
        "close": np.asarray(arrays["close.npy"][np.ix_(asset_positions, date_positions)].T, dtype=float),
        "close_valid": np.asarray(arrays["close_validity.npy"][np.ix_(asset_positions, date_positions)].T, dtype=bool),
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
    values = {
        "open": np.full(shape, np.nan, dtype=float),
        "close": np.full(shape, np.nan, dtype=float),
    }
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
    action_dates: dict[str, set[int]] = {}
    date_index = {date: index for index, date in enumerate(dates)}
    for action in corporate_actions:
        asset = str(action.get("asset", action.get("ts_code", "")))
        raw = action.get("effective_index", action.get("ex_index", action.get("ex_date")))
        index = date_index.get(str(raw), int(raw) if str(raw).isdigit() else -1)
        if 0 <= index < len(dates):
            action_dates.setdefault(asset, set()).add(index)
    last_close_index = np.full(len(assets), -1, dtype=np.int32)
    for day, date in enumerate(dates):
        for asset_index, asset in enumerate(assets):
            open_value = float(matrix["open"][day, asset_index])
            close_value = float(matrix["close"][day, asset_index])
            open_valid = bool(matrix["open_valid"][day, asset_index]) and math.isfinite(open_value) and open_value > 0
            close_valid = bool(matrix["close_valid"][day, asset_index]) and math.isfinite(close_value) and close_value > 0
            if open_valid:
                values["open"][day, asset_index] = open_value
                _set_mark(metadata["open"], day, asset_index, OFFICIAL_OPEN_METHOD, date, 0, canonical_hash((asset, date, "open", open_value)))
            if close_valid:
                values["close"][day, asset_index] = close_value
                _set_mark(metadata["close"], day, asset_index, OFFICIAL_CLOSE_METHOD, date, 0, canonical_hash((asset, date, "close", close_value)))
            row = truth_by_key.get((asset, date))
            if not open_valid or not close_valid:
                if row is None:
                    reason = "no_truth_v2_row_for_missing_matrix_mark"
                elif row.get("state") == "MATRIX_SOURCE_CONFLICT":
                    reason = "matrix_source_conflict"
                elif not row.get("modeled_stale_candidate"):
                    reason = str(row.get("reason_code") or "truth_v2_not_modeled_candidate")
                else:
                    source = int(last_close_index[asset_index])
                    age = day - source if source >= 0 else -1
                    if source < 0:
                        reason = "no_prior_finite_positive_close"
                    elif age > MAX_STALE_AGE_TRADE_DAYS:
                        reason = "stale_age_gt_250"
                    elif any(source < action_day <= day for action_day in action_dates.get(asset, ())):
                        reason = "corporate_action_between_anchor_and_mark"
                    else:
                        stale_value = float(matrix["close"][source, asset_index])
                        source_date = dates[source]
                        evidence_id = str(row["evidence_hash"])
                        if not open_valid:
                            values["open"][day, asset_index] = stale_value
                            _set_mark(metadata["open"], day, asset_index, MODELED_STALE_METHOD, source_date, age, evidence_id)
                        if not close_valid:
                            values["close"][day, asset_index] = stale_value
                            _set_mark(metadata["close"], day, asset_index, MODELED_STALE_METHOD, source_date, age, evidence_id)
                        reason = ""
                if reason:
                    if not open_valid:
                        blockers[(asset, date, "open_pretrade")] = reason
                    if not close_valid:
                        blockers[(asset, date, "close")] = reason
            if close_valid:
                last_close_index[asset_index] = day
    return {"values": values, "metadata": metadata, "blockers": blockers}


def _trace_runs(
    bundle: Mapping[str, Any],
    prepared: Mapping[str, Any],
    surface: Mapping[str, Any],
    calculator: FeeScheduleCalculator,
) -> dict[str, Any]:
    dates = list(prepared["market"]["dates"])
    assets = list(prepared["market"]["assets"])
    signal_count = int(prepared["signal_count"])
    exact_ids = list(bundle["manifest"]["exact20_ids"])
    if len(exact_ids) != 20 or len(set(exact_ids)) != 20:
        raise CausalTraceError("exact20_identity_invalid")
    market = dict(prepared["market"])
    market["valuation_open"] = surface["values"]["open"]
    market["valuation_close"] = surface["values"]["close"]
    for point in ("open", "close"):
        for name in ("method", "source_date", "stale_age", "evidence_id"):
            market[f"valuation_{point}_{name}"] = surface["metadata"][point][name]
    run_rows: list[dict[str, Any]] = []
    held_rows: list[dict[str, Any]] = []
    terminal_counts = Counter()
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

            def observer(_index: int, _date: str, _point: str, rows: Any) -> None:
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
    pairs = [(row["factor_id"], row["scenario"]) for row in run_rows]
    expected = sorted((factor_id, scenario) for factor_id in exact_ids for scenario in SCENARIO_NAMES)
    if sorted(pairs) != expected or len(pairs) != len(set(pairs)):
        raise CausalTraceError("causal_trace_cartesian_invalid")
    held_rows.sort(key=lambda row: (row["factor_id"], row["scenario"], row["trade_date"], row["reporting_point"], row["ts_code"]))
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


def _seal_round_one_plan(
    *,
    trace: Mapping[str, Any],
    truth: Mapping[str, Any],
    matrix_content_hash: str,
    bundle_content_hash: str,
    fee_content_hash: str,
    builder_code_hash: str,
) -> dict[str, Any]:
    frontier = [tuple(item) for item in trace["round_one_frontier"]]
    if len(frontier) > MAX_UNIQUE_SECURITY_DATES:
        raise CausalTraceError("round_one_frontier_exceeds_unique_key_budget")
    truth_by_key = {(row["ts_code"], row["trade_date"]): row for row in truth["records"]}
    requests = []
    for code, date in frontier:
        row = truth_by_key.get((code, date)) or {}
        request = {
            "stage": "L1_daily_exact",
            "api_name": "daily",
            "params": {"ts_code": code, "trade_date": date},
            "fields": list(DAILY_FIELDS),
            "ts_code": code,
            "trade_date": date,
            "max_date": MAX_DATE,
            "existing_suspend_type": row.get("suspend_type", "none"),
            "empty_daily_result_semantics": "vendor_absence_only_not_suspension_proof",
            "post_empty_route": (
                "historical_anchor_or_authority_blocker"
                if row.get("suspend_type") == "S"
                else "eligible_for_dynamic_exact_suspend_l2_after_l1_apply"
            ),
        }
        request["transport_hash"] = transport_identity(
            request["api_name"], request["params"], request["fields"]
        )
        request["evidence_use_hash"] = evidence_use_identity(
            stage="L1_daily_exact",
            parent_plan_hash=trace["missing_key_root"],
            frontier_root=trace["missing_key_root"],
            transport_hash=request["transport_hash"],
        )
        requests.append(request)
    if len(requests) > MAX_LOGICAL_REQUESTS:
        raise CausalTraceError("round_one_plan_exceeds_logical_request_budget")
    semantic = {
        "schema_version": NETWORK_PLAN_SCHEMA,
        "status": "sealed_round_one_daily_only",
        "network_executed": False,
        "max_date": MAX_DATE,
        "unique_security_date_limit": MAX_UNIQUE_SECURITY_DATES,
        "logical_request_limit": MAX_LOGICAL_REQUESTS,
        "physical_attempt_limit": MAX_PHYSICAL_ATTEMPTS,
        "frontier_semantics": "round_1_first_blocker_frontier_not_total_gap_count",
        "frontier_count": len(frontier),
        "frontier_root": trace["missing_key_root"],
        "truth_v2_content_hash": truth["content_hash"],
        "matrix_content_hash": matrix_content_hash,
        "simulation_bundle_content_hash": bundle_content_hash,
        "fee_schedule_content_hash": fee_content_hash,
        "builder_code_hash": builder_code_hash,
        "requests": requests,
        "l2_requests": [],
        "l2_generation_gate": "only_after_l1_apply_and_full_truth_v2_causal_rebuild",
    }
    semantic["plan_hash"] = canonical_hash(semantic)
    return semantic


def _publish(
    *,
    output_root: Path,
    trace: Mapping[str, Any],
    plan: Mapping[str, Any],
    lineage: Mapping[str, Any],
    valuation_projection: Mapping[str, Any],
) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".task055f.causal.", dir=output_root))
    try:
        run_path = staging / "causal_run_rows.jsonl"
        held_path = staging / "held_mark_ledger.jsonl"
        plan_path = staging / "round_one_network_plan.json"
        run_path.write_text(
            "".join(json.dumps(row, sort_keys=True, separators=(",", ":"), default=str) + "\n" for row in trace["run_rows"]),
            encoding="utf-8",
        )
        held_path.write_text(
            "".join(json.dumps(row, sort_keys=True, separators=(",", ":"), default=str) + "\n" for row in trace["held_rows"]),
            encoding="utf-8",
        )
        write_artifact_sidecar(
            run_path,
            {
                "artifact_type": "task055f_causal_run_rows",
                "schema_version": "1.0",
                "producer": "task_055_f.causal",
                "created_at": "1970-01-01T00:00:00Z",
                "extra": {"record_count": len(trace["run_rows"])},
            },
        )
        write_artifact_sidecar(
            held_path,
            {
                "artifact_type": "task055f_held_mark_ledger",
                "schema_version": "1.0",
                "producer": "task_055_f.causal",
                "created_at": "1970-01-01T00:00:00Z",
                "extra": {"record_count": len(trace["held_rows"])},
            },
        )
        plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        partitions = {
            "run_rows": _partition(run_path),
            "held_marks": _partition(held_path),
            "network_plan": _partition(plan_path),
        }
        semantic = {
            "schema_version": CAUSAL_SCHEMA,
            "status": "published",
            "scope": "exact20_x_five_scenarios_fee_v2_first_terminal_frontier",
            "exact20_ids": list(trace["exact20_ids"]),
            "run_count": len(trace["run_rows"]),
            "terminal_counts": dict(trace["terminal_counts"]),
            "round_one_frontier_count": trace["round_one_frontier_count"],
            "round_one_frontier_semantics": "first_terminal_blocker_frontier_not_total_gap_count",
            "held_mark_count": trace["held_mark_count"],
            "authorized_modeled_held_mark_count": trace["authorized_modeled_held_mark_count"],
            "run_rows_root": trace["run_rows_root"],
            "held_mark_root": trace["held_mark_root"],
            "missing_key_root": trace["missing_key_root"],
            "lineage": dict(lineage),
            "valuation_projection": {
                "content_hash": valuation_projection["content_hash"],
                "generation_id": valuation_projection["generation_id"],
                "manifest_relative": (
                    "valuation_projection/generations/"
                    f"{valuation_projection['generation_id']}/valuation_projection_manifest.json"
                ),
            },
            "partitions": partitions,
        }
        content_hash = canonical_hash(semantic)
        generation_id = f"causal_frontier_{content_hash[:24]}"
        manifest = semantic | {"content_hash": content_hash, "generation_id": generation_id}
        (staging / "causal_frontier_manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        target = output_root / "generations" / generation_id
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(staging)
        else:
            os.replace(staging, target)
        _atomic_json(
            output_root / "current.json",
            {
                "generation_id": generation_id,
                "content_hash": content_hash,
                "manifest": f"generations/{generation_id}/causal_frontier_manifest.json",
            },
        )
        return manifest | {
            "manifest_path": str(target / "causal_frontier_manifest.json"),
            "valuation_projection_manifest_path": valuation_projection["manifest_path"],
        }
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


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


def _partition(path: Path) -> dict[str, Any]:
    return {"path": path.name, "sha256": sha256_file(path), "size_bytes": path.stat().st_size}


def _resolve_manifest(path: str | Path) -> Path:
    value = Path(path)
    if value.is_file():
        return value
    pointer = value / "current.json"
    if pointer.is_file():
        return value / str(json.loads(pointer.read_text(encoding="utf-8"))["manifest"])
    candidate = value / "causal_frontier_manifest.json"
    if candidate.is_file():
        return candidate
    raise CausalTraceError("causal_manifest_missing")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)

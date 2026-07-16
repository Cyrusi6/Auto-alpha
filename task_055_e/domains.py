"""Direct anchor reprojection and causal held-position domain tracing."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

from task_054_c.validators import sha256_file
from task_055_a.bundle import load_simulation_bundle, validate_simulation_bundle
from task_055_a.policy import PREREGISTERED_SCENARIOS
from task_055_a.run import SCENARIO_NAMES, prepare_simulation_inputs
from task_055_a.simulator import EventLedgerSimulator, SimulationDataBlocker
from task_055_c.evidence import MODELED, validate_truth_table

from .contracts import (
    ANCHOR_SCHEMA,
    DOMAIN_SCHEMA,
    MAX_DATE,
    MAX_STALE_AGE_TRADE_DAYS,
    NETWORK_PLAN_SCHEMA,
    PROBE_KEYS,
    SIMULATION_END,
    SIMULATION_START_FLOOR,
)
from .provenance import canonical_hash


class OfflineDomainError(RuntimeError):
    pass


def build_anchor_and_domain_generation(
    *,
    truth_manifest: str | Path,
    matrix_root: str | Path,
    simulation_bundle_manifest: str | Path,
    provenance_manifest: str | Path,
    output_root: str | Path,
    builder_code_hash: str,
) -> dict[str, Any]:
    truth = validate_truth_table(truth_manifest)
    matrix = Path(matrix_root)
    matrix_manifest = _validate_matrix(matrix)
    bundle_manifest = validate_simulation_bundle(simulation_bundle_manifest, require_ready=True)
    bundle = load_simulation_bundle(simulation_bundle_manifest)
    prepared = prepare_simulation_inputs(bundle)
    simulation_start = str(prepared["simulation_start_date"])
    simulation_end = str(prepared["market"]["dates"][-1])
    if simulation_start != SIMULATION_START_FLOOR or simulation_end != SIMULATION_END:
        raise OfflineDomainError(f"simulator_axis_contract_mismatch:{simulation_start}:{simulation_end}")
    if bundle_manifest.get("execution_cutoff") != simulation_end:
        raise OfflineDomainError("simulation_bundle_execution_cutoff_mismatch")

    codes = json.loads((matrix / "ts_codes.json").read_text(encoding="utf-8"))
    dates = json.loads((matrix / "trade_dates.json").read_text(encoding="utf-8"))
    code_index = {code: index for index, code in enumerate(codes)}
    date_index = {date: index for index, date in enumerate(dates)}
    close = np.load(matrix / "close.npy", mmap_mode="r", allow_pickle=False)
    close_valid = np.load(matrix / "close_validity.npy", mmap_mode="r", allow_pickle=False)
    anchors, prior_close_index = _reproject_anchors(
        truth=truth,
        codes=codes,
        dates=dates,
        close=close,
        close_valid=close_valid,
        code_index=code_index,
        date_index=date_index,
        simulation_start=simulation_start,
        simulation_end=simulation_end,
    )
    provenance = json.loads(Path(provenance_manifest).read_text(encoding="utf-8"))
    reconciliation_path = Path(provenance_manifest).parent / provenance["partitions"]["reconciliation"]["path"]
    reconciliation = _read_jsonl(reconciliation_path)
    causal = _trace_causal_domain(
        truth=truth,
        matrix_codes=codes,
        matrix_dates=dates,
        close=close,
        close_valid=close_valid,
        prior_close_index=prior_close_index,
        bundle=bundle,
        prepared=prepared,
    )
    domains = _domain_summary(
        truth=truth,
        reconciliation=reconciliation,
        anchors=anchors,
        causal=causal,
        simulation_start=simulation_start,
        simulation_end=simulation_end,
    )
    network_plan = _minimal_network_plan(
        reconciliation=reconciliation,
        causal=causal,
        trade_dates=dates,
        simulation_start=simulation_start,
        simulation_end=simulation_end,
    )
    return _publish(
        output_root=Path(output_root),
        anchors=anchors,
        causal_rows=causal["run_rows"],
        domains=domains,
        network_plan=network_plan,
        lineage={
            "truth_content_hash": truth["content_hash"],
            "matrix_content_hash": matrix_manifest["content_hash"],
            "simulation_bundle_hash": bundle_manifest["content_hash"],
            "provenance_content_hash": provenance["content_hash"],
            "builder_code_hash": builder_code_hash,
        },
    )


def validate_anchor_and_domain_generation(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != DOMAIN_SCHEMA or manifest.get("status") != "published":
        raise OfflineDomainError("offline_domain_manifest_invalid")
    for entry in (manifest.get("partitions") or {}).values():
        artifact = manifest_path.parent / str(entry.get("path"))
        if not artifact.is_file() or sha256_file(artifact) != entry.get("sha256"):
            raise OfflineDomainError("offline_domain_partition_sha_mismatch")
    semantic = {key: value for key, value in manifest.items() if key not in {"content_hash", "generation_id"}}
    if canonical_hash(semantic) != manifest.get("content_hash"):
        raise OfflineDomainError("offline_domain_content_hash_mismatch")
    plan = json.loads((manifest_path.parent / manifest["partitions"]["network_plan"]["path"]).read_text(encoding="utf-8"))
    if plan.get("network_executed") is not False or plan.get("max_date") != MAX_DATE:
        raise OfflineDomainError("offline_network_plan_boundary_invalid")
    return manifest | {"manifest_path": str(manifest_path)}


def _reproject_anchors(
    *,
    truth: Mapping[str, Any],
    codes: list[str],
    dates: list[str],
    close: np.ndarray,
    close_valid: np.ndarray,
    code_index: Mapping[str, int],
    date_index: Mapping[str, int],
    simulation_start: str,
    simulation_end: str,
) -> tuple[list[dict[str, Any]], np.ndarray]:
    prior = np.full(close_valid.shape, -1, dtype=np.int32)
    last = np.full(len(codes), -1, dtype=np.int32)
    for date_pos in range(len(dates)):
        prior[:, date_pos] = last
        valid = np.asarray(close_valid[:, date_pos], dtype=bool)
        last[valid] = date_pos
    rows: list[dict[str, Any]] = []
    for truth_row in truth["records"]:
        if not truth_row.get("valuation_domain_intersection") or truth_row.get("state") != MODELED:
            continue
        code = str(truth_row["ts_code"])
        date = str(truth_row["trade_date"])
        if code not in code_index or date not in date_index:
            continue
        asset = code_index[code]
        day = date_index[date]
        prior_day = int(prior[asset, day])
        if bool(close_valid[asset, day]) and truth_row.get("daily_bar") == "absent":
            cause = "matrix_truth_conflict"
        elif prior_day < 0:
            cause = "no_historical_close"
        elif day - prior_day > MAX_STALE_AGE_TRADE_DAYS:
            cause = "stale_age_gt_250"
        elif not truth_row.get("listed", True) or not truth_row.get("active", True):
            cause = "lifecycle_or_listing"
        elif truth_row.get("lifecycle_corporate_action_conflict"):
            cause = "corporate_action_discontinuity"
        else:
            continue
        row = {
            "ts_code": code,
            "trade_date": date,
            "cause": cause,
            "prior_close_date": dates[prior_day] if prior_day >= 0 else None,
            "prior_close_value": float(close[asset, prior_day]) if prior_day >= 0 else None,
            "stale_age_trade_days": day - prior_day if prior_day >= 0 else None,
            "simulation_axis": simulation_start <= date <= simulation_end,
            "domain": (
                "pre_simulation_or_warmup"
                if date < simulation_start
                else "post_simulation_diagnostic"
                if date > simulation_end
                else "simulator_axis"
            ),
            "evidence_hash": truth_row["evidence_hash"],
        }
        row["row_hash"] = canonical_hash(row)
        rows.append(row)
    rows.sort(key=lambda row: (row["ts_code"], row["trade_date"]))
    return rows, prior


def _trace_causal_domain(
    *,
    truth: Mapping[str, Any],
    matrix_codes: list[str],
    matrix_dates: list[str],
    close: np.ndarray,
    close_valid: np.ndarray,
    prior_close_index: np.ndarray,
    bundle: Mapping[str, Any],
    prepared: Mapping[str, Any],
) -> dict[str, Any]:
    assets = list(prepared["market"]["assets"])
    dates = list(prepared["market"]["dates"])
    signal_count = int(prepared["signal_count"])
    asset_index = {asset: index for index, asset in enumerate(assets)}
    matrix_asset = {asset: index for index, asset in enumerate(matrix_codes)}
    matrix_date = {date: index for index, date in enumerate(matrix_dates)}
    truth_by_key = {(row["ts_code"], row["trade_date"]): row for row in truth["records"]}
    valuation_open = np.asarray(prepared["market"]["valuation_open"], dtype=float).copy()
    valuation_close = np.asarray(prepared["market"]["valuation_close"], dtype=float).copy()
    authorized_stale_points = 0
    for date_pos, date in enumerate(dates):
        matrix_day = matrix_date[date]
        for asset, local_asset in asset_index.items():
            row = truth_by_key.get((asset, date))
            if not row or row.get("state") != MODELED or row.get("daily_bar") != "absent":
                continue
            source = int(prior_close_index[matrix_asset[asset], matrix_day])
            age = matrix_day - source if source >= 0 else -1
            if source >= 0 and 0 < age <= MAX_STALE_AGE_TRADE_DAYS and bool(close_valid[matrix_asset[asset], source]):
                value = float(close[matrix_asset[asset], source])
                valuation_open[date_pos, local_asset] = value
                valuation_close[date_pos, local_asset] = value
                authorized_stale_points += 2
    market = dict(prepared["market"])
    market["valuation_open"] = valuation_open
    market["valuation_close"] = valuation_close
    run_rows: list[dict[str, Any]] = []
    held_unique: set[tuple[int, int, int]] = set()
    missing_unique: set[tuple[int, int, int]] = set()
    held_observations = 0
    missing_observations = 0
    exact_ids = list(bundle["manifest"]["exact20_ids"])
    for factor_id in exact_ids:
        values = np.asarray(prepared["factor_values"][factor_id])
        valid = np.asarray(prepared["factor_validity"][factor_id], dtype=bool)
        scores = np.full((len(dates), len(assets)), np.nan, dtype=float)
        scores[:signal_count] = values.T
        selection = np.zeros((len(dates), len(assets)), dtype=bool)
        selection[:signal_count] = valid.T & prepared["signal_common"]
        for scenario in SCENARIO_NAMES:
            run_held = 0
            run_missing: list[dict[str, Any]] = []

            def observer(index: int, date: str, point: str, held: Mapping[str, int], prices: np.ndarray) -> None:
                nonlocal run_held, held_observations, missing_observations
                point_code = 0 if point == "open_pretrade" else 1
                for asset, shares in held.items():
                    local_asset = asset_index[asset]
                    held_unique.add((local_asset, index, point_code))
                    run_held += 1
                    held_observations += 1
                    price = float(prices[local_asset])
                    if not math.isfinite(price) or price <= 0:
                        missing_unique.add((local_asset, index, point_code))
                        missing_observations += 1
                        run_missing.append(
                            {
                                "ts_code": asset,
                                "trade_date": date,
                                "reporting_point": point,
                                "shares": int(shares),
                            }
                        )

            terminal = "diagnostic_completed_without_valuation_blocker"
            blocker = None
            try:
                EventLedgerSimulator(PREREGISTERED_SCENARIOS[scenario]).run(
                    market,
                    scores,
                    masks={"select": selection, "buy": prepared["buy"], "sell": prepared["sell"]},
                    corporate_actions=prepared["corporate_actions"],
                    diagnostic_mark_observer=observer,
                )
            except SimulationDataBlocker as exc:
                terminal = "causal_valuation_blocked"
                blocker = _parse_mark_blocker(str(exc))
            except (ValueError, RuntimeError) as exc:
                terminal = "causal_infrastructure_blocked"
                blocker = {"detail": str(exc)}
            run = {
                "factor_id": factor_id,
                "scenario": scenario,
                "terminal_state": terminal,
                "held_reporting_point_observations_before_terminal": run_held,
                "missing_mark_observations": run_missing,
                "blocker": blocker,
            }
            run["row_hash"] = canonical_hash(run)
            run_rows.append(run)
    return {
        "run_rows": run_rows,
        "run_count": len(run_rows),
        "terminal_counts": dict(sorted(Counter(row["terminal_state"] for row in run_rows).items())),
        "held_reporting_point_observations": held_observations,
        "unique_held_reporting_points": len(held_unique),
        "missing_mark_observations": missing_observations,
        "unique_missing_held_reporting_points": len(missing_unique),
        "unique_missing_keys": sorted({(assets[asset], dates[date]) for asset, date, _ in missing_unique}),
        "authorized_modeled_stale_reporting_points": authorized_stale_points,
        "causal_scope": "exact20_x_five_scenarios_proven_prefix_until_first_terminal_blocker",
    }


def _domain_summary(
    *,
    truth: Mapping[str, Any],
    reconciliation: list[dict[str, Any]],
    anchors: list[dict[str, Any]],
    causal: Mapping[str, Any],
    simulation_start: str,
    simulation_end: str,
) -> dict[str, Any]:
    static_rows = [row for row in reconciliation if simulation_start <= row["trade_date"] <= simulation_end]
    static_unresolved = [
        row
        for row in static_rows
        if row["classification"] not in {"existing_valid_daily_bar", "existing_positive_suspend_event"}
    ]
    anchor_counts = Counter(row["cause"] for row in anchors)
    return {
        "schema_version": DOMAIN_SCHEMA,
        "full_historical_research_quality_domain": {
            "truth_cell_count": int(truth["record_count"]),
            "remediation_key_count": len(reconciliation),
            "classification_counts": dict(sorted(Counter(row["classification"] for row in reconciliation).items())),
            "closed": False,
            "readiness_effect": "future_research_data_ready",
        },
        "static_simulator_axis_data_domain": {
            "start_date": simulation_start,
            "end_date": simulation_end,
            "remediation_key_count": len(static_rows),
            "nonterminal_evidence_key_count": len(static_unresolved),
            "anchor_cause_counts": dict(sorted(anchor_counts.items())),
            "readiness_effect": "diagnostic_only_not_simulator_gate",
        },
        "causal_held_position_valuation_domain": {
            "scope": causal["causal_scope"],
            "run_count": causal["run_count"],
            "terminal_counts": causal["terminal_counts"],
            "held_reporting_point_observations": causal["held_reporting_point_observations"],
            "unique_held_reporting_points": causal["unique_held_reporting_points"],
            "missing_mark_observations": causal["missing_mark_observations"],
            "unique_missing_held_reporting_points": causal["unique_missing_held_reporting_points"],
            "remaining_security_date_count": len(causal["unique_missing_keys"]),
            "closed": causal["unique_missing_held_reporting_points"] == 0
            and not causal["terminal_counts"].get("causal_infrastructure_blocked"),
            "readiness_effect": "continuous_portfolio_valuation_ready",
        },
        "anchor_schema_version": ANCHOR_SCHEMA,
        "anchor_count": len(anchors),
        "anchor_cause_counts": dict(sorted(anchor_counts.items())),
    }


def _minimal_network_plan(
    *,
    reconciliation: list[dict[str, Any]],
    causal: Mapping[str, Any],
    trade_dates: list[str],
    simulation_start: str,
    simulation_end: str,
) -> dict[str, Any]:
    causal_keys = set(tuple(key) for key in causal["unique_missing_keys"])
    reconciliation_by_key = {(row["ts_code"], row["trade_date"]): row for row in reconciliation}
    remaining = [
        reconciliation_by_key.get(key)
        or {"ts_code": key[0], "trade_date": key[1], "classification": "genuinely_not_found_offline"}
        for key in sorted(causal_keys)
    ]
    by_stock: dict[str, list[str]] = defaultdict(list)
    for row in remaining:
        by_stock[row["ts_code"]].append(row["trade_date"])
    date_index = {date: index for index, date in enumerate(trade_dates)}
    episodes = []
    for code, dates in sorted(by_stock.items()):
        ordered = sorted(set(dates), key=date_index.get)
        current = []
        for date in ordered:
            if current and date_index[date] != date_index[current[-1]] + 1:
                episodes.append({"ts_code": code, "start_date": current[0], "end_date": current[-1], "cell_count": len(current)})
                current = []
            current.append(date)
        if current:
            episodes.append({"ts_code": code, "start_date": current[0], "end_date": current[-1], "cell_count": len(current)})
    daily_requests = [
        {"api_name": "daily", "ts_code": code, "start_date": min(dates), "end_date": max(dates)}
        for code, dates in sorted(by_stock.items())
    ]
    suspend_requests = [
        {"api_name": "suspend_d", "ts_code": row["ts_code"], "start_date": row["start_date"], "end_date": row["end_date"]}
        for row in episodes
        if any(
            item["classification"] not in {"existing_positive_suspend_event", "existing_valid_daily_bar"}
            for item in remaining
            if item["ts_code"] == row["ts_code"] and row["start_date"] <= item["trade_date"] <= row["end_date"]
        )
    ]
    probes = []
    for key in PROBE_KEYS:
        row = reconciliation_by_key.get(key)
        probes.append(
            {
                "ts_code": key[0],
                "trade_date": key[1],
                "offline_classification": row.get("classification") if row else "outside_current_remediation_key_set",
                "local_authoritative_evidence": bool(row and row.get("classification") in {"existing_valid_daily_bar", "existing_positive_suspend_event"}),
            }
        )
    solved_direct = sum(row["classification"] in {"existing_valid_daily_bar", "existing_positive_suspend_event"} for row in reconciliation)
    raw_repair = sum(row.get("formal_raw_repair_eligible") is True for row in reconciliation)
    plan = {
        "schema_version": NETWORK_PLAN_SCHEMA,
        "status": "sealed_offline_only",
        "network_executed": False,
        "credential_required_for_offline_stage": False,
        "max_date": MAX_DATE,
        "simulation_axis": {"start_date": simulation_start, "end_date": simulation_end},
        "existing_data_directly_resolved": solved_direct,
        "offline_raw_repair_resolved": raw_repair,
        "simulator_held_domain_remaining_security_dates": len(causal_keys),
        "remaining_stock_count": len(by_stock),
        "remaining_date_count": len({date for dates in by_stock.values() for date in dates}),
        "remaining_episode_count": len(episodes),
        "estimated_daily_request_count": len(daily_requests),
        "estimated_suspend_d_request_count": len(suspend_requests),
        "daily_requests": daily_requests,
        "suspend_d_requests": suspend_requests,
        "episodes": episodes,
        "fixed_probes": probes,
        "next_stage_gate": "credential_and_single_request_canary_only_if_remaining_security_dates_positive",
    }
    plan["plan_hash"] = canonical_hash(plan)
    return plan


def _publish(
    *,
    output_root: Path,
    anchors: list[dict[str, Any]],
    causal_rows: list[dict[str, Any]],
    domains: Mapping[str, Any],
    network_plan: Mapping[str, Any],
    lineage: Mapping[str, Any],
) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".task055e.domains.", dir=output_root))
    try:
        _write_jsonl(staging / "anchor_reprojection.jsonl", anchors)
        _write_jsonl(staging / "causal_run_frontiers.jsonl", causal_rows)
        _write_json(staging / "valuation_domains.json", domains)
        _write_json(staging / "minimal_network_plan.json", network_plan)
        partitions = {
            "anchors": _partition(staging / "anchor_reprojection.jsonl"),
            "causal_runs": _partition(staging / "causal_run_frontiers.jsonl"),
            "domains": _partition(staging / "valuation_domains.json"),
            "network_plan": _partition(staging / "minimal_network_plan.json"),
        }
        semantic = {
            "schema_version": DOMAIN_SCHEMA,
            "status": "published",
            "network_accessed": False,
            "prospective_holdout_accessed": False,
            "lineage": dict(lineage),
            "anchor_count": len(anchors),
            "anchor_cause_counts": domains["anchor_cause_counts"],
            "causal_terminal_counts": domains["causal_held_position_valuation_domain"]["terminal_counts"],
            "causal_remaining_security_dates": domains["causal_held_position_valuation_domain"]["remaining_security_date_count"],
            "partitions": partitions,
        }
        content_hash = canonical_hash(semantic)
        generation_id = f"offline_domains_{content_hash[:24]}"
        manifest = semantic | {"content_hash": content_hash, "generation_id": generation_id}
        _write_json(staging / "domain_manifest.json", manifest)
        target = output_root / "generations" / generation_id
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(staging)
        else:
            os.replace(staging, target)
        _atomic_json(output_root / "current.json", {"generation_id": generation_id, "content_hash": content_hash, "manifest": f"generations/{generation_id}/domain_manifest.json"})
        return manifest | {"manifest_path": str(target / "domain_manifest.json"), "generation_dir": str(target)}
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _parse_mark_blocker(detail: str) -> dict[str, Any]:
    match = re.search(r"valuation_(open|close)_blocked:([0-9]{8}):[0-9]+:cannot mark held asset ([0-9]{6}\.(?:SH|SZ|BJ))", detail)
    if not match:
        return {"detail": detail}
    return {"reporting_point": match.group(1), "trade_date": match.group(2), "ts_code": match.group(3), "detail": detail}


def _validate_matrix(root: Path) -> dict[str, Any]:
    manifest = json.loads((root / "task_052a_strict_matrix_manifest.json").read_text(encoding="utf-8"))
    for name in ("close.npy", "close_validity.npy", "ts_codes.json", "trade_dates.json"):
        expected = (manifest.get("partition_sha256") or {}).get(name)
        if expected and sha256_file(root / name) != expected:
            raise OfflineDomainError(f"matrix_partition_sha_mismatch:{name}")
    if not manifest.get("content_hash") or not manifest.get("stock_axis_hash") or not manifest.get("date_axis_hash"):
        raise OfflineDomainError("matrix_lineage_incomplete")
    return manifest


def _partition(path: Path) -> dict[str, Any]:
    return {"path": path.name, "sha256": sha256_file(path), "size_bytes": path.stat().st_size}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":"), default=str) + "\n")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    _write_json(temporary, payload)
    os.replace(temporary, path)

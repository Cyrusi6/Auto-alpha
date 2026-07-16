"""Native exact-20 × five-scenario simulator replay for Task 055-F."""

from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from task_055_a.artifacts import publish_simulation_run, resume_simulation_run
from task_055_a.bundle import load_simulation_bundle, validate_simulation_bundle
from task_055_a.policy import PREREGISTERED_SCENARIOS
from task_055_a.run import SCENARIO_NAMES, prepare_simulation_inputs
from task_055_a.simulator import EventLedgerSimulator, SimulationDataBlocker
from task_055_a.verifier import verify_simulation_run

from .causal import validate_causal_frontier
from .fees import (
    ALL_COMPONENTS,
    MODELED_COMPONENTS,
    FeeScheduleCalculator,
    validate_fee_schedule_v2,
)
from .read_ledger import canonical_hash, sha256_file
from .valuation import METHOD_NAMES, load_valuation_projection, validate_valuation_projection


REPLAY_GENERATION_SCHEMA = "task055f_native_replay_generation_v1"
REPLAY_FINAL_SCHEMA = "task055f_native_replay_verification_v1"


class NativeReplayError(RuntimeError):
    pass


def run_native_replay(
    *,
    causal_manifest: str | Path,
    simulation_bundle_manifest: str | Path,
    fee_schedule_manifest: str | Path,
    output_root: str | Path,
) -> dict[str, Any]:
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    causal = validate_causal_frontier(causal_manifest)
    if int(causal.get("round_one_frontier_count") or 0) != 0:
        raise NativeReplayError("native_replay_causal_frontier_not_closed")
    if causal.get("terminal_counts") != {"causal_trace_completed": 100}:
        raise NativeReplayError("native_replay_causal_terminal_gate_invalid")
    bundle = validate_simulation_bundle(simulation_bundle_manifest, require_ready=True)
    exact_ids = list(bundle.get("exact20_ids") or ())
    if len(exact_ids) != 20 or len(set(exact_ids)) != 20 or exact_ids != list(causal.get("exact20_ids") or ()):
        raise NativeReplayError("native_replay_exact20_identity_invalid")
    projection = validate_valuation_projection(causal["valuation_projection"]["manifest_path"])
    if projection.get("status") != "ready":
        raise NativeReplayError("native_replay_valuation_projection_not_ready")
    fee = _clone_fee_schedule(fee_schedule_manifest, root / "fee_schedule")
    primary = _execute_generation(
        root=root / "replay" / "primary",
        role="primary_uncached",
        exact_ids=exact_ids,
        causal=causal,
        bundle_manifest=simulation_bundle_manifest,
        fee_manifest=fee["manifest_path"],
        projection_manifest=projection["manifest_path"],
        require_uncached=True,
    )
    sibling = _execute_generation(
        root=root / "replay" / "sibling",
        role="sibling_uncached",
        exact_ids=exact_ids,
        causal=causal,
        bundle_manifest=simulation_bundle_manifest,
        fee_manifest=fee["manifest_path"],
        projection_manifest=projection["manifest_path"],
        require_uncached=True,
    )
    if primary["truth_root"] != sibling["truth_root"]:
        raise NativeReplayError("native_replay_primary_sibling_truth_mismatch")
    resumed = _execute_generation(
        root=root / "replay" / "primary",
        role="primary_uncached",
        exact_ids=exact_ids,
        causal=causal,
        bundle_manifest=simulation_bundle_manifest,
        fee_manifest=fee["manifest_path"],
        projection_manifest=projection["manifest_path"],
        require_uncached=False,
    )
    if resumed.get("resume_hit_count") != 100 or resumed["truth_root"] != primary["truth_root"]:
        raise NativeReplayError("native_replay_resume_invalid")
    verified = verify_native_replay_tree(root)
    if verified["primary_truth_root"] != primary["truth_root"]:
        raise NativeReplayError("native_replay_final_verifier_root_mismatch")
    return verified


def verify_native_replay_tree(output_root: str | Path) -> dict[str, Any]:
    root = Path(output_root)
    generations: dict[str, dict[str, Any]] = {}
    for role in ("primary", "sibling"):
        pointer = _read_json(root / "replay" / role / "current.json")
        manifest_path = root / "replay" / role / str(pointer.get("manifest") or "")
        manifest = _read_json(manifest_path)
        if manifest.get("schema_version") != REPLAY_GENERATION_SCHEMA or manifest.get("status") != "complete":
            raise NativeReplayError(f"native_replay_generation_invalid:{role}")
        semantic = {key: value for key, value in manifest.items() if key not in {"content_hash", "generation_id"}}
        if canonical_hash(semantic) != manifest.get("content_hash"):
            raise NativeReplayError(f"native_replay_generation_content_hash_mismatch:{role}")
        rows = list(manifest.get("runs") or ())
        pairs = [(row.get("factor_id"), row.get("scenario")) for row in rows]
        expected = sorted(
            (factor_id, scenario)
            for factor_id in manifest.get("exact20_ids") or ()
            for scenario in SCENARIO_NAMES
        )
        if sorted(pairs) != expected or len(rows) != 100 or len(set(pairs)) != 100:
            raise NativeReplayError(f"native_replay_generation_cartesian_invalid:{role}")
        verified_rows = []
        for row in rows:
            run_root = manifest_path.parents[2] / str(row["path"])
            verified = verify_task055f_simulation_run(run_root)
            if verified["truth_hash"] != row.get("truth_hash") or verified["content_hash"] != row.get("content_hash"):
                raise NativeReplayError(f"native_replay_run_hash_mismatch:{role}:{row.get('factor_id')}:{row.get('scenario')}")
            verified_rows.append(verified)
        truth_root = canonical_hash([row["truth_hash"] for row in rows])
        if truth_root != manifest.get("truth_root"):
            raise NativeReplayError(f"native_replay_truth_root_mismatch:{role}")
        generations[role] = {"truth_root": truth_root, "verified_run_count": len(verified_rows)}
    if generations["primary"]["truth_root"] != generations["sibling"]["truth_root"]:
        raise NativeReplayError("native_replay_ab_truth_mismatch")
    primary_manifest = _load_generation(root / "replay" / "primary")
    resume_manifest = _load_resume(root / "replay" / "primary")
    if (
        int(resume_manifest.get("resume_hit_count") or 0) != 100
        or resume_manifest.get("truth_root") != primary_manifest.get("truth_root")
        or resume_manifest.get("generation_content_hash") != primary_manifest.get("content_hash")
    ):
        raise NativeReplayError("native_replay_resume_evidence_missing")
    semantic = {
        "schema_version": REPLAY_FINAL_SCHEMA,
        "status": "verified",
        "terminal_count": 100,
        "primary_truth_root": generations["primary"]["truth_root"],
        "sibling_truth_root": generations["sibling"]["truth_root"],
        "resume_truth_root": primary_manifest["truth_root"],
        "resume_hit_count": 100,
        "certification_ready": False,
        "portfolio_ready": False,
        "paper_ready": False,
        "live_ready": False,
    }
    semantic["verification_hash"] = canonical_hash(semantic)
    return semantic


def verify_task055f_simulation_run(path: str | Path) -> dict[str, Any]:
    verified = verify_simulation_run(path)
    generation = Path(verified["root"])
    reference = _read_json(generation / "fee_schedule_reference.json")
    fee_path = _locate_generation_artifact(
        generation,
        directory="fee_schedule",
        generation_id=str(reference.get("generation_id") or ""),
        manifest_name="fee_schedule_v2_manifest.json",
    )
    fee = validate_fee_schedule_v2(fee_path)
    if fee.get("content_hash") != reference.get("content_hash"):
        raise NativeReplayError("native_replay_fee_reference_mismatch")
    projection_reference = _read_json(generation / "valuation_projection_reference.json")
    projection_path = _locate_generation_artifact(
        generation,
        directory="valuation_projection",
        generation_id=str(projection_reference.get("generation_id") or ""),
        manifest_name="valuation_projection_manifest.json",
    )
    projection = load_valuation_projection(projection_path)
    if projection.get("content_hash") != projection_reference.get("content_hash"):
        raise NativeReplayError("native_replay_projection_reference_mismatch")
    spec = _read_json(generation / "spec.json")
    axes = _read_json(generation / "axes.json")
    fills = _read_jsonl(generation / "fills.jsonl")
    held_marks = _read_jsonl(generation / "held_marks.jsonl")
    _verify_fill_fees(fills, axes["dates"], spec["policy"], fee)
    _verify_held_marks(held_marks, projection)
    return verified | {
        "fee_schedule_content_hash": fee["content_hash"],
        "valuation_projection_content_hash": projection["content_hash"],
        "held_mark_root": canonical_hash(held_marks),
    }


def _execute_generation(
    *,
    root: Path,
    role: str,
    exact_ids: Sequence[str],
    causal: Mapping[str, Any],
    bundle_manifest: str | Path,
    fee_manifest: str | Path,
    projection_manifest: str | Path,
    require_uncached: bool,
) -> dict[str, Any]:
    identity = canonical_hash(
        {
            "role": role,
            "causal": causal["content_hash"],
            "bundle": causal["lineage"]["simulation_bundle_content_hash"],
            "fee": causal["lineage"]["fee_schedule_content_hash"],
            "projection": causal["valuation_projection"]["content_hash"],
            "exact20": list(exact_ids),
        }
    )
    generation_id = f"native_replay_{identity[:24]}"
    target = root / "generations" / generation_id
    manifest_path = target / "native_replay_generation.json"
    if manifest_path.is_file():
        if require_uncached:
            raise NativeReplayError(f"native_replay_uncached_generation_exists:{role}")
        manifest = _read_json(manifest_path)
        resume_hits = 0
        for row in manifest.get("runs") or ():
            run_root = root / str(row["path"])
            resumed = resume_simulation_run(
                run_root,
                expected_spec_hash=str(row["spec_hash"]),
                expected_input_lineage_hash=str(row["input_lineage_hash"]),
            )
            verify_task055f_simulation_run(run_root)
            if resumed.get("resume_hit"):
                resume_hits += 1
        resume = _publish_resume(
            root / "resume",
            {
                "schema_version": "task055f_native_replay_resume_v1",
                "status": "verified",
                "generation_id": generation_id,
                "generation_content_hash": manifest["content_hash"],
                "truth_root": manifest["truth_root"],
                "resume_hit_count": resume_hits,
                "run_count": len(manifest.get("runs") or ()),
            },
        )
        return manifest | {
            "manifest_path": str(manifest_path),
            "resume_hit_count": resume_hits,
            "resume_manifest_path": resume["manifest_path"],
        }
    if not require_uncached:
        raise NativeReplayError("native_replay_resume_generation_missing")
    loaded = load_simulation_bundle(bundle_manifest)
    prepared = prepare_simulation_inputs(loaded)
    projection = load_valuation_projection(
        projection_manifest,
        dates=prepared["market"]["dates"],
        assets=prepared["market"]["assets"],
    )
    market = _market_with_projection(prepared["market"], projection)
    calculator = FeeScheduleCalculator(fee_manifest)
    rows: list[dict[str, Any]] = []
    signal_count = int(prepared["signal_count"])
    date_count = len(market["dates"])
    asset_count = len(market["assets"])
    for factor_id in exact_ids:
        values = np.asarray(prepared["factor_values"][factor_id])
        validity = np.asarray(prepared["factor_validity"][factor_id], dtype=bool)
        if values.shape != (asset_count, signal_count) or validity.shape != values.shape:
            raise NativeReplayError(f"native_replay_factor_shape_invalid:{factor_id}")
        scores = np.full((date_count, asset_count), np.nan, dtype=float)
        scores[:signal_count] = values.T
        selection = np.zeros((date_count, asset_count), dtype=bool)
        selection[:signal_count] = validity.T & prepared["signal_common"]
        factor_entries = prepared["bundle_manifest"].get("artifacts") or {}
        for scenario in SCENARIO_NAMES:
            held_marks: list[dict[str, Any]] = []

            def observer(_index: int, _date: str, _point: str, marks: Sequence[Mapping[str, Any]]) -> None:
                held_marks.extend(dict(row) for row in marks)

            try:
                result = EventLedgerSimulator(
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
                raise NativeReplayError(f"native_replay_data_blocked:{factor_id}:{scenario}:{exc}") from exc
            run_spec = {
                "factor_id": factor_id,
                "scenario": scenario,
                "terminal_state": "retrospective_modeled_completed",
                "policy": PREREGISTERED_SCENARIOS[scenario].to_dict(),
            }
            input_lineage = {
                "bundle_content_hash": prepared["bundle_manifest"].get("content_hash"),
                "causal_content_hash": causal["content_hash"],
                "valuation_projection_content_hash": projection["content_hash"],
                "fee_schedule_content_hash": calculator.schedule["content_hash"],
                "factor_values_sha256": (factor_entries.get(f"factor:{factor_id}:values") or {}).get("sha256"),
                "factor_validity_sha256": (factor_entries.get(f"factor:{factor_id}:validity") or {}).get("sha256"),
            }
            if not input_lineage["factor_values_sha256"] or not input_lineage["factor_validity_sha256"]:
                raise NativeReplayError(f"native_replay_factor_lineage_missing:{factor_id}")
            relative = Path("runs") / factor_id / scenario
            published = publish_simulation_run(
                output_root=root / relative,
                result=result,
                spec=run_spec,
                input_lineage=input_lineage,
                market=market,
                benchmark=prepared["benchmark"],
                valuation_projection_manifest=projection["manifest_path"],
                held_marks=held_marks,
                fee_schedule_manifest=calculator.schedule["manifest_path"],
                allow_resume=False,
            )
            verified = verify_task055f_simulation_run(root / relative)
            if verified["truth_hash"] != published["truth_hash"]:
                raise NativeReplayError("native_replay_run_verification_mismatch")
            rows.append(
                {
                    "factor_id": factor_id,
                    "scenario": scenario,
                    "terminal_state": "retrospective_modeled_completed",
                    "path": str(relative),
                    "truth_hash": verified["truth_hash"],
                    "content_hash": verified["content_hash"],
                    "spec_hash": verified["spec_hash"],
                    "input_lineage_hash": verified["input_lineage_hash"],
                    "held_mark_root": verified["held_mark_root"],
                }
            )
    truth_root = canonical_hash([row["truth_hash"] for row in rows])
    semantic = {
        "schema_version": REPLAY_GENERATION_SCHEMA,
        "status": "complete",
        "role": role,
        "exact20_ids": list(exact_ids),
        "terminal_count": len(rows),
        "truth_root": truth_root,
        "causal_content_hash": causal["content_hash"],
        "valuation_projection_content_hash": projection["content_hash"],
        "fee_schedule_content_hash": calculator.schedule["content_hash"],
        "runs": rows,
    }
    content_hash = canonical_hash(semantic)
    manifest = semantic | {"content_hash": content_hash, "generation_id": generation_id}
    target.mkdir(parents=True, exist_ok=False)
    _atomic_json(manifest_path, manifest)
    _atomic_json(
        root / "current.json",
        {
            "generation_id": generation_id,
            "content_hash": content_hash,
            "manifest": f"generations/{generation_id}/native_replay_generation.json",
        },
    )
    return manifest | {"manifest_path": str(manifest_path)}


def _market_with_projection(market: Mapping[str, Any], projection: Mapping[str, Any]) -> dict[str, Any]:
    dates = list(market["dates"])
    result = dict(market)
    result["valuation_open"] = projection["valuation_open"]
    result["valuation_close"] = projection["valuation_close"]
    for point in ("open", "close"):
        method_codes = projection[f"{point}_method"]
        source_indices = projection[f"{point}_source_date"]
        evidence = projection[f"{point}_evidence_id"]
        result[f"valuation_{point}_method"] = np.vectorize(
            lambda value: METHOD_NAMES[int(value)], otypes=[object]
        )(method_codes)
        result[f"valuation_{point}_source_date"] = np.vectorize(
            lambda value: dates[int(value)] if int(value) >= 0 else "", otypes=[object]
        )(source_indices)
        result[f"valuation_{point}_stale_age"] = projection[f"{point}_stale_age"]
        result[f"valuation_{point}_evidence_id"] = np.vectorize(
            lambda value: bytes(value).decode("ascii"), otypes=[object]
        )(evidence)
    return result


def _verify_fill_fees(
    fills: Sequence[Mapping[str, Any]],
    dates: Sequence[str],
    policy: Mapping[str, Any],
    schedule: Mapping[str, Any],
) -> None:
    for fill in fills:
        index = int(fill.get("execution_index", -1))
        if index < 0 or index >= len(dates):
            raise NativeReplayError("native_replay_fill_date_index_invalid")
        date = str(dates[index])
        market = "SSE" if str(fill["asset"]).endswith(".SH") else "SZSE" if str(fill["asset"]).endswith(".SZ") else ""
        if not market:
            raise NativeReplayError("native_replay_fill_market_invalid")
        expected = _independent_fee_components(
            schedule,
            date=date,
            market=market,
            side=str(fill["side"]),
            notional=float(fill["notional"]),
            shares=int(fill["filled_shares"]),
            zero_all_costs=bool(policy.get("zero_all_costs")),
            modeled_multiplier=float(policy.get("modeled_cost_multiplier", 1.0)),
        )
        for component, value in expected.items():
            if abs(float(fill.get(component, 0.0)) - value) > 0.005:
                raise NativeReplayError(f"native_replay_fill_fee_mismatch:{fill.get('fill_id')}:{component}")


def _independent_fee_components(
    schedule: Mapping[str, Any],
    *,
    date: str,
    market: str,
    side: str,
    notional: float,
    shares: int,
    zero_all_costs: bool,
    modeled_multiplier: float,
) -> dict[str, float]:
    if zero_all_costs:
        return {component: 0.0 for component in sorted(ALL_COMPONENTS)} | {"total_cost": 0.0}
    result: dict[str, float] = {}
    for component in sorted(ALL_COMPONENTS):
        matches = [
            row
            for row in schedule["rules"]
            if row["component"] == component
            and row["market"] == market
            and row["side"] == side
            and row["effective_start"] <= date <= row["effective_end"]
        ]
        if len(matches) != 1:
            raise NativeReplayError(f"native_replay_fee_rule_match_invalid:{component}:{market}:{side}:{date}")
        rule = matches[0]
        base = notional if rule["basis"] == "notional" else float(shares)
        value = 0.0 if rule["explicit_zero"] else max(float(rule["minimum_cny"]), base * float(rule["rate"]))
        if component in MODELED_COMPONENTS:
            value *= modeled_multiplier
        result[component] = _round(value, str(rule["rounding"]))
    result["total_cost"] = _round(sum(result.values()), "cent_half_up")
    return result


def _verify_held_marks(rows: Sequence[Mapping[str, Any]], projection: Mapping[str, Any]) -> None:
    dates = {date: index for index, date in enumerate(projection["dates"])}
    assets = {asset: index for index, asset in enumerate(projection["assets"])}
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        key = (str(row["trade_date"]), str(row["ts_code"]), str(row["reporting_point"]))
        if key in seen or key[0] not in dates or key[1] not in assets or int(row.get("shares") or 0) <= 0:
            raise NativeReplayError("native_replay_held_mark_identity_invalid")
        seen.add(key)
        point = "open" if key[2] == "open_pretrade" else "close" if key[2] == "close" else ""
        if not point:
            raise NativeReplayError("native_replay_held_mark_reporting_point_invalid")
        day, asset = dates[key[0]], assets[key[1]]
        method = METHOD_NAMES[int(projection[f"{point}_method"][day, asset])]
        source_index = int(projection[f"{point}_source_date"][day, asset])
        evidence = bytes(projection[f"{point}_evidence_id"][day, asset]).decode("ascii")
        if (
            str(row.get("method")) != method
            or str(row.get("source_date")) != projection["dates"][source_index]
            or int(row.get("stale_age_trade_days")) != int(projection[f"{point}_stale_age"][day, asset])
            or str(row.get("evidence_id")) != evidence
            or abs(float(row.get("mark_price")) - float(projection[f"valuation_{point}"][day, asset])) > 1e-8
        ):
            raise NativeReplayError("native_replay_held_mark_projection_mismatch")


def _clone_fee_schedule(source: str | Path, target_root: Path) -> dict[str, Any]:
    validated = validate_fee_schedule_v2(source)
    source_root = Path(validated["manifest_path"]).parent
    target = target_root / "generations" / validated["generation_id"]
    target_root.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        staging = Path(tempfile.mkdtemp(prefix=".task055f.fee_clone.", dir=target_root))
        shutil.rmtree(staging)
        shutil.copytree(source_root, staging)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, target)
    manifest_path = target / "fee_schedule_v2_manifest.json"
    cloned = validate_fee_schedule_v2(manifest_path)
    if cloned["content_hash"] != validated["content_hash"]:
        raise NativeReplayError("native_replay_fee_clone_mismatch")
    _atomic_json(
        target_root / "current.json",
        {
            "generation_id": cloned["generation_id"],
            "content_hash": cloned["content_hash"],
            "manifest": f"generations/{cloned['generation_id']}/fee_schedule_v2_manifest.json",
        },
    )
    return cloned


def _locate_generation_artifact(
    generation: Path,
    *,
    directory: str,
    generation_id: str,
    manifest_name: str,
) -> Path:
    for ancestor in generation.parents:
        candidate = ancestor / directory / "generations" / generation_id / manifest_name
        if candidate.is_file():
            return candidate
    raise NativeReplayError(f"native_replay_reference_target_missing:{directory}")


def _load_generation(root: Path) -> dict[str, Any]:
    pointer = _read_json(root / "current.json")
    return _read_json(root / str(pointer["manifest"]))


def _load_resume(root: Path) -> dict[str, Any]:
    pointer = _read_json(root / "resume" / "current.json")
    return _read_json(root / "resume" / str(pointer["manifest"]))


def _publish_resume(root: Path, semantic: Mapping[str, Any]) -> dict[str, Any]:
    content_hash = canonical_hash(semantic)
    generation_id = f"resume_{content_hash[:24]}"
    payload = dict(semantic) | {"content_hash": content_hash, "resume_generation_id": generation_id}
    target = root / "generations" / generation_id
    target.mkdir(parents=True, exist_ok=True)
    manifest_path = target / "resume_manifest.json"
    _atomic_json(manifest_path, payload)
    _atomic_json(
        root / "current.json",
        {
            "generation_id": generation_id,
            "content_hash": content_hash,
            "manifest": f"generations/{generation_id}/resume_manifest.json",
        },
    )
    return payload | {"manifest_path": str(manifest_path)}


def _round(value: float, policy: str) -> float:
    if policy == "none":
        return float(value)
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise NativeReplayError(f"native_replay_artifact_missing:{path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise NativeReplayError(f"native_replay_artifact_missing:{path.name}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)

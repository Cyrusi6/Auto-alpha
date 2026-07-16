"""Fee-aware exact-20 causal frontier producer for Task 055-G."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping

from task_055_a.run import SCENARIO_NAMES, prepare_simulation_inputs
from task_055_f.causal import (
    _build_valuation_surface,
    _load_matrix_marks,
    _trace_runs,
)
from task_055_f.valuation import publish_valuation_projection, validate_valuation_projection

from .access import AccessBroker, canonical_hash, sha256_file
from .bundle import load_audited_simulation_bundle
from .contracts import CAUSAL_SCHEMA, SIMULATION_END, SIMULATION_START
from .fees import FeeScheduleCalculator, validate_fee_schedule_v2
from .network_state import seal_round_one_l1_plan
from .truth import validate_truth_v2


class CausalFrontierError(RuntimeError):
    pass


def build_fee_aware_causal_frontier(
    *,
    truth_manifest: str | Path,
    matrix_root: str | Path,
    simulation_bundle_manifest: str | Path,
    fee_schedule_manifest: str | Path,
    output_root: str | Path,
    broker: AccessBroker,
    parent_lineage_content_hash: str,
    builder_code_hash: str,
) -> dict[str, Any]:
    truth = validate_truth_v2(truth_manifest)
    fee = validate_fee_schedule_v2(fee_schedule_manifest)
    bundle = load_audited_simulation_bundle(
        manifest_path=simulation_bundle_manifest,
        broker=broker,
    )
    prepared = prepare_simulation_inputs(bundle)
    dates = [str(value) for value in prepared["market"]["dates"]]
    assets = [str(value) for value in prepared["market"]["assets"]]
    if not dates or dates[0] != SIMULATION_START or dates[-1] != SIMULATION_END:
        raise CausalFrontierError(
            f"simulation_axis_mismatch:{dates[0] if dates else None}:{dates[-1] if dates else None}"
        )
    if fee.get("simulation_start") != dates[0] or fee.get("simulation_end") != dates[-1]:
        raise CausalFrontierError("fee_schedule_simulation_axis_mismatch")
    matrix = _load_matrix_marks(broker, matrix_root, assets, dates)
    surface = _build_valuation_surface(
        truth=truth,
        assets=assets,
        dates=dates,
        matrix=matrix,
        corporate_actions=prepared["corporate_actions"],
    )
    output = Path(output_root)
    projection = publish_valuation_projection(
        output_root=output.parent / "valuation_projection",
        dates=dates,
        assets=assets,
        surface=surface,
        truth_v2_content_hash=str(truth["content_hash"]),
        matrix_content_hash=str(matrix["manifest"].get("content_hash")),
        builder_code_hash=builder_code_hash,
    )
    calculator = FeeScheduleCalculator(fee_schedule_manifest)
    trace = _trace_runs(bundle, prepared, surface, calculator)
    exact_ids = list(trace["exact20_ids"])
    expected_pairs = sorted(
        (factor_id, scenario) for factor_id in exact_ids for scenario in SCENARIO_NAMES
    )
    actual_pairs = sorted(
        (row["factor_id"], row["scenario"]) for row in trace["run_rows"]
    )
    if len(exact_ids) != 20 or actual_pairs != expected_pairs or len(actual_pairs) != len(set(actual_pairs)):
        raise CausalFrontierError("causal_exact20_cartesian_invalid")
    lineage = {
        "parent_lineage_content_hash": parent_lineage_content_hash,
        "access_plan_content_hash": broker.plan["content_hash"],
        "truth_v2_content_hash": truth["content_hash"],
        "matrix_content_hash": matrix["manifest"].get("content_hash"),
        "simulation_bundle_content_hash": bundle["manifest"].get("content_hash"),
        "fee_schedule_content_hash": fee.get("content_hash"),
        "builder_code_hash": builder_code_hash,
        "key_root": truth["key_root"],
    }
    plan = seal_round_one_l1_plan(
        frontier_keys=trace["round_one_frontier"],
        lineage=lineage,
    )
    return _publish(
        output,
        trace=trace,
        plan=plan,
        lineage=lineage,
        projection=projection,
    )


def validate_fee_aware_causal_frontier(path: str | Path) -> dict[str, Any]:
    manifest_path = _resolve(path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != CAUSAL_SCHEMA or manifest.get("status") != "published":
        raise CausalFrontierError("causal_manifest_invalid")
    semantic = {
        key: value
        for key, value in manifest.items()
        if key not in {"content_hash", "generation_id"}
    }
    if canonical_hash(semantic) != manifest.get("content_hash"):
        raise CausalFrontierError("causal_content_hash_mismatch")
    root = manifest_path.parent
    rows = _read_jsonl(root / manifest["partitions"]["run_rows"]["path"])
    marks = _read_jsonl(root / manifest["partitions"]["held_marks"]["path"])
    plan_path = root / manifest["partitions"]["network_plan"]["path"]
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    for entry in manifest["partitions"].values():
        artifact = root / str(entry["path"])
        if not artifact.is_file() or sha256_file(artifact) != entry["sha256"]:
            raise CausalFrontierError("causal_partition_mismatch")
    exact_ids = list(manifest.get("exact20_ids") or ())
    expected = sorted(
        (factor_id, scenario) for factor_id in exact_ids for scenario in SCENARIO_NAMES
    )
    actual = sorted((row["factor_id"], row["scenario"]) for row in rows)
    if len(exact_ids) != 20 or actual != expected or len(actual) != len(set(actual)):
        raise CausalFrontierError("causal_cartesian_invalid")
    if canonical_hash(rows) != manifest.get("run_rows_root"):
        raise CausalFrontierError("causal_run_rows_root_mismatch")
    if canonical_hash(marks) != manifest.get("held_mark_root"):
        raise CausalFrontierError("causal_held_mark_root_mismatch")
    frontier = sorted(
        {
            (str(row["blocker"]["ts_code"]), str(row["blocker"]["trade_date"]))
            for row in rows
            if (row.get("blocker") or {}).get("code") == "held_position_mark_unavailable"
        }
    )
    if canonical_hash(frontier) != manifest.get("missing_key_root"):
        raise CausalFrontierError("causal_frontier_root_mismatch")
    if plan.get("frontier_root") != manifest.get("missing_key_root") or plan.get("network_executed") is not False:
        raise CausalFrontierError("causal_network_plan_invalid")
    task_root = manifest_path.parents[3]
    projection_path = task_root / str(manifest["valuation_projection"]["manifest_relative"])
    projection = validate_valuation_projection(projection_path)
    if projection.get("content_hash") != manifest["valuation_projection"]["content_hash"]:
        raise CausalFrontierError("causal_projection_hash_mismatch")
    return manifest | {
        "manifest_path": str(manifest_path),
        "run_rows": rows,
        "held_marks": marks,
        "frontier_keys": frontier,
        "frontier_root": manifest["missing_key_root"],
        "network_plan": plan,
        "valuation_projection": projection,
    }


def _publish(
    root: Path,
    *,
    trace: Mapping[str, Any],
    plan: Mapping[str, Any],
    lineage: Mapping[str, Any],
    projection: Mapping[str, Any],
) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".task055g.causal.", dir=root))
    try:
        run_path = staging / "causal_run_rows.jsonl"
        held_path = staging / "held_mark_ledger.jsonl"
        plan_path = staging / "round_one_exact_daily_plan.json"
        _write_jsonl(run_path, trace["run_rows"])
        _write_jsonl(held_path, trace["held_rows"])
        plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        partitions = {
            "run_rows": _partition(run_path),
            "held_marks": _partition(held_path),
            "network_plan": _partition(plan_path),
        }
        semantic = {
            "schema_version": CAUSAL_SCHEMA,
            "status": "published",
            "scope": "exact20_x_five_scenarios_fee_v2_first_terminal_held_mark_frontier",
            "exact20_ids": list(trace["exact20_ids"]),
            "run_count": len(trace["run_rows"]),
            "terminal_counts": dict(trace["terminal_counts"]),
            "round_one_frontier_count": int(trace["round_one_frontier_count"]),
            "round_one_frontier_semantics": "first_terminal_held_mark_blocker_not_total_gap_count",
            "held_mark_count": int(trace["held_mark_count"]),
            "authorized_modeled_held_mark_count": int(trace["authorized_modeled_held_mark_count"]),
            "run_rows_root": trace["run_rows_root"],
            "held_mark_root": trace["held_mark_root"],
            "missing_key_root": trace["missing_key_root"],
            "lineage": dict(lineage),
            "valuation_projection": {
                "content_hash": projection["content_hash"],
                "generation_id": projection["generation_id"],
                "manifest_relative": (
                    "valuation_projection/generations/"
                    f"{projection['generation_id']}/valuation_projection_manifest.json"
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
        target = root / "generations" / generation_id
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(staging)
        else:
            os.replace(staging, target)
        _atomic_json(
            root / "current.json",
            {
                "generation_id": generation_id,
                "content_hash": content_hash,
                "manifest": f"generations/{generation_id}/causal_frontier_manifest.json",
            },
        )
        return manifest | {
            "manifest_path": str(target / "causal_frontier_manifest.json"),
            "valuation_projection_manifest_path": projection["manifest_path"],
        }
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _partition(path: Path) -> dict[str, Any]:
    return {"path": path.name, "sha256": sha256_file(path), "size_bytes": path.stat().st_size}


def _write_jsonl(path: Path, rows: Any) -> None:
    path.write_text(
        "".join(
            json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _resolve(path: str | Path) -> Path:
    value = Path(path)
    if value.is_file():
        return value
    pointer = value / "current.json"
    if pointer.is_file():
        return value / str(json.loads(pointer.read_text(encoding="utf-8"))["manifest"])
    candidate = value / "causal_frontier_manifest.json"
    if candidate.is_file():
        return candidate
    raise CausalFrontierError("causal_manifest_missing")


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)

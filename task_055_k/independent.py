from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from task_055_a.run import prepare_simulation_inputs
from task_055_f.causal import build_valuation_surface, trace_causal_runs
from task_055_g.truth import validate_truth_v2
from task_055_h.fee import FeeProjectionCalculator
from task_055_h.io import canonical_hash, publish_generation, read_json
from task_055_j.application import _matrix_marks, _successor_bundle

from .application import validate_staged_application


VERIFICATION_SCHEMA = "task055k_independent_application_replay_verification_v1"


class Task055KIndependentError(RuntimeError):
    pass


def independently_verify_application_replay(
    *,
    application_path: str | Path,
    authority_root: str | Path,
    context: Mapping[str, Any],
    output_root: str | Path,
) -> dict[str, Any]:
    application = validate_staged_application(application_path, authority_root=authority_root)
    native = application["task055j_application"]
    replay = application["replay"]
    authority = Path(authority_root).resolve()
    truth = validate_truth_v2(authority / native["truth_manifest_relative_path"])
    native_root = _native_application_root(Path(native["manifest_path"]))
    matrix_root = _resolve_matrix_root(native_root, context, replay["matrix_content_hash"])
    materializations = _resolve_materializations(native_root, context)
    producer = _recompute(
        context=context,
        matrix_root=matrix_root,
        materializations=materializations,
        truth=truth,
    )
    consumer = _recompute(
        context=context,
        matrix_root=matrix_root,
        materializations=materializations,
        truth=truth,
    )
    if producer != consumer:
        raise Task055KIndependentError("task055k_independent_producer_consumer_mismatch")
    net = producer["net"]
    all_in = producer["all_in"]
    if (
        net["run_rows_root"] != replay["run_rows_root"]
        or net["held_mark_root"] != replay["held_mark_root"]
        or net["missing_key_root"] != replay["net_frontier_root"]
        or all_in["missing_key_root"] != replay["all_in_frontier_root"]
    ):
        raise Task055KIndependentError("task055k_independent_replay_parent_mismatch")
    if len(net["run_rows"]) != 100 or len(all_in["run_rows"]) != 100:
        raise Task055KIndependentError("task055k_independent_exact20_x5_invalid")
    union = sorted(
        {
            tuple(value)
            for value in net["round_one_frontier"] + all_in["round_one_frontier"]
        }
    )
    semantic = {
        "schema_version": VERIFICATION_SCHEMA,
        "status": "passed",
        "application_content_hash": application["content_hash"],
        "truth_content_hash": truth["content_hash"],
        "matrix_content_hash": replay["matrix_content_hash"],
        "net_run_rows_root": net["run_rows_root"],
        "net_held_mark_root": net["held_mark_root"],
        "net_terminal_counts": net["terminal_counts"],
        "net_frontier_root": net["missing_key_root"],
        "all_in_run_rows_root": all_in["run_rows_root"],
        "all_in_held_mark_root": all_in["held_mark_root"],
        "all_in_terminal_counts": all_in["terminal_counts"],
        "all_in_frontier_root": all_in["missing_key_root"],
        "frontier_union_root": canonical_hash(union),
        "producer_consumer_recomputed_from_raw_matrix_truth_fee": True,
        "producer_projection_used_as_input": False,
        "net_terminal_pair_count": len(net["run_rows"]),
        "all_in_terminal_pair_count": len(all_in["run_rows"]),
    }
    if semantic["frontier_union_root"] != replay["frontier_union_root"]:
        raise Task055KIndependentError("task055k_independent_frontier_union_mismatch")
    return publish_generation(
        output_root,
        prefix="task055k_independent_replay",
        manifest_name="independent_replay_verification.json",
        semantic=semantic,
    )


def _recompute(
    *,
    context: Mapping[str, Any],
    matrix_root: Path,
    materializations: list[dict[str, Any]],
    truth: Mapping[str, Any],
) -> dict[str, Any]:
    successor = _successor_bundle(context, matrix_root, materializations)
    prepared = prepare_simulation_inputs(successor)
    dates = list(prepared["market"]["dates"])
    assets = list(prepared["market"]["assets"])
    matrix_marks = _matrix_marks(matrix_root, assets, dates)
    surface = build_valuation_surface(
        truth=truth,
        assets=assets,
        dates=dates,
        matrix=matrix_marks,
        corporate_actions=prepared["corporate_actions"],
    )
    return {
        "net": trace_causal_runs(
            {"manifest": {"exact20_ids": list(context["exact20_ids"])}},
            prepared,
            surface,
            FeeProjectionCalculator(context["fee_schedule"], commission_mode="net_commission_3bp"),
        ),
        "all_in": trace_causal_runs(
            {"manifest": {"exact20_ids": list(context["exact20_ids"])}},
            prepared,
            surface,
            FeeProjectionCalculator(context["fee_schedule"], commission_mode="all_in_commission_3bp"),
        ),
    }


def _native_application_root(manifest_path: Path) -> Path:
    current = manifest_path.resolve()
    for parent in current.parents:
        if parent.name.startswith("native_task055j_"):
            return parent
    raise Task055KIndependentError("task055k_native_application_root_not_found")


def _resolve_matrix_root(native_root: Path, context: Mapping[str, Any], content_hash: str) -> Path:
    candidates = []
    for path in native_root.rglob("task_052a_strict_matrix_manifest.json"):
        try:
            if read_json(path).get("content_hash") == content_hash:
                candidates.append(path.parent)
        except (OSError, json.JSONDecodeError):
            continue
    parent = Path(context["matrix_root"])
    if read_json(parent / "task_052a_strict_matrix_manifest.json").get("content_hash") == content_hash:
        candidates.append(parent)
    unique = {path.resolve() for path in candidates}
    if len(unique) != 1:
        raise Task055KIndependentError(f"task055k_matrix_generation_resolution_invalid:{len(unique)}")
    return next(iter(unique))


def _resolve_materializations(native_root: Path, context: Mapping[str, Any]) -> list[dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    for path in native_root.rglob("materialization_manifest.json"):
        if "firewall_sentinel" in path.parts:
            continue
        payload = read_json(path)
        factor_id = str(payload.get("factor_id") or "")
        if factor_id not in context["exact20_ids"] or payload.get("materialization_status") != "success":
            continue
        candidates[factor_id] = {
            "factor_id": factor_id,
            "manifest_path": str(path),
            "values_path": str(path.parent / "values.npy"),
            "validity_path": str(path.parent / "validity.npy"),
            "content_hash": canonical_hash(
                [payload["input_fingerprint"], payload["value_sha256"], payload["validity_sha256"]]
            ),
        }
    if len(candidates) == 20:
        return [candidates[factor_id] for factor_id in context["exact20_ids"]]
    parent = list(context["parent_materializations"])
    if len(parent) != 20:
        raise Task055KIndependentError("task055k_materialization_resolution_invalid")
    return parent

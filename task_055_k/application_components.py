from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from data_lake.task052_freeze import validate_task052_governed_freeze
from matrix_store.strict_engineering import StrictEngineeringPITMatrixBuilder, StrictEngineeringPITMatrixConfig
from task_053_a.orchestrator import build_v3_tensor_generation
from task_054_b.sentinel import validate_task054b_production_sentinel
from task_054_c.validators import validate_strict_matrix_generation, validate_v3_tensor_generation
from task_055_a.run import prepare_simulation_inputs
from task_055_f.causal import build_valuation_surface, trace_causal_runs
from task_055_f.valuation import (
    load_valuation_projection,
    publish_valuation_projection,
    valuation_surface_from_projection,
)
from task_055_g.truth import publish_truth_successor, validate_truth_v2
from task_055_h.fee import FeeProjectionCalculator
from task_055_h.io import canonical_hash, publish_generation, read_json, sha256_file, validate_generation
from task_055_i.application import (
    _assert_repair_in_matrix,
    _build_repaired_freeze,
    _materialize_exact20,
    _publish_raw_repair,
)
from task_055_j.application import (
    _matrix_marks,
    _publish_dynamic_l2,
    _run_production_sentinel,
    _successor_bundle,
)

from .contracts import APPLICATION_STAGES, FEE_REPLAY_SCHEMA
from .stage_machine import NativeStageResult, StageDefinition, StageRuntime, Task055KStageMachineError


def production_stage_definitions() -> tuple[StageDefinition, ...]:
    executors = {
        "response_acceptance": _execute_response_acceptance,
        "raw_repair": _execute_raw_repair,
        "truth_successor": _execute_truth_successor,
        "freeze": _execute_freeze,
        "strict_matrix": _execute_strict_matrix,
        "v3_tensor": _execute_v3_tensor,
        "exact20_materialization": _execute_materialization,
        "firewall_sentinel": _execute_sentinel,
        "valuation": _execute_valuation,
        "net_replay": _execute_net_replay,
        "all_in_replay": _execute_all_in_replay,
        "final_publication": _execute_final_publication,
    }
    validators = {
        "response_acceptance": _validate_response_acceptance,
        "raw_repair": _validate_raw_repair,
        "truth_successor": _validate_truth,
        "freeze": _validate_freeze,
        "strict_matrix": _validate_matrix,
        "v3_tensor": _validate_tensor,
        "exact20_materialization": _validate_materialization,
        "firewall_sentinel": _validate_sentinel,
        "valuation": _validate_valuation,
        "net_replay": _validate_replay,
        "all_in_replay": _validate_replay,
        "final_publication": _validate_final,
    }
    return tuple(
        StageDefinition(
            name=name,
            executor=executors[name],
            validator=validators[name],
            validator_fqn=f"task_055_k.application_components.{validators[name].__name__}",
        )
        for name in APPLICATION_STAGES
    )


def _execute_response_acceptance(runtime: StageRuntime) -> NativeStageResult:
    accepted = runtime.accepted
    semantic = {
        "schema_version": "task055kr_response_acceptance_validation_v1",
        "status": "passed",
        "evidence_scope": accepted.scope,
        "candidate_checkpoint_content_hash": accepted.checkpoint["content_hash"],
        "acceptance_content_hash": accepted.acceptance["content_hash"],
        "reservation_content_hash": accepted.reservation["content_hash"],
        "receipt_content_hash": accepted.receipt["content_hash"],
        "cache_sha256": sha256_file(accepted.cache_path),
        "request": accepted.request,
        "item_count": len(accepted.records),
        "empty_response_semantics": accepted.receipt.get("empty_response_semantics"),
    }
    result = publish_generation(
        runtime.stage_work_root / "acceptance_validation",
        prefix="task055kr_response_acceptance_validation",
        manifest_name="acceptance_validation.json",
        semantic=semantic,
    )
    return _result(
        runtime,
        outputs={
            "branch": _branch(runtime),
            "acceptance_content_hash": accepted.acceptance["content_hash"],
            "receipt_content_hash": accepted.receipt["content_hash"],
            "cache_sha256": sha256_file(accepted.cache_path),
            "validation_manifest": _relative(runtime, result["manifest_path"]),
            "validation_content_hash": result["content_hash"],
        },
        summary={"item_count": len(accepted.records), "branch": _branch(runtime)},
        paths=[result["manifest_path"]],
    )


def _execute_raw_repair(runtime: StageRuntime) -> NativeStageResult:
    if _branch(runtime) == "positive":
        raw = _publish_raw_repair(
            parent_freeze_root=Path(runtime.context["freeze_root"]),
            row=dict(runtime.accepted.records[0]),
            request=_builder_request(runtime),
            output_root=runtime.stage_work_root / "raw_repair",
        )
        outputs = {
            "branch": "positive",
            "raw_repair_manifest": _relative(runtime, raw["manifest_path"]),
            "raw_repair_content_hash": raw["content_hash"],
            "merged_daily_bars": _relative(runtime, raw["merged_daily_bars_path"]),
            "merged_daily_bars_sha256": sha256_file(raw["merged_daily_bars_path"]),
        }
        paths = [raw["manifest_path"], raw["merged_daily_bars_path"]]
    else:
        parent = validate_task052_governed_freeze(runtime.context["freeze_root"])
        noop = publish_generation(
            runtime.stage_work_root / "raw_repair",
            prefix="task055kr_no_raw_repair",
            manifest_name="raw_repair.json",
            semantic={
                "schema_version": "task055kr_no_raw_repair_v1",
                "status": "vendor_daily_absence_no_raw_mutation",
                "parent_freeze_content_hash": parent["content_hash"],
                "security_date": [
                    runtime.accepted.request["ts_code"],
                    runtime.accepted.request["trade_date"],
                ],
            },
        )
        outputs = {
            "branch": "empty",
            "raw_repair_manifest": _relative(runtime, noop["manifest_path"]),
            "raw_repair_content_hash": noop["content_hash"],
            "parent_freeze_content_hash": parent["content_hash"],
        }
        paths = [noop["manifest_path"]]
    return _result(runtime, outputs=outputs, summary={"branch": _branch(runtime)}, paths=paths)


def _execute_truth_successor(runtime: StageRuntime) -> NativeStageResult:
    records = [dict(row) for row in runtime.accepted.records]
    truth = publish_truth_successor(
        parent_truth_manifest=runtime.context["truth_manifest"],
        api_name="daily",
        request=_builder_request(runtime),
        records=records,
        response_evidence={
            "cache_sha256": sha256_file(runtime.accepted.cache_path),
            "transport_receipt_content_hash": runtime.accepted.receipt["content_hash"],
        },
        output_root=runtime.stage_work_root / "truth",
        parent_apply_hash=runtime.accepted.acceptance["content_hash"],
        expected_record_count=int(runtime.context["expected_truth_record_count"]),
    )
    return _result(
        runtime,
        outputs={
            "truth_manifest": _relative(runtime, truth["manifest_path"]),
            "truth_content_hash": truth["content_hash"],
            "record_count": truth["record_count"],
        },
        summary={"record_count": truth["record_count"], "branch": _branch(runtime)},
        paths=[Path(truth["manifest_path"]).parent],
    )


def _execute_freeze(runtime: StageRuntime) -> NativeStageResult:
    if _branch(runtime) == "positive":
        raw = _stage(runtime, "raw_repair")["native_outputs"]
        raw_payload = read_json(runtime.application_root / raw["raw_repair_manifest"])
        raw_payload |= {
            "manifest_path": str(runtime.application_root / raw["raw_repair_manifest"]),
            "merged_daily_bars_path": str(runtime.application_root / raw["merged_daily_bars"]),
        }
        freeze = _build_repaired_freeze(
            parent_freeze_root=Path(runtime.context["freeze_root"]),
            raw_repair=raw_payload,
            output_root=runtime.stage_work_root / "freeze",
        )
        outputs = {
            "branch": "positive",
            "freeze_root": _relative(runtime, freeze.generation_dir),
            "freeze_manifest": _relative(runtime, freeze.manifest_path),
            "freeze_content_hash": freeze.content_hash,
        }
        paths = [Path(freeze.generation_dir)]
    else:
        freeze = validate_task052_governed_freeze(runtime.context["freeze_root"])
        reference = publish_generation(
            runtime.stage_work_root / "freeze_reference",
            prefix="task055kr_parent_freeze_reference",
            manifest_name="freeze_reference.json",
            semantic={
                "schema_version": "task055kr_parent_freeze_reference_v1",
                "status": "validated_parent_reference",
                "freeze_content_hash": freeze["content_hash"],
            },
        )
        outputs = {
            "branch": "empty",
            "parent_reference": True,
            "freeze_content_hash": freeze["content_hash"],
            "reference_manifest": _relative(runtime, reference["manifest_path"]),
        }
        paths = [reference["manifest_path"]]
    return _result(runtime, outputs=outputs, summary={"branch": _branch(runtime)}, paths=paths)


def _execute_strict_matrix(runtime: StageRuntime) -> NativeStageResult:
    if _branch(runtime) == "positive":
        freeze_root = _freeze_root(runtime)
        matrix = StrictEngineeringPITMatrixBuilder(
            StrictEngineeringPITMatrixConfig(
                min_cross_section_breadth=int(runtime.context.get("min_cross_section_breadth", 30)),
                research_observable_cutoff=str(runtime.context["research_cutoff"]),
                target_endpoint_horizon_trade_days=2,
            )
        ).build(
            governed_freeze_dir=freeze_root,
            historical_universe_dir=runtime.context["universe_root"],
            output_root=runtime.stage_work_root / "matrix",
        )
        _assert_repair_in_matrix(
            Path(matrix.generation_dir),
            runtime.accepted.request,
            dict(runtime.accepted.records[0]),
        )
        outputs = {
            "branch": "positive",
            "matrix_root": _relative(runtime, matrix.generation_dir),
            "matrix_manifest": _relative(runtime, matrix.manifest_path),
            "matrix_content_hash": matrix.content_hash,
        }
        paths = [Path(matrix.generation_dir)]
    else:
        matrix = validate_strict_matrix_generation(runtime.context["matrix_root"])
        reference = _reference(
            runtime,
            role="matrix",
            content_hash=matrix["content_hash"],
        )
        outputs = {
            "branch": "empty",
            "parent_reference": True,
            "matrix_content_hash": matrix["content_hash"],
            "reference_manifest": _relative(runtime, reference["manifest_path"]),
        }
        paths = [reference["manifest_path"]]
    return _result(runtime, outputs=outputs, summary={"branch": _branch(runtime)}, paths=paths)


def _execute_v3_tensor(runtime: StageRuntime) -> NativeStageResult:
    matrix_root = _matrix_root(runtime)
    matrix = validate_strict_matrix_generation(matrix_root)
    if _branch(runtime) == "positive":
        tensor = build_v3_tensor_generation(
            matrix_dir=matrix_root,
            feature_manifest_path=runtime.context["feature_manifest"],
            output_root=runtime.stage_work_root / "tensor",
        )
        validated = validate_v3_tensor_generation(tensor["generation_dir"], matrix=matrix)
        outputs = {
            "branch": "positive",
            "tensor_root": _relative(runtime, tensor["generation_dir"]),
            "tensor_manifest": _relative(runtime, validated["manifest_path"]),
            "tensor_content_hash": validated["content_hash"],
        }
        paths = [Path(tensor["generation_dir"])]
    else:
        tensor = validate_v3_tensor_generation(runtime.context["tensor_root"], matrix=matrix)
        reference = _reference(runtime, role="tensor", content_hash=tensor["content_hash"])
        outputs = {
            "branch": "empty",
            "parent_reference": True,
            "tensor_content_hash": tensor["content_hash"],
            "reference_manifest": _relative(runtime, reference["manifest_path"]),
        }
        paths = [reference["manifest_path"]]
    return _result(runtime, outputs=outputs, summary={"branch": _branch(runtime)}, paths=paths)


def _execute_materialization(runtime: StageRuntime) -> NativeStageResult:
    if _branch(runtime) == "positive":
        rows = _materialize_exact20(
            factors=runtime.context["factors"],
            freeze_root=_freeze_root(runtime),
            matrix_root=_matrix_root(runtime),
            tensor_root=_tensor_root(runtime),
            feature_manifest=Path(runtime.context["feature_manifest"]),
            promotion_policy=Path(runtime.context["promotion_policy"]),
            output_root=runtime.stage_work_root / "materializations",
            research_cutoff=str(runtime.context["research_cutoff"]),
        )
        serialized = [
            {
                "factor_id": row["factor_id"],
                "content_hash": row["content_hash"],
                "manifest_path": _relative(runtime, row["manifest_path"]),
                "values_path": _relative(runtime, row["values_path"]),
                "validity_path": _relative(runtime, row["validity_path"]),
                "values_sha256": sha256_file(row["values_path"]),
                "validity_sha256": sha256_file(row["validity_path"]),
            }
            for row in rows
        ]
        paths = [runtime.stage_work_root / "materializations"]
    else:
        serialized = [
            {
                "factor_id": row["factor_id"],
                "content_hash": row["content_hash"],
                "parent_reference": True,
                "values_sha256": sha256_file(row["values_path"]),
                "validity_sha256": sha256_file(row["validity_path"]),
            }
            for row in runtime.context["parent_materializations"]
        ]
        reference = publish_generation(
            runtime.stage_work_root / "materializations_reference",
            prefix="task055kr_parent_materializations_reference",
            manifest_name="materializations_reference.json",
            semantic={
                "schema_version": "task055kr_parent_materializations_reference_v1",
                "status": "validated_parent_reference",
                "exact20_identity_root": runtime.context["exact20_identity_root"],
                "materialization_root": canonical_hash(serialized),
            },
        )
        paths = [reference["manifest_path"]]
    return _result(
        runtime,
        outputs={
            "branch": _branch(runtime),
            "materializations": serialized,
            "materialization_root": canonical_hash(serialized),
        },
        summary={"factor_count": len(serialized), "branch": _branch(runtime)},
        paths=paths,
    )


def _execute_sentinel(runtime: StageRuntime) -> NativeStageResult:
    cache_root_raw = runtime.context.get("component_cache_root")
    cache_status = "miss_written"
    if cache_root_raw:
        matrix = validate_strict_matrix_generation(_matrix_root(runtime))
        tensor = validate_v3_tensor_generation(_tensor_root(runtime), matrix=matrix)
        freeze = validate_task052_governed_freeze(_freeze_root(runtime))
        base_identity = canonical_hash(
            {
                "component": "task054b_production_sentinel",
                "freeze": freeze["content_hash"],
                "matrix": matrix["content_hash"],
                "tensor": tensor["content_hash"],
                "context_root": runtime.context["context_root"],
                "evidence_scope": runtime.evidence_scope,
            }
        )
        cache_identity = base_identity
        cache_root = Path(cache_root_raw).resolve() / "firewall_sentinel" / cache_identity
        artifact = cache_root / "task_054b_production_sentinel.json"
        if artifact.is_file():
            try:
                validate_task054b_production_sentinel(
                    artifact,
                    scheduler_state_dir=cache_root / "scheduler_state",
                    expected_evidence_scope=runtime.evidence_scope,
                )
            except Exception:
                cache_identity = canonical_hash(
                    {
                        "base_identity": base_identity,
                        "runtime_semantic_source_hash": runtime.context[
                            "runtime_semantic_source_hash"
                        ],
                    }
                )
                cache_root = (
                    Path(cache_root_raw).resolve()
                    / "firewall_sentinel"
                    / cache_identity
                )
                artifact = cache_root / "task_054b_production_sentinel.json"
                if artifact.is_file():
                    validate_task054b_production_sentinel(
                        artifact,
                        scheduler_state_dir=cache_root / "scheduler_state",
                        expected_evidence_scope=runtime.evidence_scope,
                    )
                    sentinel = read_json(artifact) | {
                        "artifact_path": str(artifact)
                    }
                    cache_status = "validated_semantic_cache_hit"
                else:
                    sentinel = _run_production_sentinel(
                        context=runtime.context,
                        freeze_root=_freeze_root(runtime),
                        matrix_root=_matrix_root(runtime),
                        tensor_root=_tensor_root(runtime),
                        stage_root=cache_root,
                        evidence_scope=runtime.evidence_scope,
                    )
                    cache_status = "miss_after_invalid_prior_cache"
            else:
                sentinel = read_json(artifact) | {"artifact_path": str(artifact)}
                cache_status = "validated_content_cache_hit"
        else:
            sentinel = _run_production_sentinel(
                context=runtime.context,
                freeze_root=_freeze_root(runtime),
                matrix_root=_matrix_root(runtime),
                tensor_root=_tensor_root(runtime),
                stage_root=cache_root,
                evidence_scope=runtime.evidence_scope,
            )
        reference = publish_generation(
            runtime.stage_work_root / "sentinel_reference",
            prefix="task055kr_sentinel_reference",
            manifest_name="sentinel_reference.json",
            semantic={
                "schema_version": "task055kr_sentinel_reference_v1",
                "status": "validated_content_cache_reference",
                "cache_identity": cache_identity,
                "sentinel_content_hash": sentinel["content_hash"],
                "exact_run_count": sentinel["exact_run_count"],
                "evidence_scope": runtime.evidence_scope,
            },
        )
        outputs = {
            "sentinel_reference": _relative(runtime, reference["manifest_path"]),
            "sentinel_content_hash": sentinel["content_hash"],
            "sentinel_cache_identity": cache_identity,
            "exact_run_count": sentinel["exact_run_count"],
        }
        paths = [reference["manifest_path"]]
    else:
        sentinel = _run_production_sentinel(
            context=runtime.context,
            freeze_root=_freeze_root(runtime),
            matrix_root=_matrix_root(runtime),
            tensor_root=_tensor_root(runtime),
            stage_root=runtime.stage_work_root / "firewall_sentinel",
            evidence_scope=runtime.evidence_scope,
        )
        outputs = {
            "sentinel_artifact": _relative(runtime, sentinel["artifact_path"]),
            "sentinel_content_hash": sentinel["content_hash"],
            "exact_run_count": sentinel["exact_run_count"],
        }
        paths = [runtime.stage_work_root / "firewall_sentinel"]
    return _result(
        runtime,
        outputs=outputs,
        summary={"status": sentinel["status"], "exact_run_count": sentinel["exact_run_count"]},
        paths=paths,
        cache_status=cache_status,
    )


def _execute_valuation(runtime: StageRuntime) -> NativeStageResult:
    matrix_root = _matrix_root(runtime)
    materializations = _materializations(runtime)
    truth = validate_truth_v2(_truth_manifest(runtime))
    successor = _successor_bundle(runtime.context, matrix_root, materializations)
    prepared = prepare_simulation_inputs(successor)
    dates = list(prepared["market"]["dates"])
    assets = list(prepared["market"]["assets"])
    surface = build_valuation_surface(
        truth=truth,
        assets=assets,
        dates=dates,
        matrix=_matrix_marks(matrix_root, assets, dates),
        corporate_actions=prepared["corporate_actions"],
    )
    projection = publish_valuation_projection(
        output_root=runtime.stage_work_root / "valuation_projection",
        dates=dates,
        assets=assets,
        surface=surface,
        truth_v2_content_hash=truth["content_hash"],
        matrix_content_hash=validate_strict_matrix_generation(matrix_root)["content_hash"],
        builder_code_hash=canonical_hash([runtime.context["context_root"], "task055kr_valuation_v1"]),
    )
    return _result(
        runtime,
        outputs={
            "valuation_manifest": _relative(runtime, projection["manifest_path"]),
            "valuation_content_hash": projection["content_hash"],
            "truth_content_hash": truth["content_hash"],
            "matrix_content_hash": validate_strict_matrix_generation(matrix_root)["content_hash"],
        },
        summary={
            "unresolved_reporting_point_count": projection["unresolved_reporting_point_count"],
            "date_count": len(dates),
            "asset_count": len(assets),
        },
        paths=[Path(projection["manifest_path"]).parents[2]],
    )


def _execute_net_replay(runtime: StageRuntime) -> NativeStageResult:
    return _execute_replay(runtime, commission_mode="net_commission_3bp")


def _execute_all_in_replay(runtime: StageRuntime) -> NativeStageResult:
    return _execute_replay(runtime, commission_mode="all_in_commission_3bp")


def _execute_replay(runtime: StageRuntime, *, commission_mode: str) -> NativeStageResult:
    matrix_root = _matrix_root(runtime)
    materializations = _materializations(runtime)
    successor = _successor_bundle(runtime.context, matrix_root, materializations)
    prepared = prepare_simulation_inputs(successor)
    surface = valuation_surface_from_projection(
        _valuation_manifest(runtime),
        dates=prepared["market"]["dates"],
        assets=prepared["market"]["assets"],
    )
    result = trace_causal_runs(
        {"manifest": {"exact20_ids": list(runtime.context["exact20_ids"])}},
        prepared,
        surface,
        FeeProjectionCalculator(runtime.context["fee_schedule"], commission_mode=commission_mode),
    )
    manifest = _publish_replay(
        runtime=runtime,
        result=result,
        commission_mode=commission_mode,
        truth_content_hash=validate_truth_v2(_truth_manifest(runtime))["content_hash"],
        matrix_content_hash=validate_strict_matrix_generation(matrix_root)["content_hash"],
    )
    return _result(
        runtime,
        outputs={
            "replay_manifest": _relative(runtime, manifest["manifest_path"]),
            "replay_content_hash": manifest["content_hash"],
            "commission_mode": commission_mode,
            "run_rows_root": manifest["run_rows_root"],
            "held_mark_root": manifest["held_mark_root"],
            "frontier_root": manifest["frontier_root"],
            "terminal_counts": manifest["terminal_counts"],
            "run_count": manifest["run_count"],
        },
        summary={
            "commission_mode": commission_mode,
            "terminal_pair_count": manifest["run_count"],
            "terminal_counts": manifest["terminal_counts"],
        },
        paths=[Path(manifest["manifest_path"]).parent],
    )


def _execute_final_publication(runtime: StageRuntime) -> NativeStageResult:
    net = _stage(runtime, "net_replay")["native_outputs"]
    all_in = _stage(runtime, "all_in_replay")["native_outputs"]
    frontier = sorted(
        {
            tuple(item)
            for item in _load_replay(runtime, net)["frontier_keys"]
            + _load_replay(runtime, all_in)["frontier_keys"]
        }
    )
    l2 = None
    paths: list[str | Path] = []
    if _branch(runtime) == "empty":
        truth = validate_truth_v2(_truth_manifest(runtime))
        combined = {
            "content_hash": canonical_hash([net["replay_content_hash"], all_in["replay_content_hash"]]),
            "frontier_union_root": canonical_hash(frontier),
        }
        l2 = _publish_dynamic_l2(
            request=runtime.accepted.request,
            truth=truth,
            replay=combined,
            output_root=runtime.stage_work_root / "dynamic_l2",
        )
        paths.append(runtime.stage_work_root / "dynamic_l2")
    final = publish_generation(
        runtime.stage_work_root / "final",
        prefix="task055kr_application_final",
        manifest_name="application_final.json",
        semantic={
            "schema_version": "task055kr_application_final_v1",
            "status": "domain_blocked"
            if net["terminal_counts"].get("causal_valuation_blocked", 0)
            or all_in["terminal_counts"].get("causal_valuation_blocked", 0)
            else "completed",
            "branch": _branch(runtime),
            "net_replay_content_hash": net["replay_content_hash"],
            "all_in_replay_content_hash": all_in["replay_content_hash"],
            "net_terminal_counts": net["terminal_counts"],
            "all_in_terminal_counts": all_in["terminal_counts"],
            "net_run_count": net["run_count"],
            "all_in_run_count": all_in["run_count"],
            "frontier_union": [list(item) for item in frontier],
            "frontier_union_root": canonical_hash(frontier),
            "dynamic_l2_content_hash": l2["content_hash"] if l2 else None,
            "dynamic_l2_status": l2["status"] if l2 else None,
            "candidate_reselection_allowed": False,
        },
    )
    paths.append(final["manifest_path"])
    terminal_counts = {
        "net": net["terminal_counts"],
        "all_in": all_in["terminal_counts"],
    }
    return _result(
        runtime,
        outputs={
            "final_manifest": _relative(runtime, final["manifest_path"]),
            "final_content_hash": final["content_hash"],
            "net_replay_content_hash": net["replay_content_hash"],
            "all_in_replay_content_hash": all_in["replay_content_hash"],
            "frontier_union_root": canonical_hash(frontier),
            "dynamic_l2_content_hash": l2["content_hash"] if l2 else None,
        },
        summary={
            "terminal_pair_count": net["run_count"] + all_in["run_count"],
            "net_terminal_pair_count": net["run_count"],
            "all_in_terminal_pair_count": all_in["run_count"],
            "terminal_counts": terminal_counts,
            "frontier_union_root": canonical_hash(frontier),
        },
        paths=paths,
    )


def _validate_response_acceptance(payload: Mapping[str, Any], runtime: StageRuntime) -> None:
    _validate_refs(payload, runtime)
    outputs = payload["native_outputs"]
    validation = validate_generation(
        runtime.application_root / outputs["validation_manifest"],
        schema="task055kr_response_acceptance_validation_v1",
        manifest_name="acceptance_validation.json",
    )
    if validation["content_hash"] != outputs["validation_content_hash"]:
        raise Task055KStageMachineError("task055k_acceptance_stage_lineage_invalid")
    if sha256_file(runtime.accepted.cache_path) != outputs["cache_sha256"]:
        raise Task055KStageMachineError("task055k_acceptance_stage_cache_drift")


def _validate_raw_repair(payload: Mapping[str, Any], runtime: StageRuntime) -> None:
    _validate_refs(payload, runtime)
    outputs = payload["native_outputs"]
    path = runtime.application_root / outputs["raw_repair_manifest"]
    schema = "task055i_raw_repair_v1" if outputs["branch"] == "positive" else "task055kr_no_raw_repair_v1"
    manifest = validate_generation(path, schema=schema, manifest_name="raw_repair.json")
    if manifest["content_hash"] != outputs["raw_repair_content_hash"]:
        raise Task055KStageMachineError("task055k_raw_repair_stage_lineage_invalid")
    if outputs["branch"] == "positive" and sha256_file(
        runtime.application_root / outputs["merged_daily_bars"]
    ) != outputs["merged_daily_bars_sha256"]:
        raise Task055KStageMachineError("task055k_raw_repair_daily_bars_drift")
    if outputs["branch"] == "positive":
        expected = dict(runtime.accepted.records[0])
        if (
            manifest.get("security_date")
            != [runtime.accepted.request["ts_code"], runtime.accepted.request["trade_date"]]
            or manifest.get("row_hash") != canonical_hash(expected)
            or manifest.get("source_transport_hash")
            != runtime.accepted.request["transport_identity"]
        ):
            raise Task055KStageMachineError("task055k_raw_repair_identity_invalid")


def _validate_truth(payload: Mapping[str, Any], runtime: StageRuntime) -> None:
    _validate_refs(payload, runtime)
    outputs = payload["native_outputs"]
    truth = validate_truth_v2(runtime.application_root / outputs["truth_manifest"])
    if truth["content_hash"] != outputs["truth_content_hash"]:
        raise Task055KStageMachineError("task055k_truth_stage_lineage_invalid")


def _validate_freeze(payload: Mapping[str, Any], runtime: StageRuntime) -> None:
    _validate_refs(payload, runtime)
    outputs = payload["native_outputs"]
    if outputs.get("parent_reference"):
        freeze = validate_task052_governed_freeze(runtime.context["freeze_root"])
    else:
        freeze = validate_task052_governed_freeze(runtime.application_root / outputs["freeze_root"])
    if freeze["content_hash"] != outputs["freeze_content_hash"]:
        raise Task055KStageMachineError("task055k_freeze_stage_lineage_invalid")
    if outputs["branch"] == "positive":
        raw = _stage(runtime, "raw_repair")["native_outputs"]
        daily = Path(freeze["manifest_path"]).parent / freeze["artifacts_by_name"]["daily_bars"]
        if sha256_file(daily) != raw["merged_daily_bars_sha256"]:
            raise Task055KStageMachineError("task055k_freeze_repaired_daily_not_published")


def _validate_matrix(payload: Mapping[str, Any], runtime: StageRuntime) -> None:
    _validate_refs(payload, runtime)
    outputs = payload["native_outputs"]
    root = runtime.context["matrix_root"] if outputs.get("parent_reference") else runtime.application_root / outputs["matrix_root"]
    matrix = validate_strict_matrix_generation(root)
    if matrix["content_hash"] != outputs["matrix_content_hash"]:
        raise Task055KStageMachineError("task055k_matrix_stage_lineage_invalid")
    if outputs["branch"] == "positive":
        _assert_repair_in_matrix(
            Path(root),
            runtime.accepted.request,
            dict(runtime.accepted.records[0]),
        )


def _validate_tensor(payload: Mapping[str, Any], runtime: StageRuntime) -> None:
    _validate_refs(payload, runtime)
    matrix = validate_strict_matrix_generation(_matrix_root(runtime))
    outputs = payload["native_outputs"]
    root = runtime.context["tensor_root"] if outputs.get("parent_reference") else runtime.application_root / outputs["tensor_root"]
    tensor = validate_v3_tensor_generation(root, matrix=matrix)
    if tensor["content_hash"] != outputs["tensor_content_hash"]:
        raise Task055KStageMachineError("task055k_tensor_stage_lineage_invalid")


def _validate_materialization(payload: Mapping[str, Any], runtime: StageRuntime) -> None:
    _validate_refs(payload, runtime)
    rows = payload["native_outputs"]["materializations"]
    if len(rows) != 20 or {row["factor_id"] for row in rows} != set(runtime.context["exact20_ids"]):
        raise Task055KStageMachineError("task055k_materialization_exact20_invalid")
    parents = {row["factor_id"]: row for row in runtime.context["parent_materializations"]}
    for row in rows:
        if row.get("parent_reference"):
            source = parents[row["factor_id"]]
            values = Path(source["values_path"])
            validity = Path(source["validity_path"])
        else:
            values = runtime.application_root / row["values_path"]
            validity = runtime.application_root / row["validity_path"]
            manifest = read_json(runtime.application_root / row["manifest_path"])
            if manifest.get("materialization_status") != "success":
                raise Task055KStageMachineError("task055k_materialization_status_invalid")
            factor = next(
                item
                for item in runtime.context["factors"]
                if item.factor_id == row["factor_id"]
            )
            identity = manifest.get("factor_identity") or {}
            if (
                identity.get("factor_id") != factor.factor_id
                or identity.get("formula_hash") != factor.formula_hash
                or identity.get("formula_tokens") != list(factor.formula_tokens)
                or identity.get("formula_names") != list(factor.formula)
                or identity.get("feature_version") != factor.feature_version
                or identity.get("operator_version") != factor.operator_version
            ):
                raise Task055KStageMachineError("task055k_materialization_factor_identity_invalid")
        if sha256_file(values) != row["values_sha256"] or sha256_file(validity) != row["validity_sha256"]:
            raise Task055KStageMachineError("task055k_materialization_partition_drift")
    if canonical_hash(rows) != payload["native_outputs"]["materialization_root"]:
        raise Task055KStageMachineError("task055k_materialization_root_invalid")


def _validate_sentinel(payload: Mapping[str, Any], runtime: StageRuntime) -> None:
    _validate_refs(payload, runtime)
    outputs = payload["native_outputs"]
    if outputs.get("sentinel_cache_identity"):
        cache_root = (
            Path(runtime.context["component_cache_root"]).resolve()
            / "firewall_sentinel"
            / outputs["sentinel_cache_identity"]
        )
        artifact = cache_root / "task_054b_production_sentinel.json"
        reference = validate_generation(
            runtime.application_root / outputs["sentinel_reference"],
            schema="task055kr_sentinel_reference_v1",
            manifest_name="sentinel_reference.json",
        )
        if reference.get("sentinel_content_hash") != outputs["sentinel_content_hash"]:
            raise Task055KStageMachineError("task055k_sentinel_reference_invalid")
        scheduler_state = cache_root / "scheduler_state"
    else:
        artifact = runtime.application_root / outputs["sentinel_artifact"]
        scheduler_state = artifact.parent / "scheduler_state"
    validate_task054b_production_sentinel(
        artifact,
        scheduler_state_dir=scheduler_state,
        expected_evidence_scope=runtime.evidence_scope,
    )
    row = read_json(artifact)
    if row["content_hash"] != outputs["sentinel_content_hash"] or row.get("exact_run_count") != 12:
        raise Task055KStageMachineError("task055k_sentinel_stage_invalid")


def _validate_valuation(payload: Mapping[str, Any], runtime: StageRuntime) -> None:
    _validate_refs(payload, runtime)
    outputs = payload["native_outputs"]
    projection = load_valuation_projection(runtime.application_root / outputs["valuation_manifest"])
    if projection["content_hash"] != outputs["valuation_content_hash"]:
        raise Task055KStageMachineError("task055k_valuation_stage_lineage_invalid")


def _validate_replay(payload: Mapping[str, Any], runtime: StageRuntime) -> None:
    _validate_refs(payload, runtime)
    outputs = payload["native_outputs"]
    replay = _load_replay(runtime, outputs)
    if replay["content_hash"] != outputs["replay_content_hash"] or replay["run_count"] != 100:
        raise Task055KStageMachineError("task055k_replay_stage_lineage_invalid")


def _validate_final(payload: Mapping[str, Any], runtime: StageRuntime) -> None:
    _validate_refs(payload, runtime)
    outputs = payload["native_outputs"]
    final = validate_generation(
        runtime.application_root / outputs["final_manifest"],
        schema="task055kr_application_final_v1",
        manifest_name="application_final.json",
    )
    if final["content_hash"] != outputs["final_content_hash"]:
        raise Task055KStageMachineError("task055k_final_stage_lineage_invalid")
    if final["net_run_count"] != 100 or final["all_in_run_count"] != 100:
        raise Task055KStageMachineError("task055k_final_stage_exact20_x5_invalid")
    if _branch(runtime) == "empty":
        if final.get("dynamic_l2_status") != "sealed_not_authorized" or not final.get("dynamic_l2_content_hash"):
            raise Task055KStageMachineError("task055k_empty_dynamic_l2_invalid")
    elif final.get("dynamic_l2_content_hash") is not None:
        raise Task055KStageMachineError("task055k_positive_dynamic_l2_forbidden")


def _publish_replay(
    *,
    runtime: StageRuntime,
    result: Mapping[str, Any],
    commission_mode: str,
    truth_content_hash: str,
    matrix_content_hash: str,
) -> dict[str, Any]:
    rows = [dict(row) for row in result["run_rows"]]
    held = [dict(row) for row in result["held_rows"]]
    row_bytes = _jsonl(rows)
    held_bytes = _jsonl(held)
    semantic = {
        "schema_version": FEE_REPLAY_SCHEMA,
        "status": "domain_blocked"
        if result["terminal_counts"].get("causal_valuation_blocked", 0)
        else "completed",
        "evidence_scope": runtime.evidence_scope,
        "commission_mode": commission_mode,
        "exact20_ids": list(runtime.context["exact20_ids"]),
        "scenarios": list(runtime.context["scenarios"]),
        "run_count": len(rows),
        "terminal_counts": result["terminal_counts"],
        "run_rows_root": canonical_hash(rows),
        "held_mark_root": canonical_hash(held),
        "frontier_keys": result["round_one_frontier"],
        "frontier_root": result["missing_key_root"],
        "truth_content_hash": truth_content_hash,
        "matrix_content_hash": matrix_content_hash,
        "fee_schedule_content_hash": runtime.context["fee_schedule_content_hash"],
        "partitions": {
            "run_rows": {
                "path": "run_rows.jsonl",
                "sha256": _sha(row_bytes),
                "record_count": len(rows),
            },
            "held_marks": {
                "path": "held_marks.jsonl",
                "sha256": _sha(held_bytes),
                "record_count": len(held),
            },
        },
    }
    return publish_generation(
        runtime.stage_work_root / "replay",
        prefix=f"task055kr_{commission_mode}",
        manifest_name="fee_replay.json",
        semantic=semantic,
        extra_files={"run_rows.jsonl": row_bytes, "held_marks.jsonl": held_bytes},
    )


def validate_fee_replay(path: str | Path) -> dict[str, Any]:
    replay = validate_generation(path, schema=FEE_REPLAY_SCHEMA, manifest_name="fee_replay.json")
    root = Path(replay["manifest_path"]).parent
    rows = _read_jsonl(root / replay["partitions"]["run_rows"]["path"])
    held = _read_jsonl(root / replay["partitions"]["held_marks"]["path"])
    if sha256_file(root / replay["partitions"]["run_rows"]["path"]) != replay["partitions"]["run_rows"]["sha256"]:
        raise Task055KStageMachineError("task055k_replay_rows_partition_invalid")
    if sha256_file(root / replay["partitions"]["held_marks"]["path"]) != replay["partitions"]["held_marks"]["sha256"]:
        raise Task055KStageMachineError("task055k_replay_held_partition_invalid")
    expected = sorted(
        (factor_id, scenario)
        for factor_id in replay["exact20_ids"]
        for scenario in replay["scenarios"]
    )
    pairs = [(row["factor_id"], row["scenario"]) for row in rows]
    if len(rows) != 100 or sorted(pairs) != expected or len(pairs) != len(set(pairs)):
        raise Task055KStageMachineError("task055k_replay_cartesian_invalid")
    if canonical_hash(rows) != replay["run_rows_root"] or canonical_hash(held) != replay["held_mark_root"]:
        raise Task055KStageMachineError("task055k_replay_semantic_root_invalid")
    return replay | {"run_rows": rows, "held_rows": held}


def _result(
    runtime: StageRuntime,
    *,
    outputs: Mapping[str, Any],
    summary: Mapping[str, Any],
    paths: Sequence[str | Path],
    cache_status: str = "miss_written",
) -> NativeStageResult:
    return NativeStageResult(
        outputs=dict(outputs),
        semantic_summary=dict(summary),
        native_artifacts=tuple(_catalog(runtime, paths)),
        cache_status=cache_status,
    )


def _catalog(runtime: StageRuntime, paths: Sequence[str | Path]) -> list[dict[str, Any]]:
    rows = []
    seen: set[Path] = set()
    for raw in paths:
        path = Path(raw).resolve()
        candidates = [path] if path.is_file() else sorted(item for item in path.rglob("*") if item.is_file())
        for item in candidates:
            if item in seen:
                continue
            seen.add(item)
            if runtime.application_root not in item.parents or item.is_symlink():
                raise Task055KStageMachineError("task055k_stage_artifact_escape")
            rows.append(
                {
                    "path": item.relative_to(runtime.application_root).as_posix(),
                    "sha256": sha256_file(item),
                    "size_bytes": item.stat().st_size,
                }
            )
    return sorted(rows, key=lambda row: row["path"])


def _validate_refs(payload: Mapping[str, Any], runtime: StageRuntime) -> None:
    rows = payload.get("native_artifacts") or []
    if not rows:
        raise Task055KStageMachineError("task055k_stage_native_artifacts_missing")
    for row in rows:
        relative = Path(str(row.get("path") or ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise Task055KStageMachineError("task055k_stage_artifact_path_invalid")
        path = (runtime.application_root / relative).resolve()
        if runtime.application_root not in path.parents or path.is_symlink() or not path.is_file():
            raise Task055KStageMachineError("task055k_stage_artifact_missing_or_escape")
        if path.stat().st_size != row.get("size_bytes") or sha256_file(path) != row.get("sha256"):
            raise Task055KStageMachineError("task055k_stage_artifact_drift")


def _reference(runtime: StageRuntime, *, role: str, content_hash: str) -> dict[str, Any]:
    return publish_generation(
        runtime.stage_work_root / f"{role}_reference",
        prefix=f"task055kr_parent_{role}_reference",
        manifest_name=f"{role}_reference.json",
        semantic={
            "schema_version": f"task055kr_parent_{role}_reference_v1",
            "status": "validated_parent_reference",
            "role": role,
            "content_hash_reference": content_hash,
            "context_root": runtime.context["context_root"],
        },
    )


def _branch(runtime: StageRuntime) -> str:
    count = len(runtime.accepted.records)
    if count not in {0, 1}:
        raise Task055KStageMachineError("task055k_canary_response_cardinality_invalid")
    return "positive" if count == 1 else "empty"


def _builder_request(runtime: StageRuntime) -> dict[str, Any]:
    request = dict(runtime.accepted.request)
    request["transport_hash"] = request["transport_identity"]
    request["evidence_use_hash"] = request["evidence_use_identity"]
    return request




def _stage(runtime: StageRuntime, name: str) -> Mapping[str, Any]:
    if name not in runtime.prior_stages:
        raise Task055KStageMachineError(f"task055k_prior_stage_missing:{name}")
    return runtime.prior_stages[name]


def _truth_manifest(runtime: StageRuntime) -> Path:
    return runtime.application_root / _stage(runtime, "truth_successor")["native_outputs"]["truth_manifest"]


def _freeze_root(runtime: StageRuntime) -> Path:
    outputs = _stage(runtime, "freeze")["native_outputs"]
    return Path(runtime.context["freeze_root"]) if outputs.get("parent_reference") else runtime.application_root / outputs["freeze_root"]


def _matrix_root(runtime: StageRuntime) -> Path:
    outputs = _stage(runtime, "strict_matrix")["native_outputs"]
    return Path(runtime.context["matrix_root"]) if outputs.get("parent_reference") else runtime.application_root / outputs["matrix_root"]


def _tensor_root(runtime: StageRuntime) -> Path:
    outputs = _stage(runtime, "v3_tensor")["native_outputs"]
    return Path(runtime.context["tensor_root"]) if outputs.get("parent_reference") else runtime.application_root / outputs["tensor_root"]


def _materializations(runtime: StageRuntime) -> list[dict[str, Any]]:
    rows = _stage(runtime, "exact20_materialization")["native_outputs"]["materializations"]
    parents = {row["factor_id"]: row for row in runtime.context["parent_materializations"]}
    result = []
    for row in rows:
        if row.get("parent_reference"):
            result.append(dict(parents[row["factor_id"]]))
        else:
            result.append(
                {
                    "factor_id": row["factor_id"],
                    "content_hash": row["content_hash"],
                    "manifest_path": str(runtime.application_root / row["manifest_path"]),
                    "values_path": str(runtime.application_root / row["values_path"]),
                    "validity_path": str(runtime.application_root / row["validity_path"]),
                }
            )
    return result


def _valuation_manifest(runtime: StageRuntime) -> Path:
    return runtime.application_root / _stage(runtime, "valuation")["native_outputs"]["valuation_manifest"]


def _load_replay(runtime: StageRuntime, outputs: Mapping[str, Any]) -> dict[str, Any]:
    return validate_fee_replay(runtime.application_root / outputs["replay_manifest"])


def _relative(runtime: StageRuntime, path: str | Path) -> str:
    resolved = Path(path).resolve()
    if runtime.application_root not in resolved.parents:
        raise Task055KStageMachineError("task055k_generated_artifact_outside_application_root")
    return resolved.relative_to(runtime.application_root).as_posix()


def _jsonl(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(
        (json.dumps(dict(row), sort_keys=True, separators=(",", ":")) + "\n").encode()
        for row in rows
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha(payload: bytes) -> str:
    import hashlib

    return hashlib.sha256(payload).hexdigest()

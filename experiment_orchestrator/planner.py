"""Experiment plan builder that emits compute job specs."""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact
from compute_cluster.models import ComputeDeviceType, ComputeJobKind, ComputeJobSpec
from model_core.vocab import FORMULA_VOCAB

from .models import ExperimentGraphEdge, ExperimentGraphNode, ExperimentPlan, ExperimentStage
from .sharding import shard_formula_corpus


def create_experiment_plan(config: dict[str, Any]) -> ExperimentPlan:
    output_dir = Path(config.get("output_dir") or "artifacts/experiment")
    output_dir.mkdir(parents=True, exist_ok=True)
    experiment_id = str(config.get("experiment_id") or f"exp_compute_{uuid.uuid4().hex[:12]}")
    workflow = str(config.get("workflow") or "full_research_compute_smoke")
    shard_count = max(1, int(config.get("shard_count") or 1))
    formula_corpus_path = config.get("formula_corpus_path")
    shards = []
    if formula_corpus_path:
        shards = shard_formula_corpus(formula_corpus_path, shard_count, output_dir / "shards")
    else:
        for idx in range(shard_count):
            shards.append(
                shard_formula_corpus(_write_inline_corpus(output_dir, idx), 1, output_dir / "shards" / f"inline_{idx}")[0]
            )
    jobs: list[ComputeJobSpec] = []
    nodes: list[ExperimentGraphNode] = []
    edges: list[ExperimentGraphEdge] = []
    device = str(config.get("device") or "auto")
    gpu_count = int(config.get("gpu_count") or 0)
    required = ComputeDeviceType.CUDA if device == "cuda" and gpu_count > 0 else ComputeDeviceType.CPU
    data_dir = str(config.get("data_dir") or (Path(config.get("data_freeze_dir", "")) / "data"))
    factor_store_base = Path(config.get("factor_store_dir") or output_dir / "factor_store_shards")
    matrix_cache_dir = config.get("matrix_cache_dir")
    max_formulas = config.get("max_formulas")
    for shard in shards:
        shard_output = output_dir / f"batch_eval_shard_{shard.shard_id}"
        job_id = f"{experiment_id}_batch_eval_shard_{shard.shard_id}"
        command = [
            sys.executable,
            "-m",
            "formula_batch_eval.run_batch_eval",
            "--data-dir",
            data_dir,
            "--factor-store-dir",
            str(factor_store_base / f"shard_{shard.shard_id}"),
            "--report-dir",
            str(shard_output / "reports"),
            "--output-dir",
            str(shard_output),
            "--corpus-path",
            str(shard.input_path),
            "--chunk-size",
            str(config.get("batch_eval_chunk_size") or 4),
            "--continue-on-error",
            "--shard-id",
            "0",
            "--shard-count",
            "1",
            "--write-shard-manifest",
        ]
        if matrix_cache_dir:
            command.extend(["--matrix-cache-dir", str(matrix_cache_dir), "--use-matrix-cache"])
        if max_formulas:
            command.extend(["--max-formulas", str(max_formulas)])
        job = ComputeJobSpec(
            job_id=job_id,
            job_kind=ComputeJobKind.FORMULA_BATCH_EVAL,
            command=command,
            output_dir=str(shard_output),
            required_device_type=required,
            gpu_count=1 if required == ComputeDeviceType.CUDA else 0,
            max_retries=int(config.get("max_retries") or 0),
            max_duration_seconds=config.get("max_duration_seconds"),
            shard_id=shard.shard_id,
            shard_count=shard.shard_count,
            data_freeze_id=config.get("data_freeze_id"),
            data_freeze_dir=config.get("data_freeze_dir"),
            metadata={"experiment_id": experiment_id, "workflow": workflow, "stage": ExperimentStage.FORMULA_BATCH_EVAL_SHARD},
        )
        jobs.append(job)
        nodes.append(ExperimentGraphNode(node_id=job_id, stage=ExperimentStage.FORMULA_BATCH_EVAL_SHARD, job_id=job_id, shard_id=shard.shard_id))
    merge_node_id = f"{experiment_id}_merge"
    nodes.append(ExperimentGraphNode(node_id=merge_node_id, stage=ExperimentStage.FORMULA_BATCH_EVAL_MERGE))
    for job in jobs:
        edges.append(ExperimentGraphEdge(source=job.job_id, target=merge_node_id))
    plan = ExperimentPlan(
        experiment_id=experiment_id,
        workflow=workflow,
        created_at=_utc_now(),
        output_dir=str(output_dir),
        shards=shards,
        graph_nodes=nodes,
        graph_edges=edges,
        compute_jobs=[job.to_dict() for job in jobs],
        resource_plan={
            "gpu_count_requested": gpu_count,
            "shard_count": shard_count,
            "device": device,
            "use_ddp_pretrain": bool(config.get("use_ddp_pretrain", False)),
        },
        metadata={k: v for k, v in config.items() if k not in {"jobs"}},
    )
    write_experiment_plan(plan, output_dir)
    return plan


def write_experiment_plan(plan: ExperimentPlan, output_dir: str | Path) -> dict[str, str]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    write_json_artifact(output / "experiment_plan.json", plan.to_dict(), "experiment_plan", "experiment_orchestrator")
    write_json_artifact(
        output / "experiment_graph.json",
        {"nodes": [node.to_dict() for node in plan.graph_nodes], "edges": [edge.to_dict() for edge in plan.graph_edges]},
        "experiment_graph",
        "experiment_orchestrator",
    )
    write_json_artifact(output / "experiment_resource_plan.json", plan.resource_plan, "experiment_resource_plan", "experiment_orchestrator")
    write_jsonl_artifact(output / "experiment_shards.jsonl", [shard.to_dict() for shard in plan.shards], "experiment_shards", "experiment_orchestrator")
    write_json_artifact(output / "compute_jobs.json", {"jobs": plan.compute_jobs}, "compute_jobs_manifest", "experiment_orchestrator")
    return {
        "experiment_plan": str(output / "experiment_plan.json"),
        "experiment_graph": str(output / "experiment_graph.json"),
        "experiment_resource_plan": str(output / "experiment_resource_plan.json"),
        "experiment_shards": str(output / "experiment_shards.jsonl"),
        "compute_jobs": str(output / "compute_jobs.json"),
    }


def _write_inline_corpus(output_dir: Path, idx: int) -> Path:
    path = output_dir / f"inline_corpus_{idx}.jsonl"
    token = FORMULA_VOCAB.encode_name("RET_1D")
    path.write_text(json.dumps({"formula_hash": f"inline_{idx}", "formula_tokens": [token], "formula_names": ["RET_1D"]}) + "\n", encoding="utf-8")
    return path


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

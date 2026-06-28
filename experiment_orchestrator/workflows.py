"""Predefined local experiment workflows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from compute_cluster import ComputeSchedulerConfig, LocalComputeJobStore, LocalComputeScheduler

from .merge import merge_formula_batch_eval_results
from .models import ExperimentRunReport
from .planner import create_experiment_plan
from .report import write_experiment_run_report


WORKFLOWS = {
    "gpu_formula_batch_eval",
    "gpu_alphagpt_pretrain",
    "distributed_formula_search",
    "full_research_compute_smoke",
}


def run_workflow_smoke(config: dict[str, Any]) -> ExperimentRunReport:
    output_dir = Path(config.get("output_dir") or "artifacts/experiment")
    compute_state_dir = Path(config.get("compute_state_dir") or output_dir / "compute_state")
    output_dir.mkdir(parents=True, exist_ok=True)
    plan = create_experiment_plan(config | {"output_dir": str(output_dir)})
    LocalComputeJobStore(compute_state_dir).submit_jobs([_job_from_payload(row) for row in plan.compute_jobs])
    compute_report = LocalComputeScheduler(
        ComputeSchedulerConfig(
            state_dir=str(compute_state_dir),
            output_dir=str(compute_state_dir),
            max_parallel_cpu_jobs=int(config.get("max_parallel_cpu_jobs") or 1),
            max_parallel_gpu_jobs=int(config.get("max_parallel_gpu_jobs") or 1),
            dry_run=bool(config.get("dry_run", False)),
            resume=bool(config.get("resume", True)),
        )
    ).run()
    shard_dirs = [str(output_dir / f"batch_eval_shard_{shard.shard_id}") for shard in plan.shards]
    merge_report = merge_formula_batch_eval_results(shard_dirs, output_dir / "merged")
    status = "success" if compute_report.status == "success" and merge_report.status in {"success", "warning"} else "failed"
    report = ExperimentRunReport(
        experiment_id=plan.experiment_id,
        workflow=plan.workflow,
        status=status,
        plan_path=str(output_dir / "experiment_plan.json"),
        compute_run_report_path=str(compute_state_dir / "compute_run_report.json"),
        merge_report_path=str(output_dir / "merged" / "experiment_merge_report.json"),
        shard_count=len(plan.shards),
        failed_shard_count=int(compute_report.failed_count),
        summary={
            "compute_run_id": compute_report.run_id,
            "compute_status": compute_report.status,
            "compute_success_count": compute_report.success_count,
            "compute_failed_count": compute_report.failed_count,
            "compute_resumed_count": compute_report.resumed_count,
            "gpu_count_detected": compute_report.gpu_count_detected,
            "total_gpu_allocated_seconds": compute_report.total_gpu_allocated_seconds,
            "fallback_to_cpu_count": compute_report.fallback_to_cpu_count,
            "cuda_oom_count": compute_report.oom_error_count,
            "merged_records": merge_report.merged_records,
            "formula_eval_throughput": float(merge_report.merged_records / max(compute_report.total_wall_time_seconds, 1e-9)),
        },
        paths={
            "experiment_plan": str(output_dir / "experiment_plan.json"),
            "experiment_graph": str(output_dir / "experiment_graph.json"),
            "experiment_resource_plan": str(output_dir / "experiment_resource_plan.json"),
            "experiment_shards": str(output_dir / "experiment_shards.jsonl"),
            "compute_jobs": str(output_dir / "compute_jobs.json"),
            "compute_run_report": str(compute_state_dir / "compute_run_report.json"),
            "experiment_merge_report": str(output_dir / "merged" / "experiment_merge_report.json"),
            "experiment_artifact_catalog": str(output_dir / "experiment_artifact_catalog.json"),
        },
    )
    write_experiment_run_report(report, output_dir)
    return report


def _job_from_payload(row: dict):
    from compute_cluster.models import ComputeJobSpec

    defaults = ComputeJobSpec(job_id="", job_kind="", command=[]).to_dict()
    defaults.update(row)
    return ComputeJobSpec(**defaults)

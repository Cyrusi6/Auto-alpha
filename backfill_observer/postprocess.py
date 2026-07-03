"""Post-download processing plan generation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from data_pipeline.ashare.dataset_registry import CORE_DATASETS

from .models import BackfillDatasetProgress, BackfillPostprocessPlan, BackfillPostprocessStep


def build_postprocess_plan(
    progress: list[BackfillDatasetProgress],
    data_dir: str | Path,
    output_dir: str | Path | None = None,
    profile_name: str | None = None,
) -> BackfillPostprocessPlan:
    by_dataset = {item.dataset: item for item in progress}
    missing_core = [dataset for dataset in CORE_DATASETS if dataset not in by_dataset or by_dataset[dataset].records <= 0]
    failed_core = [dataset for dataset in CORE_DATASETS if by_dataset.get(dataset) and by_dataset[dataset].failed_jobs]
    pending_jobs = sum(item.pending_jobs for item in progress)
    failed_jobs = sum(item.failed_jobs for item in progress)
    blockers: list[str] = []
    if missing_core:
        blockers.append(f"missing core datasets: {','.join(missing_core)}")
    if failed_core:
        blockers.append(f"failed core jobs: {','.join(failed_core)}")
    if pending_jobs:
        blockers.append(f"pending jobs remain: {pending_jobs}")
    if failed_jobs:
        blockers.append(f"failed jobs remain: {failed_jobs}")
    blocked = bool(blockers)
    out_root = Path(output_dir) if output_dir else Path(data_dir).parent / "postprocess"
    command_specs = [
        ("compact", "Compact append JSONL datasets", f"uv run python -m data_pipeline.run_pipeline --compact --data-dir {data_dir} --pretty"),
        ("validate", "Validate governed datasets", f"uv run python -m data_pipeline.run_pipeline --validate-only --data-dir {data_dir} --pretty"),
        ("stats", "Compute dataset statistics", f"uv run python -m data_pipeline.run_pipeline --stats --data-dir {data_dir} --pretty"),
        ("data_lake_version", "Create data lake version", f"uv run python -m data_lake.run_lake create-version --data-dir {data_dir} --registry-dir {out_root / 'registry'} --pretty"),
        ("data_lake_freeze", "Create research data freeze", f"uv run python -m data_lake.run_lake create-freeze --data-dir {data_dir} --registry-dir {out_root / 'registry'} --freeze-dir {out_root / 'freeze'} --freeze-name {profile_name or 'full_research_data'} --pretty"),
        ("pit_validate", "Validate point-in-time inputs", f"uv run python -m point_in_time.run_pit validate --data-dir {data_dir} --output-dir {out_root / 'pit'} --pretty"),
        ("leakage_audit", "Run leakage audit", f"uv run python -m leakage_audit.run_audit --data-dir {data_dir} --output-dir {out_root / 'leakage'} --pretty"),
        ("corporate_actions", "Build corporate action report", f"uv run python -m corporate_actions.run_actions report --data-dir {data_dir} --output-dir {out_root / 'corporate_actions'} --pretty"),
        ("matrix_refresh", "Refresh matrix cache", f"uv run python -m matrix_refresh.run_matrix_refresh --data-dir {data_dir} --matrix-cache-dir {Path(data_dir) / 'matrix_cache'} --output-dir {out_root / 'matrix_refresh'} --pretty"),
        ("real_data_sla", "Generate real data SLA report", f"uv run python -m real_data_ops.run_real_data sla --data-dir {data_dir} --output-dir {out_root / 'sla'} --pretty"),
        ("artifact_schema", "Validate generated artifacts", f"uv run python -m artifact_schema.run_validate --artifact-dir {out_root} --output-dir {out_root / 'schema'} --write-manifest --pretty"),
    ]
    steps = [
        BackfillPostprocessStep(step_id=step_id, description=description, command=command, blocked=blocked, reason="download prerequisites are not complete" if blocked else None)
        for step_id, description, command in command_specs
    ]
    digest = hashlib.sha256(json.dumps([item.to_dict() for item in progress], sort_keys=True).encode("utf-8")).hexdigest()
    return BackfillPostprocessPlan(
        plan_id=f"postprocess_{digest[:16]}",
        prerequisites={
            "core_datasets_complete": not missing_core,
            "no_failed_core_jobs": not failed_core,
            "no_pending_jobs": pending_jobs == 0,
            "no_failed_jobs": failed_jobs == 0,
            "data_dir_readable": Path(data_dir).exists(),
        },
        steps=steps,
        commands=[step.command for step in steps],
        blockers=blockers,
        warnings=[] if not blocked else ["Commands are generated for review but marked blocked until downloads and repairs finish."],
    )

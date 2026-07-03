"""Repair plan generation for incomplete backfills."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from artifact_schema.writer import utc_now
from data_pipeline.ashare.dataset_registry import TRADE_DAY_DATASETS, TS_CODE_SPLIT_DATASETS

from .models import BackfillDatasetProgress, BackfillRepairPlan


def build_repair_plan(
    progress: list[BackfillDatasetProgress],
    data_dir: str | Path,
    output_dir: str | Path | None,
    start_date: str | None,
    end_date: str | None,
    index_codes: list[str] | None,
    rate_limit_per_minute: int = 150,
    env_file_name: str = ".env.local",
) -> BackfillRepairPlan:
    failed = sum(item.failed_jobs for item in progress)
    missing = sum(item.pending_jobs for item in progress)
    empty = sum(item.empty_response_count for item in progress)
    commands: list[str] = []
    warnings: list[str] = []
    for item in progress:
        if not item.failed_jobs and not item.pending_jobs and not item.empty_response_count:
            continue
        commands.append(_command_for_dataset(item.dataset, data_dir, output_dir, start_date, end_date, index_codes, rate_limit_per_minute, env_file_name))
    if not commands:
        warnings.append("No repair commands are needed based on current observer inputs.")
    digest = hashlib.sha256(json.dumps([item.to_dict() for item in progress], sort_keys=True).encode("utf-8")).hexdigest()
    return BackfillRepairPlan(
        repair_plan_id=f"repair_{digest[:16]}",
        generated_at=utc_now(),
        failed_jobs=int(failed),
        missing_jobs=int(missing),
        empty_but_expected_jobs=int(empty),
        commands=commands,
        warnings=warnings,
    )


def _command_for_dataset(
    dataset: str,
    data_dir: str | Path,
    output_dir: str | Path | None,
    start_date: str | None,
    end_date: str | None,
    index_codes: list[str] | None,
    rate_limit_per_minute: int,
    env_file_name: str,
) -> str:
    root = Path(output_dir) if output_dir else Path(data_dir).parent / "repair_runs"
    base = [
        f"source {env_file_name}",
        "uv run python -m data_backfill.run_backfill resume",
        "--provider tushare",
        f"--data-dir {data_dir}",
        f"--output-dir {root / dataset}",
        f"--staging-dir {root / 'staging' / dataset}",
        f"--datasets {dataset}",
        f"--start-date {start_date or ''}".strip(),
        f"--end-date {end_date or start_date or ''}".strip(),
        f"--index-codes {','.join(index_codes or ['000300.SH'])}",
        "--mode append",
        "--cache",
        "--audit",
        "--resume",
        "--direct-append",
        "--allow-network",
        "--require-token",
        f"--rate-limit-per-minute {rate_limit_per_minute}",
    ]
    if dataset in TRADE_DAY_DATASETS:
        base.extend(["--trade-days-only", f"--trade-day-datasets {dataset}"])
    if dataset in TS_CODE_SPLIT_DATASETS:
        base.extend(["--financial-by-ts-code", f"--ts-code-split-datasets {dataset}"])
    return " \\\n  ".join(item for item in base if item)

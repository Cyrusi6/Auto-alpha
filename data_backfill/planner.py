"""Backfill plan construction built on top of A-share sync plans."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from artifact_schema.writer import write_json_artifact
from data_pipeline.ashare.config import AShareDataConfig
from data_pipeline.ashare.pipeline import ASHARE_DATASETS
from data_pipeline.ashare.sync_plan import build_sync_plan

from .models import BackfillJob, BackfillPlan, BackfillScope


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_backfill_plan(
    config: AShareDataConfig,
    datasets: Sequence[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    index_codes: Sequence[str] | None = None,
    security_list_statuses: Sequence[str] | None = None,
    chunk_days: int = 30,
    chunk_strategy: str = "uniform",
    dataset_chunk_days: dict[str, int] | None = None,
    max_requests: int | None = None,
    universe_name: str | None = None,
) -> BackfillPlan:
    selected = list(ASHARE_DATASETS if datasets is None else datasets)
    statuses = [str(item).strip().upper() for item in (security_list_statuses or config.security_list_statuses) if str(item).strip()]
    scoped_config = replace(
        config,
        start_date=start_date or config.start_date,
        end_date=end_date or config.end_date,
        index_codes=tuple(index_codes or config.index_codes),
        security_list_statuses=tuple(statuses or config.security_list_statuses),
    )
    per_dataset_chunks = dict(dataset_chunk_days or {})
    if per_dataset_chunks:
        sync_jobs = []
        for dataset in selected:
            sync_jobs.extend(
                build_sync_plan(
                    scoped_config,
                    datasets=[dataset],
                    chunk_days=max(1, int(per_dataset_chunks.get(dataset, chunk_days))),
                    index_codes=scoped_config.index_codes,
                    start_date=scoped_config.start_date,
                    end_date=scoped_config.end_date,
                ).jobs
            )
    else:
        sync_jobs = build_sync_plan(
            scoped_config,
            datasets=selected,
            chunk_days=chunk_days,
            index_codes=scoped_config.index_codes,
            start_date=scoped_config.start_date,
            end_date=scoped_config.end_date,
        ).jobs
    jobs: list[BackfillJob] = []
    for job in sync_jobs:
        if job.dataset == "securities" and statuses:
            for status in statuses:
                jobs.append(
                    _make_backfill_job(
                        provider=scoped_config.provider,
                        dataset=job.dataset,
                        start_date=job.start_date,
                        end_date=job.end_date,
                        index_code=job.index_code,
                        list_status=status,
                    )
                )
        else:
            jobs.append(
                _make_backfill_job(
                    provider=scoped_config.provider,
                    dataset=job.dataset,
                    start_date=job.start_date,
                    end_date=job.end_date,
                    index_code=job.index_code,
                    metadata={"sync_job_id": job.job_id},
                )
            )
    scope = BackfillScope(
        provider=scoped_config.provider,
        datasets=selected,
        start_date=scoped_config.start_date,
        end_date=scoped_config.end_date or scoped_config.start_date,
        index_codes=list(scoped_config.index_codes),
        security_list_statuses=statuses,
        chunk_days=chunk_days,
        universe_name=universe_name,
        metadata={
            "corporate_action_query_date_field": scoped_config.corporate_action_query_date_field,
            "chunk_strategy": chunk_strategy,
            "dataset_chunk_days": per_dataset_chunks,
        },
    )
    payload = {
        "scope": scope.to_dict(),
        "jobs": [job.to_dict() for job in jobs],
        "max_requests": max_requests,
        "chunk_strategy": chunk_strategy,
        "dataset_chunk_days": per_dataset_chunks,
    }
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return BackfillPlan(
        plan_id=f"bfplan_{digest[:16]}",
        scope=scope,
        jobs=jobs,
        dataset_count=len(selected),
        job_count=len(jobs),
        estimated_request_count=sum(job.estimated_requests for job in jobs),
        expected_artifacts=[
            "backfill_plan.json",
            "backfill_state.json",
            "backfill_run_report.json",
            "backfill_job_results.jsonl",
            "backfill_coverage_report.json",
            "backfill_quota_summary.json",
        ],
        online_required=scoped_config.provider == "tushare",
        token_required=scoped_config.provider == "tushare",
        max_requests=max_requests,
        created_at=utc_now(),
    )


def write_backfill_plan(plan: BackfillPlan, output_dir: str | Path) -> tuple[Path, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = write_json_artifact(root / "backfill_plan.json", plan.to_dict(), "backfill_plan", "data_backfill")
    md_path = root / "backfill_plan.md"
    md_path.write_text(_plan_markdown(plan), encoding="utf-8")
    return json_path, md_path


def _make_backfill_job(
    provider: str,
    dataset: str,
    start_date: str | None = None,
    end_date: str | None = None,
    index_code: str | None = None,
    list_status: str | None = None,
    metadata: dict[str, object] | None = None,
) -> BackfillJob:
    payload = {
        "provider": provider,
        "dataset": dataset,
        "start_date": start_date,
        "end_date": end_date,
        "index_code": index_code,
        "list_status": list_status,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return BackfillJob(
        job_id=f"bfjob_{digest[:16]}",
        dataset=dataset,
        provider=provider,
        start_date=start_date,
        end_date=end_date,
        index_code=index_code,
        list_status=list_status,
        request_budget_group=dataset,
        metadata=dict(metadata or {}),
    )


def _plan_markdown(plan: BackfillPlan) -> str:
    lines = [
        f"# Backfill Plan {plan.plan_id}",
        "",
        f"- Provider: {plan.scope.provider}",
        f"- Date range: {plan.scope.start_date} - {plan.scope.end_date}",
        f"- Datasets: {', '.join(plan.scope.datasets)}",
        f"- Jobs: {plan.job_count}",
        f"- Estimated requests: {plan.estimated_request_count}",
        "",
        "| Dataset | Start | End | Index | List Status | Job |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for job in plan.jobs:
        lines.append(
            f"| {job.dataset} | {job.start_date or ''} | {job.end_date or ''} | {job.index_code or ''} | {job.list_status or ''} | {job.job_id} |"
        )
    return "\n".join(lines) + "\n"

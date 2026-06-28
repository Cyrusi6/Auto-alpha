"""Backfill execution using existing A-share providers and storage."""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

from artifact_schema.writer import utc_now
from data_pipeline.ashare.audit import ApiRequestAuditor
from data_pipeline.ashare.cache import TushareResponseCache
from data_pipeline.ashare.config import AShareDataConfig
from data_pipeline.ashare.providers import create_ashare_provider
from data_pipeline.ashare.providers.tushare import TushareAShareDataProvider
from data_pipeline.ashare.quality import validate_all_datasets, write_quality_report
from data_pipeline.ashare.stats import compute_all_dataset_stats, write_dataset_stats
from data_pipeline.ashare.storage import LocalAshareStorage, StorageWriteResult
from data_pipeline.ashare.sync_plan import SyncJob
from data_source_validation.fake_tushare import FakeTushareHttpClient

from .coverage import analyze_backfill_coverage, write_backfill_coverage
from .models import BackfillJob, BackfillJobStatus, BackfillPlan, BackfillRunReport
from .planner import build_backfill_plan
from .quota import evaluate_backfill_quota
from .report import write_full_backfill_report
from .staging import (
    load_backfill_state,
    mark_job,
    quarantine_job,
    save_backfill_state,
    successful_job_ids,
    write_staging_records,
)


def execute_backfill_plan(
    plan: BackfillPlan,
    config: AShareDataConfig,
    data_dir: str | Path,
    output_dir: str | Path,
    staging_dir: str | Path | None = None,
    state_path: str | Path | None = None,
    mode: str = "append",
    cache_enabled: bool = False,
    audit_enabled: bool = False,
    resume: bool = False,
    validate: bool = False,
    write_stats: bool = False,
    compact: bool = False,
    snapshot: bool = False,
    allow_network: bool = False,
    require_token: bool = False,
    max_requests: int | None = None,
    fail_fast: bool = False,
    fake_tushare_scenario: str | None = None,
    dry_run: bool = False,
) -> BackfillRunReport:
    started = utc_now()
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    storage = LocalAshareStorage(data_dir)
    staging_root = Path(staging_dir) if staging_dir else root / "staging"
    quarantine_root = root / "quarantine"
    target_state = Path(state_path) if state_path else root / "backfill_state.json"
    state = load_backfill_state(target_state, plan.plan_id)
    quota = evaluate_backfill_quota(plan, config, allow_network=allow_network or bool(fake_tushare_scenario), require_token=require_token, max_requests=max_requests)

    if mode == "overwrite" and not resume and not dry_run and quota.status == "ok":
        for dataset in plan.scope.datasets:
            storage.write_dataset(dataset, [], mode="overwrite")

    provider = _provider(config, fake_tushare_scenario)
    cache = TushareResponseCache(data_dir, enabled=cache_enabled) if cache_enabled else None
    auditor = ApiRequestAuditor(Path(data_dir) / "api_audit.jsonl") if audit_enabled else None
    completed = successful_job_ids(state)
    jobs: list[BackfillJob] = []
    blocked = quota.status != "ok"
    for job in plan.jobs:
        if resume and job.job_id in completed:
            jobs.append(replace(job, status=BackfillJobStatus.resumed, records=int(state.jobs[job.job_id].get("records", 0))))
            continue
        if blocked:
            skipped = replace(job, status=BackfillJobStatus.skipped, error=quota.reason)
            jobs.append(skipped)
            state = mark_job(state, skipped)
            save_backfill_state(state, target_state)
            continue
        if dry_run:
            jobs.append(replace(job, status=BackfillJobStatus.skipped, error="dry_run"))
            continue
        try:
            records = _fetch_job(provider, config, job, cache=cache, auditor=auditor)
            staging_records_path, _, record_count = write_staging_records(staging_root, job, records)
            payloads = _read_jsonl(staging_records_path)
            storage.write_dataset(job.dataset, payloads, mode="append")
            merged_records = len(storage.read_dataset(job.dataset))
            done = replace(
                job,
                status=BackfillJobStatus.success,
                records=merged_records,
                output_path=str(storage.dataset_path(job.dataset)),
                staging_path=str(staging_records_path),
            )
            state = mark_job(state, done)
            save_backfill_state(state, target_state)
            jobs.append(done)
        except Exception as exc:
            failed = replace(job, status=BackfillJobStatus.failed, error=str(exc))
            quarantine_job(staging_root, quarantine_root, job.job_id)
            state = mark_job(state, failed)
            save_backfill_state(state, target_state)
            jobs.append(failed)
            if fail_fast:
                break

    if compact:
        for dataset in plan.scope.datasets:
            if storage.dataset_exists(dataset):
                storage.compact_dataset(dataset)
    manifest_results = [
        StorageWriteResult(dataset=dataset, path=str(storage.dataset_path(dataset)), records=len(storage.read_dataset(dataset)))
        for dataset in plan.scope.datasets
        if storage.dataset_exists(dataset)
    ]
    manifest = storage.write_manifest(config, manifest_results)
    snapshot_path: str | None = None
    if snapshot:
        paths = [storage.snapshot_dataset(dataset, snapshot_name="backfill") for dataset in plan.scope.datasets if storage.dataset_exists(dataset)]
        if paths:
            snapshot_path = str(paths[0].parents[1])
    quality_path: str | None = None
    if validate:
        quality_path = str(write_quality_report(validate_all_datasets(storage), Path(data_dir) / "quality_report.json"))
    stats_path: str | None = None
    if write_stats:
        stats_path = str(write_dataset_stats(compute_all_dataset_stats(storage), Path(data_dir) / "dataset_stats.json"))
    coverage = analyze_backfill_coverage(data_dir, plan)
    coverage_paths = write_backfill_coverage(coverage, root)
    status = "blocked" if blocked else ("failed" if any(job.status == BackfillJobStatus.failed for job in jobs) else "success")
    summary = {
        "job_count": len(jobs),
        "success_jobs": sum(job.status == BackfillJobStatus.success for job in jobs),
        "failed_jobs": sum(job.status == BackfillJobStatus.failed for job in jobs),
        "skipped_jobs": sum(job.status == BackfillJobStatus.skipped for job in jobs),
        "resumed_jobs": sum(job.status == BackfillJobStatus.resumed for job in jobs),
        "records": sum(job.records for job in jobs if job.status in {BackfillJobStatus.success, BackfillJobStatus.resumed}),
        "coverage_gap_count": coverage.gap_count,
    }
    report = BackfillRunReport(
        plan_id=plan.plan_id,
        provider=config.provider,
        status=status,
        started_at=started,
        finished_at=utc_now(),
        jobs=jobs,
        quota=quota,
        coverage=coverage.to_dict(),
        paths={
            "manifest_path": str(manifest.path),
            "state_path": str(target_state),
            "quality_report_path": quality_path,
            "dataset_stats_path": stats_path,
            "snapshot_path": snapshot_path,
            **coverage_paths,
        },
        summary=summary,
    )
    paths = write_full_backfill_report(plan, report, root)
    # Ensure state file is schema-tagged after all updates.
    save_backfill_state(state, target_state)
    report.paths.update(paths)
    return report


def run_sample_backfill(
    config: AShareDataConfig,
    output_dir: str | Path,
    datasets: list[str],
    chunk_days: int = 30,
) -> BackfillRunReport:
    plan = build_backfill_plan(config, datasets=datasets, chunk_days=chunk_days)
    return execute_backfill_plan(plan, config, config.data_dir, output_dir, validate=True, write_stats=True)


def _provider(config: AShareDataConfig, fake_tushare_scenario: str | None):
    if config.provider == "tushare" and fake_tushare_scenario:
        return TushareAShareDataProvider(client=FakeTushareHttpClient(fake_tushare_scenario))
    return create_ashare_provider(config)


def _fetch_job(provider: Any, config: AShareDataConfig, job: BackfillJob, cache: Any, auditor: Any) -> list[object]:
    overrides: dict[str, Any] = {}
    if job.start_date:
        overrides["start_date"] = job.start_date
    if job.end_date:
        overrides["end_date"] = job.end_date
    if job.index_code:
        overrides["index_codes"] = (job.index_code,)
    if job.list_status:
        overrides["security_list_statuses"] = (job.list_status,)
    job_config = replace(config, **overrides) if overrides else config
    sync_job = SyncJob(
        job_id=job.job_id,
        dataset=job.dataset,
        provider=job.provider,
        start_date=job.start_date,
        end_date=job.end_date,
        index_code=job.index_code,
    )
    if hasattr(provider, "fetch_dataset_job"):
        return list(provider.fetch_dataset_job(sync_job, job_config, cache=cache, auditor=auditor))
    fetcher = getattr(provider, f"fetch_{job.dataset}")
    return list(fetcher(job_config))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows

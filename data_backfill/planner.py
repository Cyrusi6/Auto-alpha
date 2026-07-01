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

TRADE_DAY_WINDOWED_DATASETS = {
    "daily_bars",
    "daily_basic",
    "daily_limits",
    "adjustment_factors",
}


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
    trade_dates: Sequence[str] | None = None,
    trade_day_datasets: Sequence[str] | None = None,
    financial_ts_codes: Sequence[str] | None = None,
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
    filtered_trade_dates = sorted({str(date) for date in (trade_dates or []) if str(date)})
    use_trade_days_for = set(trade_day_datasets or TRADE_DAY_WINDOWED_DATASETS)
    financial_codes = sorted({str(code).strip() for code in (financial_ts_codes or []) if str(code).strip()})
    sync_jobs = []
    for dataset in selected:
        if dataset == "financial_features" and financial_codes:
            continue
        dataset_trade_dates = filtered_trade_dates if dataset in use_trade_days_for and filtered_trade_dates else None
        if per_dataset_chunks:
            sync_jobs.extend(
                build_sync_plan(
                    scoped_config,
                    datasets=[dataset],
                    chunk_days=max(1, int(per_dataset_chunks.get(dataset, chunk_days))),
                    index_codes=scoped_config.index_codes,
                    start_date=scoped_config.start_date,
                    end_date=scoped_config.end_date,
                    trade_dates=dataset_trade_dates,
                ).jobs
            )
        else:
            sync_jobs.extend(
                build_sync_plan(
                    scoped_config,
                    datasets=[dataset],
                    chunk_days=chunk_days,
                    index_codes=scoped_config.index_codes,
                    start_date=scoped_config.start_date,
                    end_date=scoped_config.end_date,
                    trade_dates=dataset_trade_dates,
                ).jobs
            )
    if not per_dataset_chunks and not selected:
        sync_jobs = build_sync_plan(
            scoped_config,
            datasets=selected,
            chunk_days=chunk_days,
            index_codes=scoped_config.index_codes,
            start_date=scoped_config.start_date,
            end_date=scoped_config.end_date,
        ).jobs
    jobs: list[BackfillJob] = []
    if "financial_features" in selected and financial_codes:
        for ts_code in financial_codes:
            jobs.append(
                _make_backfill_job(
                    provider=scoped_config.provider,
                    dataset="financial_features",
                    start_date=scoped_config.start_date,
                    end_date=scoped_config.end_date,
                    ts_code=ts_code,
                    metadata={"split": "ts_code"},
                )
            )
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
            "trade_days_only": bool(filtered_trade_dates),
            "trade_day_datasets": sorted(use_trade_days_for),
            "financial_split": "ts_code" if financial_codes else None,
            "financial_ts_code_count": len(financial_codes),
        },
    )
    payload = {
        "scope": scope.to_dict(),
        "jobs": [job.to_dict() for job in jobs],
        "max_requests": max_requests,
        "chunk_strategy": chunk_strategy,
        "dataset_chunk_days": per_dataset_chunks,
        "trade_dates_hash": hashlib.sha256(json.dumps(filtered_trade_dates, sort_keys=True).encode("utf-8")).hexdigest() if filtered_trade_dates else None,
        "financial_ts_codes_hash": hashlib.sha256(json.dumps(financial_codes, sort_keys=True).encode("utf-8")).hexdigest() if financial_codes else None,
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
    ts_code: str | None = None,
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
    if ts_code is not None:
        payload["ts_code"] = ts_code
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    return BackfillJob(
        job_id=f"bfjob_{digest[:16]}",
        dataset=dataset,
        provider=provider,
        start_date=start_date,
        end_date=end_date,
        index_code=index_code,
        ts_code=ts_code,
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
        "| Dataset | Start | End | Index | TS Code | List Status | Job |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for job in plan.jobs:
        lines.append(
            f"| {job.dataset} | {job.start_date or ''} | {job.end_date or ''} | {job.index_code or ''} | {job.ts_code or ''} | {job.list_status or ''} | {job.job_id} |"
        )
    return "\n".join(lines) + "\n"

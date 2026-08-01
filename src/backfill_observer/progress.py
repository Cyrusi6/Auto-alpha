"""Backfill progress computation."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

from artifact_schema.writer import utc_now
from data_pipeline.ashare.dataset_registry import DATASET_DEFINITIONS, INDEX_CODE_DATASETS, TRADE_DAY_DATASETS, TS_CODE_SPLIT_DATASETS

from .loader import jobs_from_state_and_results, load_run_artifacts, scan_dataset_file
from .models import BackfillDatasetProgress, BackfillObservedRun, BackfillObserverIssue


def build_progress_report(
    run_dir: str | Path | None,
    data_dir: str | Path,
    staging_dir: str | Path | None = None,
    cache_dir: str | Path | None = None,
    logs_dir: str | Path | None = None,
    datasets: Sequence[str] | None = None,
    expected_trade_days: int | None = None,
    expected_security_count: int | None = None,
    index_codes: Sequence[str] | None = None,
) -> tuple[BackfillObservedRun, list[BackfillDatasetProgress], list[BackfillObserverIssue], dict[str, Any]]:
    artifacts = load_run_artifacts(run_dir, logs_dir=logs_dir)
    jobs = jobs_from_state_and_results(artifacts)
    selected = list(datasets or _datasets_from_artifacts(artifacts, jobs) or _datasets_from_data_dir(data_dir))
    job_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for job in jobs:
        dataset = str(job.get("dataset") or "")
        if dataset:
            job_groups[dataset].append(job)

    progress: list[BackfillDatasetProgress] = []
    issues: list[BackfillObserverIssue] = []
    for dataset in selected:
        dataset_jobs = job_groups.get(dataset, [])
        total_jobs = _expected_jobs(dataset, dataset_jobs, expected_trade_days, expected_security_count, index_codes)
        counts = _status_counts(dataset_jobs)
        scan = scan_dataset_file(data_dir, dataset, _date_field(dataset))
        errors = [_classify_error(str(job.get("error") or "")) for job in dataset_jobs if str(job.get("error") or "")]
        failed_jobs = counts["failed"]
        pending_jobs = max(0, total_jobs - counts["success"] - failed_jobs - counts["skipped"] - counts["resumed"])
        ratio = 1.0 if total_jobs <= 0 else min(1.0, (counts["success"] + counts["resumed"]) / total_jobs)
        warnings: list[str] = []
        if not scan["exists"]:
            warnings.append("records file missing")
        if failed_jobs:
            issues.append(BackfillObserverIssue("error", "failed_jobs", f"{dataset} has failed jobs", dataset, {"failed_jobs": failed_jobs}))
        if pending_jobs:
            issues.append(BackfillObserverIssue("warning", "pending_jobs", f"{dataset} has pending jobs", dataset, {"pending_jobs": pending_jobs}))
        if errors.count("rate_limit"):
            issues.append(BackfillObserverIssue("warning", "rate_limit", f"{dataset} has rate limit failures", dataset, {"count": errors.count("rate_limit")}))
        progress.append(
            BackfillDatasetProgress(
                dataset=dataset,
                total_jobs=total_jobs,
                success_jobs=counts["success"],
                failed_jobs=failed_jobs,
                skipped_jobs=counts["skipped"],
                pending_jobs=pending_jobs,
                quarantined_jobs=counts["quarantined"] + failed_jobs,
                resumed_jobs=counts["resumed"],
                progress_ratio=float(ratio),
                records=int(scan["records"]),
                size_bytes=int(scan["size_bytes"]),
                first_date=scan["first_date"],
                last_date=scan["last_date"],
                ts_code_count=int(scan["ts_code_count"]),
                empty_response_count=sum(1 for job in dataset_jobs if int(job.get("records", 0) or 0) == 0 and job.get("status") in {"success", "resumed"}),
                permission_error_count=errors.count("permission"),
                rate_limit_error_count=errors.count("rate_limit"),
                timeout_count=errors.count("timeout"),
                latest_event_at=_latest_event_at(dataset_jobs),
                warnings=warnings,
            )
        )
    summary = _summary(progress)
    active = artifacts.get("active_from_logs") or {}
    observed = BackfillObservedRun(
        run_id=Path(run_dir).name if run_dir else "unknown",
        run_dir=str(run_dir or ""),
        data_dir=str(data_dir),
        staging_dir=str(staging_dir) if staging_dir else None,
        cache_dir=str(cache_dir) if cache_dir else None,
        observed_at=utc_now(),
        active_dataset=active.get("active_dataset") or _active_dataset(progress),
        active_job_id=active.get("active_job_id"),
        status="running" if summary["pending_jobs"] or summary["failed_jobs"] else "complete",
        metadata={"warnings": artifacts.get("warnings", []), "latest_log_line": active.get("latest_log_line")},
    )
    return observed, progress, issues, summary


def _datasets_from_artifacts(artifacts: dict[str, Any], jobs: list[dict[str, Any]]) -> list[str]:
    plan_datasets = artifacts.get("plan", {}).get("scope", {}).get("datasets", [])
    if isinstance(plan_datasets, list) and plan_datasets:
        return [str(item) for item in plan_datasets]
    return sorted({str(job.get("dataset")) for job in jobs if job.get("dataset")})


def _datasets_from_data_dir(data_dir: str | Path) -> list[str]:
    root = Path(data_dir)
    if not root.exists():
        return []
    return sorted(path.name for path in root.iterdir() if path.is_dir() and (path / "records.jsonl").exists())


def _date_field(dataset: str) -> str | None:
    definition = DATASET_DEFINITIONS.get(dataset)
    if definition is not None:
        return definition.date_field
    if dataset in {"securities"}:
        return "list_date"
    if dataset in {"trade_calendar"}:
        return "trade_date"
    if dataset in {"financial_features"}:
        return "report_period"
    return "trade_date"


def _expected_jobs(
    dataset: str,
    jobs: list[dict[str, Any]],
    expected_trade_days: int | None,
    expected_security_count: int | None,
    index_codes: Sequence[str] | None,
) -> int:
    if dataset in TS_CODE_SPLIT_DATASETS and expected_security_count:
        return max(len(jobs), int(expected_security_count))
    if dataset in TRADE_DAY_DATASETS and expected_trade_days:
        return max(len(jobs), int(expected_trade_days))
    if dataset in INDEX_CODE_DATASETS and expected_trade_days and index_codes:
        return max(len(jobs), int(expected_trade_days) * max(1, len(index_codes)))
    if jobs:
        return len(jobs)
    return 1


def _status_counts(jobs: list[dict[str, Any]]) -> dict[str, int]:
    counts = defaultdict(int)
    for job in jobs:
        counts[str(job.get("status") or "pending").lower()] += 1
    return {
        "success": counts["success"],
        "failed": counts["failed"],
        "skipped": counts["skipped"],
        "resumed": counts["resumed"],
        "quarantined": counts["quarantined"],
    }


def _classify_error(message: str) -> str:
    lowered = message.lower()
    if "429" in lowered or "rate" in lowered or "limit" in lowered or "频率" in lowered:
        return "rate_limit"
    if "permission" in lowered or "权限" in lowered or "积分" in lowered:
        return "permission"
    if "timeout" in lowered or "timed out" in lowered:
        return "timeout"
    return "other"


def _latest_event_at(jobs: list[dict[str, Any]]) -> str | None:
    values = [str(job.get("updated_at") or job.get("finished_at") or job.get("created_at") or "") for job in jobs]
    values = [value for value in values if value]
    return max(values) if values else None


def _summary(progress: list[BackfillDatasetProgress]) -> dict[str, Any]:
    total_jobs = sum(item.total_jobs for item in progress)
    success = sum(item.success_jobs + item.resumed_jobs for item in progress)
    failed = sum(item.failed_jobs for item in progress)
    pending = sum(item.pending_jobs for item in progress)
    return {
        "dataset_count": len(progress),
        "total_jobs": total_jobs,
        "success_jobs": success,
        "failed_jobs": failed,
        "pending_jobs": pending,
        "quarantined_jobs": sum(item.quarantined_jobs for item in progress),
        "records": sum(item.records for item in progress),
        "size_bytes": sum(item.size_bytes for item in progress),
        "progress_ratio": 1.0 if total_jobs <= 0 else min(1.0, success / total_jobs),
        "rate_limit_error_count": sum(item.rate_limit_error_count for item in progress),
        "permission_error_count": sum(item.permission_error_count for item in progress),
        "empty_response_count": sum(item.empty_response_count for item in progress),
    }


def _active_dataset(progress: list[BackfillDatasetProgress]) -> str | None:
    for item in progress:
        if item.pending_jobs:
            return item.dataset
    return None

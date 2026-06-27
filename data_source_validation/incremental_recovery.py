"""Incremental sync and recovery smoke checks."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Iterable

from data_pipeline.ashare.config import AShareDataConfig
from data_pipeline.ashare.manager import AShareDataManager
from data_pipeline.ashare.providers.tushare import TushareAShareDataProvider
from data_pipeline.ashare.quality import validate_all_datasets, write_quality_report
from data_pipeline.ashare.state import default_pipeline_state_path, load_pipeline_state
from data_pipeline.ashare.stats import compute_all_dataset_stats, write_dataset_stats
from data_pipeline.ashare.storage import DATASET_PRIMARY_KEYS, LocalAshareStorage

from .audit_summary import summarize_api_audit
from .fake_tushare import FakeTushareHttpClient
from .models import IncrementalRecoveryResult


def run_incremental_recovery_check(
    config: AShareDataConfig,
    datasets: Iterable[str],
    chunk_days: int = 2,
    fake_tushare_scenario: str | None = None,
    output_dir: str | Path | None = None,
) -> IncrementalRecoveryResult:
    selected = list(datasets)
    storage = LocalAshareStorage(config.data_dir)
    provider = None
    recovery_config = config
    if config.provider == "tushare" and fake_tushare_scenario:
        recovery_config = replace(config, tushare_token=config.tushare_token or "fake-token-redacted")
        provider = TushareAShareDataProvider(FakeTushareHttpClient(fake_tushare_scenario))

    manager = AShareDataManager(recovery_config, provider=provider, storage=storage)
    errors: list[str] = []
    duplicate_counts_before: dict[str, int] = {}
    duplicate_counts_after: dict[str, int] = {}
    first_result = None
    second_result = None
    try:
        first_result = manager.sync(
            datasets=selected,
            mode="append",
            validate=True,
            use_plan=True,
            chunk_days=chunk_days,
            cache_enabled=True,
            audit_enabled=True,
            resume=False,
            compact_after_sync=False,
            snapshot_after_sync=False,
            write_stats=True,
        )
        duplicate_counts_before = {dataset: _duplicate_count(storage, dataset) for dataset in selected}
        second_result = manager.sync(
            datasets=selected,
            mode="append",
            validate=True,
            use_plan=True,
            chunk_days=chunk_days,
            cache_enabled=True,
            audit_enabled=True,
            resume=True,
            compact_after_sync=True,
            snapshot_after_sync=True,
            snapshot_name="incremental_recovery",
            write_stats=True,
        )
        report = validate_all_datasets(storage)
        write_quality_report(report, storage.data_dir / "quality_report.json")
        write_dataset_stats(compute_all_dataset_stats(storage), storage.data_dir / "dataset_stats.json")
        duplicate_counts_after = {dataset: _duplicate_count(storage, dataset) for dataset in selected}
    except Exception as exc:  # smoke reports should capture recovery failures structurally
        errors.append(str(exc))

    state_path = default_pipeline_state_path(config.data_dir)
    state = load_pipeline_state(state_path)
    successful_jobs = sum(len(dataset.successful_job_ids) for dataset in state.datasets.values())
    failed_jobs = sum(len(dataset.failed_job_ids) for dataset in state.datasets.values())
    audit = summarize_api_audit(Path(config.data_dir) / "api_audit.jsonl")
    snapshot_path = str(Path(config.data_dir) / "snapshots" / "incremental_recovery")
    result = IncrementalRecoveryResult(
        ok=not errors and all(count == 0 for count in duplicate_counts_after.values()),
        initial_sync_ok=first_result is not None,
        resume_sync_ok=second_result is not None,
        validate_only_ok=(Path(config.data_dir) / "quality_report.json").exists(),
        compact_ok=all(count == 0 for count in duplicate_counts_after.values()) if duplicate_counts_after else False,
        snapshot_ok=(Path(snapshot_path).exists()),
        stats_ok=(Path(config.data_dir) / "dataset_stats.json").exists(),
        successful_job_count=successful_jobs,
        failed_job_count=failed_jobs,
        duplicate_counts_before=duplicate_counts_before,
        duplicate_counts_after=duplicate_counts_after,
        cache_hit_count=audit.cache_hit_count,
        audit_total_requests=audit.total_requests,
        errors=errors,
        paths={
            "state_path": str(state_path),
            "quality_report_path": str(Path(config.data_dir) / "quality_report.json"),
            "dataset_stats_path": str(Path(config.data_dir) / "dataset_stats.json"),
            "snapshot_path": snapshot_path,
            "audit_path": str(Path(config.data_dir) / "api_audit.jsonl"),
        },
    )
    if output_dir is not None:
        from .report import write_incremental_recovery_report

        write_incremental_recovery_report(result, output_dir)
    return result


def _duplicate_count(storage: LocalAshareStorage, dataset: str) -> int:
    fields = DATASET_PRIMARY_KEYS.get(dataset, ())
    records = storage.read_dataset(dataset)
    if not fields:
        return 0
    keys = [tuple(record.get(field) for field in fields) for record in records if all(record.get(field) not in {None, ""} for field in fields)]
    return max(0, len(keys) - len(set(keys)))

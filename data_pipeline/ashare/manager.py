"""A-share local synchronization manager."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from .audit import ApiRequestAuditEntry, ApiRequestAuditor, utc_now
from .cache import TushareResponseCache
from .config import AShareDataConfig
from .pipeline import ASHARE_DATASETS
from .providers import AShareDataProvider, create_ashare_provider
from .quality import validate_all_datasets, write_quality_report
from .stats import compute_all_dataset_stats, write_dataset_stats
from .state import default_pipeline_state_path, load_pipeline_state, save_pipeline_state
from .storage import LocalAshareStorage, StorageWriteResult
from .sync_plan import SyncJob, build_sync_plan


@dataclass(frozen=True)
class SyncDatasetResult:
    dataset: str
    path: str
    records: int


@dataclass(frozen=True)
class SyncResult:
    provider: str
    universe: str
    start_date: str
    end_date: str | None
    adjust: str
    data_dir: str
    datasets: list[SyncDatasetResult]
    manifest_path: str
    state_path: str | None = None
    quality_report_path: str | None = None
    has_errors: bool = False
    quality_summary: dict[str, Any] | None = None
    plan_path: str | None = None
    audit_path: str | None = None
    stats_path: str | None = None
    snapshot_path: str | None = None
    compaction_summary: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "universe": self.universe,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "adjust": self.adjust,
            "data_dir": self.data_dir,
            "datasets": [asdict(dataset) for dataset in self.datasets],
            "manifest_path": self.manifest_path,
            "state_path": self.state_path,
            "quality_report_path": self.quality_report_path,
            "has_errors": self.has_errors,
            "quality_summary": self.quality_summary,
            "plan_path": self.plan_path,
            "audit_path": self.audit_path,
            "stats_path": self.stats_path,
            "snapshot_path": self.snapshot_path,
            "compaction_summary": self.compaction_summary,
        }


class AShareDataManager:
    def __init__(
        self,
        config: AShareDataConfig,
        provider: AShareDataProvider | None = None,
        storage: LocalAshareStorage | None = None,
    ):
        self.config = config
        self.provider = provider or create_ashare_provider(config)
        self.storage = storage or LocalAshareStorage(config.data_dir)

    def sync(
        self,
        datasets: list[str] | None = None,
        mode: str = "overwrite",
        validate: bool = False,
        write_state: bool = True,
        state_file: str | Path | None = None,
        use_plan: bool = False,
        chunk_days: int = 30,
        cache_enabled: bool = False,
        audit_enabled: bool = False,
        resume: bool = False,
        fail_on_quality_error: bool = False,
        compact_after_sync: bool = False,
        snapshot_after_sync: bool = False,
        snapshot_name: str | None = None,
        write_stats: bool = False,
    ) -> SyncResult:
        if mode not in {"overwrite", "append"}:
            raise ValueError("mode must be one of: overwrite, append")

        selected = list(ASHARE_DATASETS if datasets is None else datasets)
        unsupported = sorted(set(selected) - set(ASHARE_DATASETS))
        if unsupported:
            raise ValueError(f"Unsupported A-share datasets: {', '.join(unsupported)}")

        plan_path: str | None = None
        audit_path: str | None = None
        if use_plan:
            write_results, plan_path, audit_path = self._sync_with_plan(
                selected=selected,
                mode=mode,
                chunk_days=chunk_days,
                write_state=write_state,
                state_file=state_file,
                cache_enabled=cache_enabled,
                audit_enabled=audit_enabled,
                resume=resume,
            )
        else:
            write_results = []
            for dataset in selected:
                fetcher = getattr(self.provider, f"fetch_{dataset}")
                records = fetcher(self.config)
                write_results.append(self.storage.write_dataset(dataset, records, mode=mode))

        manifest = self.storage.write_manifest(self.config, write_results)
        state_path: str | None = None
        if write_state and not use_plan:
            target_state_path = Path(state_file) if state_file is not None else default_pipeline_state_path(self.config.data_dir)
            state = load_pipeline_state(target_state_path)
            for result in write_results:
                state.update_dataset(
                    dataset=result.dataset,
                    records=result.records,
                    start_date=self.config.start_date,
                    end_date=self.config.end_date,
                )
            state_path = str(save_pipeline_state(state, target_state_path))
        elif write_state:
            target_state_path = Path(state_file) if state_file is not None else default_pipeline_state_path(self.config.data_dir)
            state_path = str(target_state_path)

        compaction_summary: list[dict[str, Any]] | None = None
        if compact_after_sync:
            compacted = [self.storage.compact_dataset(dataset) for dataset in selected if self.storage.dataset_exists(dataset)]
            compaction_summary = [asdict(result) for result in compacted]
            write_results = [
                StorageWriteResult(dataset=dataset, path=str(self.storage.dataset_path(dataset)), records=len(self.storage.read_dataset(dataset)))
                for dataset in selected
                if self.storage.dataset_exists(dataset)
            ]

        snapshot_path: str | None = None
        if snapshot_after_sync:
            snapshot_paths = [
                self.storage.snapshot_dataset(dataset, snapshot_name=snapshot_name)
                for dataset in selected
                if self.storage.dataset_exists(dataset)
            ]
            if snapshot_paths:
                snapshot_path = str(snapshot_paths[0].parents[1])

        stats_path: str | None = None
        if write_stats:
            stats_path = str(write_dataset_stats(compute_all_dataset_stats(self.storage), self.storage.data_dir / "dataset_stats.json"))

        quality_report_path: str | None = None
        quality_summary: dict[str, Any] | None = None
        has_errors = False
        if validate:
            report = validate_all_datasets(self.storage)
            target_report_path = self.storage.data_dir / "quality_report.json"
            quality_report_path = str(write_quality_report(report, target_report_path))
            report_payload = report.to_dict()
            has_errors = bool(report_payload["has_errors"])
            quality_summary = {
                "total_errors": report_payload["total_errors"],
                "total_warnings": report_payload["total_warnings"],
                "datasets": [
                    {
                        "dataset": dataset["dataset"],
                        "records": dataset["records"],
                        "errors": dataset["errors"],
                        "warnings": dataset["warnings"],
                    }
                    for dataset in report_payload["datasets"]
                ],
            }
            if fail_on_quality_error and has_errors:
                quality_summary["quality_gate"] = "failed"

        return SyncResult(
            provider=self.config.provider,
            universe=self.config.universe,
            start_date=self.config.start_date,
            end_date=self.config.end_date,
            adjust=self.config.adjust,
            data_dir=str(self.config.data_dir),
            datasets=[
                SyncDatasetResult(
                    dataset=result.dataset,
                    path=result.path,
                    records=result.records,
                )
                for result in write_results
            ],
            manifest_path=manifest.path,
            state_path=state_path,
            quality_report_path=quality_report_path,
            has_errors=has_errors,
            quality_summary=quality_summary,
            plan_path=plan_path,
            audit_path=audit_path,
            stats_path=stats_path,
            snapshot_path=snapshot_path,
            compaction_summary=compaction_summary,
        )

    def _sync_with_plan(
        self,
        selected: list[str],
        mode: str,
        chunk_days: int,
        write_state: bool,
        state_file: str | Path | None,
        cache_enabled: bool,
        audit_enabled: bool,
        resume: bool,
    ) -> tuple[list[StorageWriteResult], str, str | None]:
        plan = build_sync_plan(self.config, datasets=selected, chunk_days=chunk_days)
        plan_path = self.storage.data_dir / "sync_plan.json"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

        state_path = Path(state_file) if state_file is not None else default_pipeline_state_path(self.config.data_dir)
        state = load_pipeline_state(state_path)
        cache = TushareResponseCache(self.config.data_dir, enabled=cache_enabled) if cache_enabled else None
        audit_path = self.storage.data_dir / "api_audit.jsonl"
        auditor = ApiRequestAuditor(audit_path) if audit_enabled else None
        if audit_enabled:
            audit_path.parent.mkdir(parents=True, exist_ok=True)
            audit_path.touch(exist_ok=True)

        if mode == "overwrite" and not resume:
            for dataset in selected:
                self.storage.write_dataset(dataset, [], mode="overwrite")

        for job in plan.jobs:
            dataset_state = state.datasets.get(job.dataset)
            if resume and dataset_state is not None and job.job_id in dataset_state.successful_job_ids:
                continue

            try:
                records = self._fetch_job(job, cache=cache, auditor=auditor)
                self.storage.write_dataset(job.dataset, records, mode="append")
                if write_state:
                    state.mark_job_success(
                        dataset=job.dataset,
                        job_id=job.job_id,
                        records=len(self.storage.read_dataset(job.dataset)),
                        start_date=job.start_date or self.config.start_date,
                        end_date=job.end_date or self.config.end_date,
                    )
                    save_pipeline_state(state, state_path)
            except Exception as exc:
                if write_state:
                    state.mark_job_failed(
                        dataset=job.dataset,
                        job_id=job.job_id,
                        error=str(exc),
                        start_date=job.start_date or self.config.start_date,
                        end_date=job.end_date or self.config.end_date,
                    )
                    save_pipeline_state(state, state_path)
                raise

        results = [
            StorageWriteResult(
                dataset=dataset,
                path=str(self.storage.dataset_path(dataset)),
                records=len(self.storage.read_dataset(dataset)),
            )
            for dataset in selected
        ]
        return results, str(plan_path), str(audit_path) if audit_enabled else None

    def _fetch_job(
        self,
        job: SyncJob,
        cache: TushareResponseCache | None,
        auditor: ApiRequestAuditor | None,
    ) -> list[object]:
        if hasattr(self.provider, "fetch_dataset_job"):
            return list(
                self.provider.fetch_dataset_job(  # type: ignore[attr-defined]
                    job,
                    self.config,
                    cache=cache,
                    auditor=auditor,
                )
            )

        job_config = self.config
        overrides: dict[str, Any] = {}
        if job.start_date is not None:
            overrides["start_date"] = job.start_date
        if job.end_date is not None:
            overrides["end_date"] = job.end_date
        if job.index_code is not None:
            overrides["index_codes"] = (job.index_code,)
        if overrides:
            job_config = replace(self.config, **overrides)

        fetcher = getattr(self.provider, f"fetch_{job.dataset}")
        started_at = utc_now()
        started = time.perf_counter()
        records: list[object] = []
        status = "success"
        error: str | None = None
        try:
            records = list(fetcher(job_config))
            return records
        except Exception as exc:
            status = "error"
            error = str(exc)
            raise
        finally:
            if auditor is not None:
                auditor.write(
                    ApiRequestAuditEntry(
                        api_name=f"{self.config.provider}:{job.dataset}",
                        dataset=job.dataset,
                        start_date=job.start_date,
                        end_date=job.end_date,
                        index_code=job.index_code,
                        cache_hit=False,
                        records=len(records),
                        status=status,
                        error=error,
                        started_at=started_at,
                        finished_at=utc_now(),
                        duration_seconds=max(0.0, time.perf_counter() - started),
                    )
                )


def sync_ashare_datasets(
    config: AShareDataConfig,
    datasets: list[str] | None = None,
    mode: str = "overwrite",
    validate: bool = False,
    write_state: bool = True,
    state_file: str | Path | None = None,
    use_plan: bool = False,
    chunk_days: int = 30,
    cache_enabled: bool = False,
    audit_enabled: bool = False,
    resume: bool = False,
    fail_on_quality_error: bool = False,
    compact_after_sync: bool = False,
    snapshot_after_sync: bool = False,
    snapshot_name: str | None = None,
    write_stats: bool = False,
) -> SyncResult:
    return AShareDataManager(config).sync(
        datasets=datasets,
        mode=mode,
        validate=validate,
        write_state=write_state,
        state_file=state_file,
        use_plan=use_plan,
        chunk_days=chunk_days,
        cache_enabled=cache_enabled,
        audit_enabled=audit_enabled,
        resume=resume,
        fail_on_quality_error=fail_on_quality_error,
        compact_after_sync=compact_after_sync,
        snapshot_after_sync=snapshot_after_sync,
        snapshot_name=snapshot_name,
        write_stats=write_stats,
    )

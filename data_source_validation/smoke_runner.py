"""End-to-end data source smoke runner."""

from __future__ import annotations

import json
import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Iterable

from artifact_schema.writer import utc_now
from data_pipeline.ashare.config import AShareDataConfig
from data_pipeline.ashare.manager import AShareDataManager
from data_pipeline.ashare.pipeline import ASHARE_DATASETS
from data_pipeline.ashare.providers.tushare import TushareAShareDataProvider
from data_pipeline.ashare.quality import validate_all_datasets, write_quality_report
from data_pipeline.ashare.stats import compute_all_dataset_stats, write_dataset_stats
from data_pipeline.ashare.storage import LocalAshareStorage
from data_lake import LocalDataLakeRegistry, create_research_freeze, validate_research_input
from data_lake.fingerprint import content_hash_for_fingerprints, fingerprint_data_dir
from data_lake.freeze import write_freeze_validation_report
from data_lake.models import DatasetVersionRecord
from data_lake.report import write_data_lake_report, write_dataset_version_manifest

from .audit_summary import summarize_api_audit
from .baseline_compare import compare_to_baseline
from .fake_tushare import FakeTushareHttpClient
from .field_coverage import analyze_field_coverage
from .incremental_recovery import run_incremental_recovery_check
from .models import (
    AuditSummary,
    BaselineCompareSummary,
    DataSourceSmokeConfig,
    DataSourceSmokeReport,
    DatasetSmokeResult,
    IncrementalRecoveryResult,
    ProviderReadinessStatus,
)
from .probe import diagnostic_code_from_exception, probe_provider
from .report import write_data_source_smoke_report


def run_data_source_smoke(
    config: AShareDataConfig,
    output_dir: str | Path,
    datasets: Iterable[str] | None = None,
    mode: str = "overwrite",
    chunk_days: int = 30,
    cache_enabled: bool = False,
    audit_enabled: bool = False,
    validate: bool = False,
    stats: bool = False,
    snapshot: bool = False,
    compact: bool = False,
    allow_network: bool = False,
    require_token: bool = False,
    fake_tushare_scenario: str | None = None,
    max_requests: int = 20,
    max_records_per_dataset: int | None = None,
    baseline_data_dir: str | Path | None = None,
    compare_baseline: bool = False,
    run_incremental_recovery: bool = False,
    run_online_incremental: bool = False,
    data_lake_registry_dir: str | Path | None = None,
    write_data_version: bool = False,
    create_data_freeze: bool = False,
    freeze_dir: str | Path | None = None,
) -> DataSourceSmokeReport:
    selected = list(ASHARE_DATASETS if datasets is None else datasets)
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    smoke_config = DataSourceSmokeConfig(
        provider=config.provider,
        data_dir=str(config.data_dir),
        output_dir=str(root),
        start_date=config.start_date,
        end_date=config.end_date,
        datasets=selected,
        index_codes=list(config.index_codes),
        allow_network=allow_network,
        fake_tushare_scenario=fake_tushare_scenario,
        max_requests=max_requests,
        max_records_per_dataset=max_records_per_dataset,
        chunk_days=chunk_days,
        mode=mode,
        cache_enabled=cache_enabled,
        audit_enabled=audit_enabled,
        validate=validate,
        stats=stats,
        snapshot=snapshot,
        compact=compact,
        run_incremental_recovery=run_incremental_recovery,
        write_data_version=write_data_version,
        create_research_freeze=create_data_freeze,
        data_lake_registry_dir=str(data_lake_registry_dir) if data_lake_registry_dir else None,
        freeze_dir=str(freeze_dir) if freeze_dir else None,
    )

    diagnostics = probe_provider(
        config,
        allow_network=allow_network,
        fake_scenario=fake_tushare_scenario,
        max_requests=max_requests,
        datasets=selected,
    )

    storage = LocalAshareStorage(config.data_dir)
    dataset_results: list[DatasetSmokeResult] = []
    sync_error: str | None = None
    should_sync = _should_sync(config, diagnostics, allow_network, require_token, fake_tushare_scenario)
    if should_sync:
        provider = _provider_for_smoke(config, fake_tushare_scenario)
        sync_config = _config_for_smoke(config, fake_tushare_scenario)
        try:
            result = AShareDataManager(sync_config, provider=provider, storage=storage).sync(
                datasets=selected,
                mode=mode,
                validate=validate,
                use_plan=True,
                chunk_days=chunk_days,
                cache_enabled=cache_enabled,
                audit_enabled=audit_enabled,
                resume=False,
                compact_after_sync=compact,
                snapshot_after_sync=snapshot,
                snapshot_name="data_source_smoke",
                write_stats=stats,
            )
            if max_records_per_dataset is not None:
                _truncate_datasets(storage, selected, max_records_per_dataset)
            quality_by_dataset = _quality_by_dataset(storage) if validate else {}
            dataset_results = [
                DatasetSmokeResult(
                    dataset=item.dataset,
                    status=ProviderReadinessStatus.OK,
                    records=len(storage.read_dataset(item.dataset)),
                    path=item.path,
                    message="synced",
                    quality_errors=int(quality_by_dataset.get(item.dataset, {}).get("errors", 0)),
                    quality_warnings=int(quality_by_dataset.get(item.dataset, {}).get("warnings", 0)),
                )
                for item in result.datasets
            ]
        except Exception as exc:
            sync_error = str(exc)
            dataset_results = [
                DatasetSmokeResult(
                    dataset=dataset,
                    status=ProviderReadinessStatus.ERROR,
                    records=len(storage.read_dataset(dataset)),
                    path=str(storage.dataset_path(dataset)),
                    diagnostic_code=diagnostic_code_from_exception(exc),
                    message=str(exc),
                )
                for dataset in selected
            ]
    else:
        dataset_results = [
            DatasetSmokeResult(
                dataset=dataset,
                status=ProviderReadinessStatus.SKIPPED,
                records=len(storage.read_dataset(dataset)),
                path=str(storage.dataset_path(dataset)),
                message="sync skipped by network/token gate",
            )
            for dataset in selected
        ]

    if validate and storage.data_dir.exists():
        quality_report = validate_all_datasets(storage)
        write_quality_report(quality_report, storage.data_dir / "quality_report.json")
    if stats and storage.data_dir.exists():
        write_dataset_stats(compute_all_dataset_stats(storage), storage.data_dir / "dataset_stats.json")

    field_coverage = analyze_field_coverage(config.data_dir, selected)
    quality_summary = _read_json(storage.data_dir / "quality_report.json")
    stats_summary = _read_json(storage.data_dir / "dataset_stats.json")
    securities_summary = _securities_status_summary(storage)
    audit_summary = summarize_api_audit(storage.data_dir / "api_audit.jsonl")
    incremental: IncrementalRecoveryResult | None = None
    if run_incremental_recovery and _can_run_incremental(config, fake_tushare_scenario, allow_network, run_online_incremental):
        incremental = run_incremental_recovery_check(
            _config_for_smoke(config, fake_tushare_scenario),
            selected,
            chunk_days=chunk_days,
            fake_tushare_scenario=fake_tushare_scenario if config.provider == "tushare" else None,
            output_dir=root,
        )
        audit_summary = summarize_api_audit(storage.data_dir / "api_audit.jsonl")
        field_coverage = analyze_field_coverage(config.data_dir, selected)
        quality_summary = _read_json(storage.data_dir / "quality_report.json")
        stats_summary = _read_json(storage.data_dir / "dataset_stats.json")

    baseline: BaselineCompareSummary | None = None
    if compare_baseline and baseline_data_dir is not None:
        baseline = compare_to_baseline(
            data_dir=config.data_dir,
            baseline_data_dir=baseline_data_dir,
            datasets=selected,
            output_dir=root,
        )

    status = _overall_status(
        diagnostics=diagnostics,
        datasets=dataset_results,
        quality_summary=quality_summary,
        baseline=baseline,
        sync_error=sync_error,
    )
    report = DataSourceSmokeReport(
        provider=config.provider,
        status=status,
        diagnostics=diagnostics,
        datasets=dataset_results,
        field_coverage=field_coverage,
        audit_summary=audit_summary,
        incremental_recovery=incremental,
        baseline_compare=baseline,
        quality_summary=quality_summary,
        stats_summary={**stats_summary, **securities_summary},
        config=smoke_config,
    )
    paths = write_data_source_smoke_report(report, root)
    data_lake_summary: dict[str, object] = {}
    if write_data_version:
        lake_summary, lake_paths = _write_data_lake_version_and_freeze(
            config=config,
            datasets=selected,
            output_dir=root,
            registry_dir=data_lake_registry_dir,
            freeze_dir=freeze_dir,
            create_data_freeze=create_data_freeze,
            paths=paths,
        )
        data_lake_summary = lake_summary
        paths.update(lake_paths)
    report = DataSourceSmokeReport(
        provider=report.provider,
        status=report.status,
        diagnostics=report.diagnostics,
        datasets=report.datasets,
        field_coverage=report.field_coverage,
        audit_summary=report.audit_summary,
        incremental_recovery=report.incremental_recovery,
        baseline_compare=report.baseline_compare,
        quality_summary=report.quality_summary,
        stats_summary=report.stats_summary,
        config=report.config,
        data_lake_summary=data_lake_summary,
        paths=paths,
    )
    write_data_source_smoke_report(report, root)
    return report


def _should_sync(
    config: AShareDataConfig,
    diagnostics: list,
    allow_network: bool,
    require_token: bool,
    fake_tushare_scenario: str | None,
) -> bool:
    if config.provider == "sample":
        return True
    if config.provider != "tushare":
        return False
    if fake_tushare_scenario is not None:
        return True
    if require_token and not config.tushare_token:
        return False
    if not allow_network:
        return False
    return bool(config.tushare_token) and not any(item.status == ProviderReadinessStatus.ERROR for item in diagnostics)


def _provider_for_smoke(config: AShareDataConfig, fake_tushare_scenario: str | None):
    if config.provider == "tushare" and fake_tushare_scenario:
        return TushareAShareDataProvider(FakeTushareHttpClient(fake_tushare_scenario))
    return None


def _config_for_smoke(config: AShareDataConfig, fake_tushare_scenario: str | None) -> AShareDataConfig:
    if config.provider == "tushare" and fake_tushare_scenario:
        return replace(config, tushare_token=config.tushare_token or "fake-token-redacted")
    return config


def _can_run_incremental(
    config: AShareDataConfig,
    fake_tushare_scenario: str | None,
    allow_network: bool,
    run_online_incremental: bool,
) -> bool:
    if config.provider == "sample":
        return True
    if config.provider == "tushare" and fake_tushare_scenario == "success":
        return True
    if config.provider == "tushare" and allow_network and run_online_incremental:
        return True
    return False


def _truncate_datasets(storage: LocalAshareStorage, datasets: list[str], limit: int) -> None:
    for dataset in datasets:
        records = storage.read_dataset(dataset)
        if len(records) > limit:
            storage.write_dataset(dataset, records[:limit], mode="overwrite")


def _quality_by_dataset(storage: LocalAshareStorage) -> dict[str, dict]:
    report = validate_all_datasets(storage).to_dict()
    return {item["dataset"]: item for item in report.get("datasets", [])}


def _overall_status(
    diagnostics: list,
    datasets: list[DatasetSmokeResult],
    quality_summary: dict,
    baseline: BaselineCompareSummary | None,
    sync_error: str | None,
) -> str:
    if sync_error or any(item.status == ProviderReadinessStatus.ERROR for item in diagnostics) or any(item.status == ProviderReadinessStatus.ERROR for item in datasets):
        return ProviderReadinessStatus.ERROR
    if diagnostics and all(item.status == ProviderReadinessStatus.SKIPPED for item in diagnostics):
        return ProviderReadinessStatus.SKIPPED
    if (
        any(item.status == ProviderReadinessStatus.WARNING for item in diagnostics)
        or any(item.quality_errors or item.quality_warnings for item in datasets)
        or int(quality_summary.get("total_errors") or 0) > 0
        or int(quality_summary.get("total_warnings") or 0) > 0
        or (baseline is not None and baseline.has_differences)
    ):
        return ProviderReadinessStatus.WARNING
    return ProviderReadinessStatus.OK


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _securities_status_summary(storage: LocalAshareStorage) -> dict[str, object]:
    records = storage.read_dataset("securities")
    distribution: dict[str, int] = {}
    delisted = 0
    missing_delist = 0
    for record in records:
        status = str(record.get("list_status") or "unknown").upper()
        distribution[status] = distribution.get(status, 0) + 1
        if status == "D":
            delisted += 1
            if record.get("delist_date") in {None, ""}:
                missing_delist += 1
    return {
        "securities_list_status_distribution": distribution,
        "delisted_security_count": delisted,
        "missing_delist_date_count": missing_delist,
        "current_only_security_master_warning": bool(records and set(distribution) <= {"L", "UNKNOWN"}),
    }


def _write_data_lake_version_and_freeze(
    config: AShareDataConfig,
    datasets: list[str],
    output_dir: Path,
    registry_dir: str | Path | None,
    freeze_dir: str | Path | None,
    create_data_freeze: bool,
    paths: dict[str, str],
) -> tuple[dict[str, object], dict[str, str]]:
    registry = LocalDataLakeRegistry(registry_dir or (output_dir / "data_lake_registry"))
    fingerprints = fingerprint_data_dir(config.data_dir, datasets)
    content_hash = content_hash_for_fingerprints(fingerprints)
    version_id = f"dsver_{hashlib.sha256(content_hash.encode('utf-8')).hexdigest()[:16]}"
    version = DatasetVersionRecord(
        dataset_version_id=version_id,
        provider=config.provider,
        data_dir=str(config.data_dir),
        start_date=config.start_date,
        end_date=config.end_date,
        datasets=[item.dataset for item in fingerprints],
        dataset_fingerprints=[item.to_dict() for item in fingerprints],
        quality_report_path=str(Path(config.data_dir) / "quality_report.json"),
        dataset_stats_path=str(Path(config.data_dir) / "dataset_stats.json"),
        api_audit_path=str(Path(config.data_dir) / "api_audit.jsonl"),
        data_source_smoke_report_path=paths.get("data_source_smoke_report_path"),
        created_at=utc_now(),
        status="validated",
        content_hash=content_hash,
        metadata={"source": "data_source_validation"},
    )
    version = registry.register_dataset_version(version)
    lake_output = output_dir / "data_lake"
    version_manifest = write_dataset_version_manifest(version, lake_output)
    lake_json, lake_md = write_data_lake_report(registry, lake_output)
    summary: dict[str, object] = {
        "dataset_version_id": version.dataset_version_id,
        "dataset_content_hash": version.content_hash,
        "data_lake_registry_dir": str(registry.root_dir),
    }
    output_paths = {
        "dataset_version_manifest_path": str(version_manifest),
        "data_lake_report_path": str(lake_json),
        "data_lake_report_md_path": str(lake_md),
        "data_lake_registry_path": str(registry.registry_path),
        "dataset_versions_path": str(registry.versions_path),
    }
    if create_data_freeze:
        target_freeze_dir = Path(freeze_dir) if freeze_dir else output_dir / "research_freeze"
        freeze = create_research_freeze(
            config.data_dir,
            target_freeze_dir,
            version,
            freeze_name=target_freeze_dir.name,
            artifact_paths={
                "data_source_smoke_report_path": paths.get("data_source_smoke_report_path"),
                "quality_report_path": str(Path(config.data_dir) / "quality_report.json"),
                "dataset_stats_path": str(Path(config.data_dir) / "dataset_stats.json"),
            },
        )
        freeze = registry.register_freeze(freeze)
        validation = validate_research_input(data_freeze_dir=freeze.freeze_dir, require_freeze=True)
        validation_path = write_freeze_validation_report(validation, lake_output)
        lake_json, lake_md = write_data_lake_report(registry, lake_output)
        summary.update(
            {
                "data_freeze_id": freeze.freeze_id,
                "data_freeze_dir": freeze.freeze_dir,
                "data_freeze_hash": freeze.content_hash,
                "freeze_validation_status": validation.status,
                "data_hash_drift_count": validation.error_count,
            }
        )
        output_paths.update(
            {
                "research_data_freeze_path": str(Path(freeze.freeze_dir) / "research_data_freeze.json"),
                "freeze_manifest_path": str(Path(freeze.freeze_dir) / "freeze_manifest.json"),
                "freeze_validation_report_path": str(validation_path),
                "research_freezes_path": str(registry.freezes_path),
                "data_lake_report_path": str(lake_json),
                "data_lake_report_md_path": str(lake_md),
            }
        )
    return summary, output_paths

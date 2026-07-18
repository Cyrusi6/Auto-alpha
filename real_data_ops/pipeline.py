"""Pipeline orchestration for real Tushare/sample data operations."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

from artifact_schema.writer import utc_now, write_json_artifact
from data_backfill.executor import execute_backfill_plan
from data_backfill.planner import build_backfill_plan, write_backfill_plan
from data_pipeline.ashare.dataset_registry import TS_CODE_SPLIT_DATASETS
from data_lake import LocalDataLakeRegistry, create_research_freeze
from data_lake.fingerprint import content_hash_for_fingerprints, fingerprint_data_dir
from data_lake.freeze import write_freeze_validation_report, validate_freeze
from data_lake.models import DatasetVersionRecord
from data_lake.report import write_data_lake_report, write_dataset_version_manifest, write_research_freeze
from data_pipeline.ashare.config import AShareDataConfig
from matrix_refresh.refresh import run_matrix_refresh
from matrix_store.builder import build_matrix_cache

from .env_file import redacted_token_metadata
from .models import RealDataPipelineRun, RealDataProfile, RealDataRunStatus
from .readiness import build_readiness_report, write_readiness_report
from .runbook import build_runbook, write_runbook
from .size_report import compute_data_size_report, write_size_report
from .sla import build_real_data_sla_report, write_sla_report


def run_real_data_pipeline(
    *,
    profile: RealDataProfile,
    config: AShareDataConfig,
    data_dir: str | Path,
    output_dir: str | Path,
    staging_dir: str | Path | None = None,
    cache_dir: str | Path | None = None,
    data_lake_registry_dir: str | Path | None = None,
    freeze_dir: str | Path | None = None,
    freeze_name: str | None = None,
    freeze_mode: str | None = None,
    matrix_cache_dir: str | Path | None = None,
    chunk_days: int = 30,
    mode: str = "append",
    cache: bool = False,
    audit: bool = False,
    resume: bool = False,
    validate: bool = False,
    stats: bool = False,
    compact: bool = False,
    snapshot: bool = False,
    direct_append: bool = False,
    trade_days_only: bool = False,
    trade_day_datasets: list[str] | None = None,
    financial_by_ts_code: bool = False,
    financial_ts_codes: list[str] | None = None,
    ts_code_split_datasets: list[str] | None = None,
    build_matrix: bool = False,
    refresh_matrix: bool = False,
    allow_network: bool = False,
    require_token: bool = False,
    fake_tushare_scenario: str | None = None,
    max_requests: int | None = None,
    rate_limit_per_minute: int | None = None,
    disable_rate_limit: bool = False,
    token_expiry: str | None = None,
    command_name: str = "run",
) -> RealDataPipelineRun:
    if config.provider == "tushare" and allow_network and fake_tushare_scenario is None:
        raise RuntimeError("superseded_by_task055j")
    started_at = utc_now()
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    effective_profile = replace(
        profile,
        allow_network=allow_network if allow_network else profile.allow_network,
        require_token=require_token if require_token else profile.require_token,
        rate_limit_per_minute=int(rate_limit_per_minute or profile.rate_limit_per_minute),
        max_requests=max_requests if max_requests is not None else profile.max_requests,
        storage_mode=mode or profile.storage_mode,
        freeze_mode=freeze_mode or profile.freeze_mode,
    )
    if token_expiry:
        metadata = dict(effective_profile.metadata)
        metadata["token_expiry"] = token_expiry
        effective_profile = replace(effective_profile, metadata=metadata)
    paths: dict[str, str | None] = {}
    warnings: list[str] = []
    profile_path = write_json_artifact(root / "real_data_profile.json", effective_profile.to_dict(), "real_data_profile", "real_data_ops")
    paths["real_data_profile_path"] = str(profile_path)
    readiness = build_readiness_report(config, effective_profile)
    readiness_json, readiness_md = write_readiness_report(readiness, root)
    paths["real_data_readiness_report_path"] = str(readiness_json)
    paths["real_data_readiness_report_md_path"] = str(readiness_md)
    if command_name == "readiness":
        plan = build_backfill_plan(
            config,
            datasets=effective_profile.datasets,
            chunk_days=chunk_days,
            chunk_strategy=effective_profile.chunk_strategy,
            dataset_chunk_days=effective_profile.dataset_chunk_days,
            trade_dates=_load_trade_dates(config.data_dir, config.start_date, config.end_date) if trade_days_only else None,
            trade_day_datasets=trade_day_datasets,
            financial_ts_codes=_effective_ts_codes(financial_by_ts_code, financial_ts_codes, config.data_dir),
            ts_code_split_datasets=ts_code_split_datasets or (list(TS_CODE_SPLIT_DATASETS) if financial_by_ts_code else None),
            max_requests=effective_profile.max_requests,
        )
        plan_json, plan_md = write_backfill_plan(plan, root)
        paths["backfill_plan_path"] = str(plan_json)
        paths["backfill_plan_md_path"] = str(plan_md)
        return _write_pipeline_run(effective_profile, root, started_at, "planned", {"readiness": readiness.to_dict()}, paths, warnings)

    if _network_blocked(effective_profile, config, fake_tushare_scenario):
        warnings.append("network_guard_blocked_real_tushare_backfill")
        return _write_pipeline_run(
            effective_profile,
            root,
            started_at,
            RealDataRunStatus.blocked,
            {"readiness": readiness.to_dict(), "token": redacted_token_metadata(config.tushare_token)},
            paths,
            warnings,
        )

    plan = build_backfill_plan(
        config,
        datasets=effective_profile.datasets,
        chunk_days=chunk_days,
        chunk_strategy=effective_profile.chunk_strategy,
        dataset_chunk_days=effective_profile.dataset_chunk_days,
        trade_dates=_load_trade_dates(config.data_dir, config.start_date, config.end_date) if trade_days_only else None,
        trade_day_datasets=trade_day_datasets,
        financial_ts_codes=_effective_ts_codes(financial_by_ts_code, financial_ts_codes, config.data_dir),
        ts_code_split_datasets=ts_code_split_datasets or (list(TS_CODE_SPLIT_DATASETS) if financial_by_ts_code else None),
        max_requests=effective_profile.max_requests,
    )
    plan_json, plan_md = write_backfill_plan(plan, root)
    paths["backfill_plan_path"] = str(plan_json)
    paths["backfill_plan_md_path"] = str(plan_md)
    backfill = execute_backfill_plan(
        plan,
        config,
        data_dir=data_dir,
        output_dir=root,
        staging_dir=staging_dir,
        cache_dir=cache_dir,
        mode=mode,
        cache_enabled=cache,
        audit_enabled=audit,
        resume=resume,
        validate=validate,
        write_stats=stats,
        compact=compact,
        snapshot=snapshot,
        direct_append=direct_append,
        allow_network=allow_network,
        require_token=require_token,
        max_requests=effective_profile.max_requests,
        rate_limit_per_minute=effective_profile.rate_limit_per_minute,
        disable_rate_limit=disable_rate_limit,
        profile_name=effective_profile.profile_name,
        profile_hash=effective_profile.profile_id,
        token_expiry=effective_profile.metadata.get("token_expiry"),
        fake_tushare_scenario=fake_tushare_scenario,
    )
    paths.update(backfill.paths)
    registry_dir = Path(data_lake_registry_dir) if data_lake_registry_dir else root / "data_lake_registry"
    registry = LocalDataLakeRegistry(registry_dir)
    version = _create_dataset_version(
        config=config,
        datasets=effective_profile.datasets,
        data_dir=data_dir,
        output_dir=root,
        registry=registry,
        backfill_paths=backfill.paths,
        profile=effective_profile,
    )
    dataset_version_path = write_dataset_version_manifest(version, root)
    paths["dataset_version_manifest_path"] = str(dataset_version_path)
    freeze_root = Path(freeze_dir) if freeze_dir else root / "research_freeze"
    freeze = create_research_freeze(
        data_dir,
        freeze_root,
        version,
        freeze_name or effective_profile.profile_name,
        mode=effective_profile.freeze_mode,
        artifact_paths=paths,
        matrix_cache_dir=matrix_cache_dir,
    )
    freeze = registry.register_freeze(freeze)
    write_research_freeze(freeze, root)
    freeze_validation = validate_freeze(freeze_root)
    write_freeze_validation_report(freeze_validation, root)
    lake_json, lake_md = write_data_lake_report(registry, root)
    paths.update(
        {
            "research_data_freeze_path": str(root / "research_data_freeze.json"),
            "freeze_validation_report_path": str(root / "freeze_validation_report.json"),
            "data_lake_report_path": str(lake_json),
            "data_lake_report_md_path": str(lake_md),
        }
    )

    matrix_root = Path(matrix_cache_dir) if matrix_cache_dir else freeze_root / "matrix_cache"
    matrix_result_payload: dict[str, Any] | None = None
    matrix_data_dir = Path(freeze.data_dir)
    matrix_missing = _missing_matrix_datasets(matrix_data_dir)
    if build_matrix and matrix_missing:
        warnings.append(f"matrix_build_skipped_missing_datasets:{','.join(matrix_missing)}")
    elif build_matrix:
        build_matrix_cache(
            data_dir=matrix_data_dir,
            output_dir=matrix_root,
            data_freeze_dir=None if effective_profile.freeze_mode == "manifest_only" else freeze_root,
            data_version_manifest_path=dataset_version_path,
            require_data_freeze=False,
            point_in_time=True,
            feature_cutoff_mode="next_trade_day_open",
            corporate_action_aware=True,
            target_return_mode="corporate_action_total_return",
        )
    if refresh_matrix and matrix_missing:
        warnings.append(f"matrix_refresh_skipped_missing_datasets:{','.join(matrix_missing)}")
        matrix_result_payload = {"status": "skipped", "reason": "missing_matrix_datasets", "missing_datasets": matrix_missing}
    elif refresh_matrix:
        matrix_result = run_matrix_refresh(
            data_dir=matrix_data_dir,
            data_freeze_dir=None if effective_profile.freeze_mode == "manifest_only" else freeze_root,
            data_version_manifest_path=dataset_version_path,
            matrix_cache_dir=matrix_root,
            output_dir=root / "matrix_refresh",
            refresh_mode=effective_profile.matrix_refresh_mode,
            point_in_time=True,
            feature_cutoff_mode="next_trade_day_open",
            corporate_action_aware=True,
            target_return_mode="corporate_action_total_return",
        )
        matrix_result_payload = matrix_result.to_dict()
        paths.update(matrix_result.paths)

    size_report = compute_data_size_report(data_dir, matrix_cache_dir=matrix_root, freeze_dir=freeze_root, staging_dir=staging_dir)
    size_json, size_md = write_size_report(size_report, root)
    paths["real_data_size_report_path"] = str(size_json)
    paths["real_data_size_report_md_path"] = str(size_md)
    sla = build_real_data_sla_report(
        backfill_report=backfill.to_dict(),
        matrix_refresh_result=matrix_result_payload,
        required_datasets=effective_profile.datasets,
    )
    sla_json, sla_md, sla_checks = write_sla_report(sla, root)
    paths["real_data_sla_report_path"] = str(sla_json)
    paths["real_data_sla_report_md_path"] = str(sla_md)
    paths["real_data_sla_checks_path"] = str(sla_checks)
    runbook = build_runbook(
        profile=effective_profile,
        data_dir=str(data_dir),
        output_dir=str(root),
        staging_dir=str(staging_dir) if staging_dir else None,
        plan_path=str(plan_json),
        state_path=backfill.paths.get("state_path"),
        completed_jobs=int(backfill.summary.get("success_jobs", 0) or 0),
        failed_jobs=int(backfill.summary.get("failed_jobs", 0) or 0),
        quarantined_jobs=int(backfill.summary.get("quarantined_jobs", 0) or 0),
        estimated_requests=plan.estimated_request_count,
        request_budget_used=int(backfill.summary.get("request_budget_used", 0) or 0),
        token_expiry=effective_profile.metadata.get("token_expiry"),
        resume_command=["python", "-m", "real_data_ops.run_real_data", "resume", "--output-dir", str(root), "--data-dir", str(data_dir)],
    )
    runbook_json, runbook_md = write_runbook(runbook, root)
    paths["real_data_runbook_path"] = str(runbook_json)
    paths["real_data_runbook_md_path"] = str(runbook_md)
    status = RealDataRunStatus.success
    if backfill.status in {"blocked", "failed"}:
        status = backfill.status
    elif sla.status == "fail":
        status = RealDataRunStatus.warning
    return _write_pipeline_run(
        effective_profile,
        root,
        started_at,
        status,
        {
            "backfill": backfill.summary,
            "sla": sla.to_dict(),
            "size": size_report.to_dict(),
            "matrix_refresh": matrix_result_payload or {},
            "token": redacted_token_metadata(config.tushare_token),
        },
        paths,
        warnings,
    )


def _create_dataset_version(
    *,
    config: AShareDataConfig,
    datasets: list[str],
    data_dir: str | Path,
    output_dir: Path,
    registry: LocalDataLakeRegistry,
    backfill_paths: dict[str, str | None],
    profile: RealDataProfile,
) -> DatasetVersionRecord:
    fingerprints = fingerprint_data_dir(data_dir, datasets)
    content_hash = content_hash_for_fingerprints(fingerprints)
    digest = hashlib.sha256(json.dumps({"provider": config.provider, "profile": profile.profile_id, "content_hash": content_hash}, sort_keys=True).encode("utf-8")).hexdigest()
    latest_trade_date = max((fp.last_date or "" for fp in fingerprints), default="") or None
    record = DatasetVersionRecord(
        dataset_version_id=f"dsver_{digest[:16]}",
        provider=config.provider,
        data_dir=str(data_dir),
        start_date=config.start_date,
        end_date=config.end_date,
        datasets=[fp.dataset for fp in fingerprints],
        dataset_fingerprints=[fp.to_dict() for fp in fingerprints],
        quality_report_path=str(Path(data_dir) / "quality_report.json"),
        dataset_stats_path=str(Path(data_dir) / "dataset_stats.json"),
        api_audit_path=str(Path(data_dir) / "api_audit.jsonl"),
        backfill_run_report_path=backfill_paths.get("backfill_run_report_path"),
        backfill_coverage_report_path=backfill_paths.get("backfill_coverage_report_path"),
        created_at=utc_now(),
        status="validated",
        content_hash=content_hash,
        metadata={"real_data_profile": profile.to_dict()},
        data_version_status="validated",
        provider_profile=profile.profile_name,
        real_data_profile_id=profile.profile_id,
        real_data_sla_status=None,
        latest_trade_date=latest_trade_date,
    )
    return registry.register_dataset_version(record)


def _network_blocked(profile: RealDataProfile, config: AShareDataConfig, fake_tushare_scenario: str | None) -> bool:
    if profile.provider != "tushare" or fake_tushare_scenario:
        return False
    if not profile.allow_network:
        return True
    if os.environ.get("RUN_TUSHARE_ONLINE_BACKFILL") != "1":
        return True
    if profile.require_token and not config.tushare_token:
        return True
    return False


def _missing_matrix_datasets(data_dir: str | Path) -> list[str]:
    root = Path(data_dir)
    required = ["securities", "trade_calendar", "daily_bars", "daily_basic", "financial_features"]
    return [dataset for dataset in required if not (root / dataset / "records.jsonl").exists()]


def _load_trade_dates(data_dir: str | Path, start_date: str, end_date: str | None) -> list[str]:
    path = Path(data_dir) / "trade_calendar" / "records.jsonl"
    if not path.exists():
        return []
    end = end_date or start_date
    dates: list[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            trade_date = str(payload.get("trade_date") or "")
            if start_date <= trade_date <= end and bool(payload.get("is_open")):
                dates.append(trade_date)
    return sorted(set(dates))


def _effective_ts_codes(
    enabled: bool,
    explicit_codes: list[str] | None,
    data_dir: str | Path,
) -> list[str] | None:
    if not enabled:
        return None
    if explicit_codes:
        return sorted(set(explicit_codes))
    path = Path(data_dir) / "securities" / "records.jsonl"
    if not path.exists():
        return []
    codes: list[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            code = str(payload.get("ts_code") or "").strip()
            if code:
                codes.append(code)
    return sorted(set(codes))


def _write_pipeline_run(
    profile: RealDataProfile,
    root: Path,
    started_at: str,
    status: str,
    summary: dict[str, Any],
    paths: dict[str, str | None],
    warnings: list[str],
) -> RealDataPipelineRun:
    run_id = f"rdrun_{hashlib.sha256((profile.profile_id + started_at).encode('utf-8')).hexdigest()[:16]}"
    run = RealDataPipelineRun(
        run_id=run_id,
        profile=profile.to_dict(),
        status=status,
        started_at=started_at,
        finished_at=utc_now(),
        summary=summary,
        paths=paths,
        warnings=warnings,
    )
    run_path = write_json_artifact(root / "real_data_pipeline_run.json", run.to_dict(), "real_data_pipeline_run", "real_data_ops")
    report_path = write_json_artifact(root / "real_data_pipeline_report.json", run.to_dict(), "real_data_pipeline_report", "real_data_ops")
    md_path = root / "real_data_pipeline_report.md"
    md_path.write_text(_pipeline_markdown(run), encoding="utf-8")
    run_paths = dict(paths)
    run_paths["real_data_pipeline_run_path"] = str(run_path)
    run_paths["real_data_pipeline_report_path"] = str(report_path)
    run_paths["real_data_pipeline_report_md_path"] = str(md_path)
    return RealDataPipelineRun(run_id, run.profile, run.status, run.started_at, run.finished_at, run.summary, run_paths, run.warnings)


def _pipeline_markdown(run: RealDataPipelineRun) -> str:
    return "\n".join(
        [
            "# Real Data Pipeline Report",
            "",
            f"- run_id: `{run.run_id}`",
            f"- status: `{run.status}`",
            f"- profile: `{run.profile.get('profile_name')}`",
            "",
            "## Paths",
            "",
            *[f"- `{key}`: `{value}`" for key, value in sorted(run.paths.items()) if value],
            "",
        ]
    )

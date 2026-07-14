"""Shard planning and validation_lab execution for validation campaigns."""

from __future__ import annotations

import json
import contextlib
import io
import hashlib
import sys
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_jsonl_artifact
from compute_cluster.models import ComputeDeviceType, ComputeJobKind, ComputeJobSpec, ComputeSchedulerConfig
from compute_cluster.scheduler import LocalComputeScheduler
from validation_lab.run_validation import main as validation_lab_main

from .models import ValidationShardRecord
from .registry import LocalValidationCampaignStore


_FINGERPRINT_FILE_CACHE: dict[tuple[str, int, int], str] = {}


def plan_validation_shards(
    store_dir: str | Path,
    output_dir: str | Path,
    *,
    validation_campaign_id: str,
    shard_count: int = 1,
    max_candidates_per_shard: int | None = None,
) -> list[ValidationShardRecord]:
    store = LocalValidationCampaignStore(store_dir)
    candidates = store.load_candidates()
    if max_candidates_per_shard and max_candidates_per_shard > 0:
        shard_count = max(shard_count, (len(candidates) + max_candidates_per_shard - 1) // max_candidates_per_shard)
    shard_count = max(1, int(shard_count or 1))
    shards: list[ValidationShardRecord] = []
    for idx in range(shard_count):
        rows = candidates[idx::shard_count]
        shard_dir = Path(output_dir) / "validation_shards" / f"shard_{idx:04d}"
        shard_dir.mkdir(parents=True, exist_ok=True)
        pool_path = shard_dir / "candidate_pool.jsonl"
        write_jsonl_artifact(pool_path, [_candidate_to_pool_row(row) for row in rows], "alpha_validation_candidate_pool", "validation_campaign_store")
        shards.append(
            ValidationShardRecord(
                shard_id=f"{validation_campaign_id}_shard_{idx:04d}",
                validation_campaign_id=validation_campaign_id,
                shard_index=idx,
                shard_count=shard_count,
                candidate_count=len(rows),
                output_dir=str(shard_dir),
                status="planned",
                metadata={"candidate_pool_path": str(pool_path)},
            )
        )
    store.write_shards(shards)
    return shards


def run_validation_shards(
    store_dir: str | Path,
    *,
    data_dir: str,
    factor_store_dir: str,
    output_dir: str | Path,
    validation_campaign_id: str,
    shard_count: int = 1,
    max_candidates_per_shard: int | None = None,
    split_method: str = "rolling_walk_forward",
    data_freeze_dir: str | None = None,
    matrix_cache_dir: str | None = None,
    feature_manifest_path: str | None = None,
    feature_tensor_path: str | None = None,
    feature_validity_tensor_path: str | None = None,
    snapshot_proof_manifest_path: str | None = None,
    campaign_manifest_path: str | None = None,
    promotion_policy_path: str | None = None,
    promotion_allowlist_path: str | None = None,
    promotion_denylist_path: str | None = None,
    device: str = "cpu",
    validation_policy: str = "real_long_history_engineering_robustness_v2",
    train_size: int = 756,
    validation_size: int = 126,
    test_size: int = 126,
    step_size: int = 126,
    embargo_size: int = 0,
    label_horizon: int = 1,
    research_end_date: str | None = None,
    holdout_start_date: str | None = None,
    use_compute_scheduler: bool = False,
    compute_state_dir: str | None = None,
    run_multiple_testing: bool = False,
    run_overfit_risk: bool = False,
    run_placebo: bool = False,
    placebo_trials: int = 12,
    run_regime: bool = False,
    run_sensitivity: bool = False,
    run_stress_backtest: bool = False,
    resume: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    store = LocalValidationCampaignStore(store_dir)
    shards = plan_validation_shards(
        store_dir,
        output_dir,
        validation_campaign_id=validation_campaign_id,
        shard_count=shard_count,
        max_candidates_per_shard=max_candidates_per_shard,
    )
    if dry_run:
        return {"status": "planned", "shard_count": len(shards), "paths": store.paths()}

    if use_compute_scheduler:
        if shard_count != 4:
            raise RuntimeError("formal GPU validation requires exactly four shards")
        if not compute_state_dir:
            raise RuntimeError("--compute-state-dir is required with compute scheduler")
        strict_inputs = {
            "data_freeze_dir": data_freeze_dir,
            "matrix_cache_dir": matrix_cache_dir,
            "feature_manifest_path": feature_manifest_path,
            "feature_tensor_path": feature_tensor_path,
            "feature_validity_tensor_path": feature_validity_tensor_path,
            "snapshot_proof_manifest_path": snapshot_proof_manifest_path,
            "promotion_policy_path": promotion_policy_path,
            "promotion_allowlist_path": promotion_allowlist_path,
            "promotion_denylist_path": promotion_denylist_path,
        }
        missing_strict = [name for name, value in strict_inputs.items() if not value or not Path(value).exists()]
        if missing_strict:
            raise RuntimeError(f"formal GPU validation strict inputs missing: {','.join(missing_strict)}")
        jobs: list[ComputeJobSpec] = []
        immutable_resume_count = 0
        for shard in shards:
            argv = _validation_argv(
                shard,
                data_dir=data_dir,
                factor_store_dir=factor_store_dir,
                split_method=split_method,
                data_freeze_dir=data_freeze_dir,
                matrix_cache_dir=matrix_cache_dir,
                feature_manifest_path=feature_manifest_path,
                feature_tensor_path=feature_tensor_path,
                feature_validity_tensor_path=feature_validity_tensor_path,
                snapshot_proof_manifest_path=snapshot_proof_manifest_path,
                campaign_manifest_path=campaign_manifest_path,
                promotion_policy_path=promotion_policy_path,
                promotion_allowlist_path=promotion_allowlist_path,
                promotion_denylist_path=promotion_denylist_path,
                device=device,
                validation_policy=validation_policy,
                train_size=train_size,
                validation_size=validation_size,
                test_size=test_size,
                step_size=step_size,
                embargo_size=embargo_size,
                label_horizon=label_horizon,
                research_end_date=research_end_date,
                holdout_start_date=holdout_start_date,
                run_multiple_testing=run_multiple_testing,
                run_overfit_risk=run_overfit_risk,
                run_placebo=run_placebo,
                placebo_trials=placebo_trials,
                run_regime=run_regime,
                run_sensitivity=run_sensitivity,
                run_stress_backtest=run_stress_backtest,
            )
            fingerprint = _job_fingerprint(argv, shard)
            marker_path = Path(shard.output_dir) / "immutable_input_fingerprint.json"
            report_path = Path(shard.output_dir) / "validation_candidate_pool_report.json"
            if resume and _valid_resume_marker(marker_path, report_path, fingerprint, shard.candidate_count):
                immutable_resume_count += 1
                continue
            jobs.append(
                ComputeJobSpec(
                    job_id=f"validation_{shard.shard_index:02d}_{fingerprint[:16]}",
                    job_kind=ComputeJobKind.SHELL_COMMAND,
                    command=[sys.executable, "-m", "validation_lab.run_validation", *argv],
                    cwd=str(Path.cwd()),
                    input_paths=[str(shard.metadata["candidate_pool_path"]), *(str(path) for path in [data_freeze_dir, matrix_cache_dir, feature_manifest_path, feature_tensor_path, feature_validity_tensor_path, snapshot_proof_manifest_path] if path)],
                    output_dir=shard.output_dir,
                    required_device_type=ComputeDeviceType.CUDA,
                    gpu_count=1,
                    max_retries=1,
                    shard_id=shard.shard_index,
                    shard_count=shard.shard_count,
                    data_freeze_dir=data_freeze_dir,
                    metadata={"immutable_input_fingerprint": fingerprint, "validation_campaign_id": validation_campaign_id},
                )
            )
        if not jobs and immutable_resume_count == len(shards):
            updated = _collect_shard_results(shards)
            store.write_shards(updated)
            return {
                "status": "success",
                "shard_count": len(updated),
                "success_count": sum(row.success_count for row in updated),
                "failed_count": sum(row.failed_count for row in updated),
                "immutable_resume_count": immutable_resume_count,
                "compute_report": _read_existing_compute_report(Path(output_dir) / "compute"),
                "paths": store.paths(),
            }
        scheduler = LocalComputeScheduler(
            ComputeSchedulerConfig(
                state_dir=compute_state_dir,
                output_dir=str(Path(output_dir) / "compute"),
                max_parallel_cpu_jobs=0,
                max_parallel_gpu_jobs=4,
                fail_fast=True,
                dry_run=dry_run,
                resume=resume,
                stale_heartbeat_seconds=300.0,
            )
        )
        previous_run_ids = {str(run.get("run_id")) for run in scheduler.store.read_runs()}
        scheduler.submit_jobs(jobs)
        compute_report = scheduler.run()
        if dry_run:
            return {"status": "planned", "shard_count": len(shards), "compute_report": compute_report.to_dict(), "paths": store.paths()}
        updated = _collect_shard_results(shards)
        submitted_job_ids = {job.job_id for job in jobs}
        current_runs = [run for run in scheduler.store.read_runs() if run.get("job_id") in submitted_job_ids and str(run.get("run_id")) not in previous_run_ids]
        current_gpu_successes = sum(
            run.get("status") == "success" and not run.get("fallback_to_cpu")
            for run in current_runs
        )
        physical_devices = {tuple(run.get("device_indices") or []) for run in current_runs if run.get("status") == "success"}
        if (
            len(current_runs) != len(jobs)
            or current_gpu_successes != len(jobs)
            or immutable_resume_count + current_gpu_successes != 4
            or (jobs and len(physical_devices) != len(jobs))
        ):
            for shard in updated:
                if shard.status == "success":
                    continue
            store.write_shards(updated)
            return {"status": "blocked", "blocked_reason": "four GPU shards did not complete without fallback", "compute_report": compute_report.to_dict(), "paths": store.paths()}
        store.write_shards(updated)
        fingerprints_by_shard = {int(job.shard_id): str(job.metadata.get("immutable_input_fingerprint")) for job in jobs if job.shard_id is not None}
        for shard in updated:
            fingerprint = fingerprints_by_shard.get(shard.shard_index)
            if shard.status == "success" and fingerprint:
                _write_resume_marker(Path(shard.output_dir), fingerprint, shard.candidate_count)
        return {
            "status": "success" if all(row.status == "success" for row in updated) else "partial",
            "shard_count": len(updated),
            "success_count": sum(row.success_count for row in updated),
            "failed_count": sum(row.failed_count for row in updated),
            "compute_report": compute_report.to_dict(),
            "immutable_resume_count": immutable_resume_count,
            "paths": store.paths(),
        }

    updated: list[ValidationShardRecord] = []
    for shard in shards:
        shard_dir = Path(shard.output_dir)
        report_path = shard_dir / "validation_candidate_pool_report.json"
        if resume and report_path.exists():
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            updated.append(_shard_from_payload(shard, payload, "success"))
            continue
        argv = _validation_argv(
            shard, data_dir=data_dir, factor_store_dir=factor_store_dir, split_method=split_method,
            data_freeze_dir=data_freeze_dir, matrix_cache_dir=matrix_cache_dir, feature_manifest_path=feature_manifest_path,
            feature_tensor_path=feature_tensor_path, promotion_policy_path=promotion_policy_path,
            campaign_manifest_path=campaign_manifest_path,
            promotion_allowlist_path=promotion_allowlist_path, promotion_denylist_path=promotion_denylist_path,
            device=device, validation_policy=validation_policy, train_size=train_size, validation_size=validation_size,
            test_size=test_size, step_size=step_size, embargo_size=embargo_size, label_horizon=label_horizon,
            research_end_date=research_end_date, holdout_start_date=holdout_start_date,
            run_multiple_testing=run_multiple_testing, run_overfit_risk=run_overfit_risk, run_placebo=run_placebo,
            placebo_trials=placebo_trials, run_regime=run_regime, run_sensitivity=run_sensitivity,
            run_stress_backtest=run_stress_backtest,
        )
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = validation_lab_main(argv)
        if code == 0 and report_path.exists():
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            updated.append(_shard_from_payload(shard, payload, "success"))
        else:
            updated.append(
                ValidationShardRecord(
                    **{
                        **shard.to_dict(),
                        "status": "failed",
                        "failed_count": shard.candidate_count,
                        "error": f"validation_lab exit code {code}",
                    }
                )
            )
    store.write_shards(updated)
    return {
        "status": "success" if all(row.status == "success" for row in updated) else "partial",
        "shard_count": len(updated),
        "success_count": sum(row.success_count for row in updated),
        "failed_count": sum(row.failed_count for row in updated),
        "paths": store.paths(),
    }


def _validation_argv(shard: ValidationShardRecord, **kwargs) -> list[str]:
    shard_dir = Path(shard.output_dir)
    argv = [
        "validate-candidates", "--data-dir", str(kwargs["data_dir"]), "--factor-store-dir", str(kwargs["factor_store_dir"]),
        "--validation-candidate-pool-path", str(shard_dir / "candidate_pool.jsonl"), "--output-dir", str(shard_dir),
        "--split-method", str(kwargs["split_method"]), "--validation-policy", str(kwargs["validation_policy"]),
        "--train-size", str(kwargs["train_size"]), "--validation-size", str(kwargs["validation_size"]),
        "--test-size", str(kwargs["test_size"]), "--step-size", str(kwargs["step_size"]),
        "--embargo-size", str(kwargs["embargo_size"]), "--label-horizon", str(kwargs["label_horizon"]),
        "--device", str(kwargs["device"]), "--materialization-dir", str(shard_dir / "materialized_factors"),
    ]
    optional = {
        "--data-freeze-dir": kwargs.get("data_freeze_dir"), "--matrix-cache-dir": kwargs.get("matrix_cache_dir"),
        "--feature-set-manifest-path": kwargs.get("feature_manifest_path"), "--feature-tensor-path": kwargs.get("feature_tensor_path"),
        "--feature-validity-tensor-path": kwargs.get("feature_validity_tensor_path"),
        "--snapshot-proof-manifest-path": kwargs.get("snapshot_proof_manifest_path"),
        "--campaign-manifest-path": kwargs.get("campaign_manifest_path"),
        "--feature-promotion-policy-path": kwargs.get("promotion_policy_path"),
        "--feature-promotion-allowlist-path": kwargs.get("promotion_allowlist_path"),
        "--feature-promotion-denylist-path": kwargs.get("promotion_denylist_path"),
        "--research-end-date": kwargs.get("research_end_date"), "--holdout-start-date": kwargs.get("holdout_start_date"),
    }
    for flag, value in optional.items():
        if value:
            argv.extend([flag, str(value)])
    if kwargs.get("feature_tensor_path"):
        argv.append("--strict-materialization")
    for key, flag in {
        "run_multiple_testing": "--run-multiple-testing", "run_overfit_risk": "--run-overfit-risk",
        "run_placebo": "--run-placebo", "run_regime": "--run-regime", "run_sensitivity": "--run-sensitivity",
        "run_stress_backtest": "--run-stress-backtest",
    }.items():
        if kwargs.get(key):
            argv.append(flag)
    if kwargs.get("run_placebo"):
        argv.extend(["--placebo-trials", str(kwargs["placebo_trials"])])
    return argv


def _job_fingerprint(argv: list[str], shard: ValidationShardRecord) -> str:
    pool_path = Path(str(shard.metadata["candidate_pool_path"]))
    path_hashes = {}
    for value in argv:
        path = Path(value)
        if path.is_file():
            path_hashes[str(path)] = _fingerprint_file(path)
        elif path.is_dir():
            manifests = [path / name for name in ["freeze_manifest.json", "dataset_version_manifest.json", "matrix_version_manifest.json"] if (path / name).exists()]
            if manifests:
                path_hashes[str(path)] = {item.name: _fingerprint_file(item) for item in manifests}
    code_files = [Path(__file__), Path(__file__).parents[1] / "validation_lab" / "run_validation.py", Path(__file__).parents[1] / "validation_lab" / "materialization.py", Path(__file__).parents[1] / "validation_lab" / "metrics.py"]
    payload = json.dumps({
        "argv": argv,
        "pool_sha256": _fingerprint_file(pool_path),
        "path_hashes": path_hashes,
        "code_hashes": {str(path): _fingerprint_file(path) for path in code_files},
    }, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _fingerprint_file(path: Path) -> str:
    stat = path.stat()
    key = (str(path.resolve()), int(stat.st_size), int(stat.st_mtime_ns))
    if key in _FINGERPRINT_FILE_CACHE:
        return _FINGERPRINT_FILE_CACHE[key]
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    value = digest.hexdigest()
    _FINGERPRINT_FILE_CACHE[key] = value
    return value


def _valid_resume_marker(marker_path: Path, report_path: Path, fingerprint: str, candidate_count: int) -> bool:
    if not marker_path.exists() or not report_path.exists():
        return False
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        marker.get("immutable_input_fingerprint") == fingerprint
        and marker.get("report_sha256") == _fingerprint_file(report_path)
        and int(marker.get("candidate_count", -1)) == int(candidate_count)
        and int(report.get("validated_candidate_count", -1)) == int(candidate_count)
    )


def _write_resume_marker(shard_dir: Path, fingerprint: str, candidate_count: int) -> None:
    report_path = shard_dir / "validation_candidate_pool_report.json"
    payload = {
        "immutable_input_fingerprint": fingerprint,
        "candidate_count": int(candidate_count),
        "report_sha256": _fingerprint_file(report_path),
    }
    (shard_dir / "immutable_input_fingerprint.json").write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _read_existing_compute_report(compute_dir: Path) -> dict[str, Any]:
    path = compute_dir / "compute_run_report.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"status": "resumed", "job_count": 0}


def _collect_shard_results(shards: list[ValidationShardRecord]) -> list[ValidationShardRecord]:
    updated = []
    for shard in shards:
        report_path = Path(shard.output_dir) / "validation_candidate_pool_report.json"
        if report_path.exists():
            updated.append(_shard_from_payload(shard, json.loads(report_path.read_text(encoding="utf-8")), "success"))
        else:
            updated.append(ValidationShardRecord(**{**shard.to_dict(), "status": "failed", "failed_count": shard.candidate_count, "error": "validation report missing"}))
    return updated


def _candidate_to_pool_row(row: dict[str, Any]) -> dict[str, Any]:
    source = row.get("metadata", {}).get("source_candidate", {}) if isinstance(row.get("metadata"), dict) else {}
    return {
        **source,
        "factor_id": row.get("factor_id"),
        "formula_hash": row.get("formula_hash"),
        "formula_names": row.get("formula_names", []),
        "feature_version": row.get("feature_version", ""),
        "source_campaign": row.get("source_campaign_id", ""),
        "rank": row.get("alpha_rank", 0),
        "final_score": row.get("alpha_score", 0.0),
        "factor_store_dir": row.get("factor_store_dir", ""),
        "factor_values_path": row.get("factor_values_path", ""),
        "family": (row.get("family_tags") or ["general"])[0],
    }


def _shard_from_payload(shard: ValidationShardRecord, payload: dict[str, Any], status: str) -> ValidationShardRecord:
    return ValidationShardRecord(
        shard_id=shard.shard_id,
        validation_campaign_id=shard.validation_campaign_id,
        shard_index=shard.shard_index,
        shard_count=shard.shard_count,
        candidate_count=shard.candidate_count,
        success_count=int(payload.get("validated_candidate_count", 0) or 0) - int(payload.get("blocked_count", 0) or 0),
        failed_count=int(payload.get("blocked_count", 0) or 0),
        skipped_count=0,
        output_dir=shard.output_dir,
        validation_lab_report_path=str(Path(shard.output_dir) / "validation_candidate_pool_report.json"),
        status=status,
        error=None,
        metadata=shard.metadata | {"payload": payload},
    )

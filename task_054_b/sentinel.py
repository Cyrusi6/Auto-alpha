"""Task 054-B fixed production-component firewall sentinel."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import torch

from alpha_factory.models import AlphaCandidateRecord
from alpha_factory.proxy_eval import run_proxy_eval
from compute_cluster import LocalComputeScheduler
from compute_cluster.models import ComputeDeviceType, ComputeJobKind, ComputeJobSpec, ComputeSchedulerConfig
from data_lake.task052_freeze import FREEZE_MANIFEST_FILENAMES
from factor_store.models import FactorRecord
from factor_store.storage import LocalFactorStore
from feature_factory import make_formula_vocab_from_manifest
from feature_factory.builder import load_feature_manifest
from formula_batch_eval import FormulaBatchEvalConfig, FormulaBatchEvaluator
from formula_batch_eval.models import FormulaEvalRequest
from matrix_store import StrictEngineeringPITMatrixBuilder, StrictEngineeringPITMatrixConfig
from model_core.data_loader import AShareDataLoader
from model_core.vm import StackVM
from research_firewall import ResearchEligibilityContract
from task_053_a.orchestrator import build_v3_tensor_generation
from task_054_c.research_view import publish_research_projection, validate_research_projection
from validation_campaign_store.consolidate import consolidate_validation_results
from validation_lab.materialization import FactorMaterializer, MaterializationInputs
from validation_lab.run_validation import main as validation_lab_main

from .audit import (
    AuditedReadBroker,
    ComponentReceiptRecorder,
    atomic_json,
    sha256_file,
    validate_component_receipts,
    validate_read_ledger,
)


EVIDENCE_SCOPE = "real_production"
PATHS = ("raw_local", "raw_scheduler", "matrix_local", "matrix_scheduler")
MUTATIONS = ("baseline", "post_cutoff", "inside_cutoff")
REQUIRED_COMPONENTS = (
    "loader",
    "strict_matrix_builder",
    "v3_tensor_builder",
    "research_projection_publisher",
    "stackvm_validity",
    "alpha_proxy",
    "formula_batch_evaluator",
    "factor_materializer",
    "validation_lab",
    "consolidation",
)
RESULT_FILE = "task_054b_path_result.json"
LEDGER_FILE = "task_054b_read_ledger.jsonl"
RECEIPTS_FILE = "task_054b_component_receipts.jsonl"


@dataclass(frozen=True)
class ProductionSentinelConfig:
    governed_freeze_dir: str
    universe_dir: str
    published_matrix_dir: str
    published_tensor_dir: str
    feature_manifest_path: str
    probe_factor_path: str
    promotion_policy_path: str
    output_root: str
    research_end_date: str = "20240530"
    holdout_start_date: str = "20240531"
    label_horizon: int = 2
    timeout_seconds: int = 1800


@dataclass(frozen=True)
class SentinelRunSpec:
    invocation_id: str
    path_name: str
    mutation_kind: str
    source_kind: str
    execution_kind: str
    config_path: str
    output_dir: str
    job_id: str | None = None


@dataclass(frozen=True)
class ProductionSentinelPlan:
    config: ProductionSentinelConfig
    runs: tuple[SentinelRunSpec, ...]
    mutation_manifest_path: str
    plan_hash: str


def build_production_sentinel_plan(config: ProductionSentinelConfig) -> ProductionSentinelPlan:
    """Build the only accepted 12-run plan from governed artifacts."""
    _validate_config_inputs(config)
    output_root = Path(config.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    mutation_manifest = _prepare_mutation_generations(config, output_root / "mutation_generations")
    mutation_manifest_path = output_root / "task_054b_mutation_generations.json"
    atomic_json(mutation_manifest_path, mutation_manifest)
    immutable_input_hashes = {
        "feature_manifest": sha256_file(config.feature_manifest_path),
        "probe_factor": sha256_file(config.probe_factor_path),
        "promotion_policy": sha256_file(config.promotion_policy_path),
        "mutation_manifest": sha256_file(mutation_manifest_path),
    }
    runs: list[SentinelRunSpec] = []
    for mutation in MUTATIONS:
        for path_name in PATHS:
            source_kind, execution_kind = path_name.split("_", 1)
            invocation = f"task054b_{mutation}_{path_name}_{mutation_manifest['content_hash'][:12]}"
            run_dir = output_root / "runs" / mutation / path_name
            worker_config = {
                "schema_version": "task_054b_worker_config_v1",
                "evidence_scope": EVIDENCE_SCOPE,
                "invocation_id": invocation,
                "path_name": path_name,
                "mutation_kind": mutation,
                "source_kind": source_kind,
                "execution_kind": execution_kind,
                "research_end_date": config.research_end_date,
                "holdout_start_date": config.holdout_start_date,
                "label_horizon": config.label_horizon,
                "universe_dir": config.universe_dir,
                "feature_manifest_path": config.feature_manifest_path,
                "probe_factor_path": config.probe_factor_path,
                "promotion_policy_path": config.promotion_policy_path,
                "freeze_dir": mutation_manifest["generations"][mutation]["freeze_dir"],
                "matrix_dir": mutation_manifest["generations"][mutation]["matrix_dir"],
                "tensor_dir": mutation_manifest["generations"][mutation]["tensor_dir"],
                "output_dir": str(run_dir),
                "mutation_manifest_path": str(mutation_manifest_path),
                "immutable_input_hashes": immutable_input_hashes,
                "research_cache_dir": str(output_root / "research_cache" / path_name),
            }
            worker_config_path = output_root / "worker_configs" / f"{mutation}_{path_name}.json"
            atomic_json(worker_config_path, worker_config)
            job_id = f"sentinel_{hashlib.sha256(invocation.encode()).hexdigest()[:20]}" if execution_kind == "scheduler" else None
            runs.append(
                SentinelRunSpec(
                    invocation_id=invocation,
                    path_name=path_name,
                    mutation_kind=mutation,
                    source_kind=source_kind,
                    execution_kind=execution_kind,
                    config_path=str(worker_config_path),
                    output_dir=str(run_dir),
                    job_id=job_id,
                )
            )
    semantic = {
        "config": {key: value for key, value in asdict(config).items() if key != "output_root"},
        "mutation_content_hash": mutation_manifest["content_hash"],
        "immutable_input_hashes": immutable_input_hashes,
        "runs": [asdict(run) for run in runs],
    }
    return ProductionSentinelPlan(config, tuple(runs), str(mutation_manifest_path), _hash_json(semantic))


def run_task054b_production_sentinel(config: ProductionSentinelConfig) -> dict[str, Any]:
    plan = build_production_sentinel_plan(config)
    output_root = Path(config.output_root)
    executions: dict[str, dict[str, Any]] = {mutation: {} for mutation in MUTATIONS}
    scheduler_runs = [run for run in plan.runs if run.execution_kind == "scheduler"]
    local_runs = [run for run in plan.runs if run.execution_kind == "local"]
    for run in local_runs:
        _run_local_worker(run, timeout=config.timeout_seconds)
    scheduler_evidence = _run_scheduler_workers(scheduler_runs, output_root, timeout=config.timeout_seconds)
    for run in plan.runs:
        result = _load_and_validate_path_result(run)
        if run.execution_kind == "scheduler":
            result["scheduler_evidence"] = scheduler_evidence[run.job_id or ""]
        executions[run.mutation_kind][run.path_name] = result
    proof, blockers = _validate_cross_run_invariants(executions)
    payload = {
        "schema_version": "task_054b_production_sentinel_v1",
        "evidence_scope": EVIDENCE_SCOPE,
        "status": "passed" if not blockers else "blocked",
        "plan_hash": plan.plan_hash,
        "mutation_manifest_sha256": sha256_file(plan.mutation_manifest_path),
        "exact_run_count": len(plan.runs),
        "executions": executions,
        "proof": proof,
        "blockers": blockers,
    }
    payload["content_hash"] = _hash_json(payload)
    artifact_path = output_root / "task_054b_production_sentinel.json"
    atomic_json(artifact_path, payload)
    validate_task054b_production_sentinel(artifact_path, scheduler_state_dir=output_root / "scheduler_state")
    return payload | {"artifact_path": str(artifact_path)}


def validate_task054b_production_sentinel(
    artifact: str | Path | Mapping[str, Any],
    *,
    scheduler_state_dir: str | Path,
) -> dict[str, Any]:
    payload = json.loads(Path(artifact).read_text(encoding="utf-8")) if not isinstance(artifact, Mapping) else dict(artifact)
    if payload.get("evidence_scope") != EVIDENCE_SCOPE:
        raise RuntimeError("production sentinel evidence_scope must equal real_production")
    if payload.get("status") != "passed" or payload.get("blockers"):
        raise RuntimeError("blocked sentinel cannot be wrapped as passed")
    executions = payload.get("executions") or {}
    expected = {(mutation, path_name) for mutation in MUTATIONS for path_name in PATHS}
    actual = {(mutation, path_name) for mutation, rows in executions.items() for path_name in rows}
    if actual != expected or int(payload.get("exact_run_count", 0)) != 12:
        raise RuntimeError("production sentinel must contain exact 12 runs")
    state_dir = Path(scheduler_state_dir)
    jobs = {row["job_id"]: row for row in _read_jsonl(state_dir / "compute_jobs.jsonl")}
    runs = {row["job_id"]: row for row in _read_jsonl(state_dir / "compute_job_runs.jsonl")}
    heartbeats = _read_jsonl(state_dir / "compute_heartbeats.jsonl")
    for mutation, path_name in sorted(expected):
        row = executions[mutation][path_name]
        if row.get("evidence_scope") != EVIDENCE_SCOPE or row.get("status") != "success":
            raise RuntimeError(f"invalid production path result:{mutation}:{path_name}")
        ledger = row.get("read_ledger") or []
        receipts = row.get("component_receipts") or []
        validate_read_ledger(ledger, invocation_id=row["invocation_id"])
        validate_component_receipts(receipts, invocation_id=row["invocation_id"], required_components=REQUIRED_COMPONENTS)
        if path_name.endswith("scheduler"):
            evidence = row.get("scheduler_evidence") or {}
            job_id = evidence.get("job_id")
            if job_id not in jobs or job_id not in runs:
                raise RuntimeError(f"scheduler job missing from state store:{job_id}")
            if runs[job_id].get("status") != "success" or int(runs[job_id].get("return_code", -1)) != 0:
                raise RuntimeError(f"scheduler run state mismatch:{job_id}")
            job_heartbeats = [item for item in heartbeats if item.get("job_id") == job_id]
            if len(job_heartbeats) < 2 or int(evidence.get("heartbeat_count", 0)) != len(job_heartbeats):
                raise RuntimeError(f"scheduler heartbeat reconciliation failed:{job_id}")
            if evidence.get("run_id") != runs[job_id].get("run_id") or int(evidence.get("attempt", 0)) != int(runs[job_id].get("attempt", -1)):
                raise RuntimeError(f"scheduler run/attempt reconciliation failed:{job_id}")
    proof = payload.get("proof") or {}
    if not all(bool(value) for value in (proof.get("post_cutoff_invariant") or {}).values()):
        raise RuntimeError("post-cutoff leakage detected")
    if not all(bool(value) for value in (proof.get("inside_cutoff_cache_miss") or {}).values()):
        raise RuntimeError("inside-cutoff cache hit detected")
    if not all(bool(value) for value in (proof.get("cache_contract") or {}).values()):
        raise RuntimeError("research cache contract failed")
    if not all(bool(value) for value in (proof.get("mutation_applied") or {}).values()):
        raise RuntimeError("sentinel mutation was not applied")
    if not all(bool(value) for value in (proof.get("semantic_consistency") or {}).values()):
        raise RuntimeError("raw/matrix or local/scheduler semantic mismatch")
    return {"status": "passed", "run_count": 12, "content_hash": payload.get("content_hash")}


def _run_local_worker(run: SentinelRunSpec, *, timeout: int) -> None:
    command = [sys.executable, "-m", "task_054_b.sentinel", "worker", "--config", run.config_path]
    completed = subprocess.run(command, cwd=str(Path(__file__).resolve().parents[1]), capture_output=True, text=True, timeout=timeout)
    if completed.returncode != 0:
        raise RuntimeError(f"local production sentinel failed:{run.invocation_id}:{completed.stderr[-2000:]}")


def _run_scheduler_workers(runs: Sequence[SentinelRunSpec], output_root: Path, *, timeout: int) -> dict[str, dict[str, Any]]:
    state_dir = output_root / "scheduler_state"
    compute_output = output_root / "scheduler_compute"
    jobs = [
        ComputeJobSpec(
            job_id=str(run.job_id),
            job_kind=ComputeJobKind.SHELL_COMMAND,
            command=[sys.executable, "-m", "task_054_b.sentinel", "worker", "--config", run.config_path],
            cwd=str(Path(__file__).resolve().parents[1]),
            input_paths=[run.config_path],
            output_dir=run.output_dir,
            required_device_type=ComputeDeviceType.CPU,
            max_duration_seconds=float(timeout),
            max_retries=0,
            metadata={"task": "054-B", "evidence_scope": EVIDENCE_SCOPE, "invocation_id": run.invocation_id},
        )
        for run in runs
    ]
    scheduler = LocalComputeScheduler(
        ComputeSchedulerConfig(
            state_dir=str(state_dir),
            output_dir=str(compute_output),
            max_parallel_cpu_jobs=4,
            max_parallel_gpu_jobs=0,
            fail_fast=True,
            resume=False,
        )
    )
    submitted_count = 0
    for mutation in MUTATIONS:
        stage_jobs = [job for job, run in zip(jobs, runs) if run.mutation_kind == mutation]
        submitted = scheduler.submit_jobs(stage_jobs)
        if submitted["submitted"] != len(stage_jobs):
            raise RuntimeError("sentinel scheduler reused stale jobs")
        submitted_count += submitted["submitted"]
        scheduler.run()
    if submitted_count != len(jobs):
        raise RuntimeError("sentinel scheduler submission count mismatch")
    run_rows = [row for row in scheduler.store.read_runs() if row.get("job_id") in {job.job_id for job in jobs}]
    heartbeat_rows = _read_jsonl(scheduler.store.heartbeats_path)
    evidence: dict[str, dict[str, Any]] = {}
    for job in jobs:
        matches = [row for row in run_rows if row.get("job_id") == job.job_id]
        if len(matches) != 1:
            raise RuntimeError(f"scheduler run cardinality mismatch:{job.job_id}")
        run = matches[0]
        heartbeats = [row for row in heartbeat_rows if row.get("job_id") == job.job_id]
        evidence[job.job_id] = {
            "job_id": job.job_id,
            "run_id": run.get("run_id"),
            "attempt": run.get("attempt"),
            "status": run.get("status"),
            "exit_code": run.get("return_code"),
            "heartbeat_count": len(heartbeats),
            "state_store_job_sha256": _hash_json(job.to_dict()),
            "command_hash": _hash_json(job.command),
        }
    return evidence


def _run_worker(config_path: str | Path) -> dict[str, Any]:
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    if config.get("evidence_scope") != EVIDENCE_SCOPE:
        raise RuntimeError("worker evidence_scope must equal real_production")
    expected_hashes = config.get("immutable_input_hashes") or {}
    actual_hashes = {
        "feature_manifest": sha256_file(config["feature_manifest_path"]),
        "probe_factor": sha256_file(config["probe_factor_path"]),
        "promotion_policy": sha256_file(config["promotion_policy_path"]),
        "mutation_manifest": sha256_file(config["mutation_manifest_path"]),
    }
    if expected_hashes != actual_hashes:
        raise RuntimeError("production sentinel immutable input hash mismatch")
    output = Path(config["output_dir"])
    output.mkdir(parents=True, exist_ok=True)
    broker = AuditedReadBroker(
        output / LEDGER_FILE,
        invocation_id=config["invocation_id"],
        principal="research",
        research_end_date=config["research_end_date"],
    )
    supervisor_broker = AuditedReadBroker(
        output / LEDGER_FILE,
        invocation_id=config["invocation_id"],
        principal="projection_publisher",
        research_end_date=config["research_end_date"],
    )
    receipts = ComponentReceiptRecorder(output / RECEIPTS_FILE, invocation_id=config["invocation_id"])
    result = _execute_production_components(config, broker, supervisor_broker, receipts)
    ledger_rows = broker.rows()
    receipt_rows = receipts.rows()
    validate_read_ledger(ledger_rows, invocation_id=config["invocation_id"])
    validate_component_receipts(receipt_rows, invocation_id=config["invocation_id"], required_components=REQUIRED_COMPONENTS)
    payload = {
        "schema_version": "task_054b_path_result_v1",
        "evidence_scope": EVIDENCE_SCOPE,
        "status": "success",
        "invocation_id": config["invocation_id"],
        "path_name": config["path_name"],
        "mutation_kind": config["mutation_kind"],
        "source_kind": config["source_kind"],
        "execution_kind": config["execution_kind"],
        **result,
        "read_ledger": ledger_rows,
        "component_receipts": receipt_rows,
    }
    payload["result_hash"] = _hash_json(payload)
    atomic_json(output / RESULT_FILE, payload)
    return payload


def _execute_production_components(
    config: Mapping[str, Any],
    broker: AuditedReadBroker,
    supervisor_broker: AuditedReadBroker,
    receipts: ComponentReceiptRecorder,
) -> dict[str, Any]:
    output = Path(config["output_dir"])
    freeze_dir = Path(config["freeze_dir"])
    matrix_dir = Path(config["matrix_dir"])
    tensor_dir = Path(config["tensor_dir"])
    feature_manifest_path = Path(config["feature_manifest_path"])
    factor_path = Path(config["probe_factor_path"])
    source_dates = supervisor_broker.read_json(
        matrix_dir / "trade_dates.json",
        component="research_projection_publisher",
        dataset="full_trade_dates",
        date_range=None,
    )
    if not source_dates or max(source_dates) > "20260630":
        raise RuntimeError("sentinel source date boundary invalid")
    if config["source_kind"] == "raw":
        matrix_output_root = output / "rebuilt_matrix"
        builder = StrictEngineeringPITMatrixBuilder(
            StrictEngineeringPITMatrixConfig(
                research_observable_cutoff=config["research_end_date"],
                target_endpoint_horizon_trade_days=int(config["label_horizon"]),
            )
        )
        matrix_result = receipts.invoke(
            "strict_matrix_builder",
            builder.build,
            governed_freeze_dir=freeze_dir,
            historical_universe_dir=config["universe_dir"],
            output_root=matrix_output_root,
            input_artifacts={"freeze_manifest": _freeze_manifest_path(freeze_dir), "universe_proof": _universe_manifest_path(Path(config["universe_dir"]))},
            output_artifacts=lambda value: {"matrix_manifest": Path(value.manifest_path)},
        )
        matrix_dir = Path(matrix_result.generation_dir)
        tensor_result = receipts.invoke(
            "v3_tensor_builder",
            build_v3_tensor_generation,
            matrix_dir=matrix_dir,
            feature_manifest_path=feature_manifest_path,
            output_root=output / "rebuilt_tensor",
            input_artifacts={"matrix_manifest": Path(matrix_result.manifest_path), "feature_manifest": feature_manifest_path},
            output_artifacts=lambda value: {"tensor_manifest": Path(value["generation_dir"]) / "task_053_v3_tensor_manifest.json"},
        )
        tensor_dir = Path(tensor_result["generation_dir"])
    else:
        receipts.invoke(
            "strict_matrix_builder",
            _validate_published_matrix,
            matrix_dir,
            input_artifacts={"matrix_manifest": _matrix_manifest_path(matrix_dir)},
            output_artifacts={"matrix_manifest": _matrix_manifest_path(matrix_dir)},
        )
        receipts.invoke(
            "v3_tensor_builder",
            _validate_published_tensor,
            tensor_dir,
            input_artifacts={"tensor_manifest": tensor_dir / "task_053_v3_tensor_manifest.json"},
            output_artifacts={"tensor_manifest": tensor_dir / "task_053_v3_tensor_manifest.json"},
        )
    projection = receipts.invoke(
        "research_projection_publisher",
        publish_research_projection,
        matrix_root=matrix_dir,
        tensor_root=tensor_dir,
        output_root=output / "research_projection",
        research_end_date=config["research_end_date"],
        input_artifacts={
            "matrix_manifest": _matrix_manifest_path(matrix_dir),
            "tensor_manifest": tensor_dir / "task_053_v3_tensor_manifest.json",
        },
        output_artifacts=lambda value: {"projection_manifest": Path(value["manifest_path"])},
    )
    projection = validate_research_projection(projection["manifest_path"])
    full_matrix_dir = matrix_dir
    full_tensor_dir = tensor_dir
    matrix_dir = Path(projection["matrix_dir"])
    tensor_dir = Path(projection["tensor_dir"])
    dates = broker.read_json(
        matrix_dir / "trade_dates.json",
        component="loader",
        dataset="research_trade_dates",
        date_range=[projection["research_date_start"], projection["research_date_end"]],
    )
    matrix_manifest = broker.read_json(_matrix_manifest_path(matrix_dir), component="loader", dataset="strict_matrix_manifest", date_range=dates)
    eligible_mask = broker.load_npy(
        matrix_dir / "research_eligible_date_mask.npy",
        component="loader",
        dataset="research_eligible_date_mask",
        date_range=dates,
    )
    eligible_mask, eligibility_hash = _validated_projection_eligibility(
        dates, eligible_mask, matrix_manifest
    )
    research_dates = [dates[index] for index, allowed in enumerate(eligible_mask) if allowed]
    if not research_dates:
        raise RuntimeError("physical research projection has no eligible signal dates")
    tensor_manifest = broker.read_json(tensor_dir / "task_053_v3_tensor_manifest.json", component="loader", dataset="v3_tensor_manifest", date_range=dates)
    values = broker.load_npy(tensor_dir / "feature_tensor.npy", component="loader", dataset="feature_tensor", date_range=dates)
    validity = broker.load_npy(tensor_dir / "feature_validity_tensor.npy", component="loader", dataset="feature_validity_tensor", date_range=dates)
    target = broker.load_npy(_first_existing(matrix_dir, "target_open_t1_t2.npy", "next_open_t1_t2_return.npy"), component="loader", dataset="target", date_range=dates)
    target_available = broker.load_npy(_first_existing(matrix_dir, "target_available.npy", "target_available_mask.npy"), component="loader", dataset="target_available", date_range=dates)
    factor_payload = broker.read_json(factor_path, component="loader", dataset="probe_factor", date_range=research_dates)
    factor = FactorRecord(**factor_payload)
    research_indices = np.asarray(eligible_mask, dtype=np.bool_)
    research_projection_hash = _hash_arrays(
        np.asarray(values)[:, :, research_indices],
        np.asarray(validity)[:, :, research_indices],
    )
    bounded_research_identity = _hash_json(
        {
            "eligible": eligibility_hash,
            "research_projection": research_projection_hash,
        }
    )
    research_cache_key = _hash_json(
        {
            "research_computation_identity": bounded_research_identity,
            "factor": factor.formula_hash,
        }
    )
    cache_probe = _probe_research_cache_contract(
        cache_dir=Path(config["research_cache_dir"]),
        mutation_kind=str(config["mutation_kind"]),
        cache_key=research_cache_key,
    )
    feature_manifest = load_feature_manifest(feature_manifest_path)
    vocab = make_formula_vocab_from_manifest(feature_manifest)
    vm = StackVM(vocab)
    values_tensor = torch.from_numpy(np.asarray(values)).to(torch.float32)
    validity_tensor = torch.from_numpy(np.asarray(validity)).to(torch.bool)
    stack_output = output / "stackvm_result.npz"
    executed = receipts.invoke(
        "stackvm_validity",
        vm.execute_with_validity,
        factor.formula_tokens,
        values_tensor,
        validity_tensor,
        input_artifacts={"values": tensor_dir / "feature_tensor.npy", "validity": tensor_dir / "feature_validity_tensor.npy"},
        output_artifacts=lambda value: _save_stack_output(stack_output, value),
    )
    if executed is None:
        raise RuntimeError("StackVM returned no result")
    loader = receipts.invoke(
        "loader",
        AShareDataLoader(
            matrix_cache_dir=matrix_dir,
            use_matrix_cache=True,
            feature_set_name=feature_manifest.feature_set_name,
            feature_set_manifest_path=feature_manifest_path,
            research_end_date=config["research_end_date"],
            holdout_start_date=config["holdout_start_date"],
            label_horizon=int(config["label_horizon"]),
            canonical_feature_tensor_path=tensor_dir / "feature_tensor.npy",
            canonical_feature_validity_tensor_path=tensor_dir / "feature_validity_tensor.npy",
            device="cpu",
        ).load_data,
        input_artifacts={"matrix_manifest": _matrix_manifest_path(matrix_dir), "feature_manifest": feature_manifest_path},
        output_artifacts={"matrix_manifest": _matrix_manifest_path(matrix_dir)},
    )
    candidate = AlphaCandidateRecord(
        alpha_candidate_id=factor.factor_id,
        formula_hash=factor.formula_hash,
        formula_tokens=factor.formula_tokens,
        formula_names=factor.formula,
        source="task054b_probe",
        source_refs=[],
        feature_set_name=feature_manifest.feature_set_name,
        feature_version=factor.feature_version,
        operator_version=factor.operator_version,
        complexity=int((factor.metadata or {}).get("complexity", 1)),
        lookback=factor.lookback_days,
        family_tags=[],
    )
    proxy_path = output / "proxy_result.json"
    proxy_result = receipts.invoke(
        "alpha_proxy",
        run_proxy_eval,
        [candidate],
        loader,
        max_candidates=1,
        max_dates=min(63, max(1, len(research_dates))),
        vocab=vocab,
        input_artifacts={"matrix_manifest": _matrix_manifest_path(matrix_dir), "factor": factor_path},
        output_artifacts=lambda value: _save_json_result(proxy_path, {"rows": value[1], "summary": value[2]}),
    )
    batch_output = output / "formula_batch"
    batch_config = FormulaBatchEvalConfig(
        data_dir=str(freeze_dir),
        factor_store_dir=str(output / "batch_factor_store"),
        report_dir=str(batch_output),
        output_dir=str(batch_output),
        matrix_cache_dir=str(matrix_dir),
        use_matrix_cache=True,
        device="cpu",
        feature_set_name=feature_manifest.feature_set_name,
        feature_set_manifest_path=str(feature_manifest_path),
        research_end_date=config["research_end_date"],
        holdout_start_date=config["holdout_start_date"],
        label_horizon=int(config["label_horizon"]),
        canonical_feature_tensor_path=str(tensor_dir / "feature_tensor.npy"),
        canonical_feature_validity_tensor_path=str(tensor_dir / "feature_validity_tensor.npy"),
        research_computation_identity=bounded_research_identity,
        skip_existing=False,
        continue_on_error=False,
    )
    request = FormulaEvalRequest(
        name=factor.factor_id,
        formula_tokens=factor.formula_tokens,
        formula_names=factor.formula,
        formula_hash=factor.formula_hash,
        complexity=int((factor.metadata or {}).get("complexity", 1)),
        lookback=factor.lookback_days,
    )
    batch_result = receipts.invoke(
        "formula_batch_evaluator",
        FormulaBatchEvaluator(batch_config).run,
        [request],
        input_artifacts={"matrix_manifest": _matrix_manifest_path(matrix_dir), "factor": factor_path},
        output_artifacts=lambda value: {"batch_result": Path(value.paths["formula_batch_eval_result_path"])},
    )
    materializer = FactorMaterializer(
        MaterializationInputs(
            data_freeze_dir=str(freeze_dir),
            matrix_cache_dir=str(matrix_dir),
            feature_manifest_path=str(feature_manifest_path),
            feature_tensor_path=str(tensor_dir / "feature_tensor.npy"),
            feature_validity_tensor_path=str(tensor_dir / "feature_validity_tensor.npy"),
            promotion_policy_path=config["promotion_policy_path"],
            research_end_date=config["research_end_date"],
            label_horizon=int(config["label_horizon"]),
            research_eligible_date_mask_path=str(matrix_dir / "research_eligible_date_mask.npy"),
            eligibility_contract_hash=eligibility_hash,
            research_computation_identity=bounded_research_identity,
        ),
        output / "materialized",
    )
    materialization = receipts.invoke(
        "factor_materializer",
        materializer.materialize,
        factor,
        input_artifacts={"tensor_manifest": tensor_dir / "task_053_v3_tensor_manifest.json", "factor": factor_path},
        output_artifacts=lambda value: {"materialization_manifest": value.manifest_path},
    )
    validation_factor_store = output / "validation_factor_store"
    LocalFactorStore(validation_factor_store).save_factor(factor)
    validation_output = output / "validation"
    validation_args = [
        "validate-factor", "--factor-store-dir", str(validation_factor_store), "--factor-id", factor.factor_id,
        "--output-dir", str(validation_output), "--data-freeze-dir", str(freeze_dir), "--matrix-cache-dir", str(matrix_dir),
        "--feature-set-manifest-path", str(feature_manifest_path), "--feature-tensor-path", str(tensor_dir / "feature_tensor.npy"),
        "--feature-validity-tensor-path", str(tensor_dir / "feature_validity_tensor.npy"), "--materialization-manifest-path", materialization.manifest_path,
        "--strict-materialization",
        "--research-end-date", config["research_end_date"], "--holdout-start-date", config["holdout_start_date"], "--label-horizon", str(config["label_horizon"]),
    ]
    validation_code = receipts.invoke(
        "validation_lab",
        validation_lab_main,
        validation_args,
        input_artifacts={"materialization_manifest": materialization.manifest_path, "matrix_manifest": _matrix_manifest_path(matrix_dir)},
        output_artifacts=lambda value: {"validation_summary": _find_validation_summary(validation_output)},
    )
    if validation_code not in {0, 2}:
        raise RuntimeError(f"validation lab failed:{validation_code}")
    campaign_store = _prepare_consolidation_store(output, factor, validation_output)
    consolidation = receipts.invoke(
        "consolidation",
        consolidate_validation_results,
        campaign_store,
        output_dir=output / "consolidated",
        input_artifacts={"validation_summary": _find_validation_summary(validation_output)},
        output_artifacts=lambda value: {"consolidation": Path(value["paths"]["validation_campaign_consolidation_report_path"])},
    )
    factor_values, factor_validity = executed
    research_factor = factor_values[:, research_indices]
    research_validity = factor_validity[:, research_indices]
    proxy_semantic = _semantic_projection(
        {"rows": proxy_result[1], "summary": proxy_result[2]},
        excluded={"lineage", "lineage_hash", "runtime_ms", "created_at"},
    )
    batch_semantic = _semantic_projection(batch_result.summary, excluded={"created_at", "runtime_seconds", "elapsed_seconds"})
    validation_semantic = _semantic_projection(
        json.loads(_find_validation_summary(validation_output).read_text(encoding="utf-8")),
        excluded={"created_at", "artifact_metadata", "runtime_ms", "duration_seconds"},
    )
    mutation_generation = json.loads(
        Path(config["mutation_manifest_path"]).read_text(encoding="utf-8")
    )["generations"][config["mutation_kind"]]
    result = {
        "artifact_ids": {"freeze": freeze_dir.name, "matrix": full_matrix_dir.name, "tensor": full_tensor_dir.name},
        "research_projection_content_hash": projection["content_hash"],
        "research_computation_identity": bounded_research_identity,
        "research_tensor_hash": research_projection_hash,
        "factor_hash": _hash_arrays(research_factor.numpy(), research_validity.numpy()),
        "proxy_hash": _hash_json(proxy_semantic),
        "full_eval_hash": _hash_json(batch_semantic),
        "materialization_quality_hash": _hash_json(materialization.metrics or {}),
        "validation_status_hash": _hash_json(validation_semantic),
        "cache_key": research_cache_key,
        "cache_lookup_phase": "before_downstream_evaluation",
        "consolidation_hash": _hash_json(consolidation),
        "diagnostic_hash": projection["diagnostic_hash"],
        "research_result_hash": _hash_json({"factor": _hash_arrays(research_factor.numpy(), research_validity.numpy()), "proxy": proxy_semantic, "full": batch_semantic}),
        "mutation_generation_hash": mutation_generation["content_hash"],
        "mutation_applied": bool(mutation_generation["mutation_applied"]),
        "mutation_target": mutation_generation.get("mutation_target"),
        "eligible_date_hash": eligibility_hash,
        "matrix_manifest_hash": matrix_manifest.get("content_hash"),
    }
    cache_semantic = {
        key: result[key]
        for key in (
            "research_tensor_hash",
            "factor_hash",
            "proxy_hash",
            "full_eval_hash",
            "materialization_quality_hash",
            "validation_status_hash",
            "research_result_hash",
        )
    }
    result["cache_status"] = _finalize_research_cache_contract(
        cache_dir=Path(config["research_cache_dir"]),
        mutation_kind=str(config["mutation_kind"]),
        cache_key=str(result["cache_key"]),
        semantic=cache_semantic,
        probe=cache_probe,
    )
    return result


def _prepare_mutation_generations(config: ProductionSentinelConfig, output_root: Path) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    dates = json.loads((Path(config.published_matrix_dir) / "trade_dates.json").read_text(encoding="utf-8"))
    contract = ResearchEligibilityContract(research_end_date=config.research_end_date, label_horizon=config.label_horizon)
    inside_index = max(index for index, allowed in enumerate(contract.eligible_mask(dates)) if allowed)
    post_index = next(index for index, date in enumerate(dates) if date > config.research_end_date)
    mutation_targets = _pre_registered_mutation_targets(config, dates, inside_index, post_index)
    generations: dict[str, dict[str, Any]] = {}
    for mutation in MUTATIONS:
        generation_root = output_root / mutation
        if generation_root.exists():
            shutil.rmtree(generation_root)
        generation_root.mkdir(parents=True)
        target = mutation_targets.get(mutation)
        freeze_dir = _copy_freeze_generation(
            Path(config.governed_freeze_dir), generation_root / "freeze", mutation, target
        )
        if mutation == "baseline":
            matrix_dir = generation_root / "matrix"
            shutil.copytree(config.published_matrix_dir, matrix_dir, copy_function=_reflink_copy)
            tensor_dir = generation_root / "tensor"
            shutil.copytree(config.published_tensor_dir, tensor_dir, copy_function=_reflink_copy)
        else:
            matrix_result = StrictEngineeringPITMatrixBuilder(
                StrictEngineeringPITMatrixConfig(
                    research_observable_cutoff=config.research_end_date,
                    target_endpoint_horizon_trade_days=int(config.label_horizon),
                )
            ).build(
                governed_freeze_dir=freeze_dir,
                historical_universe_dir=config.universe_dir,
                output_root=generation_root / "matrix_build",
            )
            matrix_dir = Path(matrix_result.generation_dir)
            tensor_result = build_v3_tensor_generation(
                matrix_dir=matrix_dir,
                feature_manifest_path=config.feature_manifest_path,
                output_root=generation_root / "tensor_build",
            )
            tensor_dir = Path(tensor_result["generation_dir"])
        content_hash = _hash_json({
            "mutation": mutation,
            "freeze_manifest": sha256_file(_freeze_manifest_path(freeze_dir)),
            "matrix_manifest": sha256_file(_matrix_manifest_path(matrix_dir)),
            "tensor_manifest": sha256_file(tensor_dir / "task_053_v3_tensor_manifest.json"),
        })
        generations[mutation] = {
            "freeze_dir": str(freeze_dir), "matrix_dir": str(matrix_dir), "tensor_dir": str(tensor_dir),
            "inside_date": dates[inside_index], "post_date": dates[post_index], "content_hash": content_hash,
            "mutation_target": target,
            "mutation_applied": mutation == "baseline" or _mutation_changed(config, tensor_dir, target),
        }
    semantic = {
        "schema_version": "task_054b_mutation_generations_v1",
        "generations": generations,
        "probe_fixed_before_results": True,
        "dependency_selected_before_results": True,
    }
    semantic["content_hash"] = _hash_json(semantic)
    return semantic


def _copy_freeze_generation(
    source: Path,
    output_root: Path,
    mutation: str,
    target_spec: Mapping[str, Any] | None,
) -> Path:
    manifest = json.loads(_freeze_manifest_path(source).read_text(encoding="utf-8"))
    staging = Path(tempfile.mkdtemp(prefix="freeze_generation_", dir=output_root.parent))
    try:
        shutil.rmtree(staging)
        shutil.copytree(source, staging, copy_function=_reflink_copy)
        artifacts = {
            str(item["logical_name"]): staging / str(item.get("relative_path") or item.get("records_path"))
            for item in manifest.get("artifacts", [])
        }
        if mutation != "baseline":
            if not target_spec or target_spec["logical_name"] not in artifacts:
                raise RuntimeError(f"pre-registered mutation source missing:{mutation}")
            source_path = artifacts[str(target_spec["logical_name"])]
            source_path.chmod(0o644)
            _mutate_source_cell(source_path, target_spec)
        updated_artifacts = []
        for item in manifest.get("artifacts", []):
            updated = dict(item)
            path = staging / str(item.get("relative_path") or item.get("records_path"))
            updated["sha256"] = sha256_file(path)
            updated["size_bytes"] = path.stat().st_size
            path.chmod(0o444)
            updated_artifacts.append(updated)
        manifest["artifacts"] = updated_artifacts
        content_inputs = {
            "semantic_hash": manifest.get("semantic_hash"),
            "source_lineage_manifest_sha256": manifest.get("source_lineage_manifest_sha256"),
            "artifacts": [
                {key: item.get(key) for key in ("logical_name", "relative_path", "sha256", "size_bytes")}
                for item in updated_artifacts
            ],
        }
        manifest["content_hash"] = _hash_json(content_inputs)
        manifest["generation_id"] = f"freeze_054b_{manifest['content_hash'][:24]}"
        manifest["mutation_contract"] = {"task": "054-B", "kind": mutation, "copy_mode": "reflink_cow"}
        atomic_json(staging / _freeze_manifest_path(source).name, manifest)
        target = output_root / manifest["generation_id"]
        output_root.mkdir(parents=True, exist_ok=True)
        os.replace(staging, target)
        return target
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _validate_cross_run_invariants(executions: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> tuple[dict[str, Any], list[str]]:
    research_fields = ("research_tensor_hash", "factor_hash", "proxy_hash", "full_eval_hash", "materialization_quality_hash", "validation_status_hash", "research_result_hash")
    baseline = executions["baseline"]
    post = executions["post_cutoff"]
    inside = executions["inside_cutoff"]
    post_invariant = {path: all(baseline[path][field] == post[path][field] for field in research_fields) for path in PATHS}
    diagnostic_changed = {path: baseline[path]["diagnostic_hash"] != post[path]["diagnostic_hash"] for path in PATHS}
    inside_cache_miss = {path: baseline[path]["cache_key"] != inside[path]["cache_key"] for path in PATHS}
    cache_contract = {
        path: (
            baseline[path].get("cache_status") == "miss_write"
            and post[path].get("cache_status") == "hit"
            and inside[path].get("cache_status") == "miss_write"
            and all(
                executions[mutation][path].get("cache_lookup_phase") == "before_downstream_evaluation"
                for mutation in MUTATIONS
            )
        )
        for path in PATHS
    }
    inside_changed = {path: any(baseline[path][field] != inside[path][field] for field in research_fields) for path in PATHS}
    mutation_applied = {
        mutation: all(bool(executions[mutation][path].get("mutation_applied")) for path in PATHS)
        for mutation in MUTATIONS
    }
    semantic_consistency = {
        mutation: (
            len({executions[mutation][path]["research_result_hash"] for path in PATHS}) == 1
            and len({executions[mutation][path]["diagnostic_hash"] for path in PATHS}) == 1
        )
        for mutation in MUTATIONS
    }
    baseline_consistent = semantic_consistency["baseline"]
    blockers = []
    for path in PATHS:
        if not post_invariant[path]: blockers.append(f"post_cutoff_research_changed:{path}")
        if not diagnostic_changed[path]: blockers.append(f"post_cutoff_mutation_not_observed:{path}")
        if not inside_cache_miss[path]: blockers.append(f"inside_cutoff_cache_hit:{path}")
        if not cache_contract[path]: blockers.append(f"research_cache_contract_failed:{path}")
        if not inside_changed[path]: blockers.append(f"inside_cutoff_output_unchanged:{path}")
    for mutation, consistent in semantic_consistency.items():
        if not consistent:
            blockers.append(f"raw_matrix_local_scheduler_mismatch:{mutation}")
    proof = {
        "post_cutoff_invariant": post_invariant,
        "diagnostic_changed": diagnostic_changed,
        "inside_cutoff_cache_miss": inside_cache_miss,
        "cache_contract": cache_contract,
        "inside_cutoff_research_changed": inside_changed,
        "mutation_applied": mutation_applied,
        "semantic_consistency": semantic_consistency,
        "baseline_consistent": baseline_consistent,
    }
    return proof, blockers


def _load_and_validate_path_result(run: SentinelRunSpec) -> dict[str, Any]:
    result_path = Path(run.output_dir) / RESULT_FILE
    if not result_path.is_file():
        raise RuntimeError(f"sentinel path result missing:{run.invocation_id}")
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    if payload.get("evidence_scope") != EVIDENCE_SCOPE or payload.get("invocation_id") != run.invocation_id:
        raise RuntimeError(f"sentinel path identity/scope mismatch:{run.invocation_id}")
    if payload.get("path_name") != run.path_name or payload.get("mutation_kind") != run.mutation_kind:
        raise RuntimeError(f"sentinel path semantics mismatch:{run.invocation_id}")
    return payload


def _validate_config_inputs(config: ProductionSentinelConfig) -> None:
    required_dirs = (config.governed_freeze_dir, config.universe_dir, config.published_matrix_dir, config.published_tensor_dir)
    required_files = (config.feature_manifest_path, config.probe_factor_path, config.promotion_policy_path)
    missing = [path for path in (*required_dirs, *required_files) if not Path(path).exists()]
    if missing:
        raise FileNotFoundError(f"production sentinel inputs missing:{missing}")
    if int(config.label_horizon) != 2:
        raise ValueError("Task 054-B production sentinel requires label_horizon=2")


def _validate_published_matrix(matrix_dir: str | Path) -> dict[str, Any]:
    manifest_path = _matrix_manifest_path(Path(matrix_dir))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for partition in manifest.get("partitions", []):
        path = Path(matrix_dir) / str(partition.get("relative_path") or partition.get("path"))
        if path.is_file() and partition.get("sha256") and sha256_file(path) != partition["sha256"]:
            raise RuntimeError(f"matrix partition hash mismatch:{path.name}")
    return manifest


def _validate_published_tensor(tensor_dir: str | Path) -> dict[str, Any]:
    root = Path(tensor_dir)
    manifest = json.loads((root / "task_053_v3_tensor_manifest.json").read_text(encoding="utf-8"))
    if sha256_file(root / "feature_tensor.npy") != manifest.get("values_sha256") or sha256_file(root / "feature_validity_tensor.npy") != manifest.get("validity_sha256"):
        raise RuntimeError("tensor partition hash mismatch")
    return manifest


def _pre_registered_mutation_targets(
    config: ProductionSentinelConfig,
    dates: Sequence[str],
    inside_index: int,
    post_index: int,
) -> dict[str, dict[str, Any]]:
    feature_manifest = json.loads(Path(config.feature_manifest_path).read_text(encoding="utf-8"))
    definitions = list(feature_manifest.get("feature_definitions") or ())
    factor = json.loads(Path(config.probe_factor_path).read_text(encoding="utf-8"))
    formula_names = set(map(str, factor.get("formula") or ()))
    source_mapping = {
        "open": "daily_bars",
        "high": "daily_bars",
        "low": "daily_bars",
        "close": "daily_bars",
        "pre_close": "daily_bars",
        "volume": "daily_bars",
        "amount": "daily_bars",
        "turnover_rate": "daily_basic",
        "volume_ratio": "daily_basic",
        "pe_ttm": "daily_basic",
        "pb": "daily_basic",
        "ps_ttm": "daily_basic",
        "total_mv": "daily_basic",
        "roe": "financial_features",
        "revenue_yoy": "financial_features",
        "adj_factor": "adjustment_factors",
    }
    chosen = None
    for feature_index, definition in enumerate(definitions):
        if str(definition.get("feature_name")) not in formula_names:
            continue
        fields = [str(value) for value in definition.get("source_fields") or ()]
        if len(fields) == 1 and fields[0] in source_mapping:
            chosen = {
                "feature_name": str(definition["feature_name"]),
                "feature_index": feature_index,
                "field": fields[0],
                "logical_name": source_mapping[fields[0]],
            }
            break
    if chosen is None:
        raise RuntimeError("probe formula has no supported direct source mutation dependency")
    freeze_manifest = json.loads(_freeze_manifest_path(Path(config.governed_freeze_dir)).read_text(encoding="utf-8"))
    artifacts = {
        str(item["logical_name"]): Path(config.governed_freeze_dir)
        / str(item.get("relative_path") or item.get("records_path"))
        for item in freeze_manifest.get("artifacts", [])
    }
    source_path = artifacts.get(str(chosen["logical_name"]))
    if source_path is None or not source_path.is_file():
        raise RuntimeError("pre-registered mutation source artifact missing")
    stocks = json.loads((Path(config.published_matrix_dir) / "ts_codes.json").read_text(encoding="utf-8"))
    validity = np.load(
        Path(config.published_tensor_dir) / "feature_validity_tensor.npy",
        mmap_mode="r",
        allow_pickle=False,
    )
    targets = {}
    for mutation, date_index in (("inside_cutoff", inside_index), ("post_cutoff", post_index)):
        valid_stock_indices = [
            int(value)
            for value in np.flatnonzero(validity[:, int(chosen["feature_index"]), date_index])
        ]
        candidates = {str(stocks[index]): index for index in valid_stock_indices}
        matched = _find_source_mutation_cell(
            source_path,
            trade_date=str(dates[date_index]),
            field=str(chosen["field"]),
            stock_indices=candidates,
        )
        targets[mutation] = {
            **chosen,
            "date_index": int(date_index),
            "trade_date": str(dates[date_index]),
            "ts_code": matched["ts_code"],
            "stock_index": int(matched["stock_index"]),
            "selection_contract": "formula_dependency_and_validity_before_results_v1",
        }
    return targets


def _find_source_mutation_cell(
    path: Path,
    *,
    trade_date: str,
    field: str,
    stock_indices: Mapping[str, int],
) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            code = str(row.get("ts_code") or "")
            value = row.get(field)
            if (
                str(row.get("trade_date") or "") == trade_date
                and code in stock_indices
                and value is not None
                and np.isfinite(float(value))
            ):
                return {"ts_code": code, "stock_index": stock_indices[code]}
    raise RuntimeError(f"no valid source mutation cell:{path.name}:{trade_date}:{field}")


def _mutation_changed(
    config: ProductionSentinelConfig,
    target: Path,
    target_spec: Mapping[str, Any] | None,
) -> bool:
    if not target_spec:
        return False
    source = np.load(Path(config.published_tensor_dir) / "feature_tensor.npy", mmap_mode="r", allow_pickle=False)
    changed = np.load(target / "feature_tensor.npy", mmap_mode="r", allow_pickle=False)
    stock_index = int(target_spec["stock_index"])
    feature_index = int(target_spec["feature_index"])
    date_index = int(target_spec["date_index"])
    return bool(source[stock_index, feature_index, date_index] != changed[stock_index, feature_index, date_index])


def _save_stack_output(path: Path, result: tuple[torch.Tensor, torch.Tensor] | None) -> Mapping[str, Path]:
    if result is None:
        raise RuntimeError("StackVM produced no output")
    np.savez(path, values=result[0].detach().cpu().numpy(), validity=result[1].detach().cpu().numpy())
    return {"stackvm_result": path}


def _save_json_result(path: Path, payload: Mapping[str, Any]) -> Mapping[str, Path]:
    atomic_json(path, payload)
    return {"result": path}


def _prepare_consolidation_store(output: Path, factor: FactorRecord, validation_output: Path) -> Path:
    store = output / "campaign_store"
    store.mkdir(parents=True, exist_ok=True)
    _write_jsonl(store / "validation_candidates.jsonl", [{"factor_id": factor.factor_id, "formula_hash": factor.formula_hash}])
    shard_dir = store / "shard_0"
    shard_dir.mkdir(exist_ok=True)
    summary = json.loads(_find_validation_summary(validation_output).read_text(encoding="utf-8"))
    _write_jsonl(shard_dir / "validation_candidate_pool_results.jsonl", [{"factor_id": factor.factor_id, "status": summary.get("status", "data_blocked"), "validation_summary": summary}])
    _write_jsonl(store / "validation_shards.jsonl", [{"shard_index": 0, "output_dir": str(shard_dir)}])
    return store


def _find_validation_summary(root: Path) -> Path:
    matches = sorted(root.rglob("factor_validation_summary.json"))
    if not matches:
        matches = sorted(root.rglob("*.json"))
    if not matches:
        raise RuntimeError("validation summary missing")
    return matches[0]


def _freeze_manifest_path(root: Path) -> Path:
    return _first_existing(root, *FREEZE_MANIFEST_FILENAMES)


def _universe_manifest_path(root: Path) -> Path:
    return _first_existing(root, "task_052a_universe_proof_manifest.json", "task_052_historical_universe_proof.json", "task_053_historical_universe_proof.json", "snapshot_proof_manifest.json")


def _matrix_manifest_path(root: Path) -> Path:
    return _first_existing(root, "task_053a_strict_matrix_manifest.json", "task_052a_strict_matrix_manifest.json", "matrix_manifest.json")


def _first_existing(root: Path, *names: str) -> Path:
    for name in names:
        path = root / name
        if path.is_file():
            return path
    raise FileNotFoundError(f"required artifact missing:{root}:{names}")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file(): return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(dict(row), sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _mutate_source_cell(path: Path, target_spec: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.mutation")
    changed = False
    with path.open("r", encoding="utf-8") as source, temporary.open("w", encoding="utf-8") as destination:
        for line in source:
            if not changed and line.strip():
                row = json.loads(line)
                if (
                    str(row.get("trade_date")) == str(target_spec["trade_date"])
                    and str(row.get("ts_code")) == str(target_spec["ts_code"])
                    and row.get(str(target_spec["field"])) is not None
                ):
                    field = str(target_spec["field"])
                    row[field] = float(row[field]) + 0.125
                    line = json.dumps(row, sort_keys=True) + "\n"
                    changed = True
            destination.write(line)
        destination.flush()
        os.fsync(destination.fileno())
    if not changed:
        temporary.unlink(missing_ok=True)
        raise RuntimeError("pre-registered source mutation cell missing")
    os.replace(temporary, path)


def _hash_arrays(*arrays: np.ndarray) -> str:
    digest = hashlib.sha256()
    for array in arrays:
        value = np.ascontiguousarray(array)
        digest.update(str(value.dtype).encode()); digest.update(json.dumps(list(value.shape)).encode()); digest.update(value.tobytes())
    return digest.hexdigest()


def _validated_projection_eligibility(
    dates: Sequence[str], mask: np.ndarray, matrix_manifest: Mapping[str, Any]
) -> tuple[np.ndarray, str]:
    eligible_mask = np.asarray(mask, dtype=np.bool_)
    if eligible_mask.shape != (len(dates),):
        raise RuntimeError("physical research projection eligibility shape mismatch")
    if not dates or not bool(np.all(eligible_mask)):
        raise RuntimeError("physical research projection contains non-signal dates")
    if str(matrix_manifest.get("max_legal_signal_date") or "") != str(dates[-1]):
        raise RuntimeError("physical research projection max signal date mismatch")
    eligibility_hash = str(matrix_manifest.get("eligible_date_hash") or "")
    if len(eligibility_hash) != 64:
        raise RuntimeError("physical research projection eligible-date hash missing")
    return eligible_mask, eligibility_hash


def _hash_json(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _semantic_projection(payload: Any, *, excluded: set[str]) -> Any:
    if isinstance(payload, Mapping):
        return {
            str(key): _semantic_projection(value, excluded=excluded)
            for key, value in sorted(payload.items())
            if str(key) not in excluded
        }
    if isinstance(payload, list):
        return [_semantic_projection(value, excluded=excluded) for value in payload]
    return payload


def _probe_research_cache_contract(
    *, cache_dir: Path, mutation_kind: str, cache_key: str
) -> dict[str, Any]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{cache_key}.json"
    if path.is_file():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("cache_key") != cache_key:
            raise RuntimeError(f"research cache key conflict:{mutation_kind}")
        if mutation_kind != "post_cutoff":
            raise RuntimeError(f"unexpected research cache hit:{mutation_kind}")
        return {"status": "hit", "payload": payload}
    if mutation_kind == "post_cutoff":
        raise RuntimeError("post-cutoff research cache miss")
    return {"status": "miss"}


def _finalize_research_cache_contract(
    *,
    cache_dir: Path,
    mutation_kind: str,
    cache_key: str,
    semantic: Mapping[str, Any],
    probe: Mapping[str, Any],
) -> str:
    path = cache_dir / f"{cache_key}.json"
    semantic_hash = _hash_json(dict(semantic))
    if probe.get("status") == "hit":
        payload = dict(probe.get("payload") or {})
        if payload.get("semantic_hash") != semantic_hash:
            raise RuntimeError(f"research cache semantic conflict:{mutation_kind}")
        return "hit"
    if probe.get("status") != "miss" or path.exists():
        raise RuntimeError(f"research cache state changed during evaluation:{mutation_kind}")
    atomic_json(
        path,
        {
            "schema_version": "task_054b_research_semantic_cache_v1",
            "cache_key": cache_key,
            "semantic_hash": semantic_hash,
            "source_mutation": mutation_kind,
        },
    )
    return "miss_write"


def _apply_research_cache_contract(
    *, cache_dir: Path, mutation_kind: str, cache_key: str, semantic: Mapping[str, Any]
) -> str:
    probe = _probe_research_cache_contract(
        cache_dir=cache_dir,
        mutation_kind=mutation_kind,
        cache_key=cache_key,
    )
    return _finalize_research_cache_contract(
        cache_dir=cache_dir,
        mutation_kind=mutation_kind,
        cache_key=cache_key,
        semantic=semantic,
        probe=probe,
    )


def _reflink_copy(source: str, destination: str) -> str:
    completed = subprocess.run(
        ["cp", "--reflink=always", "--preserve=mode,timestamps", source, destination],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        shutil.copy2(source, destination)
    return destination


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    worker = subparsers.add_parser("worker")
    worker.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    if args.command == "worker":
        _run_worker(args.config)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(_main())

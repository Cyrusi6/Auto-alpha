"""Executable research-firewall sentinel for strict feature tensors."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import torch

from artifact_schema.writer import attach_artifact_metadata
from model_core.vm import StackVM
from model_core.vocab import FormulaVocab

from .firewall import DateFirewall, ResearchDataView


REQUIRED_SENTINEL_PATHS = (
    "raw_local",
    "raw_scheduler",
    "matrix_local",
    "matrix_scheduler",
)

TASK054_PATH_RESULT = "task_054a_blackbox_path_result.json"
TASK054_ACCESS_LEDGER = "task_054a_actual_read_ledger.jsonl"
TASK054_REQUIRED_COMPONENTS = frozenset(
    {
        "loader",
        "stackvm_validity",
        "alpha_proxy",
        "formula_batch_evaluator",
        "factor_materializer",
        "validation_lab",
        "consolidation_cache",
    }
)


@dataclass(frozen=True)
class ProductionSentinelCommand:
    """One isolated production-path invocation used by the Task 054 sentinel."""

    path_name: str
    source_kind: str
    execution_kind: str
    mutation_kind: str
    command: tuple[str, ...]
    output_dir: str
    environment: Mapping[str, str] | None = None
    timeout_seconds: int = 900

    def validate(self) -> None:
        if self.path_name not in REQUIRED_SENTINEL_PATHS:
            raise ValueError(f"unknown sentinel path:{self.path_name}")
        expected_source, expected_execution = self.path_name.split("_", 1)
        if self.source_kind != expected_source or self.execution_kind != expected_execution:
            raise ValueError(f"sentinel path semantics mismatch:{self.path_name}")
        if self.mutation_kind not in {"baseline", "post_cutoff", "inside_cutoff"}:
            raise ValueError(f"unknown sentinel mutation:{self.mutation_kind}")
        if not self.command:
            raise ValueError(f"sentinel command missing:{self.path_name}:{self.mutation_kind}")


@dataclass(frozen=True)
class ProductionSentinelPlan:
    commands: tuple[ProductionSentinelCommand, ...]
    research_end_date: str
    label_horizon: int = 2
    allow_synthetic_test_fixture: bool = False

    def validate(self) -> None:
        keys: set[tuple[str, str]] = set()
        for command in self.commands:
            command.validate()
            key = (command.mutation_kind, command.path_name)
            if key in keys:
                raise ValueError(f"duplicate sentinel command:{key}")
            keys.add(key)
        expected = {
            (mutation, path_name)
            for mutation in ("baseline", "post_cutoff", "inside_cutoff")
            for path_name in REQUIRED_SENTINEL_PATHS
        }
        if keys != expected:
            missing = sorted(expected - keys)
            extra = sorted(keys - expected)
            raise ValueError(f"sentinel command matrix incomplete:missing={missing}:extra={extra}")


@dataclass(frozen=True)
class FirewallSentinelDataset:
    trade_dates: tuple[str, ...]
    feature_values: torch.Tensor
    feature_validity: torch.Tensor
    target: torch.Tensor
    target_available: torch.Tensor
    formula_tokens: tuple[int, ...]
    source_fingerprint: str
    source_name: str = "source"
    source_path: str | None = None

    def validate(self) -> None:
        if self.feature_values.shape != self.feature_validity.shape:
            raise ValueError("sentinel feature values/validity shape mismatch")
        if self.feature_values.ndim != 3:
            raise ValueError("sentinel feature tensor must be [stock, feature, date]")
        expected = (self.feature_values.shape[0], self.feature_values.shape[2])
        if tuple(self.target.shape) != expected or tuple(self.target_available.shape) != expected:
            raise ValueError("sentinel target axis mismatch")
        if len(self.trade_dates) != expected[1]:
            raise ValueError("sentinel date axis mismatch")
        if not self.formula_tokens:
            raise ValueError("sentinel formula is empty")


def run_research_firewall_sentinel(
    raw: FirewallSentinelDataset,
    matrix: FirewallSentinelDataset,
    output_dir: str | Path,
    *,
    research_end_date: str = "20240530",
    diagnostic_start_date: str = "20240531",
    label_horizon: int = 2,
    vocab: FormulaVocab | None = None,
) -> dict[str, Any]:
    """Run all four firewall paths and persist an auditable proof artifact."""
    raw.validate()
    matrix.validate()
    if raw.trade_dates != matrix.trade_dates:
        raise ValueError("raw/matrix sentinel date axes differ")
    firewall = DateFirewall(research_end_date, diagnostic_start_date, label_horizon)
    paths = {
        "raw_local": (raw, "local"),
        "raw_scheduler": (raw, "scheduler"),
        "matrix_local": (matrix, "local"),
        "matrix_scheduler": (matrix, "scheduler"),
    }
    baseline = {
        name: _evaluate_path(dataset, firewall, vocab=vocab, execution=execution)
        for name, (dataset, execution) in paths.items()
    }
    post_raw = _mutate_dataset(raw, firewall, after_cutoff=True)
    post_matrix = _mutate_dataset(matrix, firewall, after_cutoff=True)
    post_paths = {
        "raw_local": (post_raw, "local"),
        "raw_scheduler": (post_raw, "scheduler"),
        "matrix_local": (post_matrix, "local"),
        "matrix_scheduler": (post_matrix, "scheduler"),
    }
    post_cutoff = {
        name: _evaluate_path(dataset, firewall, vocab=vocab, execution=execution)
        for name, (dataset, execution) in post_paths.items()
    }
    inside_raw = _mutate_dataset(raw, firewall, after_cutoff=False)
    inside_matrix = _mutate_dataset(matrix, firewall, after_cutoff=False)
    inside_paths = {
        "raw_local": (inside_raw, "local"),
        "raw_scheduler": (inside_raw, "scheduler"),
        "matrix_local": (inside_matrix, "local"),
        "matrix_scheduler": (inside_matrix, "scheduler"),
    }
    inside_cutoff = {
        name: _evaluate_path(dataset, firewall, vocab=vocab, execution=execution)
        for name, (dataset, execution) in inside_paths.items()
    }

    research_fields = ("research_tensor_hash", "factor_hash", "proxy_hash", "full_eval_hash", "shortlist_hash", "cache_key")
    post_cutoff_research_changes = sum(
        baseline[name][field] != post_cutoff[name][field]
        for name in REQUIRED_SENTINEL_PATHS
        for field in research_fields
    )
    diagnostic_changes = sum(
        baseline[name]["diagnostic_hash"] != post_cutoff[name]["diagnostic_hash"]
        for name in REQUIRED_SENTINEL_PATHS
    )
    in_cutoff_cache_misses = sum(
        baseline[name]["cache_key"] != inside_cutoff[name]["cache_key"]
        for name in REQUIRED_SENTINEL_PATHS
    )
    baseline_research_hashes = {baseline[name]["result_hash"] for name in REQUIRED_SENTINEL_PATHS}
    research_consistent = len(baseline_research_hashes) == 1
    access_violations = [row for row in firewall.access_audit if not row.get("allowed", False)]
    passed = (
        post_cutoff_research_changes == 0
        and diagnostic_changes == len(REQUIRED_SENTINEL_PATHS)
        and in_cutoff_cache_misses == len(REQUIRED_SENTINEL_PATHS)
        and research_consistent
        and not access_violations
    )
    payload = {
        "artifact_type": "task_053a_research_firewall_sentinel",
        "policy_version": firewall.policy_version,
        "research_end_date": research_end_date,
        "diagnostic_start_date": diagnostic_start_date,
        "label_horizon": int(label_horizon),
        "paths": list(REQUIRED_SENTINEL_PATHS),
        "baseline": baseline,
        "post_cutoff_mutation": post_cutoff,
        "inside_cutoff_mutation": inside_cutoff,
        "proof": {
            "post_cutoff_research_change_count": int(post_cutoff_research_changes),
            "diagnostic_change_count": int(diagnostic_changes),
            "inside_cutoff_cache_miss_count": int(in_cutoff_cache_misses),
            "raw_matrix_local_scheduler_consistent": research_consistent,
            "access_violation_count": len(access_violations),
        },
        "actual_read_ledger": firewall.access_audit,
        "status": "passed" if passed else "blocked",
        "blockers": [] if passed else _blockers(
            post_cutoff_research_changes, diagnostic_changes, in_cutoff_cache_misses, research_consistent, access_violations
        ),
    }
    payload["content_hash"] = _hash_json({key: value for key, value in payload.items() if key != "actual_read_ledger"})
    payload = attach_artifact_metadata(payload, "task_053a_research_firewall_sentinel", "research_firewall")
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    _atomic_json(target / "task_053a_research_firewall_sentinel.json", payload)
    return payload


def _evaluate_path(dataset, firewall: DateFirewall, *, vocab, execution: str) -> dict[str, Any]:
    view = ResearchDataView(firewall, dataset.trade_dates)
    research_indices = list(view.eligible_indices)
    diagnostic_indices = list(view.diagnostic_indices)
    if not research_indices or not diagnostic_indices:
        raise ValueError("sentinel requires non-empty research and diagnostic periods")
    research_values = dataset.feature_values.index_select(2, _index(research_indices, dataset.feature_values.device))
    research_validity = dataset.feature_validity.index_select(2, _index(research_indices, dataset.feature_values.device))
    research_target = dataset.target.index_select(1, _index(research_indices, dataset.target.device))
    research_target_validity = dataset.target_available.index_select(1, _index(research_indices, dataset.target.device))
    firewall.audit_observation_access(view.eligible_dates, component=f"sentinel:{execution}", purpose="research_tensor", view="research")
    vm = StackVM(vocab)
    executed = vm.execute_with_validity(list(dataset.formula_tokens), research_values, research_validity)
    if executed is None:
        raise RuntimeError("sentinel formula execution failed")
    factor, factor_validity = executed
    metric_validity = factor_validity & research_target_validity & torch.isfinite(research_target)
    proxy = _metric_payload(factor, research_target, metric_validity)
    full_eval = {**proxy, "date_count": int((metric_validity.sum(dim=0) >= 2).sum().item())}
    shortlist = {"formula_tokens": list(dataset.formula_tokens), "score": proxy["rank_ic"], "eligible": proxy["coverage"] > 0}
    cache_payload = {
        "eligible_date_hash": firewall.eligible_date_hash(dataset.trade_dates),
        "research_input_hash": _hash_tensors(research_values, research_validity, research_target, research_target_validity),
        "formula_tokens": list(dataset.formula_tokens),
        "code_semantic_hash": _code_semantic_hash(),
    }
    firewall.access_audit.append(
        {
            "component": f"sentinel:{execution}",
            "purpose": "actual_source_read",
            "access_type": "artifact_read",
            "view": "research",
            "source_name": dataset.source_name,
            "source_path": dataset.source_path,
            "source_fingerprint": dataset.source_fingerprint,
            "start_date": view.eligible_dates[0],
            "end_date": view.eligible_dates[-1],
            "allowed": True,
        }
    )
    diagnostic_values = dataset.feature_values.index_select(2, _index(diagnostic_indices, dataset.feature_values.device))
    diagnostic_validity = dataset.feature_validity.index_select(2, _index(diagnostic_indices, dataset.feature_values.device))
    diagnostic_target = dataset.target.index_select(1, _index(diagnostic_indices, dataset.target.device))
    diagnostic_available = dataset.target_available.index_select(1, _index(diagnostic_indices, dataset.target.device))
    firewall.audit_observation_access(view.diagnostic_dates, component=f"sentinel:{execution}", purpose="diagnostic_tensor", view="diagnostic")
    diagnostic_execution = vm.execute_with_validity(list(dataset.formula_tokens), diagnostic_values, diagnostic_validity)
    if diagnostic_execution is None:
        raise RuntimeError("sentinel diagnostic formula execution failed")
    diagnostic_factor, diagnostic_factor_validity = diagnostic_execution
    diagnostic_hash = _hash_tensors(
        diagnostic_factor,
        diagnostic_factor_validity,
        diagnostic_target,
        diagnostic_available,
    )
    result_core = {
        "research_tensor_hash": _hash_tensors(research_values, research_validity),
        "factor_hash": _hash_tensors(factor, factor_validity),
        "proxy_hash": _hash_json(proxy),
        "full_eval_hash": _hash_json(full_eval),
        "shortlist_hash": _hash_json(shortlist),
        "cache_key": _hash_json(cache_payload),
    }
    evaluation_core = {key: value for key, value in result_core.items() if key != "cache_key"}
    return {
        **result_core,
        "result_hash": _hash_json(evaluation_core),
        "diagnostic_hash": diagnostic_hash,
        "research_date_count": len(research_indices),
        "diagnostic_date_count": len(diagnostic_indices),
        "execution": execution,
    }


def _mutate_dataset(dataset: FirewallSentinelDataset, firewall: DateFirewall, *, after_cutoff: bool) -> FirewallSentinelDataset:
    values = dataset.feature_values.clone()
    target = dataset.target.clone()
    dates = list(dataset.trade_dates)
    candidates = [index for index, date in enumerate(dates) if (date > firewall.research_end_date) == after_cutoff]
    if not candidates:
        raise ValueError("sentinel mutation has no candidate date")
    selected = candidates[0] if after_cutoff else next(
        (index for index in reversed(candidates) if index in ResearchDataView(firewall, dataset.trade_dates).eligible_indices),
        candidates[-1],
    )
    dependency = next((token for token in dataset.formula_tokens if 0 <= token < dataset.feature_values.shape[1]), None)
    dependency_mask = dataset.feature_validity[:, dependency, selected] if dependency is not None else None
    feature_positions = torch.nonzero(dataset.feature_validity[:, :, selected], as_tuple=False)
    target_positions = torch.nonzero(dataset.target_available[:, selected], as_tuple=False)
    if feature_positions.numel() == 0 or target_positions.numel() == 0:
        raise ValueError("sentinel mutation requires an actually valid cell")
    if dependency is not None and dependency_mask is not None and dependency_mask.any():
        stock = int(torch.nonzero(dependency_mask, as_tuple=False)[0, 0].item())
        feature = int(dependency)
    else:
        stock, feature = (int(value) for value in feature_positions[0].tolist())
    target_stock = int(target_positions[0, 0].item())
    values[stock, feature, selected] += 1000.0
    target[target_stock, selected] += 1000.0
    return FirewallSentinelDataset(
        trade_dates=dataset.trade_dates,
        feature_values=values,
        feature_validity=dataset.feature_validity,
        target=target,
        target_available=dataset.target_available,
        formula_tokens=dataset.formula_tokens,
        source_fingerprint=_hash_tensors(values, dataset.feature_validity, target, dataset.target_available),
        source_name=dataset.source_name,
        source_path=dataset.source_path,
    )


def _metric_payload(factor: torch.Tensor, target: torch.Tensor, validity: torch.Tensor) -> dict[str, float]:
    rank_ics = []
    for date_index in range(factor.shape[1]):
        mask = validity[:, date_index]
        if int(mask.sum().item()) < 2:
            continue
        x = _rank(factor[mask, date_index])
        y = _rank(target[mask, date_index])
        x -= x.mean()
        y -= y.mean()
        denom = x.norm() * y.norm()
        if float(denom.item()) > 1e-12:
            rank_ics.append(float((x * y).sum().item() / denom.item()))
    return {
        "coverage": float(validity.float().mean().item()),
        "rank_ic": float(sum(rank_ics) / len(rank_ics)) if rank_ics else 0.0,
        "valid_count": float(validity.sum().item()),
    }


def _rank(values: torch.Tensor) -> torch.Tensor:
    order = torch.argsort(values, stable=True)
    ranked = torch.empty(values.numel(), dtype=torch.float32, device=values.device)
    ranked[order] = torch.arange(values.numel(), dtype=torch.float32, device=values.device)
    return ranked


def _index(indices: list[int], device: torch.device) -> torch.Tensor:
    return torch.tensor(indices, dtype=torch.long, device=device)


def _hash_tensors(*values: torch.Tensor) -> str:
    digest = hashlib.sha256()
    for value in values:
        array = value.detach().cpu().contiguous().numpy()
        digest.update(str(array.dtype).encode())
        digest.update(json.dumps(list(array.shape)).encode())
        digest.update(array.tobytes())
    return digest.hexdigest()


def _hash_json(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _code_semantic_hash() -> str:
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for relative in (
        "research_firewall/firewall.py",
        "research_firewall/sentinel.py",
        "model_core/vm.py",
        "model_core/validity.py",
    ):
        digest.update(relative.encode())
        digest.update((root / relative).read_bytes())
    return digest.hexdigest()


def _blockers(post_changes, diagnostic_changes, inside_misses, consistent, violations) -> list[str]:
    blockers = []
    if post_changes:
        blockers.append(f"post_cutoff_research_changed:{post_changes}")
    if diagnostic_changes != len(REQUIRED_SENTINEL_PATHS):
        blockers.append(f"diagnostic_mutation_not_observed:{diagnostic_changes}")
    if inside_misses != len(REQUIRED_SENTINEL_PATHS):
        blockers.append(f"inside_cutoff_cache_not_invalidated:{inside_misses}")
    if not consistent:
        blockers.append("raw_matrix_local_scheduler_mismatch")
    if violations:
        blockers.append(f"firewall_access_violation:{len(violations)}")
    return blockers


def run_production_firewall_sentinel(
    plan: ProductionSentinelPlan,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Execute twelve isolated black-box jobs and verify the four real paths.

    Each command must write a production result plus a loader-produced access
    ledger. Scheduler paths additionally carry scheduler job evidence; changing
    an execution label without launching a separate command cannot satisfy this
    contract.
    """
    plan.validate()
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    executions: dict[str, dict[str, dict[str, Any]]] = {
        mutation: {} for mutation in ("baseline", "post_cutoff", "inside_cutoff")
    }
    for command in sorted(plan.commands, key=lambda item: (item.mutation_kind, item.path_name)):
            executions[command.mutation_kind][command.path_name] = _execute_production_command(
                command,
                allow_synthetic_test_fixture=plan.allow_synthetic_test_fixture,
            )

    research_fields = (
        "research_tensor_hash",
        "factor_hash",
        "proxy_hash",
        "full_eval_hash",
        "materialization_quality_hash",
        "validation_status_hash",
        "cache_key",
        "consolidation_hash",
    )
    baseline = executions["baseline"]
    post_cutoff = executions["post_cutoff"]
    inside_cutoff = executions["inside_cutoff"]
    post_changes = {
        path_name: [field for field in research_fields if baseline[path_name][field] != post_cutoff[path_name][field]]
        for path_name in REQUIRED_SENTINEL_PATHS
    }
    inside_changes = {
        path_name: [field for field in research_fields if baseline[path_name][field] != inside_cutoff[path_name][field]]
        for path_name in REQUIRED_SENTINEL_PATHS
    }
    diagnostic_changed = {
        path_name: baseline[path_name]["diagnostic_hash"] != post_cutoff[path_name]["diagnostic_hash"]
        for path_name in REQUIRED_SENTINEL_PATHS
    }
    baseline_core_hashes = {row["research_result_hash"] for row in baseline.values()}
    post_core_hashes = {row["research_result_hash"] for row in post_cutoff.values()}
    inside_cache_misses = {
        path_name: baseline[path_name]["cache_key"] != inside_cutoff[path_name]["cache_key"]
        for path_name in REQUIRED_SENTINEL_PATHS
    }
    access_violations = [
        row
        for mutation_rows in executions.values()
        for result in mutation_rows.values()
        for row in result["access_ledger"]
        if row.get("allowed") is not True
    ]
    blockers: list[str] = []
    for path_name, fields in post_changes.items():
        if fields:
            blockers.append(f"post_cutoff_research_changed:{path_name}:{','.join(fields)}")
    for path_name, changed in diagnostic_changed.items():
        if not changed:
            blockers.append(f"diagnostic_mutation_not_observed:{path_name}")
    for path_name, missed in inside_cache_misses.items():
        if not missed:
            blockers.append(f"inside_cutoff_cache_not_invalidated:{path_name}")
        if not inside_changes[path_name]:
            blockers.append(f"inside_cutoff_output_not_changed:{path_name}")
    if len(baseline_core_hashes) != 1 or len(post_core_hashes) != 1:
        blockers.append("raw_matrix_local_scheduler_research_mismatch")
    if access_violations:
        blockers.append(f"actual_read_access_violation:{len(access_violations)}")

    semantic = {
        "schema_version": "1.0",
        "contract_version": "task_054a_production_firewall_sentinel_v1",
        "research_end_date": plan.research_end_date,
        "label_horizon": plan.label_horizon,
        "executions": executions,
        "proof": {
            "post_cutoff_research_changes": post_changes,
            "inside_cutoff_research_changes": inside_changes,
            "inside_cutoff_cache_misses": inside_cache_misses,
            "diagnostic_changed": diagnostic_changed,
            "raw_matrix_local_scheduler_consistent": len(baseline_core_hashes) == 1,
            "access_violation_count": len(access_violations),
        },
        "blockers": blockers,
    }
    semantic["content_hash"] = _hash_json(semantic)
    payload = attach_artifact_metadata(
        {
            **semantic,
            "status": "passed" if not blockers else "blocked",
            "research_firewall_ready": not blockers,
        },
        "task_054a_production_firewall_sentinel",
        "research_firewall",
    )
    _atomic_json(target / "task_054a_production_firewall_sentinel.json", payload)
    return payload


def _execute_production_command(
    spec: ProductionSentinelCommand,
    *,
    allow_synthetic_test_fixture: bool,
) -> dict[str, Any]:
    output_dir = Path(spec.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "process.log"
    environment = os.environ.copy()
    environment.update(dict(spec.environment or {}))
    started = time.monotonic()
    with log_path.open("wb") as log_handle:
        process = subprocess.Popen(
            list(spec.command),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            env=environment,
            start_new_session=True,
        )
        try:
            exit_code = process.wait(timeout=spec.timeout_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise RuntimeError(f"sentinel subprocess timed out:{spec.path_name}:{spec.mutation_kind}")
    elapsed = time.monotonic() - started
    if exit_code != 0:
        raise RuntimeError(
            f"sentinel subprocess failed:{spec.path_name}:{spec.mutation_kind}:exit={exit_code}:log={log_path}"
        )
    result_path = output_dir / TASK054_PATH_RESULT
    ledger_path = output_dir / TASK054_ACCESS_LEDGER
    if not result_path.is_file() or not ledger_path.is_file():
        raise RuntimeError(f"sentinel production artifacts missing:{spec.path_name}:{spec.mutation_kind}")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    ledger = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    _validate_production_path_result(
        spec,
        result,
        ledger,
        allow_synthetic_test_fixture=allow_synthetic_test_fixture,
    )
    result_core = dict(result)
    result_core.pop("process_evidence", None)
    return {
        **result,
        "access_ledger": ledger,
        "access_ledger_sha256": _sha256_file(ledger_path),
        "result_artifact_sha256": _sha256_file(result_path),
        "process_log_sha256": _sha256_file(log_path),
        "launcher_evidence": {
            "command": list(spec.command),
            "pid": process.pid,
            "exit_code": exit_code,
            "elapsed_seconds": elapsed,
        },
        "verified_result_hash": _hash_json(result_core),
    }


def _validate_production_path_result(
    spec: ProductionSentinelCommand,
    result: Mapping[str, Any],
    ledger: Sequence[Mapping[str, Any]],
    *,
    allow_synthetic_test_fixture: bool,
) -> None:
    if result.get("evidence_scope") == "synthetic_test_fixture" and not allow_synthetic_test_fixture:
        raise RuntimeError(f"synthetic sentinel evidence forbidden in production:{spec.path_name}")
    if result.get("status") != "success":
        raise RuntimeError(f"sentinel path did not succeed:{spec.path_name}:{spec.mutation_kind}")
    if result.get("path_name") != spec.path_name or result.get("mutation_kind") != spec.mutation_kind:
        raise RuntimeError(f"sentinel path identity mismatch:{spec.path_name}:{spec.mutation_kind}")
    if result.get("source_kind") != spec.source_kind or result.get("execution_kind") != spec.execution_kind:
        raise RuntimeError(f"sentinel path source/execution mismatch:{spec.path_name}")
    component_names = {str(row.get("component")) for row in result.get("component_evidence", []) if isinstance(row, dict)}
    if component_names != TASK054_REQUIRED_COMPONENTS:
        raise RuntimeError(f"sentinel component evidence incomplete:{spec.path_name}:{sorted(component_names)}")
    for component in result.get("component_evidence", []):
        if not component.get("source_hash") or component.get("invoked") is not True:
            raise RuntimeError(f"sentinel component invocation unproved:{spec.path_name}:{component.get('component')}")
    required_hashes = {
        "research_tensor_hash",
        "factor_hash",
        "proxy_hash",
        "full_eval_hash",
        "materialization_quality_hash",
        "validation_status_hash",
        "cache_key",
        "consolidation_hash",
        "diagnostic_hash",
        "research_result_hash",
    }
    if any(not result.get(field) for field in required_hashes):
        raise RuntimeError(f"sentinel result hashes incomplete:{spec.path_name}:{spec.mutation_kind}")
    if not ledger or any(not row.get("component") or not row.get("path") or not row.get("date_range") for row in ledger):
        raise RuntimeError(f"sentinel actual-read ledger incomplete:{spec.path_name}:{spec.mutation_kind}")
    process_evidence = result.get("process_evidence") or {}
    if int(process_evidence.get("pid") or 0) <= 0 or int(process_evidence.get("exit_code", -1)) != 0:
        raise RuntimeError(f"sentinel worker process evidence invalid:{spec.path_name}")
    if spec.execution_kind == "scheduler":
        scheduler = result.get("scheduler_evidence") or {}
        if (
            not scheduler.get("job_id")
            or int(scheduler.get("worker_pid") or 0) <= 0
            or int(scheduler.get("exit_code", -1)) != 0
            or not scheduler.get("heartbeat_sha256")
            or not scheduler.get("artifact_sha256")
            or not scheduler.get("command")
        ):
            raise RuntimeError(f"sentinel scheduler evidence invalid:{spec.path_name}:{spec.mutation_kind}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)

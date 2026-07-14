"""Executable research-firewall sentinel for strict feature tensors."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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

"""Cheap proxy evaluation for Alpha Factory."""

from __future__ import annotations

import time
from dataclasses import replace

import torch

from model_core.vm import StackVM
from research_firewall.lineage import build_loader_lineage


def run_proxy_eval(candidates, loader, *, max_candidates: int, max_dates: int, vocab=None, seed: int = 0) -> tuple[list, list[dict], dict]:
    vm = StackVM(vocab)
    updated = []
    rows: list[dict] = []
    passed = 0
    attempted = 0
    date_count = min(max_dates, len(loader.trade_dates))
    if date_count <= 0:
        raise RuntimeError("proxy has no eligible research dates")
    if date_count == 1:
        date_indices = [0]
    else:
        positions = [(idx * (len(loader.trade_dates) - 1)) // (date_count - 1) for idx in range(date_count)]
        offset = int(seed) % len(loader.trade_dates)
        date_indices = sorted({(position + offset) % len(loader.trade_dates) for position in positions})
    date_tensor = torch.tensor(date_indices, dtype=torch.long, device=loader.feat_tensor.device)
    lineage = build_loader_lineage(loader, stage="alpha_proxy_eval", extra={"max_dates": int(max_dates), "seed": int(seed)})
    _audit_sampled_target_reads(loader, date_indices)
    for candidate in candidates:
        if candidate.status == "rejected" or attempted >= max_candidates:
            updated.append(candidate)
            continue
        attempted += 1
        start = time.perf_counter()
        try:
            feature_validity = _loader_feature_validity(loader)
            executed = vm.execute_with_validity(candidate.formula_tokens, loader.feat_tensor, feature_validity)
            if executed is None:
                raise RuntimeError("vm returned no factor")
            factor, factor_validity = executed
            factor = factor.index_select(1, date_tensor)
            factor_validity = factor_validity.index_select(1, date_tensor)
            target = loader.target_ret.index_select(1, date_tensor)
            target_available = _loader_target_available(loader).index_select(1, date_tensor)
            metric_validity = factor_validity & target_available & torch.isfinite(target)
            eligible = _loader_signal_eligibility(loader).index_select(1, date_tensor)
            denominator = int(eligible.sum().item())
            valid_count = int((factor_validity & eligible).sum().item())
            coverage = float(valid_count / denominator) if denominator else 0.0
            valid_values = factor[factor_validity & eligible]
            std = float(valid_values.std(unbiased=False).item()) if valid_values.numel() else 0.0
            nonzero = float((valid_values != 0).to(torch.float32).mean().item()) if valid_values.numel() else 0.0
            missing = float(1.0 - coverage)
            rank_ic = _rank_ic(factor, target, metric_validity)
            turnover = _turnover_proxy(factor, factor_validity & eligible)
            score = float(rank_ic + 0.1 * std + 0.1 * nonzero - 0.05 * turnover)
            status = "proxy_passed" if coverage > 0 and std > 1e-8 else "rejected"
            if status == "proxy_passed":
                passed += 1
            row = {
                "alpha_candidate_id": candidate.alpha_candidate_id,
                "formula_hash": candidate.formula_hash,
                "status": status,
                "coverage": coverage,
                "cross_sectional_std": std,
                "nonzero_ratio": nonzero,
                "missing_value_ratio": missing,
                "preliminary_rank_ic": rank_ic,
                "turnover_proxy": turnover,
                "runtime_ms": float((time.perf_counter() - start) * 1000.0),
                "proxy_score": score,
                "sampled_dates": [loader.trade_dates[index] for index in date_indices],
                "lineage_hash": lineage["lineage_hash"],
            }
            updated.append(replace(candidate, proxy_score=score, status=status, reject_reason=None if status == "proxy_passed" else "zero_variance_proxy"))
            rows.append(row)
        except Exception as exc:
            updated.append(replace(candidate, status="rejected", reject_reason=f"proxy_eval_failed:{exc}"))
            rows.append(
                {
                    "alpha_candidate_id": candidate.alpha_candidate_id,
                    "formula_hash": candidate.formula_hash,
                    "status": "failed",
                    "error": str(exc),
                    "proxy_score": 0.0,
                    "runtime_ms": float((time.perf_counter() - start) * 1000.0),
                }
            )
    summary = {
        "attempted": attempted,
        "passed": passed,
        "failed": sum(1 for row in rows if row.get("status") == "failed"),
        "max_dates": date_count,
        "sampled_dates": [loader.trade_dates[index] for index in date_indices],
        "eligible_date_hash": __import__("hashlib").sha256("\n".join(loader.trade_dates).encode()).hexdigest(),
        "seed": int(seed),
        "lineage": lineage,
        "lineage_hash": lineage["lineage_hash"],
    }
    return updated, rows, summary


def _audit_sampled_target_reads(loader, date_indices: list[int]) -> None:
    firewall = getattr(loader, "date_firewall", None)
    source_dates = list(getattr(loader, "firewall_source_trade_dates", None) or [])
    if firewall is None or not source_dates:
        return
    source_index = {date: index for index, date in enumerate(source_dates)}
    horizon = int(getattr(loader, "label_horizon", 1))
    for index in date_indices:
        start = loader.trade_dates[index]
        endpoint_index = source_index[start] + horizon
        if endpoint_index >= len(source_dates):
            raise RuntimeError(f"proxy target endpoint unavailable: {start}+{horizon}")
        firewall.assert_target_access(start, source_dates[endpoint_index], component="alpha_proxy_eval", purpose="sampled_target_read")


def _rank_ic(factor: torch.Tensor, target: torch.Tensor, validity: torch.Tensor) -> float:
    values = []
    for idx in range(factor.shape[1]):
        mask = validity[:, idx]
        if int(mask.sum().item()) < 2:
            continue
        x = _average_tie_rank(factor[mask, idx])
        y = _average_tie_rank(target[mask, idx])
        x = x - x.mean()
        y = y - y.mean()
        denom = torch.clamp(x.std(unbiased=False) * y.std(unbiased=False), min=1e-6)
        values.append(float((x * y).mean().item() / denom.item()))
    return float(sum(values) / len(values)) if values else 0.0


def _turnover_proxy(factor: torch.Tensor, validity: torch.Tensor) -> float:
    if factor.shape[1] <= 1:
        return 0.0
    changes = []
    for date_index in range(1, factor.shape[1]):
        mask = validity[:, date_index - 1] & validity[:, date_index]
        if int(mask.sum().item()) < 2:
            continue
        previous = _average_tie_rank(factor[mask, date_index - 1])
        current = _average_tie_rank(factor[mask, date_index])
        changes.append(float((current - previous).abs().mean().item() / max(int(mask.sum().item()) - 1, 1)))
    return float(sum(changes) / len(changes)) if changes else 0.0


def _average_tie_rank(values: torch.Tensor) -> torch.Tensor:
    order = torch.argsort(values, stable=True)
    sorted_values = values[order]
    sorted_ranks = torch.empty_like(sorted_values, dtype=torch.float32)
    start = 0
    while start < values.numel():
        end = start + 1
        while end < values.numel() and bool(sorted_values[end] == sorted_values[start]):
            end += 1
        sorted_ranks[start:end] = (start + end - 1) / 2.0
        start = end
    ranks = torch.empty_like(sorted_ranks)
    ranks[order] = sorted_ranks
    return ranks


def _loader_feature_validity(loader) -> torch.Tensor:
    validity = getattr(loader, "feature_validity", None)
    if validity is None:
        validity = getattr(loader, "feature_validity_tensor", None)
    if validity is None:
        if getattr(loader, "use_matrix_cache", False):
            raise RuntimeError("strict proxy requires feature validity tensor")
        validity = torch.isfinite(loader.feat_tensor)
    return validity.bool()


def _loader_target_available(loader) -> torch.Tensor:
    validity = getattr(loader, "target_available", None)
    if validity is None:
        validity = getattr(loader, "raw_data_cache", {}).get("target_available_mask")
    return validity.bool() if validity is not None else torch.isfinite(loader.target_ret)


def _loader_signal_eligibility(loader) -> torch.Tensor:
    raw = getattr(loader, "raw_data_cache", {})
    for name in ("signal_eligible_at_close", "signal_eligible", "pit_available_mask"):
        if name in raw:
            return raw[name].bool()
    return torch.ones_like(loader.target_ret, dtype=torch.bool)

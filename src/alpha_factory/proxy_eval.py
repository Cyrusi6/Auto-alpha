"""Cheap proxy evaluation for Alpha Factory."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace
from pathlib import Path

import torch

from evaluation import normalize_objective_rows
from factor_engine.transforms import preprocess_factor_with_validity
from .research_policy import AlphaResearchPolicy, load_alpha_research_policy
from model_core.vm import StackVM
from research_firewall.lineage import build_loader_lineage


def run_proxy_eval(
    candidates,
    loader,
    *,
    max_candidates: int,
    max_dates: int,
    vocab=None,
    seed: int = 0,
    policy: AlphaResearchPolicy | None = None,
    family_novelty_scores: dict[str, float] | None = None,
    existing_factor_matrices: list[torch.Tensor] | None = None,
    proxy_context_hash: str | None = None,
) -> tuple[list, list[dict], dict]:
    policy = policy or load_alpha_research_policy("alpha_factory_two_stage_smoke_v1")
    family_novelty_scores = family_novelty_scores or {}
    existing_factor_matrices = existing_factor_matrices or []
    vm = StackVM(vocab)
    candidates_by_id = {item.alpha_candidate_id: item for item in candidates}
    unchanged = []
    rows: list[dict] = []
    attempted = 0
    research_indices, eligible_date_hash = _loader_research_indices(loader)
    date_count = min(max_dates, len(research_indices))
    if date_count <= 0:
        raise RuntimeError("proxy has no eligible research dates")
    if date_count == 1:
        date_indices = [0]
    else:
        positions = [(idx * (len(research_indices) - 1)) // (date_count - 1) for idx in range(date_count)]
        offset = int(seed) % len(research_indices)
        date_indices = sorted({research_indices[(position + offset) % len(research_indices)] for position in positions})
    if date_count == 1:
        date_indices = [research_indices[0]]
    date_tensor = torch.tensor(date_indices, dtype=torch.long, device=loader.feat_tensor.device)
    lineage = build_loader_lineage(
        loader,
        stage="alpha_proxy_eval",
        extra={
            "max_dates": int(max_dates),
            "seed": int(seed),
            "research_policy_hash": policy.policy_hash,
            "existing_factor_count": len(existing_factor_matrices),
            "proxy_context_hash": proxy_context_hash,
        },
    )
    _audit_sampled_target_reads(loader, date_indices)
    for candidate in candidates:
        if candidate.status == "rejected" or attempted >= max_candidates:
            unchanged.append(candidate)
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
            eligible = _loader_signal_eligibility(loader).index_select(1, date_tensor)
            neutralized, neutralized_validity, neutralization_status = _neutralize_proxy_factor(
                factor,
                factor_validity,
                loader,
                date_tensor,
                eligible,
                policy,
            )
            metric_validity = neutralized_validity & target_available & torch.isfinite(target) & eligible
            denominator = int(eligible.sum().item())
            valid_count = int((neutralized_validity & eligible).sum().item())
            coverage = float(valid_count / denominator) if denominator else 0.0
            valid_values = neutralized[neutralized_validity & eligible]
            std = float(valid_values.std(unbiased=False).item()) if valid_values.numel() else 0.0
            nonzero = float((valid_values != 0).to(torch.float32).mean().item()) if valid_values.numel() else 0.0
            missing = float(1.0 - coverage)
            ic_values = _daily_rank_ics(neutralized, target, metric_validity, policy.proxy_min_cross_section_breadth)
            rank_ic = float(sum(ic_values) / len(ic_values)) if ic_values else 0.0
            rank_ic_std = float(torch.tensor(ic_values).std(unbiased=False).item()) if len(ic_values) > 1 else 0.0
            hit_rate = float(sum(value > 0 for value in ic_values) / len(ic_values)) if ic_values else 0.0
            ic_stability = float(hit_rate / (1.0 + rank_ic_std))
            turnover = _turnover_proxy(neutralized, neutralized_validity & eligible)
            max_corr = _max_existing_correlation(
                neutralized,
                neutralized_validity & eligible,
                existing_factor_matrices,
                date_tensor,
            )
            universe_metrics = _universe_direction_metrics(
                neutralized,
                target,
                metric_validity,
                loader,
                policy.proxy_min_cross_section_breadth,
            )
            blockers = []
            if coverage < policy.proxy_min_coverage:
                blockers.append("proxy_coverage_below_policy")
            if std <= 1e-8:
                blockers.append("proxy_zero_variance")
            if len(ic_values) < policy.proxy_min_evaluable_dates:
                blockers.append("proxy_insufficient_evaluable_dates")
            if universe_metrics["evaluable_universe_count"] < policy.proxy_min_universe_count:
                blockers.append("proxy_insufficient_universe_evidence")
            if max_corr > policy.proxy_max_abs_existing_correlation:
                blockers.append("proxy_existing_factor_correlation_above_policy")
            if neutralization_status != "applied" and getattr(loader, "production_research", False):
                blockers.append("proxy_pit_neutralization_unavailable")
            row = {
                "alpha_candidate_id": candidate.alpha_candidate_id,
                "formula_hash": candidate.formula_hash,
                "status": "proxy_evaluated",
                "pit_safe": bool(getattr(loader, "production_research", False) or neutralization_status in {"applied", "nonproduction_fallback"}),
                "semantic_valid": str(getattr(candidate, "static_check_status", "passed")) == "passed",
                "neutralization_method": policy.proxy_neutralization,
                "neutralization_status": neutralization_status,
                "coverage": coverage,
                "cross_sectional_std": std,
                "nonzero_ratio": nonzero,
                "missing_value_ratio": missing,
                "preliminary_rank_ic": rank_ic,
                "neutralized_rank_ic_mean": rank_ic,
                "neutralized_rank_ic_std": rank_ic_std,
                "rank_ic_positive_ratio": hit_rate,
                "rank_ic_evaluable_dates": len(ic_values),
                "ic_stability": ic_stability,
                "turnover_proxy": turnover,
                "max_abs_existing_correlation": max_corr,
                "family_novelty": float(family_novelty_scores.get(candidate.alpha_candidate_id, 0.5)),
                "universe_direction_consistency": universe_metrics["direction_consistency"],
                "universe_rank_ic": universe_metrics["rank_ic_by_universe"],
                "evaluable_universe_count": universe_metrics["evaluable_universe_count"],
                "complexity": int(getattr(candidate, "complexity", 0) or 0),
                "lookback": int(getattr(candidate, "lookback", 0) or 0),
                "proxy_blockers": blockers,
                "runtime_ms": float((time.perf_counter() - start) * 1000.0),
                "proxy_score": None,
                "sampled_dates": [loader.trade_dates[index] for index in date_indices],
                "lineage_hash": lineage["lineage_hash"],
                "research_policy_id": policy.policy_id,
                "research_policy_hash": policy.policy_hash,
            }
            rows.append(row)
        except Exception as exc:
            rows.append(
                {
                    "alpha_candidate_id": candidate.alpha_candidate_id,
                    "formula_hash": candidate.formula_hash,
                    "status": "failed",
                    "error": str(exc),
                    "proxy_score": 0.0,
                    "runtime_ms": float((time.perf_counter() - start) * 1000.0),
                    "proxy_blockers": [f"proxy_eval_failed:{type(exc).__name__}"],
                }
            )
    scoreable = [row for row in rows if row.get("status") == "proxy_evaluated"]
    scores, components, normalization = normalize_objective_rows(
        scoreable,
        policy.proxy_objectives,
        id_field="alpha_candidate_id",
    )
    rows_by_id = {str(row["alpha_candidate_id"]): row for row in rows}
    passed = 0
    updated = []
    for candidate in candidates:
        row = rows_by_id.get(candidate.alpha_candidate_id)
        if row is None:
            updated.append(candidate)
            continue
        if row.get("status") == "failed":
            updated.append(replace(candidate, status="rejected", reject_reason=str((row.get("proxy_blockers") or ["proxy_eval_failed"])[0])))
            continue
        score = scores.get(candidate.alpha_candidate_id, float("nan"))
        blockers = list(row.get("proxy_blockers") or [])
        if not torch.isfinite(torch.tensor(score)):
            blockers.append("proxy_objective_missing")
            score = 0.0
        status = "proxy_passed" if not blockers else "rejected"
        row.update(
            {
                "status": status,
                "proxy_score": float(score),
                "normalized_objectives": components.get(candidate.alpha_candidate_id, {}),
                "normalization_reference_hash": normalization["reference_hash"],
                "proxy_blockers": blockers,
            }
        )
        if status == "proxy_passed":
            passed += 1
        updated.append(
            replace(
                candidate,
                proxy_score=float(score),
                status=status,
                reject_reason=None if status == "proxy_passed" else ";".join(blockers),
            )
        )
    summary = {
        "attempted": attempted,
        "passed": passed,
        "failed": sum(1 for row in rows if row.get("status") == "failed"),
        "max_dates": date_count,
        "sampled_dates": [loader.trade_dates[index] for index in date_indices],
        "eligible_date_hash": eligible_date_hash,
        "seed": int(seed),
        "lineage": lineage,
        "lineage_hash": lineage["lineage_hash"],
        "research_policy_id": policy.policy_id,
        "research_policy_hash": policy.policy_hash,
        "normalization": normalization,
        "score_method": "dimensionless_cohort_multi_objective_v1",
        "proxy_context_hash": proxy_context_hash,
    }
    return updated, rows, summary


def _audit_sampled_target_reads(loader, date_indices: list[int]) -> None:
    if getattr(loader, "physical_research_projection", False):
        return
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
    values = _daily_rank_ics(factor, target, validity, 2)
    return float(sum(values) / len(values)) if values else 0.0


def _daily_rank_ics(factor: torch.Tensor, target: torch.Tensor, validity: torch.Tensor, min_breadth: int) -> list[float]:
    values = []
    for idx in range(factor.shape[1]):
        mask = validity[:, idx]
        if int(mask.sum().item()) < int(min_breadth):
            continue
        x = _average_tie_rank(factor[mask, idx])
        y = _average_tie_rank(target[mask, idx])
        x = x - x.mean()
        y = y - y.mean()
        denom = torch.clamp(x.std(unbiased=False) * y.std(unbiased=False), min=1e-6)
        values.append(float((x * y).mean().item() / denom.item()))
    return values


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


def _neutralize_proxy_factor(
    factor: torch.Tensor,
    validity: torch.Tensor,
    loader,
    date_tensor: torch.Tensor,
    eligible: torch.Tensor,
    policy: AlphaResearchPolicy,
) -> tuple[torch.Tensor, torch.Tensor, str]:
    raw = getattr(loader, "raw_data_cache", {})
    if not policy.proxy_neutralization.startswith("neutralize_"):
        transformed, transformed_validity = preprocess_factor_with_validity(
            factor,
            validity,
            raw,
            policy.proxy_neutralization,
            eligible,
        )
        return transformed, transformed_validity, "applied"
    required = ("industry_codes", "log_mkt_cap")
    missing = [name for name in required if name not in raw]
    if missing:
        if getattr(loader, "production_research", False):
            raise RuntimeError(f"PIT neutralization inputs missing: {','.join(missing)}")
        return (
            torch.where(validity & eligible, factor, torch.zeros_like(factor)),
            validity & eligible & torch.isfinite(factor),
            "nonproduction_fallback",
        )
    selected_raw = {}
    for name, value in raw.items():
        if not isinstance(value, torch.Tensor):
            continue
        aligned = value
        if aligned.ndim == 1:
            aligned = aligned.unsqueeze(1).expand(-1, len(loader.trade_dates))
        if aligned.ndim >= 2 and aligned.shape[1] == len(loader.trade_dates):
            aligned = aligned.index_select(1, date_tensor.to(device=aligned.device))
        selected_raw[name] = aligned.to(device=factor.device)
    governed_validity = validity & eligible
    validity_cache = getattr(loader, "raw_validity_cache", {}) or {}
    for name, aliases in {
        "log_mkt_cap": ("log_mkt_cap", "total_mv"),
        "industry_codes": ("industry_codes", "industry_code_matrix", "industry_status_known"),
    }.items():
        source_validity = None
        for alias in aliases:
            candidate = validity_cache.get(alias)
            if candidate is None:
                candidate = raw.get(f"{alias}_validity")
            if isinstance(candidate, torch.Tensor):
                source_validity = candidate
                break
        if source_validity is None:
            if getattr(loader, "production_research", False):
                raise RuntimeError(f"PIT neutralization validity missing: {name}")
            continue
        if source_validity.ndim == 1:
            source_validity = source_validity.unsqueeze(1).expand(-1, len(loader.trade_dates))
        source_validity = source_validity.index_select(1, date_tensor.to(device=source_validity.device))
        governed_validity &= source_validity.to(device=factor.device, dtype=torch.bool)
    transformed, transformed_validity = preprocess_factor_with_validity(
        factor,
        governed_validity,
        selected_raw,
        policy.proxy_neutralization,
        eligible,
    )
    return transformed, transformed_validity, "applied"


def _max_existing_correlation(
    factor: torch.Tensor,
    validity: torch.Tensor,
    existing: list[torch.Tensor],
    date_tensor: torch.Tensor,
) -> float:
    correlations = []
    for matrix in existing:
        reference = matrix.to(device=factor.device)
        if reference.ndim != 2 or reference.shape[0] != factor.shape[0]:
            raise RuntimeError("existing factor axis mismatch")
        if reference.shape[1] != factor.shape[1]:
            if int(date_tensor.max().item()) >= reference.shape[1]:
                raise RuntimeError("existing factor date axis mismatch")
            reference = reference.index_select(1, date_tensor.to(device=reference.device))
        mask = validity & torch.isfinite(reference)
        if int(mask.sum().item()) < 2:
            continue
        left = factor[mask].float()
        right = reference[mask].float()
        left = left - left.mean()
        right = right - right.mean()
        denom = left.norm() * right.norm()
        if float(denom.item()) > 1e-12:
            correlations.append(abs(float((left * right).sum().item() / denom.item())))
    return max(correlations, default=0.0)


def _universe_direction_metrics(
    factor: torch.Tensor,
    target: torch.Tensor,
    validity: torch.Tensor,
    loader,
    min_breadth: int,
) -> dict[str, object]:
    masks: dict[str, torch.Tensor] = {"all": torch.ones_like(validity, dtype=torch.bool)}
    raw = getattr(loader, "raw_data_cache", {})
    provided = raw.get("research_universe_masks")
    if isinstance(provided, dict):
        for name, value in provided.items():
            if isinstance(value, torch.Tensor) and value.shape == validity.shape:
                masks[str(name)] = value.to(device=validity.device, dtype=torch.bool)
    ts_codes = list(getattr(loader, "ts_codes", []) or [])
    if len(ts_codes) == factor.shape[0]:
        for name, suffix in (("sse", ".SH"), ("szse", ".SZ")):
            stock_mask = torch.tensor(
                [str(code).endswith(suffix) for code in ts_codes],
                dtype=torch.bool,
                device=validity.device,
            ).unsqueeze(1)
            masks.setdefault(name, stock_mask.expand_as(validity))
    rank_ic_by_universe: dict[str, float] = {}
    for name, mask in masks.items():
        values = _daily_rank_ics(factor, target, validity & mask, min_breadth)
        if values:
            rank_ic_by_universe[name] = float(sum(values) / len(values))
    anchor = rank_ic_by_universe.get("all")
    comparable = [value for name, value in rank_ic_by_universe.items() if name != "all"]
    if anchor is None:
        consistency = 0.0
    elif not comparable:
        consistency = 1.0
    elif abs(anchor) <= 1e-12:
        consistency = float(sum(abs(value) <= 1e-12 for value in comparable) / len(comparable))
    else:
        consistency = float(sum(value * anchor > 0 for value in comparable) / len(comparable))
    return {
        "rank_ic_by_universe": rank_ic_by_universe,
        "direction_consistency": consistency,
        "evaluable_universe_count": len(rank_ic_by_universe),
    }


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
    if validity is None:
        raise RuntimeError("strict target availability is required for proxy evaluation")
    if validity.shape != loader.target_ret.shape:
        raise RuntimeError("target availability shape mismatch")
    return validity.bool()


def _loader_signal_eligibility(loader) -> torch.Tensor:
    raw = getattr(loader, "raw_data_cache", {})
    for name in ("signal_eligible_at_close", "signal_eligible", "pit_available_mask"):
        if name in raw:
            return raw[name].bool()
    if getattr(loader, "production_research", False):
        raise RuntimeError("production proxy evaluation requires PIT signal eligibility")
    return torch.ones_like(loader.target_ret, dtype=torch.bool)


def _loader_research_indices(loader) -> tuple[list[int], str]:
    dates = tuple(str(value) for value in loader.trade_dates)
    if getattr(loader, "physical_research_projection", False):
        matrix_manifest = Path(getattr(loader, "matrix_cache_dir")) / "task_052a_strict_matrix_manifest.json"
        payload = json.loads(matrix_manifest.read_text(encoding="utf-8"))
        return list(range(len(dates))), str(payload["eligible_date_hash"])
    firewall = getattr(loader, "date_firewall", None)
    if firewall is None:
        payload = {"trade_dates": dates, "contract": "unbounded"}
        return list(range(len(dates))), hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    source_dates = tuple(getattr(loader, "firewall_source_trade_dates", ()) or dates)
    source_eligible = firewall.contract.eligible_dates(source_dates)
    if dates == source_eligible:
        return list(range(len(dates))), firewall.contract.eligible_date_hash(source_dates)
    return list(firewall.contract.eligible_indices(dates)), firewall.contract.eligible_date_hash(dates)

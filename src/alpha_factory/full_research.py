"""Governed second-stage research for Alpha Factory proxy shortlists."""

from __future__ import annotations

import hashlib
import json
import math
import random
from statistics import mean
from typing import Any

import torch

from evaluation import normalize_objective_rows
from factor_engine.transforms import preprocess_factor_with_validity
from factor_store import make_factor_id
from model_core.vm import StackVM
from research_firewall.lineage import build_loader_lineage
from validation_lab.metrics import evaluate_factor_dates, evaluate_factor_splits
from validation_lab.policy import EngineeringRobustnessPolicy
from validation_lab.splits import build_splits_for_eligible_segments

from .research_policy import AlphaResearchPolicy


def run_full_research(
    candidates,
    loader,
    *,
    policy: AlphaResearchPolicy,
    vocab,
    factor_transform: str,
    total_trial_count: int,
    seed: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    vm = StackVM(vocab)
    formula_root = hashlib.sha256(
        json.dumps(
            [
                {
                    "candidate_id": candidate.alpha_candidate_id,
                    "formula_hash": candidate.formula_hash,
                    "tokens": candidate.formula_tokens,
                    "lookback": candidate.lookback,
                }
                for candidate in candidates
            ],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    lineage = build_loader_lineage(
        loader,
        stage="alpha_full_research",
        extra={
            "policy_hash": policy.policy_hash,
            "formula_root": formula_root,
            "total_trial_count": int(total_trial_count),
            "factor_transform": factor_transform,
            "seed": int(seed),
        },
    )
    target_available = _target_available(loader)
    signal_eligible = _signal_eligible(loader)
    validation_common = _validation_common(loader, signal_eligible, target_available)
    date_eligible = validation_common.sum(dim=0) >= int(policy.proxy_min_cross_section_breadth)
    segments = _eligible_segments(loader.trade_dates, date_eligible)
    beta = _asof_beta(loader, signal_eligible)
    rows: list[dict[str, Any]] = []
    for ordinal, candidate in enumerate(candidates):
        try:
            executed = vm.execute_with_validity(
                candidate.formula_tokens,
                loader.feat_tensor,
                _feature_validity(loader),
            )
            if executed is None:
                raise RuntimeError("StackVM returned no factor")
            raw_factor, formula_validity = executed
            formula_validity = _transform_input_validity(loader, formula_validity, factor_transform)
            factor, formula_validity = preprocess_factor_with_validity(
                raw_factor,
                formula_validity,
                loader.raw_data_cache,
                factor_transform,
                signal_eligible,
            )
            effective_embargo = int(candidate.lookback) + int(getattr(loader, "label_horizon", 0) or 0)
            splits = build_splits_for_eligible_segments(
                "rolling_walk_forward",
                segments,
                policy.train_size,
                policy.validation_size,
                policy.test_size,
                policy.step_size,
                effective_embargo,
                8,
                64,
            )
            validation_policy = _validation_policy(policy)
            windows, summary, issues = evaluate_factor_splits(
                factor,
                loader.target_ret,
                loader.trade_dates,
                splits,
                make_factor_id(candidate.formula_hash),
                validity=formula_validity,
                active_mask=_optional_mask(loader, "active_mask"),
                target_available_mask=target_available,
                index_member_mask=_optional_mask(loader, "index_member_matrix", "membership"),
                eligible_date_mask=date_eligible,
                validation_common_mask=validation_common,
                policy=validation_policy,
            )
            oos_dates = sorted({date for split in splits for date in split.test_dates})
            regime = _regime_diagnostics(
                factor,
                formula_validity,
                loader,
                oos_dates,
                validation_common,
                policy.proxy_min_cross_section_breadth,
            )
            placebo = _placebo_diagnostics(
                factor,
                formula_validity,
                loader,
                oos_dates,
                validation_common,
                policy,
                seed + ordinal,
                summary.out_of_sample_score,
            )
            time_sensitivity = _time_sensitivity(
                factor,
                formula_validity,
                loader,
                oos_dates,
                validation_common,
                policy.proxy_min_cross_section_breadth,
            )
            parameter_sensitivity = _parameter_sensitivity(
                factor,
                formula_validity,
                loader,
                oos_dates,
                validation_common,
                policy.proxy_min_cross_section_breadth,
            )
            stress = _cost_capacity_stress(
                factor,
                formula_validity,
                loader,
                oos_dates,
                validation_common,
                policy,
            )
            exposures = _style_exposures(
                factor,
                formula_validity,
                loader,
                beta,
                oos_dates,
                validation_common,
                policy.proxy_min_cross_section_breadth,
            )
            p_value = _aggregate_rank_ic_p_value(windows)
            pbo = _rolling_pbo(windows)
            data_blockers = [issue.code for issue in issues if issue.severity == "blocker" and issue.code in _DATA_BLOCKERS]
            if not stress["supported"]:
                data_blockers.append(str(stress["reason"]))
            if not exposures["supported"]:
                data_blockers.append(str(exposures["reason"]))
            statistical_blockers = [issue.code for issue in issues if issue.severity == "blocker" and issue.code not in _DATA_BLOCKERS]
            row = {
                "alpha_candidate_id": candidate.alpha_candidate_id,
                "factor_id": make_factor_id(candidate.formula_hash),
                "formula_hash": candidate.formula_hash,
                "request": {
                    "name": candidate.alpha_candidate_id,
                    "formula_hash": candidate.formula_hash,
                    "formula_tokens": candidate.formula_tokens,
                    "formula_names": candidate.formula_names,
                    "lookback": candidate.lookback,
                    "complexity": candidate.complexity,
                },
                "status": "full_research_evaluated",
                "score": None,
                "metrics_by_split": {
                    "all": summary.to_dict(),
                    "windows": [item.to_dict() for item in windows],
                },
                "validation_summary": summary.to_dict(),
                "validation_issues": [issue.to_dict() for issue in issues],
                "effective_embargo": effective_embargo,
                "split_count": len(splits),
                "oos_date_count": len(oos_dates),
                "oos_observation_count": int(
                    sum(float(item.test_metrics.get("n_observations") or 0.0) for item in windows)
                ),
                "mean_rank_ic": summary.mean_rank_ic,
                "mean_icir": summary.mean_icir,
                "window_pass_ratio": summary.window_pass_ratio,
                "stability_score": summary.stability_score,
                "train_test_decay": summary.train_test_decay,
                "placebo": placebo,
                "placebo_percentile": placebo["percentile"],
                "regime": regime,
                "regime_pass_ratio": regime["pass_ratio"],
                "time_sensitivity": time_sensitivity,
                "time_sensitivity_ratio": time_sensitivity["pass_ratio"],
                "parameter_sensitivity": parameter_sensitivity,
                "parameter_sensitivity_ratio": parameter_sensitivity["pass_ratio"],
                "cost_capacity_stress": stress,
                "modeled_net_spread": stress.get("modeled_net_spread"),
                "capacity_feasible_ratio": stress.get("capacity_feasible_ratio"),
                "style_exposures": exposures,
                "max_style_exposure": exposures.get("max_style_exposure"),
                "raw_p_value": p_value,
                "pbo_estimate": pbo,
                "pbo_method": "rolling_train_test_degradation_proxy_v1",
                "pbo_approximate": True,
                "data_blockers": sorted(set(data_blockers)),
                "statistical_blockers": sorted(set(statistical_blockers)),
                "research_policy_id": policy.policy_id,
                "research_policy_hash": policy.policy_hash,
                "lineage_hash": lineage["lineage_hash"],
                "score_method": "dimensionless_cohort_multi_objective_v1",
                "stress_evidence_level": "modeled_daily_bar_proxy",
                "certification_supported": False,
            }
            rows.append(row)
        except Exception as exc:
            rows.append(
                {
                    "alpha_candidate_id": candidate.alpha_candidate_id,
                    "factor_id": make_factor_id(candidate.formula_hash),
                    "formula_hash": candidate.formula_hash,
                    "request": {
                        "name": candidate.alpha_candidate_id,
                        "formula_hash": candidate.formula_hash,
                        "formula_tokens": candidate.formula_tokens,
                        "formula_names": candidate.formula_names,
                        "lookback": candidate.lookback,
                        "complexity": candidate.complexity,
                    },
                    "status": "data_blocked",
                    "score": 0.0,
                    "data_blockers": [f"full_research_failed:{type(exc).__name__}:{exc}"],
                    "statistical_blockers": [],
                    "research_policy_id": policy.policy_id,
                    "research_policy_hash": policy.policy_hash,
                    "lineage_hash": lineage["lineage_hash"],
                    "certification_supported": False,
                }
            )
    correction = _apply_multiple_testing(rows, max(int(total_trial_count), len(rows), 1))
    scoreable = [
        row
        for row in rows
        if row.get("status") == "full_research_evaluated"
        and not row.get("data_blockers")
        and all(_is_finite(row.get(spec.name)) for spec in policy.full_objectives if spec.required)
    ]
    scores, components, normalization = normalize_objective_rows(
        scoreable,
        policy.full_objectives,
        id_field="alpha_candidate_id",
    )
    for row in rows:
        if row.get("status") != "full_research_evaluated":
            continue
        candidate_id = str(row["alpha_candidate_id"])
        row["score"] = float(scores.get(candidate_id, 0.0))
        row["normalized_objectives"] = components.get(candidate_id, {})
        row["normalization_reference_hash"] = normalization["reference_hash"]
        _finalize_status(row, policy)
    status_counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    summary = {
        "enabled": True,
        "evaluated": len(rows),
        "status_counts": status_counts,
        "research_policy_id": policy.policy_id,
        "research_policy_hash": policy.policy_hash,
        "score_method": "dimensionless_cohort_multi_objective_v1",
        "normalization": normalization,
        "multiple_testing": correction,
        "pbo": {
            "method": "rolling_train_test_degradation_proxy_v1",
            "approximate": True,
            "certification_supported": False,
        },
        "selection_bias": {
            "total_trials": int(total_trial_count),
            "full_research_trials": len(rows),
            "selection_fraction": float(len(rows) / max(int(total_trial_count), 1)),
            "selection_data_reused": True,
            "untouched_holdout": False,
        },
        "certification_ready": False,
        "formula_root": formula_root,
        "lineage": lineage,
        "lineage_hash": lineage["lineage_hash"],
    }
    summary["content_hash"] = hashlib.sha256(
        json.dumps(
            {
                "rows": rows,
                "policy_hash": policy.policy_hash,
                "multiple_testing": correction,
                "normalization": normalization,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return rows, summary


def _validation_policy(policy: AlphaResearchPolicy) -> EngineeringRobustnessPolicy:
    return EngineeringRobustnessPolicy(
        policy_id=f"{policy.policy_id}:rolling_oos",
        train_size=policy.train_size,
        validation_size=policy.validation_size,
        test_size=policy.test_size,
        step_size=policy.step_size,
        min_cross_section_breadth=policy.proxy_min_cross_section_breadth,
        min_oos_dates=policy.test_size,
        min_coverage=policy.proxy_min_coverage,
        min_mean_rank_ic=policy.min_mean_rank_ic,
        min_mean_icir=policy.min_mean_icir,
        min_window_pass_ratio=policy.min_window_pass_ratio,
        max_train_test_decay=policy.max_train_test_decay,
        min_valid_oos_ratio=policy.min_valid_oos_dates / max(policy.test_size, 1),
        min_valid_oos_dates=policy.min_valid_oos_dates,
        min_evaluable_windows=policy.min_evaluable_windows,
        min_cumulative_oos_dates=policy.min_cumulative_oos_dates,
        parameters_locked=True,
    )


def _regime_diagnostics(factor, validity, loader, dates, common, min_breadth) -> dict[str, Any]:
    raw = loader.raw_data_cache
    close = raw.get("close")
    amount = raw.get("amount")
    if not isinstance(close, torch.Tensor) or not isinstance(amount, torch.Tensor):
        return {"pass_ratio": 0.0, "supported": False, "reason": "regime_inputs_missing", "regimes": {}}
    close_ret = torch.full_like(close, float("nan"))
    close_ret[:, 1:] = close[:, 1:] / close[:, :-1] - 1.0
    market = _masked_mean(close_ret, common, dim=0)
    trailing_vol = _rolling_std(market, 20)
    liquidity = _masked_mean(torch.log1p(torch.clamp(amount, min=0.0)), common, dim=0)
    index = {date: idx for idx, date in enumerate(loader.trade_dates)}
    date_indices = [index[date] for date in dates if date in index]
    vol_median = _finite_median(trailing_vol[date_indices])
    liq_median = _finite_median(liquidity[date_indices])
    buckets = {
        "market_up": [date for date in dates if math.isfinite(float(market[index[date]])) and float(market[index[date]]) > 0],
        "market_down": [date for date in dates if math.isfinite(float(market[index[date]])) and float(market[index[date]]) <= 0],
        "high_vol": [date for date in dates if math.isfinite(float(trailing_vol[index[date]])) and float(trailing_vol[index[date]]) > vol_median],
        "low_vol": [date for date in dates if math.isfinite(float(trailing_vol[index[date]])) and float(trailing_vol[index[date]]) <= vol_median],
        "high_liquidity": [date for date in dates if math.isfinite(float(liquidity[index[date]])) and float(liquidity[index[date]]) > liq_median],
        "low_liquidity": [date for date in dates if math.isfinite(float(liquidity[index[date]])) and float(liquidity[index[date]]) <= liq_median],
    }
    results = {}
    passed = 0
    for name, selected in buckets.items():
        metrics = _evaluate(factor, validity, loader, selected, common, min_breadth)
        ok = bool(metrics.get("evaluable")) and float(metrics.get("rank_ic_mean") or 0.0) >= 0.0
        passed += int(ok)
        results[name] = {"date_count": len(selected), "passed": ok, "metrics": metrics}
    return {"pass_ratio": passed / len(results) if results else 0.0, "supported": True, "regimes": results}


def _placebo_diagnostics(factor, validity, loader, dates, common, policy, seed, candidate_score) -> dict[str, Any]:
    rng = random.Random(seed)
    scores = []
    for trial in range(max(1, int(policy.placebo_trials))):
        generator = torch.Generator(device="cpu").manual_seed(rng.randrange(2**31))
        permuted = loader.target_ret.clone()
        for date in dates:
            idx = loader.trade_dates.index(date)
            mask = common[:, idx] & validity[:, idx] & torch.isfinite(permuted[:, idx])
            positions = torch.where(mask)[0]
            if positions.numel() < 2:
                continue
            order = torch.randperm(positions.numel(), generator=generator, device="cpu").to(positions.device)
            permuted[positions, idx] = permuted[positions[order], idx]
        metrics = evaluate_factor_dates(
            factor,
            permuted,
            loader.trade_dates,
            dates,
            validity=validity,
            target_available_mask=_target_available(loader),
            validation_common_mask=common,
            min_breadth=policy.proxy_min_cross_section_breadth,
        )
        scores.append(float(metrics.get("out_of_sample_score") or 0.0))
    percentile = sum(value <= float(candidate_score) for value in scores) / len(scores) if scores else 0.0
    return {
        "trial_count": len(scores),
        "percentile": float(percentile),
        "null_exceedance_ratio": float(sum(value >= float(candidate_score) for value in scores) / len(scores)) if scores else 1.0,
        "score_root": hashlib.sha256(json.dumps(scores, separators=(",", ":")).encode()).hexdigest(),
    }


def _time_sensitivity(factor, validity, loader, dates, common, min_breadth) -> dict[str, Any]:
    scenarios = {}
    baseline = _evaluate(factor, validity, loader, dates, common, min_breadth)
    delayed = torch.zeros_like(factor)
    delayed_validity = torch.zeros_like(validity)
    delayed[:, 1:] = factor[:, :-1]
    delayed_validity[:, 1:] = validity[:, :-1]
    scenarios["signal_lag_1"] = _evaluate(delayed, delayed_validity, loader, dates, common, min_breadth)
    midpoint = len(dates) // 2
    scenarios["early_oos"] = _evaluate(factor, validity, loader, dates[:midpoint], common, min_breadth)
    scenarios["late_oos"] = _evaluate(factor, validity, loader, dates[midpoint:], common, min_breadth)
    baseline_sign = math.copysign(1.0, float(baseline.get("rank_ic_mean") or 0.0))
    passed = [
        bool(value.get("evaluable"))
        and float(value.get("rank_ic_mean") or 0.0) * baseline_sign >= 0.0
        for value in scenarios.values()
    ]
    return {"pass_ratio": sum(passed) / len(passed), "baseline": baseline, "scenarios": scenarios}


def _parameter_sensitivity(factor, validity, loader, dates, common, min_breadth) -> dict[str, Any]:
    scenarios = {}
    for n_mad in (3.0, 5.0, 7.0):
        perturbed = torch.zeros_like(factor)
        for idx in range(factor.shape[1]):
            mask = validity[:, idx]
            values = factor[mask, idx]
            if values.numel() < 2:
                continue
            center = values.median()
            mad = (values - center).abs().median().clamp(min=1e-6)
            perturbed[mask, idx] = values.clamp(center - n_mad * mad, center + n_mad * mad)
        scenarios[f"winsor_mad_{int(n_mad)}"] = _evaluate(perturbed, validity, loader, dates, common, min_breadth)
    passed = [bool(value.get("evaluable")) and float(value.get("rank_ic_mean") or 0.0) >= 0.0 for value in scenarios.values()]
    return {"pass_ratio": sum(passed) / len(passed), "scenarios": scenarios}


def _cost_capacity_stress(factor, validity, loader, dates, common, policy) -> dict[str, Any]:
    amount = loader.raw_data_cache.get("amount")
    amount_semantics = getattr(loader, "amount_semantics", None) or loader.raw_data_cache.get("amount_semantics")
    if not isinstance(amount, torch.Tensor):
        return {"supported": False, "reason": "lagged_amount_missing"}
    if getattr(loader, "production_research", False) and amount_semantics != "raw_turnover_CNY":
        return {"supported": False, "reason": "amount_unit_contract_unproven"}
    index = {date: idx for idx, date in enumerate(loader.trade_dates)}
    gross = []
    selected_sets = []
    feasible = []
    per_name = float(policy.capacity_aum_cny) / 20.0
    for date in dates:
        idx = index.get(date)
        if idx is None:
            continue
        mask = common[:, idx] & validity[:, idx] & torch.isfinite(factor[:, idx]) & torch.isfinite(loader.target_ret[:, idx])
        positions = torch.where(mask)[0]
        if positions.numel() < policy.proxy_min_cross_section_breadth:
            continue
        top_n = min(20, int(positions.numel()))
        order = torch.argsort(factor[positions, idx], descending=True)[:top_n]
        selected = positions[order]
        selected_sets.append(set(int(value) for value in selected.tolist()))
        gross.append(float(loader.target_ret[selected, idx].mean().item()))
        capacity = amount[selected, idx]
        valid_capacity = torch.isfinite(capacity) & (capacity > 0)
        feasible.append(bool(valid_capacity.all() and torch.all(per_name <= capacity * float(policy.capacity_participation))))
    if not gross:
        return {"supported": False, "reason": "cost_capacity_no_evaluable_dates"}
    turnover = _set_turnover(selected_sets)
    gross_mean = float(mean(gross))
    modeled_cost = turnover * float(policy.modeled_cost_bps) / 10_000.0
    feasible_ratio = float(sum(feasible) / len(feasible)) if feasible else 0.0
    return {
        "supported": True,
        "evidence_level": "modeled_daily_bar_proxy",
        "lagged_amount_only": True,
        "amount_semantics": amount_semantics or "nonproduction_unproven",
        "gross_spread": gross_mean,
        "turnover": turnover,
        "modeled_cost": modeled_cost,
        "modeled_net_spread": gross_mean - modeled_cost,
        "double_modeled_cost_net_spread": gross_mean - 2.0 * modeled_cost,
        "capacity_feasible_ratio": feasible_ratio,
        "capacity_participation": float(policy.capacity_participation),
        "capacity_aum_cny": float(policy.capacity_aum_cny),
    }


def _style_exposures(factor, validity, loader, beta, dates, common, min_breadth) -> dict[str, Any]:
    raw = loader.raw_data_cache
    size = raw.get("log_mkt_cap")
    amount = raw.get("amount")
    industry = raw.get("industry_code_matrix", raw.get("industry_codes"))
    if not all(isinstance(value, torch.Tensor) for value in (size, amount, industry)) or beta is None:
        return {"supported": False, "reason": "style_exposure_inputs_missing"}
    size = _align(size, factor)
    amount = torch.log1p(torch.clamp(_align(amount, factor), min=0.0))
    industry = _align(industry, factor)
    index = {date: idx for idx, date in enumerate(loader.trade_dates)}
    size_corr = []
    beta_corr = []
    liquidity_corr = []
    concentrations = []
    for date in dates:
        idx = index.get(date)
        if idx is None:
            continue
        mask = common[:, idx] & validity[:, idx] & torch.isfinite(factor[:, idx])
        mask &= torch.isfinite(size[:, idx]) & torch.isfinite(amount[:, idx]) & torch.isfinite(beta[:, idx])
        if int(mask.sum().item()) < min_breadth:
            continue
        size_corr.append(_corr(factor[mask, idx], size[mask, idx]))
        beta_corr.append(_corr(factor[mask, idx], beta[mask, idx]))
        liquidity_corr.append(_corr(factor[mask, idx], amount[mask, idx]))
        positions = torch.where(mask)[0]
        top = positions[torch.argsort(factor[positions, idx], descending=True)[: min(20, int(positions.numel()))]]
        _, counts = torch.unique(industry[top, idx].long(), return_counts=True)
        concentrations.append(float(counts.max().item() / max(int(top.numel()), 1)))
    if not size_corr:
        return {"supported": False, "reason": "style_exposure_no_evaluable_dates"}
    summary = {
        "size_exposure": float(mean(size_corr)),
        "beta_exposure": float(mean(beta_corr)),
        "liquidity_exposure": float(mean(liquidity_corr)),
        "industry_concentration": float(mean(concentrations)),
    }
    summary["max_style_exposure"] = max(
        abs(summary["size_exposure"]),
        abs(summary["beta_exposure"]),
        abs(summary["liquidity_exposure"]),
        summary["industry_concentration"],
    )
    return {"supported": True, **summary}


def _apply_multiple_testing(rows: list[dict[str, Any]], total_trials: int) -> dict[str, Any]:
    valid = [(idx, float(row["raw_p_value"])) for idx, row in enumerate(rows) if row.get("raw_p_value") is not None]
    ordered = sorted(valid, key=lambda item: (item[1], str(rows[item[0]].get("formula_hash"))))
    m = max(len(ordered), 1)
    bh = [0.0] * len(ordered)
    running = 1.0
    for position in range(len(ordered) - 1, -1, -1):
        _, p_value = ordered[position]
        running = min(running, p_value * m / (position + 1))
        bh[position] = min(1.0, running)
    holm_running = 0.0
    for position, ((row_index, p_value), q_value) in enumerate(zip(ordered, bh)):
        holm_running = max(holm_running, min(1.0, (m - position) * p_value))
        rows[row_index]["bh_q_value"] = float(q_value)
        rows[row_index]["holm_adjusted_p_value"] = float(holm_running)
        rows[row_index]["selection_adjusted_p_value"] = float(min(1.0, p_value * total_trials))
    return {
        "method": "benjamini_hochberg_and_holm_v1",
        "total_generated_trials": int(total_trials),
        "full_research_trials": len(ordered),
        "effective_trial_count": len({row.get("formula_hash") for row in rows if row.get("formula_hash")}),
        "minimum_bh_q_value": min((value for value in bh), default=1.0),
    }


def _finalize_status(row: dict[str, Any], policy: AlphaResearchPolicy) -> None:
    if row.get("data_blockers"):
        row["status"] = "data_blocked"
        row["gate_reasons"] = list(row["data_blockers"])
        return
    blockers = list(row.get("statistical_blockers") or [])
    checks = (
        (float(row["mean_rank_ic"]) > 0.0, "positive_oos_rank_ic_missing"),
        (float(row["placebo_percentile"]) >= policy.min_placebo_percentile, "placebo_below_policy"),
        (float(row["regime_pass_ratio"]) >= policy.min_regime_pass_ratio, "regime_stability_below_policy"),
        (float(row["time_sensitivity_ratio"]) >= policy.min_time_sensitivity_ratio, "time_sensitivity_below_policy"),
        (float(row["parameter_sensitivity_ratio"]) >= policy.min_parameter_sensitivity_ratio, "parameter_sensitivity_below_policy"),
        (float(row["bh_q_value"]) <= policy.max_bh_q_value, "multiple_testing_q_value_above_policy"),
        (float(row["selection_adjusted_p_value"]) <= policy.max_selection_adjusted_p_value, "selection_adjusted_p_value_above_policy"),
        (float(row["pbo_estimate"]) <= policy.max_pbo, "pbo_above_policy"),
        (float(row["capacity_feasible_ratio"]) >= policy.min_capacity_feasible_ratio, "capacity_below_policy"),
        (float(row["cost_capacity_stress"]["double_modeled_cost_net_spread"]) >= 0.0, "double_cost_stress_failed"),
        (abs(float(row["style_exposures"]["size_exposure"])) <= policy.max_abs_size_exposure, "size_exposure_above_policy"),
        (abs(float(row["style_exposures"]["beta_exposure"])) <= policy.max_abs_beta_exposure, "beta_exposure_above_policy"),
        (abs(float(row["style_exposures"]["liquidity_exposure"])) <= policy.max_abs_liquidity_exposure, "liquidity_exposure_above_policy"),
        (float(row["style_exposures"]["industry_concentration"]) <= policy.max_industry_concentration, "industry_concentration_above_policy"),
    )
    blockers.extend(reason for passed, reason in checks if not passed)
    row["statistical_blockers"] = sorted(set(blockers))
    row["gate_reasons"] = row["statistical_blockers"]
    row["status"] = "validation_candidate" if not blockers else "research_rejected"
    row["gate_decision"] = {
        "passed": not blockers,
        "status": row["status"],
        "reasons": row["gate_reasons"],
        "policy_id": policy.policy_id,
        "policy_hash": policy.policy_hash,
        "positive_oos_evidence": not blockers,
        "checks": {
            "oos_evidence_positive": not blockers,
            "test_evaluable_date_count": int(row.get("oos_date_count") or 0),
            "test_valid_observation_count": int(row.get("oos_observation_count") or 0),
            "test_rank_ic_mean": float(row.get("mean_rank_ic") or 0.0),
            "test_rank_ic_ir": float(row.get("mean_icir") or 0.0),
            "window_pass_ratio": float(row.get("window_pass_ratio") or 0.0),
            "bh_q_value": float(row.get("bh_q_value") or 1.0),
            "selection_adjusted_p_value": float(row.get("selection_adjusted_p_value") or 1.0),
        },
        "certification_supported": False,
    }


def _evaluate(factor, validity, loader, dates, common, min_breadth):
    return evaluate_factor_dates(
        factor,
        loader.target_ret,
        loader.trade_dates,
        dates,
        validity=validity,
        active_mask=_optional_mask(loader, "active_mask"),
        target_available_mask=_target_available(loader),
        index_member_mask=_optional_mask(loader, "index_member_matrix", "membership"),
        validation_common_mask=common,
        min_breadth=min_breadth,
    )


def _aggregate_rank_ic_p_value(windows) -> float:
    values = [float(item.test_metrics.get("rank_ic_t_stat") or 0.0) for item in windows if item.test_metrics.get("evaluable")]
    if not values:
        return 1.0
    z_value = sum(values) / math.sqrt(len(values))
    return float(math.erfc(abs(z_value) / math.sqrt(2.0)))


def _rolling_pbo(windows) -> float:
    comparable = []
    for item in windows:
        train = item.train_metrics.get("out_of_sample_score")
        test = item.test_metrics.get("out_of_sample_score")
        if train is not None and test is not None:
            comparable.append(float(test) < float(train))
    return float(sum(comparable) / len(comparable)) if comparable else 1.0


def _asof_beta(loader, signal_eligible) -> torch.Tensor | None:
    close = loader.raw_data_cache.get("close")
    if not isinstance(close, torch.Tensor):
        return None
    returns = torch.full_like(close, float("nan"))
    valid = torch.isfinite(close) & (close > 0)
    pair = valid[:, 1:] & valid[:, :-1]
    returns[:, 1:] = torch.where(pair, close[:, 1:] / close[:, :-1] - 1.0, torch.full_like(close[:, 1:], float("nan")))
    market = _masked_mean(returns, signal_eligible & torch.isfinite(returns), dim=0)
    beta = torch.full_like(close, float("nan"))
    for idx in range(close.shape[1]):
        start = max(1, idx - 59)
        stock = returns[:, start : idx + 1]
        benchmark = market[start : idx + 1].unsqueeze(0).expand_as(stock)
        mask = torch.isfinite(stock) & torch.isfinite(benchmark)
        count = mask.sum(dim=1)
        stock_mean = torch.where(mask, stock, torch.zeros_like(stock)).sum(dim=1) / count.clamp(min=1)
        market_mean = torch.where(mask, benchmark, torch.zeros_like(benchmark)).sum(dim=1) / count.clamp(min=1)
        covariance = torch.where(mask, (stock - stock_mean[:, None]) * (benchmark - market_mean[:, None]), torch.zeros_like(stock)).sum(dim=1)
        variance = torch.where(mask, (benchmark - market_mean[:, None]) ** 2, torch.zeros_like(benchmark)).sum(dim=1)
        valid_beta = (count >= 20) & (variance > 1e-12)
        beta[valid_beta, idx] = covariance[valid_beta] / variance[valid_beta]
    return beta


def _feature_validity(loader):
    value = getattr(loader, "feature_validity", None)
    if value is None:
        value = getattr(loader, "feature_validity_tensor", None)
    if value is None:
        raise RuntimeError("feature validity tensor missing")
    return value.bool()


def _transform_input_validity(loader, formula_validity, method):
    if not str(method).startswith("neutralize_"):
        return formula_validity
    validity_cache = getattr(loader, "raw_validity_cache", {}) or {}
    required = ["total_mv"] if method == "neutralize_market_cap" else ["industry_codes"]
    if method == "neutralize_industry_size":
        required = ["total_mv", "industry_codes"]
    result = formula_validity.bool()
    for name in required:
        aliases = ("log_mkt_cap", "total_mv") if name == "total_mv" else ("industry_codes", "industry_code_matrix", "industry_status_known")
        mask = next((validity_cache.get(alias) for alias in aliases if isinstance(validity_cache.get(alias), torch.Tensor)), None)
        if mask is None:
            if getattr(loader, "production_research", False):
                raise RuntimeError(f"transform validity missing: {name}")
            continue
        if mask.ndim == 1:
            mask = mask.unsqueeze(1).expand_as(result)
        result &= mask.to(device=result.device, dtype=torch.bool)
    return result


def _target_available(loader):
    value = getattr(loader, "target_available", None)
    if value is None:
        value = loader.raw_data_cache.get("target_available_mask")
    if not isinstance(value, torch.Tensor) or value.shape != loader.target_ret.shape:
        raise RuntimeError("strict target availability missing")
    return value.bool()


def _signal_eligible(loader):
    for name in ("signal_candidate_cells", "signal_eligible_at_close", "signal_eligible", "pit_available_mask"):
        value = loader.raw_data_cache.get(name)
        if isinstance(value, torch.Tensor) and value.shape == loader.target_ret.shape:
            return value.bool()
    raise RuntimeError("PIT signal eligibility missing")


def _validation_common(loader, signal_eligible, target_available):
    value = loader.raw_data_cache.get("validation_common_cells")
    if isinstance(value, torch.Tensor) and value.shape == loader.target_ret.shape:
        return value.bool()
    if getattr(loader, "production_research", False):
        raise RuntimeError("validation_common_cells missing")
    return signal_eligible & target_available & torch.isfinite(loader.target_ret)


def _optional_mask(loader, *names):
    for name in names:
        value = loader.raw_data_cache.get(name)
        if isinstance(value, torch.Tensor) and value.shape == loader.target_ret.shape:
            return value.bool()
    return None


def _eligible_segments(dates, mask):
    segments = []
    start = None
    values = mask.detach().bool().cpu().tolist()
    for idx, value in enumerate(values + [False]):
        if value and start is None:
            start = idx
        elif not value and start is not None:
            segments.append(list(dates[start:idx]))
            start = None
    return segments


def _align(value, reference):
    if value.ndim == 1:
        return value.to(reference.device).unsqueeze(1).expand_as(reference)
    return value.to(reference.device)


def _corr(left, right):
    mask = torch.isfinite(left) & torch.isfinite(right)
    if int(mask.sum().item()) < 2:
        return 0.0
    x = left[mask].float()
    y = right[mask].float()
    x = x - x.mean()
    y = y - y.mean()
    denom = x.norm() * y.norm()
    return float((x * y).sum().item() / denom.item()) if float(denom.item()) > 1e-12 else 0.0


def _masked_mean(values, mask, dim):
    valid = mask & torch.isfinite(values)
    return torch.where(valid, values, torch.zeros_like(values)).sum(dim=dim) / valid.sum(dim=dim).clamp(min=1)


def _rolling_std(values, window):
    result = torch.full_like(values, float("nan"))
    for idx in range(values.numel()):
        selected = values[max(0, idx - window + 1) : idx + 1]
        selected = selected[torch.isfinite(selected)]
        if selected.numel() >= max(2, window // 2):
            result[idx] = selected.std(unbiased=False)
    return result


def _finite_median(values):
    finite = values[torch.isfinite(values)]
    return float(finite.median().item()) if finite.numel() else 0.0


def _is_finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _set_turnover(sets):
    if len(sets) < 2:
        return 0.0
    values = []
    for left, right in zip(sets[:-1], sets[1:]):
        values.append(1.0 - len(left & right) / max(len(left | right), 1))
    return float(mean(values))


_DATA_BLOCKERS = {
    "data_blocked_window",
    "no_oos_windows",
    "insufficient_evaluable_windows",
    "insufficient_cumulative_oos_dates",
    "insufficient_oos_dates",
    "no_valid_factor_values",
    "zero_variance_factor",
    "all_zero_factor",
}

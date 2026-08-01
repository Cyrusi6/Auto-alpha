"""Strict portfolio-level walk-forward evaluation on the event ledger."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Sequence

import numpy as np

from live_readiness.holdout_simulation.policy import ScenarioPolicy
from live_readiness.holdout_simulation.simulator import EventLedgerSimulator, SimulationDataBlocker

from .admission import validate_factor_certified_records
from .combination import build_combined_signal, fit_factor_combination
from .contracts import (
    DATA_BLOCKED_STATUS,
    PORTFOLIO_REJECTED_STATUS,
    SHADOW_CANDIDATE_STATUS,
    PortfolioResearchError,
    PortfolioResearchPolicy,
    stable_hash,
    validate_production_policy,
)
from .walk_forward import build_portfolio_splits


REQUIRED_MARKET_FIELDS = ("open", "close", "valuation_open", "valuation_close", "lagged_adv")
REQUIRED_MASK_FIELDS = (
    "signal_candidate",
    "membership",
    "active",
    "open_execution_known",
    "buyable_at_open",
    "sellable_at_open",
    "open_validity",
    "close_validity",
    "valuation_open_validity",
    "valuation_close_validity",
    "lagged_adv_validity",
)


@dataclass(frozen=True)
class PortfolioResearchData:
    trade_dates: tuple[str, ...]
    assets: tuple[str, ...]
    factor_records: tuple[dict[str, Any], ...]
    factor_values: np.ndarray
    factor_validity: np.ndarray
    target: np.ndarray
    target_available: np.ndarray
    market: Mapping[str, np.ndarray]
    masks: Mapping[str, np.ndarray]
    universes: Mapping[str, np.ndarray]
    benchmarks: Mapping[str, Mapping[str, np.ndarray]]
    regimes: Mapping[str, np.ndarray]
    corporate_actions: tuple[dict[str, Any], ...] = ()
    lineage: Mapping[str, Any] = field(default_factory=dict)


def evaluate_portfolio_research(
    data: PortfolioResearchData,
    policy: PortfolioResearchPolicy,
    *,
    fee_calculator: Any,
    allow_test_policy: bool = False,
) -> dict[str, Any]:
    try:
        if not allow_test_policy:
            validate_production_policy(policy)
        validated = _validate_data(data, policy)
        return _evaluate(validated, policy, fee_calculator)
    except Exception as exc:
        return _blocked_result(policy, exc)


def _evaluate(data: PortfolioResearchData, policy: PortfolioResearchPolicy, fee_calculator: Any) -> dict[str, Any]:
    if fee_calculator is None:
        raise PortfolioResearchError("external_fee_schedule_required")
    factor_records = list(data.factor_records)
    max_lookback = max(int(row["effective_lookback"]) for row in factor_records)
    embargo = policy.effective_embargo(max_lookback)
    all_windows: list[dict[str, Any]] = []
    all_weights: list[dict[str, Any]] = []
    simulation_runs: list[dict[str, Any]] = []
    universe_summaries: dict[str, dict[str, Any]] = {}
    previous_weights_by_universe: dict[str, tuple[float, ...]] = {}

    signal_base = _strict_mask_product(data.masks, ("signal_candidate", "membership", "active", "close_validity"))
    factor_count = (data.factor_validity & signal_base[None, :, :]).sum(axis=0)
    signal_base &= factor_count >= min(2, policy.min_factor_count)
    target_common = signal_base & data.target_available & np.isfinite(data.target)

    for universe_name in sorted(data.universes):
        universe_mask = np.asarray(data.universes[universe_name], dtype=bool)
        universe_signal = signal_base & universe_mask
        universe_evaluation = target_common & universe_mask
        eligible_dates = universe_evaluation.sum(axis=1) >= policy.min_cross_section_breadth
        splits = build_portfolio_splits(eligible_dates, policy, effective_embargo=embargo)
        universe_runs: list[dict[str, Any]] = []
        positive_baseline_windows = 0
        evaluable_baseline_windows = 0
        for split in splits:
            fit = fit_factor_combination(
                data.factor_values,
                data.factor_validity,
                data.target,
                data.target_available,
                universe_signal,
                split.train_indices,
                factor_records,
                policy,
                previous_weights=previous_weights_by_universe.get(universe_name),
            )
            previous_weights_by_universe[universe_name] = fit.weights
            combined, combined_validity = build_combined_signal(
                data.factor_values,
                data.factor_validity,
                universe_signal,
                fit,
                min_breadth=policy.min_cross_section_breadth,
            )
            test_indices = np.asarray(split.test_indices, dtype=int)
            test_evaluation = universe_evaluation[test_indices] & combined_validity[test_indices]
            valid_test_dates = test_evaluation.sum(axis=1) >= policy.min_cross_section_breadth
            valid_test_date_count = int(valid_test_dates.sum())
            if valid_test_date_count < policy.min_valid_test_dates:
                raise PortfolioResearchError(
                    f"portfolio_test_dates_insufficient:{universe_name}:{split.split_id}:{valid_test_date_count}"
                )
            validation_ic = _combined_rank_ic(
                combined,
                data.target,
                universe_evaluation & combined_validity,
                split.validation_indices,
                policy.min_cross_section_breadth,
            )
            test_ic = _combined_rank_ic(
                combined,
                data.target,
                universe_evaluation & combined_validity,
                split.test_indices,
                policy.min_cross_section_breadth,
            )
            weight_row = {
                "universe": universe_name,
                "split_id": split.split_id,
                "fit_hash": fit.fit_hash,
                "factor_ids": list(fit.factor_ids),
                "families": list(fit.families),
                "cluster_ids": list(fit.cluster_ids),
                "weights": list(fit.weights),
                "mean_rank_ic": list(fit.mean_rank_ic),
                "icir": list(fit.icir),
                "training_observations": list(fit.training_observations),
                "train_start": data.trade_dates[split.train_indices[0]],
                "train_end": data.trade_dates[split.train_indices[-1]],
                "validation_rank_ic": validation_ic,
                "test_rank_ic": test_ic,
            }
            all_weights.append(weight_row)
            scenario_rows: list[dict[str, Any]] = []
            for scenario in policy.required_scenarios:
                run = _simulate_window(
                    data,
                    policy,
                    fee_calculator,
                    universe_name,
                    split.split_id,
                    test_indices,
                    combined,
                    combined_validity & universe_signal,
                    scenario,
                )
                scenario_rows.append(run["summary"])
                simulation_runs.append(run)
                universe_runs.append(run["summary"])
                if scenario.scenario_id == "baseline":
                    evaluable_baseline_windows += 1
                    if run["summary"]["net_total_return"] > policy.min_cost_adjusted_return:
                        positive_baseline_windows += 1
            all_windows.append(
                {
                    "universe": universe_name,
                    "split_id": split.split_id,
                    "effective_embargo": embargo,
                    "valid_test_date_count": valid_test_date_count,
                    "validation_rank_ic": validation_ic,
                    "test_rank_ic": test_ic,
                    "scenarios": scenario_rows,
                }
            )
        universe_summaries[universe_name] = _summarize_universe(
            universe_name,
            universe_runs,
            positive_baseline_windows,
            evaluable_baseline_windows,
            policy,
        )

    gate = _portfolio_gate(universe_summaries, simulation_runs, policy)
    status = SHADOW_CANDIDATE_STATUS if gate["passed"] else PORTFOLIO_REJECTED_STATUS
    semantic = {
        "status": status,
        "policy_id": policy.policy_id,
        "policy_hash": policy.policy_hash,
        "factor_ids": [row["factor_id"] for row in factor_records],
        "formula_hashes": [row["formula_hash"] for row in factor_records],
        "factor_certified_count": len(factor_records),
        "effective_embargo": embargo,
        "walk_forward_window_count": len(all_windows),
        "universe_count": len(data.universes),
        "benchmark_count": len(data.benchmarks),
        "scenario_count": len(policy.required_scenarios),
        "universe_summaries": universe_summaries,
        "gate": gate,
        "lineage": dict(data.lineage),
        "factor_weights": all_weights,
        "windows": all_windows,
        "simulation_runs": simulation_runs,
        "shadow_ready": status == SHADOW_CANDIDATE_STATUS,
        "independent_audit_required_for_paper": True,
        "paper_ready": False,
        "live_ready": False,
        "portfolio_ready": False,
        "certification_ready": False,
        "certification_supported": False,
        "direct_live_forbidden": True,
    }
    semantic["content_hash"] = stable_hash(semantic)
    return semantic


def _simulate_window(
    data: PortfolioResearchData,
    policy: PortfolioResearchPolicy,
    fee_calculator: Any,
    universe_name: str,
    split_id: str,
    indices: np.ndarray,
    combined: np.ndarray,
    combined_validity: np.ndarray,
    scenario,
) -> dict[str, Any]:
    dates = [data.trade_dates[index] for index in indices]
    regime = None
    if scenario.required_regime:
        regime = np.asarray(data.regimes[scenario.required_regime], dtype=bool)[indices]
        if int(regime.sum()) < 2:
            raise PortfolioResearchError(
                f"portfolio_required_regime_dates_insufficient:{scenario.required_regime}:{split_id}"
            )
    market = {
        "dates": dates,
        "assets": list(data.assets),
        "open": np.asarray(data.market["open"], dtype=float)[indices],
        "close": np.asarray(data.market["close"], dtype=float)[indices],
        "valuation_open": np.asarray(data.market["valuation_open"], dtype=float)[indices],
        "valuation_close": np.asarray(data.market["valuation_close"], dtype=float)[indices],
        "adv": np.asarray(data.market["lagged_adv"], dtype=float)[indices] * scenario.lagged_adv_multiplier,
        "valuation_open_method": np.asarray(data.market.get("valuation_open_method"), dtype=object)[indices],
        "valuation_open_source_date": np.asarray(data.market.get("valuation_open_source_date"), dtype=object)[indices],
        "valuation_open_evidence_id": np.asarray(data.market.get("valuation_open_evidence_id"), dtype=object)[indices],
        "valuation_open_stale_age": np.asarray(data.market.get("valuation_open_stale_age"), dtype=np.int32)[indices],
        "valuation_close_method": np.asarray(data.market.get("valuation_close_method"), dtype=object)[indices],
        "valuation_close_source_date": np.asarray(data.market.get("valuation_close_source_date"), dtype=object)[indices],
        "valuation_close_evidence_id": np.asarray(data.market.get("valuation_close_evidence_id"), dtype=object)[indices],
        "valuation_close_stale_age": np.asarray(data.market.get("valuation_close_stale_age"), dtype=np.int32)[indices],
    }
    buy = (
        np.asarray(data.masks["buyable_at_open"], dtype=bool)
        & np.asarray(data.masks["open_execution_known"], dtype=bool)
        & np.asarray(data.masks["open_validity"], dtype=bool)
    )[indices]
    sell = (
        np.asarray(data.masks["sellable_at_open"], dtype=bool)
        & np.asarray(data.masks["open_execution_known"], dtype=bool)
        & np.asarray(data.masks["open_validity"], dtype=bool)
    )[indices]
    select = combined_validity[indices]
    scenario_policy = ScenarioPolicy(
        name=scenario.scenario_id,
        initial_aum=policy.initial_aum,
        top_n=policy.top_n,
        max_weight=policy.max_stock_weight,
        lot_size=policy.lot_size,
        adv_participation=0.10,
        modeled_cost_multiplier=scenario.modeled_cost_multiplier,
    )
    simulator = EventLedgerSimulator(
        scenario_policy,
        fee_calculator=fee_calculator,
        require_external_fee_schedule=True,
        require_explicit_valuation_marks=True,
    )
    try:
        result = simulator.run(
            market,
            combined[indices],
            masks={"buy": buy, "sell": sell, "select": select},
            corporate_actions=_window_actions(data.corporate_actions, data.trade_dates, indices),
        )
    except SimulationDataBlocker as exc:
        raise PortfolioResearchError(f"portfolio_event_ledger_blocked:{universe_name}:{split_id}:{scenario.scenario_id}:{exc}") from exc
    returns = []
    return_dates = []
    for row in result.nav:
        if row.open_to_open_return is None:
            continue
        if regime is not None and not bool(regime[row.index]):
            continue
        returns.append(float(row.open_to_open_return))
        return_dates.append(row.date)
    if not returns:
        raise PortfolioResearchError(f"portfolio_oos_nav_returns_missing:{universe_name}:{split_id}:{scenario.scenario_id}")
    net_return = _compound(returns)
    nav_values = [float(row.open_post) for row in result.nav if regime is None or bool(regime[row.index])]
    max_drawdown = _max_drawdown(nav_values)
    total_cost = float(sum(fill.total_cost for fill in result.fills))
    avg_nav = float(np.mean(nav_values)) if nav_values else 0.0
    turnover = float(sum(fill.notional for fill in result.fills) / avg_nav) if avg_nav > 0.0 else 0.0
    capacity_rejections = sum(rejection.reason in {"capacity_zero", "insufficient_capacity"} for rejection in result.rejections)
    benchmark_metrics = {}
    for benchmark_name, benchmark in sorted(data.benchmarks.items()):
        benchmark_returns = np.asarray(benchmark["returns"], dtype=float)[indices]
        benchmark_validity = np.asarray(benchmark["validity"], dtype=bool)[indices]
        selected = []
        for local_index, date in enumerate(dates):
            if date not in return_dates:
                continue
            if benchmark_validity[local_index] and np.isfinite(benchmark_returns[local_index]):
                selected.append(float(benchmark_returns[local_index]))
        if len(selected) != len(returns):
            raise PortfolioResearchError(
                f"benchmark_oos_alignment_invalid:{benchmark_name}:{universe_name}:{split_id}:{scenario.scenario_id}"
            )
        benchmark_return = _compound(selected)
        benchmark_metrics[benchmark_name] = {
            "benchmark_total_return": benchmark_return,
            "active_total_return": net_return - benchmark_return,
            "observation_count": len(selected),
        }
    summary = {
        "run_id": f"{universe_name}:{split_id}:{scenario.scenario_id}",
        "universe": universe_name,
        "split_id": split_id,
        "scenario_id": scenario.scenario_id,
        "net_total_return": net_return,
        "max_drawdown": max_drawdown,
        "turnover": turnover,
        "total_cost": total_cost,
        "fill_count": len(result.fills),
        "rejection_count": len(result.rejections),
        "capacity_rejection_count": int(capacity_rejections),
        "return_observation_count": len(returns),
        "benchmark_metrics": benchmark_metrics,
        "modeled_cost_multiplier": scenario.modeled_cost_multiplier,
        "lagged_adv_multiplier": scenario.lagged_adv_multiplier,
        "required_regime": scenario.required_regime,
    }
    return {
        "summary": summary,
        "orders": [item.to_dict() for item in result.orders],
        "fills": [item.to_dict() for item in result.fills],
        "rejections": [item.to_dict() for item in result.rejections],
        "settlements": [item.to_dict() for item in result.settlements],
        "nav": [item.to_dict() for item in result.nav],
        "event_ledger": result.event_ledger,
        "run_hash": stable_hash(result.to_dict()),
    }


def _summarize_universe(name, runs, positive_baseline, evaluable_baseline, policy):
    scenarios: dict[str, list[dict[str, Any]]] = {}
    for row in runs:
        scenarios.setdefault(str(row["scenario_id"]), []).append(row)
    scenario_summary = {}
    for scenario_id, rows in scenarios.items():
        returns = [float(row["net_total_return"]) for row in rows]
        scenario_summary[scenario_id] = {
            "window_count": len(rows),
            "positive_window_ratio": sum(value > policy.min_cost_adjusted_return for value in returns) / len(returns),
            "compounded_net_return": _compound(returns),
            "worst_window_return": min(returns),
            "max_drawdown": max(float(row["max_drawdown"]) for row in rows),
        }
    return {
        "universe": name,
        "baseline_window_count": evaluable_baseline,
        "baseline_positive_window_ratio": positive_baseline / evaluable_baseline if evaluable_baseline else 0.0,
        "scenarios": scenario_summary,
    }


def _portfolio_gate(universe_summaries, runs, policy):
    reasons: list[str] = []
    universe_passes = []
    stress_passes = []
    benchmark_passes = []
    for universe_name, summary in universe_summaries.items():
        baseline = (summary.get("scenarios") or {}).get("baseline") or {}
        passed = bool(
            summary.get("baseline_positive_window_ratio", 0.0) >= policy.min_positive_window_ratio
            and baseline.get("compounded_net_return", -1.0) > policy.min_cost_adjusted_return
            and baseline.get("max_drawdown", 1.0) <= policy.max_drawdown
        )
        universe_passes.append(passed)
        if not passed:
            reasons.append(f"universe_robustness_failed:{universe_name}")
        for scenario_id, scenario in (summary.get("scenarios") or {}).items():
            if scenario_id == "baseline":
                continue
            scenario_passed = bool(
                scenario.get("compounded_net_return", -1.0) > policy.min_cost_adjusted_return
                and scenario.get("positive_window_ratio", 0.0) >= policy.min_positive_window_ratio
                and scenario.get("max_drawdown", 1.0) <= policy.max_drawdown
            )
            stress_passes.append(scenario_passed)
            if not scenario_passed:
                reasons.append(f"stress_robustness_failed:{universe_name}:{scenario_id}")
    for row in runs:
        if row["summary"]["scenario_id"] != "baseline":
            continue
        for benchmark_name, benchmark in row["summary"]["benchmark_metrics"].items():
            passed = float(benchmark["active_total_return"]) > policy.min_active_return
            benchmark_passes.append(passed)
            if not passed:
                reasons.append(f"benchmark_active_return_failed:{row['summary']['universe']}:{benchmark_name}:{row['summary']['split_id']}")
    universe_ratio = sum(universe_passes) / len(universe_passes) if universe_passes else 0.0
    stress_ratio = sum(stress_passes) / len(stress_passes) if stress_passes else 0.0
    benchmark_ratio = sum(benchmark_passes) / len(benchmark_passes) if benchmark_passes else 0.0
    if universe_ratio < policy.min_universe_pass_ratio:
        reasons.append("multi_universe_pass_ratio_below_policy")
    if stress_ratio < policy.min_stress_pass_ratio:
        reasons.append("stress_pass_ratio_below_policy")
    if benchmark_ratio < policy.min_benchmark_pass_ratio:
        reasons.append("multi_benchmark_pass_ratio_below_policy")
    return {
        "passed": not reasons,
        "reasons": sorted(set(reasons)),
        "universe_pass_ratio": universe_ratio,
        "stress_pass_ratio": stress_ratio,
        "benchmark_pass_ratio": benchmark_ratio,
        "shadow_only": True,
        "paper_requires_independent_audit": True,
        "live_forbidden": True,
    }


def _validate_data(data: PortfolioResearchData, policy: PortfolioResearchPolicy) -> PortfolioResearchData:
    records = validate_factor_certified_records(
        data.factor_records,
        min_factor_count=policy.min_factor_count,
        min_family_count=policy.min_family_count,
    )
    dates = tuple(str(value) for value in data.trade_dates)
    assets = tuple(str(value) for value in data.assets)
    if len(set(dates)) != len(dates) or list(dates) != sorted(dates) or len(set(assets)) != len(assets):
        raise PortfolioResearchError("portfolio_axes_invalid")
    shape = (len(dates), len(assets))
    factor_shape = (len(records), *shape)
    factor_values = np.asarray(data.factor_values, dtype=float)
    factor_validity = np.asarray(data.factor_validity, dtype=bool)
    if factor_values.shape != factor_shape or factor_validity.shape != factor_shape:
        raise PortfolioResearchError("portfolio_factor_axes_mismatch")
    if np.any(factor_values[~factor_validity] != 0.0):
        raise PortfolioResearchError("invalid_factor_cells_must_store_zero")
    target = _finite_matrix(data.target, shape, "target", allow_invalid=True)
    target_available = _bool_matrix(data.target_available, shape, "target_available")
    if np.any(target_available & ~np.isfinite(target)):
        raise PortfolioResearchError("target_available_contains_nonfinite_target")
    if np.any(~target_available & (target != 0.0)):
        raise PortfolioResearchError("unavailable_target_cells_must_store_zero")
    if np.any(target_available[-policy.label_horizon :]):
        raise PortfolioResearchError("target_tail_endpoint_unavailable_contract_violated")
    market = dict(data.market)
    for field in REQUIRED_MARKET_FIELDS:
        market[field] = _finite_matrix(market.get(field), shape, f"market:{field}", allow_invalid=True)
    for field in (
        "valuation_open_method",
        "valuation_open_source_date",
        "valuation_open_evidence_id",
        "valuation_close_method",
        "valuation_close_source_date",
        "valuation_close_evidence_id",
    ):
        raw = np.asarray(market.get(field), dtype=object)
        if raw.shape != shape or np.any(raw == ""):
            raise PortfolioResearchError(f"explicit_valuation_metadata_invalid:{field}")
        market[field] = raw
    for field in ("valuation_open_stale_age", "valuation_close_stale_age"):
        raw = np.asarray(market.get(field), dtype=np.int32)
        if raw.shape != shape or np.any(raw < 0):
            raise PortfolioResearchError(f"explicit_valuation_metadata_invalid:{field}")
        market[field] = raw
    masks = dict(data.masks)
    for field in REQUIRED_MASK_FIELDS:
        masks[field] = _bool_matrix(masks.get(field), shape, f"mask:{field}")
    validity_by_field = {
        "open": "open_validity",
        "close": "close_validity",
        "valuation_open": "valuation_open_validity",
        "valuation_close": "valuation_close_validity",
        "lagged_adv": "lagged_adv_validity",
    }
    for field, validity_name in validity_by_field.items():
        valid = masks[validity_name]
        values = market[field]
        if np.any(valid & (~np.isfinite(values) | (values <= 0.0))):
            raise PortfolioResearchError(f"market_valid_cell_invalid:{field}")
        if np.any(~valid & np.isfinite(values) & (values != 0.0)):
            raise PortfolioResearchError(f"market_invalid_cell_not_zero:{field}")
    universes = {name: _bool_matrix(value, shape, f"universe:{name}") for name, value in data.universes.items()}
    if len(universes) < 2:
        raise PortfolioResearchError("multi_universe_evidence_required")
    benchmarks = {}
    for name, raw in data.benchmarks.items():
        returns = np.asarray(raw.get("returns"), dtype=float).reshape(-1)
        validity = np.asarray(raw.get("validity"), dtype=bool).reshape(-1)
        if returns.shape != (len(dates),) or validity.shape != returns.shape:
            raise PortfolioResearchError(f"benchmark_axis_invalid:{name}")
        if np.any(validity & ~np.isfinite(returns)):
            raise PortfolioResearchError(f"benchmark_validity_invalid:{name}")
        benchmarks[str(name)] = {"returns": returns, "validity": validity}
    if len(benchmarks) < 2:
        raise PortfolioResearchError("multi_benchmark_evidence_required")
    regimes = {name: np.asarray(value, dtype=bool).reshape(-1) for name, value in data.regimes.items()}
    if "extreme_volatility" not in regimes or regimes["extreme_volatility"].shape != (len(dates),):
        raise PortfolioResearchError("extreme_volatility_regime_required")
    return replace(
        data,
        trade_dates=dates,
        assets=assets,
        factor_records=tuple(records),
        factor_values=factor_values,
        factor_validity=factor_validity,
        target=target,
        target_available=target_available,
        market=market,
        masks=masks,
        universes=universes,
        benchmarks=benchmarks,
        regimes=regimes,
    )


def _strict_mask_product(masks: Mapping[str, np.ndarray], fields: Sequence[str]) -> np.ndarray:
    result = np.ones_like(np.asarray(masks[fields[0]], dtype=bool))
    for field in fields:
        result &= np.asarray(masks[field], dtype=bool)
    return result


def _combined_rank_ic(factor, target, validity, indices, min_breadth):
    values = []
    for index in indices:
        mask = validity[index] & np.isfinite(factor[index]) & np.isfinite(target[index])
        if int(mask.sum()) < min_breadth:
            continue
        left = _average_rank(factor[index, mask])
        right = _average_rank(target[index, mask])
        if np.std(left) <= 1e-12 or np.std(right) <= 1e-12:
            continue
        value = float(np.corrcoef(left, right)[0, 1])
        if np.isfinite(value):
            values.append(value)
    return float(np.mean(values)) if values else None


def _average_rank(values):
    values = np.asarray(values, dtype=float)
    order = np.argsort(values, kind="mergesort")
    result = np.empty(values.size, dtype=float)
    start = 0
    while start < values.size:
        end = start + 1
        while end < values.size and values[order[end]] == values[order[start]]:
            end += 1
        result[order[start:end]] = (start + end - 1) / 2.0 + 1.0
        start = end
    return result


def _window_actions(actions, dates, indices):
    selected_dates = {dates[index] for index in indices}
    result = []
    for raw in actions:
        row = dict(raw)
        date = str(row.get("ex_date") or row.get("effective_date") or "")
        if not date and row.get("effective_index") is not None:
            absolute = int(row["effective_index"])
            if 0 <= absolute < len(dates):
                date = dates[absolute]
        if date not in selected_dates:
            continue
        row.pop("effective_index", None)
        row["ex_date"] = date
        pay_date = str(row.get("pay_date") or "")
        if pay_date and pay_date not in selected_dates:
            row["pay_date"] = date
        result.append(row)
    return result


def _finite_matrix(value, shape, name, *, allow_invalid=False):
    array = np.asarray(value, dtype=float)
    if array.shape != shape:
        raise PortfolioResearchError(f"{name}_shape_invalid")
    if not allow_invalid and (np.any(~np.isfinite(array)) or np.any(array <= 0.0)):
        raise PortfolioResearchError(f"{name}_value_invalid")
    return array


def _bool_matrix(value, shape, name):
    array = np.asarray(value)
    if array.shape != shape or array.dtype != np.bool_:
        raise PortfolioResearchError(f"{name}_must_be_explicit_bool")
    return array.astype(bool, copy=True)


def _compound(returns):
    value = 1.0
    for item in returns:
        if not math.isfinite(float(item)):
            raise PortfolioResearchError("portfolio_return_nonfinite")
        value *= 1.0 + float(item)
    return value - 1.0


def _max_drawdown(nav_values):
    peak = 0.0
    drawdown = 0.0
    for value in nav_values:
        if not math.isfinite(value) or value < 0.0:
            raise PortfolioResearchError("portfolio_nav_invalid")
        peak = max(peak, value)
        if peak > 0.0:
            drawdown = max(drawdown, 1.0 - value / peak)
    return drawdown


def _blocked_result(policy: PortfolioResearchPolicy, exc: Exception) -> dict[str, Any]:
    blocker = f"{type(exc).__name__}:{exc}"
    semantic = {
        "status": DATA_BLOCKED_STATUS,
        "policy_id": policy.policy_id,
        "policy_hash": policy.policy_hash,
        "factor_ids": [],
        "formula_hashes": [],
        "factor_certified_count": 0,
        "effective_embargo": 0,
        "walk_forward_window_count": 0,
        "universe_count": 0,
        "benchmark_count": 0,
        "scenario_count": len(policy.required_scenarios),
        "universe_summaries": {},
        "gate": {"passed": False, "reasons": [blocker]},
        "lineage": {},
        "factor_weights": [],
        "windows": [],
        "simulation_runs": [],
        "blockers": [blocker],
        "shadow_ready": False,
        "independent_audit_required_for_paper": True,
        "certification_ready": False,
        "portfolio_ready": False,
        "paper_ready": False,
        "live_ready": False,
        "certification_supported": False,
        "direct_live_forbidden": True,
    }
    semantic["content_hash"] = stable_hash(semantic)
    return semantic

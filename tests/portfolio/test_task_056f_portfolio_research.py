from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import numpy as np

from auto_alpha.platform.artifacts.schema.run_validate import main as artifact_validate_main
from auto_alpha.research.factors.store import FactorLifecycleStatus, FactorRecord, LocalFactorStore
from auto_alpha.portfolio.construction.campaigns import ingest_certified_factor_pool
from auto_alpha.portfolio.construction.lab import PortfolioLabConfig
from auto_alpha.portfolio.construction.lab import run_portfolio_lab
from auto_alpha.portfolio.construction.research import validate_factor_certified_records
from auto_alpha.portfolio.construction.research import build_combined_signal
from auto_alpha.portfolio.construction.research import fit_factor_combination
from auto_alpha.portfolio.construction.research import PortfolioResearchError
from auto_alpha.portfolio.construction.research import PortfolioResearchPolicy
from auto_alpha.portfolio.construction.research import PortfolioResearchData
from auto_alpha.portfolio.construction.research import evaluate_portfolio_research
from auto_alpha.portfolio.construction.research import publish_portfolio_research_result


class _FeeCalculator:
    def calculate(self, *, date, market, side, notional, shares, zero_all_costs, modeled_multiplier):
        if zero_all_costs:
            values = {name: 0.0 for name in _FEE_COMPONENTS}
        else:
            values = {
                "commission": notional * 0.00001 * modeled_multiplier,
                "stamp_duty": notional * 0.00001 if side == "SELL" else 0.0,
                "transfer_fee": notional * 0.000001,
                "handling_fee": notional * 0.000001,
                "securities_management_fee": notional * 0.000001,
                "slippage": notional * 0.00001 * modeled_multiplier,
                "impact": notional * 0.00001 * modeled_multiplier,
            }
        return values | {"total": sum(values.values())}


_FEE_COMPONENTS = (
    "commission",
    "stamp_duty",
    "transfer_fee",
    "handling_fee",
    "securities_management_fee",
    "slippage",
    "impact",
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _policy(**changes) -> PortfolioResearchPolicy:
    base = PortfolioResearchPolicy(
        policy_id="factor_certified_portfolio_test_v1",
        train_size=6,
        validation_size=2,
        test_size=4,
        step_size=4,
        label_horizon=2,
        min_embargo=2,
        min_factor_count=4,
        min_family_count=2,
        min_cross_section_breadth=4,
        min_pair_observations=8,
        min_evaluable_windows=2,
        min_valid_test_dates=4,
        family_weight_cap=0.70,
        cluster_weight_cap=1.0,
        factor_weight_cap=0.70,
        weight_shrinkage=0.20,
        max_weight_change=0.20,
        min_positive_window_ratio=0.50,
        min_universe_pass_ratio=1.0,
        min_benchmark_pass_ratio=1.0,
        min_stress_pass_ratio=1.0,
        max_drawdown=0.90,
        top_n=2,
        max_stock_weight=0.50,
    )
    return replace(base, **changes)


def _factor_records() -> tuple[dict, ...]:
    return tuple(
        {
            "factor_id": f"factor_certified_{index}",
            "formula_hash": _hash(f"formula:{index}"),
            "status": "factor_certified",
            "family": "trend" if index < 2 else "quality",
            "effective_lookback": 2,
            "sealed_holdout_status": "sealed_holdout_passed",
            "independent_audit_passed": True,
            "certification_evidence_hash": _hash(f"evidence:{index}"),
        }
        for index in range(4)
    )


def _data() -> PortfolioResearchData:
    dates = tuple(f"2020{index + 1:04d}" for index in range(22))
    assets = tuple(f"00000{index + 1}.SZ" for index in range(6))
    daily_returns = np.asarray([-0.004, 0.001, 0.004, 0.009, 0.014, 0.020], dtype=float)
    open_price = np.zeros((22, 6), dtype=float)
    open_price[0] = 10.0
    for date_index in range(1, 22):
        open_price[date_index] = open_price[date_index - 1] * (1.0 + daily_returns)
    close_price = open_price.copy()
    asset_rank = np.arange(6, dtype=float)
    factor_values = np.zeros((4, 22, 6), dtype=np.float32)
    factor_values[0] = asset_rank[None, :] + np.arange(22)[:, None] * 0.001
    factor_values[1] = asset_rank[None, :] * 1.02 + np.sin(np.arange(22)[:, None]) * 0.001
    factor_values[2] = np.square(asset_rank[None, :] + 1.0) + np.arange(22)[:, None] * 0.0001
    factor_values[3] = asset_rank[None, :] + (np.arange(22)[:, None] % 3) * 0.002
    factor_validity = np.ones_like(factor_values, dtype=bool)
    target = np.zeros((22, 6), dtype=float)
    target_available = np.zeros((22, 6), dtype=bool)
    for date_index in range(20):
        target[date_index] = open_price[date_index + 2] / open_price[date_index + 1] - 1.0
        target_available[date_index] = True
    shape = target.shape
    methods_open = np.full(shape, "OFFICIAL_OPEN", dtype="U32")
    methods_close = np.full(shape, "OFFICIAL_CLOSE", dtype="U32")
    source_dates = np.asarray([[date] * 6 for date in dates], dtype="U16")
    evidence = np.asarray([[f"evidence:{date}:{asset}" for asset in assets] for date in dates], dtype="U64")
    truth = np.ones(shape, dtype=bool)
    second_universe = truth.copy()
    second_universe[:, 0] = False
    benchmark_validity = np.ones(22, dtype=bool)
    benchmark_validity[0] = False
    return PortfolioResearchData(
        trade_dates=dates,
        assets=assets,
        factor_records=_factor_records(),
        factor_values=factor_values,
        factor_validity=factor_validity,
        target=target,
        target_available=target_available,
        market={
            "open": open_price,
            "close": close_price,
            "valuation_open": open_price,
            "valuation_close": close_price,
            "lagged_adv": np.full(shape, 2_000_000.0),
            "valuation_open_method": methods_open,
            "valuation_open_source_date": source_dates,
            "valuation_open_stale_age": np.zeros(shape, dtype=np.int32),
            "valuation_open_evidence_id": evidence,
            "valuation_close_method": methods_close,
            "valuation_close_source_date": source_dates,
            "valuation_close_stale_age": np.zeros(shape, dtype=np.int32),
            "valuation_close_evidence_id": evidence,
        },
        masks={
            "signal_candidate": truth,
            "membership": truth,
            "active": truth,
            "open_execution_known": truth,
            "buyable_at_open": truth,
            "sellable_at_open": truth,
            "open_validity": truth,
            "close_validity": truth,
            "valuation_open_validity": truth,
            "valuation_close_validity": truth,
            "lagged_adv_validity": truth,
        },
        universes={"csi300": truth, "liquid_subset": second_universe},
        benchmarks={
            "csi300": {"returns": np.full(22, -0.001), "validity": benchmark_validity},
            "broad_market": {"returns": np.full(22, -0.002), "validity": benchmark_validity},
        },
        regimes={"extreme_volatility": np.arange(22) % 2 == 0, "normal": np.arange(22) % 2 == 1},
        lineage={"freeze_hash": _hash("freeze"), "matrix_hash": _hash("matrix")},
    )


def test_only_factor_certified_records_are_admitted():
    records = list(_factor_records())
    admitted = validate_factor_certified_records(records, min_factor_count=4, min_family_count=2)
    assert [row["factor_id"] for row in admitted] == sorted(row["factor_id"] for row in records)
    records[0] = {**records[0], "status": "sealed_holdout_passed"}
    try:
        validate_factor_certified_records(records, min_factor_count=4, min_family_count=2)
    except PortfolioResearchError as error:
        assert "factor_not_factor_certified" in str(error)
    else:
        raise AssertionError("non-certified factor entered portfolio research")
    assert FactorLifecycleStatus.factor_certified.value == "factor_certified"


def test_combination_fit_is_train_only_clustered_capped_and_stable():
    data = _data()
    policy = _policy()
    common = data.masks["signal_candidate"] & data.masks["membership"] & data.masks["active"]
    fit = fit_factor_combination(
        data.factor_values,
        data.factor_validity,
        data.target,
        data.target_available,
        common,
        range(6),
        data.factor_records,
        policy,
    )
    changed_target = data.target.copy()
    changed_target[10:] = -0.90
    unchanged = fit_factor_combination(
        data.factor_values,
        data.factor_validity,
        changed_target,
        data.target_available,
        common,
        range(6),
        data.factor_records,
        policy,
    )
    assert fit.fit_hash == unchanged.fit_hash
    assert abs(sum(fit.weights) - 1.0) < 1e-10
    assert max(fit.weights) <= policy.factor_weight_cap + 1e-10
    family_weights = {}
    for family, weight in zip(fit.families, fit.weights):
        family_weights[family] = family_weights.get(family, 0.0) + weight
    assert max(family_weights.values()) <= policy.family_weight_cap + 1e-10
    shifted = fit_factor_combination(
        data.factor_values,
        data.factor_validity,
        data.target,
        data.target_available,
        common,
        range(4, 10),
        data.factor_records,
        policy,
        previous_weights=fit.weights,
    )
    assert max(abs(left - right) for left, right in zip(fit.weights, shifted.weights)) <= policy.max_weight_change + 1e-10
    signal, validity = build_combined_signal(
        data.factor_values,
        data.factor_validity,
        common,
        fit,
        min_breadth=policy.min_cross_section_breadth,
    )
    assert signal.shape == data.target.shape
    assert validity.shape == data.target.shape


def test_invalid_extreme_values_do_not_change_eligible_combined_output():
    data = _data()
    policy = _policy()
    validity = data.factor_validity.copy()
    validity[:, :, 0] = False
    values = data.factor_values.copy()
    values[:, :, 0] = 0.0
    common = data.masks["signal_candidate"]
    fit = fit_factor_combination(
        values,
        validity,
        data.target,
        data.target_available,
        common,
        range(6),
        data.factor_records,
        policy,
    )
    baseline, baseline_validity = build_combined_signal(values, validity, common, fit, min_breadth=4)
    mutated = values.copy()
    mutated[:, :, 0] = 1e30
    actual, actual_validity = build_combined_signal(mutated, validity, common, fit, min_breadth=4)
    assert np.array_equal(baseline_validity, actual_validity)
    assert np.array_equal(baseline[:, 1:], actual[:, 1:])


def test_strict_target_tail_and_missing_masks_block_portfolio_research():
    data = _data()
    policy = _policy()
    bad_tail = data.target_available.copy()
    bad_tail[-1] = True
    result = evaluate_portfolio_research(
        replace(data, target_available=bad_tail),
        policy,
        fee_calculator=_FeeCalculator(),
        allow_test_policy=True,
    )
    assert result["status"] == "data_blocked"
    assert "target_tail_endpoint_unavailable_contract_violated" in result["blockers"][0]
    masks = dict(data.masks)
    masks.pop("buyable_at_open")
    result = evaluate_portfolio_research(
        replace(data, masks=masks),
        policy,
        fee_calculator=_FeeCalculator(),
        allow_test_policy=True,
    )
    assert result["status"] == "data_blocked"
    assert "mask:buyable_at_open" in result["blockers"][0]


def test_event_ledger_walk_forward_runs_all_universes_benchmarks_and_stresses():
    result = evaluate_portfolio_research(
        _data(),
        _policy(),
        fee_calculator=_FeeCalculator(),
        allow_test_policy=True,
    )
    assert result["status"] in {"shadow_candidate", "portfolio_rejected"}
    assert result["status"] != "data_blocked"
    assert result["factor_certified_count"] == 4
    assert result["walk_forward_window_count"] == 4
    assert len(result["simulation_runs"]) == 16
    assert {run["summary"]["scenario_id"] for run in result["simulation_runs"]} == {
        "baseline",
        "double_modeled_cost",
        "volume_down_50pct",
        "extreme_volatility",
    }
    assert all(len(run["summary"]["benchmark_metrics"]) == 2 for run in result["simulation_runs"])
    assert all(run["fills"] is not None and run["event_ledger"] for run in result["simulation_runs"])
    assert result["paper_ready"] is False
    assert result["live_ready"] is False
    assert result["portfolio_ready"] is False
    assert result["independent_audit_required_for_paper"] is True


def test_shadow_publication_never_creates_paper_or_live_queue(tmp_path, capsys):
    result = evaluate_portfolio_research(
        _data(),
        _policy(min_active_return=-1.0, min_cost_adjusted_return=-1.0),
        fee_calculator=_FeeCalculator(),
        allow_test_policy=True,
    )
    assert result["status"] == "shadow_candidate"
    manifest = publish_portfolio_research_result(result, tmp_path / "portfolio")
    generation = tmp_path / "portfolio" / "generations" / manifest["generation_id"]
    shadow_rows = [json.loads(line) for line in (generation / "portfolio_shadow_queue.jsonl").read_text().splitlines() if line]
    assert len(shadow_rows) == 1
    assert shadow_rows[0]["paper_ready"] is False
    assert shadow_rows[0]["live_ready"] is False
    assert not (generation / "paper_queue.jsonl").exists()
    assert not (generation / "live_queue.jsonl").exists()
    assert artifact_validate_main(
        ["--artifact-dir", str(generation), "--output-dir", str(tmp_path / "schema"), "--pretty"]
    ) == 0
    schema = json.loads(capsys.readouterr().out)
    assert schema["error_count"] == 0
    assert schema["unknown_artifact_count"] == 0


def test_blocked_publication_is_schema_valid_and_enters_no_queue(tmp_path, capsys):
    data = _data()
    masks = dict(data.masks)
    masks.pop("buyable_at_open")
    result = evaluate_portfolio_research(
        replace(data, masks=masks),
        _policy(),
        fee_calculator=_FeeCalculator(),
        allow_test_policy=True,
    )
    assert result["status"] == "data_blocked"
    manifest = publish_portfolio_research_result(result, tmp_path / "blocked")
    generation = tmp_path / "blocked" / "generations" / manifest["generation_id"]
    assert not (generation / "portfolio_shadow_queue.jsonl").read_text(encoding="utf-8").strip()
    assert artifact_validate_main(
        ["--artifact-dir", str(generation), "--output-dir", str(tmp_path / "blocked_schema"), "--pretty"]
    ) == 0
    schema = json.loads(capsys.readouterr().out)
    assert schema["error_count"] == 0
    assert schema["unknown_artifact_count"] == 0


def test_legacy_portfolio_lab_and_pool_reject_non_factor_certified(tmp_path):
    store = LocalFactorStore(tmp_path / "store")
    store.save_factor(
        FactorRecord(
            factor_id="factor_legacy",
            formula=["RET_1D"],
            formula_tokens=[0],
            formula_hash=_hash("legacy"),
            feature_version="v3",
            operator_version="v3",
            lookback_days=1,
            created_at="2026-01-01T00:00:00Z",
            status="approved",
        )
    )
    config = PortfolioLabConfig(
        data_dir=str(tmp_path / "data"),
        factor_store_dir=str(tmp_path / "store"),
        output_dir=str(tmp_path / "lab"),
        factor_id="factor_legacy",
        factor_type="any",
        latest_approved=False,
    )
    try:
        run_portfolio_lab(config, policies=[])
    except RuntimeError as error:
        assert "requires_factor_certified" in str(error)
    else:
        raise AssertionError("legacy approved factor entered portfolio lab")
    pool = tmp_path / "pool.jsonl"
    pool.write_text(json.dumps({"factor_id": "factor_legacy", "certification_status": "certified"}) + "\n")
    try:
        ingest_certified_factor_pool(tmp_path / "campaign", pool)
    except ValueError as error:
        assert "factor_certified" in str(error)
    else:
        raise AssertionError("legacy certification status entered portfolio campaign")

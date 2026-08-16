"""Fixed-factor development replay over an immutable local A-share bundle.

This is a vertical engineering diagnostic, not an Alpha search or promotion
path.  It locks one formula, materializes it without reading the target, runs
next-open event-ledger mechanics under explicit development assumptions, and
publishes tamper-evident evidence while preserving every upstream governance
blocker.
"""

from __future__ import annotations

import argparse
import inspect
import json
import math
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import torch

from auto_alpha.data.lake.store.local_development_bundle import (
    EVIDENCE_FLAGS,
    LocalDevelopmentBundleError,
    LocalDevelopmentBundleLoader,
)
from auto_alpha.platform.artifacts.storage import (
    canonical_hash,
    publish_prepared_generation,
    read_json,
    sha256_file,
    validate_generation,
)
from auto_alpha.portfolio.simulator.ledger_models import SimulationResult
from auto_alpha.portfolio.simulator.ledger_policy import ScenarioPolicy
from auto_alpha.portfolio.simulator.ledger_simulator import (
    SimulationDataBlocker,
    simulate_event_ledger,
)
from auto_alpha.research.features.vocab import make_formula_vocab
from auto_alpha.research.formulas.operators import get_operator_spec
from auto_alpha.research.formulas.semantics import execute_operator_with_validity
from auto_alpha.research.formulas.vm import StackVM


SCHEMA_VERSION = "fixed_factor_replay_evidence_v1"
MANIFEST_NAME = "fixed_factor_replay_evidence.json"
GENERATION_PREFIX = "fixed_factor_replay"
TERMINAL_STATUS = "diagnostic_completed"
FACTOR_ID = "volume_ratio_cs_rank_v1"
FORMULA_NAMES = ("volume_ratio", "CS_RANK")
VOLUME_TO_SHARES = 100.0
AMOUNT_TO_CNY = 1_000.0
ADV_WINDOW = 20
ADV_MIN_PERIODS = 1
LOCAL_FEATURE_NAMES = (
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "volume",
    "amount",
    "turnover_rate",
    "volume_ratio",
    "total_mv",
)
_COMMON_GOVERNANCE_BLOCKERS = (
    "adjustment_revision_history_unproven",
    "corporate_action_lineage_unproven",
    "pit_membership_publication_unproven",
    "st_status_unproven",
    "suspension_state_unproven",
)
_BLOCKERS_BY_SOURCE_GRADE = {
    "source_freeze_bound": tuple(
        sorted(("provider_coverage_unproven", *_COMMON_GOVERNANCE_BLOCKERS))
    ),
    "legacy_unproven": tuple(
        sorted(
            (
                "legacy_provider_coverage_unproven",
                "legacy_source_artifact_root_unavailable",
                *_COMMON_GOVERNANCE_BLOCKERS,
            )
        )
    ),
}

_TABLE_NAMES = (
    "orders",
    "fills",
    "rejections",
    "settlements",
    "corporate_actions",
    "nav",
    "event_ledger",
)
_SCENARIOS = ("baseline", "zero_cost")
_ARTIFACT_PATHS = {
    "factor_values": "factor_values.npy",
    "factor_validity": "factor_validity.npy",
    "signal_eligibility": "signal_eligibility.npy",
    "buy_execution_proxy": "buy_execution_proxy.npy",
    "sell_execution_proxy": "sell_execution_proxy.npy",
    "underwater_series": "underwater_series.jsonl",
    "factor_diagnostics": "factor_diagnostics.json",
    "backtest_summary": "backtest_summary.json",
    "unit_assumption_receipt": "unit_assumption_receipt.json",
    "replay_contract": "replay_contract.json",
    "input_lineage": "input_lineage.json",
} | {
    f"{scenario}_{name}": f"{scenario}_{name}.jsonl"
    for scenario in _SCENARIOS
    for name in _TABLE_NAMES
}
_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "generation_id",
        "content_hash",
        "mode",
        "terminal_status",
        "input_bundle",
        "replay_contract",
        "replay_contract_hash",
        "builder_semantic_hash",
        "simulation_truth_hash",
        "artifact_root",
        "artifacts",
        "factor_diagnostics",
        "backtest_summary",
        "drawdown_summary",
        "evidence_flags",
        "blockers",
        "data_admission_eligible",
        "alpha_search_authorized",
        "validation_candidate_eligible",
        "lifecycle_publication_allowed",
        "holdout_accessed",
        "network_accessed",
        "deterministic_build",
    }
)


class FixedFactorReplayError(RuntimeError):
    """Raised when development replay inputs or evidence fail closed."""


@dataclass(frozen=True)
class FixedFactorReplayEvidence:
    """Validated immutable evidence identity."""

    payload: Mapping[str, Any]

    @classmethod
    def load(
        cls,
        manifest: str | Path,
        *,
        trusted_bundle_manifest: str | Path | None = None,
    ) -> "FixedFactorReplayEvidence":
        return cls(
            validate_fixed_factor_replay_evidence(
                manifest,
                trusted_bundle_manifest=trusted_bundle_manifest,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload)


def run_fixed_factor_replay(
    bundle_manifest: str | Path,
    output_root: str | Path,
    *,
    trusted_source_freeze_manifest: str | Path | None = None,
) -> dict[str, Any]:
    """Run the one locked factor through a development-only strict ledger."""

    try:
        loader = LocalDevelopmentBundleLoader(
            bundle_manifest,
            trusted_source_freeze_manifest=trusted_source_freeze_manifest,
        )
        lexical_output = Path(output_root)
        if _has_symlink_component(lexical_output):
            raise FixedFactorReplayError(
                "fixed factor replay output symlink forbidden"
            )
        output = lexical_output.resolve()
        _reject_output_overlap(output, loader.root)
        contract = _replay_contract(loader)
        contract_hash = canonical_hash(contract)
        builder_hash = _builder_semantic_hash()
        cached = _compatible_current(
            output,
            loader=loader,
            contract_hash=contract_hash,
            builder_hash=builder_hash,
        )
        if cached is not None:
            return cached | {"cache_hit": True}

        # Factor materialization is deliberately complete before target access.
        factor_values, factor_validity = _materialize_factor(loader, contract)
        target_values = loader.load_array("target_values", dtype=np.float32)
        target_available = loader.load_array(
            "target_availability", dtype=np.bool_
        )
        factor_diagnostics = _factor_diagnostics(
            factor_values,
            factor_validity,
            target_values,
            target_available,
        )
        prepared = _prepare_ledger_inputs(loader, factor_values, factor_validity, contract)
        baseline = _run_scenario(prepared, contract["scenario_policies"]["baseline"])
        baseline_repeat = _run_scenario(
            prepared, contract["scenario_policies"]["baseline"]
        )
        baseline_root = canonical_hash(baseline.to_dict())
        if baseline_root != canonical_hash(baseline_repeat.to_dict()):
            raise FixedFactorReplayError("fixed factor replay uncached AB drift")
        zero_cost = _run_scenario(
            prepared, contract["scenario_policies"]["zero_cost"]
        )
        scenario_results = {"baseline": baseline, "zero_cost": zero_cost}
        summaries = {
            name: _summarize_result(result, contract["scenario_policies"][name])
            for name, result in scenario_results.items()
        }
        backtest_summary = {
            "schema_version": "fixed_factor_replay_backtest_summary_v1",
            "interpretation": "strict_ledger_mechanics_non_admissible_development_proxy",
            "baseline": summaries["baseline"],
            "zero_cost": summaries["zero_cost"],
            "modeled_cost_scenario_return_difference": float(
                summaries["zero_cost"]["total_return"]
                - summaries["baseline"]["total_return"]
            ),
            "scenario_paths_may_differ": True,
            "uncached_ab_truth_equal": True,
            "uncached_ab_scenario": "baseline",
            "corporate_actions_applied": False,
            "benchmark_supported": False,
            "formal_research_backtest_eligible": False,
        }
        drawdown_summary, underwater = _drawdown_evidence(
            [row.to_dict() for row in baseline.nav],
            initial_aum=float(contract["scenario_policies"]["baseline"]["initial_aum"]),
        )
        input_lineage = _input_lineage(loader)
        unit_receipt = prepared["unit_receipt"]

        output.mkdir(parents=True, exist_ok=True)
        preparation_root = Path(
            tempfile.mkdtemp(prefix=".fixed_factor_replay.", dir=output)
        )
        staging = preparation_root / "working"
        staging.mkdir()
        try:
            _write_npy(staging / _ARTIFACT_PATHS["factor_values"], factor_values)
            _write_npy(
                staging / _ARTIFACT_PATHS["factor_validity"], factor_validity
            )
            _write_npy(
                staging / _ARTIFACT_PATHS["signal_eligibility"],
                prepared["selection"].T,
            )
            _write_npy(
                staging / _ARTIFACT_PATHS["buy_execution_proxy"],
                prepared["buy"].T,
            )
            _write_npy(
                staging / _ARTIFACT_PATHS["sell_execution_proxy"],
                prepared["sell"].T,
            )
            _write_json(staging / _ARTIFACT_PATHS["factor_diagnostics"], factor_diagnostics)
            _write_json(staging / _ARTIFACT_PATHS["backtest_summary"], backtest_summary)
            _write_json(staging / _ARTIFACT_PATHS["unit_assumption_receipt"], unit_receipt)
            _write_json(staging / _ARTIFACT_PATHS["replay_contract"], contract)
            _write_json(staging / _ARTIFACT_PATHS["input_lineage"], input_lineage)
            _write_jsonl(staging / _ARTIFACT_PATHS["underwater_series"], underwater)
            for scenario, result in scenario_results.items():
                payload = result.to_dict()
                for table in _TABLE_NAMES:
                    _write_jsonl(
                        staging / _ARTIFACT_PATHS[f"{scenario}_{table}"],
                        payload[table],
                    )

            artifacts = [
                _artifact_row(staging, role, path)
                for role, path in sorted(_ARTIFACT_PATHS.items())
            ]
            truth_hash = _simulation_truth_hash(
                artifacts=artifacts,
                factor_diagnostics=factor_diagnostics,
                backtest_summary=backtest_summary,
                drawdown_summary=drawdown_summary,
                replay_contract_hash=contract_hash,
            )
            semantic = {
                "schema_version": SCHEMA_VERSION,
                "mode": "development_replay",
                "terminal_status": TERMINAL_STATUS,
                "input_bundle": input_lineage,
                "replay_contract": contract,
                "replay_contract_hash": contract_hash,
                "builder_semantic_hash": builder_hash,
                "simulation_truth_hash": truth_hash,
                "artifact_root": canonical_hash(artifacts),
                "artifacts": artifacts,
                "factor_diagnostics": factor_diagnostics,
                "backtest_summary": backtest_summary,
                "drawdown_summary": drawdown_summary,
                "evidence_flags": dict(loader.manifest["evidence_flags"]),
                "blockers": list(loader.manifest["blockers"]),
                "data_admission_eligible": False,
                "alpha_search_authorized": False,
                "validation_candidate_eligible": False,
                "lifecycle_publication_allowed": False,
                "holdout_accessed": False,
                "network_accessed": False,
                "deterministic_build": True,
            }
            content_hash = canonical_hash(semantic)
            generation_id = f"{GENERATION_PREFIX}_{content_hash[:24]}"
            manifest = semantic | {
                "content_hash": content_hash,
                "generation_id": generation_id,
            }
            _write_json(staging / MANIFEST_NAME, manifest)
            prepared_generation = preparation_root / generation_id
            os.replace(staging, prepared_generation)
            return publish_prepared_generation(
                output,
                prepared_directory=prepared_generation,
                manifest_name=MANIFEST_NAME,
                validator=lambda path: validate_fixed_factor_replay_evidence(
                    path,
                    trusted_bundle_manifest=loader.manifest["manifest_path"],
                ),
                pointer_schema="fixed_factor_replay_pointer_v1",
                pointer_fields={
                    "mode": "development_replay",
                    "validation_candidate_eligible": False,
                },
            )
        finally:
            _remove_preparation_root(preparation_root)
    except FixedFactorReplayError:
        raise
    except (LocalDevelopmentBundleError, SimulationDataBlocker, OSError, ValueError) as exc:
        raise FixedFactorReplayError(str(exc)) from exc


def validate_fixed_factor_replay_evidence(
    manifest: str | Path,
    *,
    trusted_bundle_manifest: str | Path | None = None,
) -> dict[str, Any]:
    """Validate replay evidence and normalize malformed inputs to one blocker."""

    try:
        return _validate_fixed_factor_replay_evidence(
            manifest,
            trusted_bundle_manifest=trusted_bundle_manifest,
        )
    except FixedFactorReplayError:
        raise
    except (
        TypeError,
        KeyError,
        ValueError,
        OverflowError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        raise FixedFactorReplayError(
            "fixed factor replay evidence malformed"
        ) from exc


def _validate_fixed_factor_replay_evidence(
    manifest: str | Path,
    *,
    trusted_bundle_manifest: str | Path | None = None,
) -> dict[str, Any]:
    """Validate immutable replay evidence and optionally replay its input factor."""

    try:
        payload = validate_generation(
            manifest,
            schema=SCHEMA_VERSION,
            manifest_name=MANIFEST_NAME,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise FixedFactorReplayError("fixed factor replay identity invalid") from exc
    if set(payload) - {"manifest_path"} != _MANIFEST_KEYS:
        raise FixedFactorReplayError("fixed factor replay manifest boundary invalid")
    if not _valid_governance_boundary(payload):
        raise FixedFactorReplayError("fixed factor replay governance boundary invalid")
    contract = payload.get("replay_contract")
    if not isinstance(contract, Mapping) or canonical_hash(contract) != payload.get(
        "replay_contract_hash"
    ):
        raise FixedFactorReplayError("fixed factor replay contract identity invalid")
    _validate_contract(contract)

    path = Path(str(payload["manifest_path"])).resolve()
    root = path.parent
    if root.stat().st_mode & 0o222 or path.stat().st_mode & 0o222:
        raise FixedFactorReplayError("fixed factor replay generation mutable")
    raw_artifacts = payload.get("artifacts")
    if not isinstance(raw_artifacts, list):
        raise FixedFactorReplayError("fixed factor replay artifacts invalid")
    artifacts: dict[str, dict[str, Any]] = {}
    normalized: list[dict[str, Any]] = []
    observed_paths: set[str] = set()
    for value in raw_artifacts:
        if not isinstance(value, Mapping):
            raise FixedFactorReplayError("fixed factor replay artifact row invalid")
        row = dict(value)
        role = str(row.get("role") or "")
        relative = Path(str(row.get("relative_path") or ""))
        if (
            role in artifacts
            or role not in _ARTIFACT_PATHS
            or relative.as_posix() != _ARTIFACT_PATHS.get(role)
            or relative.is_absolute()
            or ".." in relative.parts
            or relative.as_posix() in observed_paths
        ):
            raise FixedFactorReplayError("fixed factor replay artifact identity invalid")
        artifact = (root / relative).resolve()
        if (
            not artifact.is_relative_to(root)
            or (root / relative).is_symlink()
            or not artifact.is_file()
            or bool(artifact.stat().st_mode & 0o222)
            or artifact.stat().st_size != row.get("size_bytes")
            or sha256_file(artifact) != row.get("sha256")
        ):
            raise FixedFactorReplayError(f"fixed factor replay artifact drift:{role}")
        if artifact.suffix == ".npy":
            array = np.load(artifact, mmap_mode="r", allow_pickle=False)
            if row.get("shape") != list(array.shape) or row.get("dtype") != str(
                array.dtype
            ):
                raise FixedFactorReplayError(
                    f"fixed factor replay array contract invalid:{role}"
                )
        artifacts[role] = row
        normalized.append(row)
        observed_paths.add(relative.as_posix())
    if (
        set(artifacts) != set(_ARTIFACT_PATHS)
        or normalized != sorted(normalized, key=lambda row: str(row["role"]))
        or payload.get("artifact_root") != canonical_hash(normalized)
    ):
        raise FixedFactorReplayError("fixed factor replay artifact root invalid")
    _validate_closure(root, path, observed_paths)

    input_bundle = payload.get("input_bundle")
    if not isinstance(input_bundle, Mapping):
        raise FixedFactorReplayError("fixed factor replay input lineage invalid")
    _validate_input_lineage_contract(input_bundle)
    if (
        input_bundle.get("feature_axis_hash") != contract.get("feature_axis_hash")
        or int(input_bundle.get("feature_count", -1)) != len(LOCAL_FEATURE_NAMES)
    ):
        raise FixedFactorReplayError(
            "fixed factor replay contract/lineage axis mismatch"
        )
    if not _sha256_hex(str(payload.get("builder_semantic_hash") or "")):
        raise FixedFactorReplayError("fixed factor replay builder identity invalid")
    lineage_artifact = _read_json(root / _ARTIFACT_PATHS["input_lineage"])
    stored_contract = _read_json(root / _ARTIFACT_PATHS["replay_contract"])
    diagnostics = _read_json(root / _ARTIFACT_PATHS["factor_diagnostics"])
    summary = _read_json(root / _ARTIFACT_PATHS["backtest_summary"])
    unit_receipt = _read_json(
        root / _ARTIFACT_PATHS["unit_assumption_receipt"]
    )
    if (
        lineage_artifact != dict(input_bundle)
        or stored_contract != dict(contract)
        or diagnostics != payload.get("factor_diagnostics")
        or summary != payload.get("backtest_summary")
    ):
        raise FixedFactorReplayError("fixed factor replay duplicated evidence drift")
    _validate_diagnostics_contract(diagnostics)
    _validate_backtest_summary_contract(summary)
    _validate_unit_receipt_contract(unit_receipt)

    stock_count = int(input_bundle.get("stock_count", -1))
    date_count = int(input_bundle.get("date_count", -1))
    factor_values = _load_output_array(
        root, artifacts["factor_values"], np.float32, (stock_count, date_count)
    )
    factor_validity = _load_output_array(
        root, artifacts["factor_validity"], np.bool_, (stock_count, date_count)
    )
    if np.any(factor_values[~factor_validity] != 0.0) or np.any(
        ~np.isfinite(factor_values[factor_validity])
    ):
        raise FixedFactorReplayError("fixed factor replay factor validity invalid")
    for role in (
        "signal_eligibility",
        "buy_execution_proxy",
        "sell_execution_proxy",
    ):
        _load_output_array(root, artifacts[role], np.bool_, (stock_count, date_count))

    tables = {
        f"{scenario}_{name}": _read_jsonl(
            root / _ARTIFACT_PATHS[f"{scenario}_{name}"]
        )
        for scenario in _SCENARIOS
        for name in _TABLE_NAMES
    }
    _validate_persisted_simulation(payload, tables)
    underwater = _read_jsonl(root / _ARTIFACT_PATHS["underwater_series"])
    recomputed_drawdown, recomputed_underwater = _drawdown_evidence(
        tables["baseline_nav"],
        initial_aum=float(contract["scenario_policies"]["baseline"]["initial_aum"]),
    )
    if (
        recomputed_drawdown != payload.get("drawdown_summary")
        or recomputed_underwater != underwater
    ):
        raise FixedFactorReplayError("fixed factor replay drawdown evidence invalid")
    expected_truth = _simulation_truth_hash(
        artifacts=normalized,
        factor_diagnostics=diagnostics,
        backtest_summary=summary,
        drawdown_summary=recomputed_drawdown,
        replay_contract_hash=str(payload["replay_contract_hash"]),
    )
    if expected_truth != payload.get("simulation_truth_hash"):
        raise FixedFactorReplayError("fixed factor replay truth hash invalid")

    if trusted_bundle_manifest is not None:
        loader = LocalDevelopmentBundleLoader(trusted_bundle_manifest)
        _validate_input_lineage(loader, input_bundle)
        if dict(contract) != _replay_contract(loader):
            raise FixedFactorReplayError(
                "fixed factor replay trusted contract mismatch"
            )
        expected_values, expected_validity = _materialize_factor(loader, contract)
        if not np.array_equal(expected_values, factor_values) or not np.array_equal(
            expected_validity, factor_validity
        ):
            raise FixedFactorReplayError("fixed factor replay trusted factor mismatch")
        target_values = loader.load_array("target_values", dtype=np.float32)
        target_available = loader.load_array(
            "target_availability", dtype=np.bool_
        )
        expected_diagnostics = _factor_diagnostics(
            expected_values,
            expected_validity,
            target_values,
            target_available,
        )
        if expected_diagnostics != diagnostics:
            raise FixedFactorReplayError(
                "fixed factor replay trusted diagnostics mismatch"
            )
        prepared = _prepare_ledger_inputs(
            loader, expected_values, expected_validity, contract
        )
        if prepared["unit_receipt"] != unit_receipt:
            raise FixedFactorReplayError("fixed factor replay unit evidence mismatch")
        for scenario in _SCENARIOS:
            rerun = _run_scenario(
                prepared, contract["scenario_policies"][scenario]
            ).to_dict()
            for table in _TABLE_NAMES:
                if rerun[table] != tables[f"{scenario}_{table}"]:
                    raise FixedFactorReplayError(
                        f"fixed factor replay trusted ledger mismatch:{scenario}:{table}"
                    )
    return payload


def _replay_contract(loader: LocalDevelopmentBundleLoader) -> dict[str, Any]:
    if loader.feature_names != LOCAL_FEATURE_NAMES:
        raise FixedFactorReplayError("fixed factor replay feature axis unsupported")
    vocab = make_formula_vocab(feature_names=list(loader.feature_names))
    tokens = [vocab.encode_name(name) for name in FORMULA_NAMES]
    vm = StackVM(vocab)
    valid, reason = vm.validate_with_reason(tokens)
    if not valid:
        raise FixedFactorReplayError(f"fixed formula invalid:{reason}")
    policies = _locked_scenario_policies()
    baseline = ScenarioPolicy(**policies["baseline"])
    if 1.0 / baseline.top_n > baseline.max_weight:
        raise FixedFactorReplayError("fixed factor replay policy weight invalid")
    return {
        "schema_version": "fixed_factor_replay_contract_v1",
        "factor_id": FACTOR_ID,
        "formula_names": list(FORMULA_NAMES),
        "formula_tokens": tokens,
        "formula_hash": canonical_hash(
            {"feature_names": list(loader.feature_names), "formula_names": list(FORMULA_NAMES)}
        ),
        "formula_lookback": vm.formula_lookback(tokens),
        "feature_axis_hash": canonical_hash(list(loader.feature_names)),
        "cross_section_domain": "pit_universe_membership_known_at_close_t",
        "factor_runtime": "StackVM.execute_with_validity",
        "factor_device": "cpu",
        "target_used_for_signal": False,
        "target_read_after_factor_materialization": True,
        "target_role": "diagnostic_only",
        "signal_timing": "close_t",
        "execution_timing": "next_open",
        "holding_semantics": "daily_target_refresh_not_independent_signal_cohorts",
        "portfolio": "long_only_top_n_equal_weight",
        "tie_break": "stable_ts_code_ascending",
        "rebalance_frequency": "each_open_trading_day",
        "adv_window_trading_days": ADV_WINDOW,
        "adv_min_periods": ADV_MIN_PERIODS,
        "volume_to_shares_multiplier": VOLUME_TO_SHARES,
        "amount_to_cny_multiplier": AMOUNT_TO_CNY,
        "valuation_rule": "causal_last_observed_price_development_proxy",
        "execution_evidence_grade": "retrospective_observed_price_band_proxy",
        "cost_evidence_grade": "versioned_modeled_development_assumption",
        "corporate_action_handling": "not_applied_lineage_unproven",
        "benchmark_handling": "unsupported_not_frozen_in_local_bundle",
        "formal_research_backtest_eligible": False,
        "scenario_policies": policies,
    }


def _locked_scenario_policies() -> dict[str, dict[str, Any]]:
    baseline = ScenarioPolicy(
        name="development_modeled_cost_v1",
        initial_aum=1_000_000.0,
        top_n=20,
        max_weight=0.10,
        lot_size=100,
        adv_participation=0.10,
        commission_rate=0.0003,
        minimum_commission=5.0,
        stamp_duty_rate=0.0005,
        transfer_fee_rate=0.00001,
        slippage_bps=5.0,
        impact_bps=5.0,
        modeled_cost_multiplier=1.0,
        zero_all_costs=False,
        fee_schedule_id="cn_ashare_historical_fees_modeled_execution_v1",
        sell_cash_lag=1,
        buy_share_lag=1,
    )
    if 1.0 / baseline.top_n > baseline.max_weight:
        raise FixedFactorReplayError("fixed factor replay policy weight invalid")
    zero_cost = replace(
        baseline,
        name="development_zero_cost_accounting_v1",
        zero_all_costs=True,
    )
    return {"baseline": baseline.to_dict(), "zero_cost": zero_cost.to_dict()}


def _validate_contract(value: Mapping[str, Any]) -> None:
    expected_names = list(FORMULA_NAMES)
    vocab = make_formula_vocab(feature_names=list(LOCAL_FEATURE_NAMES))
    expected_tokens = [vocab.encode_name(name) for name in FORMULA_NAMES]
    expected_feature_hash = canonical_hash(list(LOCAL_FEATURE_NAMES))
    expected_formula_hash = canonical_hash(
        {
            "feature_names": list(LOCAL_FEATURE_NAMES),
            "formula_names": expected_names,
        }
    )
    expected_keys = {
        "schema_version",
        "factor_id",
        "formula_names",
        "formula_tokens",
        "formula_hash",
        "formula_lookback",
        "feature_axis_hash",
        "cross_section_domain",
        "factor_runtime",
        "factor_device",
        "target_used_for_signal",
        "target_read_after_factor_materialization",
        "target_role",
        "signal_timing",
        "execution_timing",
        "holding_semantics",
        "portfolio",
        "tie_break",
        "rebalance_frequency",
        "adv_window_trading_days",
        "adv_min_periods",
        "volume_to_shares_multiplier",
        "amount_to_cny_multiplier",
        "valuation_rule",
        "execution_evidence_grade",
        "cost_evidence_grade",
        "corporate_action_handling",
        "benchmark_handling",
        "formal_research_backtest_eligible",
        "scenario_policies",
    }
    if (
        set(value) != expected_keys
        or value.get("schema_version") != "fixed_factor_replay_contract_v1"
        or value.get("factor_id") != FACTOR_ID
        or value.get("formula_names") != expected_names
        or value.get("formula_tokens") != expected_tokens
        or value.get("formula_hash") != expected_formula_hash
        or value.get("feature_axis_hash") != expected_feature_hash
        or value.get("target_used_for_signal") is not False
        or value.get("target_read_after_factor_materialization") is not True
        or value.get("target_role") != "diagnostic_only"
        or value.get("signal_timing") != "close_t"
        or value.get("execution_timing") != "next_open"
        or value.get("holding_semantics")
        != "daily_target_refresh_not_independent_signal_cohorts"
        or value.get("portfolio") != "long_only_top_n_equal_weight"
        or value.get("tie_break") != "stable_ts_code_ascending"
        or value.get("rebalance_frequency") != "each_open_trading_day"
        or value.get("formal_research_backtest_eligible") is not False
        or value.get("factor_runtime") != "StackVM.execute_with_validity"
        or value.get("cross_section_domain")
        != "pit_universe_membership_known_at_close_t"
        or value.get("factor_device") != "cpu"
        or value.get("formula_lookback") != 0
        or value.get("volume_to_shares_multiplier") != VOLUME_TO_SHARES
        or value.get("amount_to_cny_multiplier") != AMOUNT_TO_CNY
        or value.get("adv_window_trading_days") != ADV_WINDOW
        or value.get("adv_min_periods") != ADV_MIN_PERIODS
        or value.get("valuation_rule")
        != "causal_last_observed_price_development_proxy"
        or value.get("execution_evidence_grade")
        != "retrospective_observed_price_band_proxy"
        or value.get("cost_evidence_grade")
        != "versioned_modeled_development_assumption"
        or value.get("corporate_action_handling")
        != "not_applied_lineage_unproven"
        or value.get("benchmark_handling")
        != "unsupported_not_frozen_in_local_bundle"
    ):
        raise FixedFactorReplayError("fixed factor replay contract semantics invalid")
    policies = value.get("scenario_policies")
    if (
        not isinstance(policies, Mapping)
        or dict(policies) != _locked_scenario_policies()
    ):
        raise FixedFactorReplayError("fixed factor replay scenario policies invalid")
    try:
        baseline = ScenarioPolicy(**dict(policies["baseline"]))
        zero_cost = ScenarioPolicy(**dict(policies["zero_cost"]))
    except (TypeError, ValueError) as exc:
        raise FixedFactorReplayError("fixed factor replay policy invalid") from exc
    if (
        baseline.zero_all_costs
        or not zero_cost.zero_all_costs
        or baseline.top_n != 20
        or baseline.lot_size != 100
        or baseline.buy_share_lag != 1
        or baseline.sell_cash_lag != 1
        or 1.0 / baseline.top_n > baseline.max_weight
    ):
        raise FixedFactorReplayError("fixed factor replay locked policy invalid")


def _materialize_factor(
    loader: LocalDevelopmentBundleLoader,
    contract: Mapping[str, Any],
) -> tuple[np.ndarray, np.ndarray]:
    vocab = make_formula_vocab(feature_names=list(loader.feature_names))
    formula_names = [str(item) for item in contract["formula_names"]]
    tokens = [vocab.encode_name(name) for name in formula_names]
    if tokens != list(contract["formula_tokens"]):
        raise FixedFactorReplayError("fixed formula token drift")
    values = np.array(
        loader.load_array("feature_values", dtype=np.float32),
        dtype=np.float32,
        copy=True,
    )
    validity = np.array(
        loader.load_array("feature_validity", dtype=np.bool_),
        dtype=np.bool_,
        copy=True,
    )
    membership = np.asarray(
        loader.load_array("pit_universe_membership", dtype=np.bool_), dtype=bool
    )
    membership_known = np.asarray(
        loader.load_array("membership_known", dtype=np.bool_), dtype=bool
    )
    validity &= (membership & membership_known)[:, np.newaxis, :]
    vm = StackVM(vocab)
    result = vm.execute_with_validity(
        tokens,
        torch.from_numpy(values),
        torch.from_numpy(validity),
    )
    if result is None:
        raise FixedFactorReplayError("fixed formula execution failed")
    factor, factor_validity = result
    factor_array = factor.detach().cpu().numpy().astype(np.float32, copy=False)
    validity_array = (
        factor_validity.detach().cpu().numpy().astype(np.bool_, copy=False)
    )
    factor_array = np.where(validity_array, factor_array, 0.0).astype(
        np.float32, copy=False
    )
    expected_shape = (len(loader.stock_ids), len(loader.trade_dates))
    if factor_array.shape != expected_shape or validity_array.shape != expected_shape:
        raise FixedFactorReplayError("fixed formula output shape invalid")
    return factor_array, validity_array


def _factor_diagnostics(
    factor_values: np.ndarray,
    factor_validity: np.ndarray,
    target_values: np.ndarray,
    target_available: np.ndarray,
) -> dict[str, Any]:
    eligibility = (
        factor_validity
        & target_available
        & np.isfinite(target_values)
        & np.isfinite(factor_values)
    )
    rank_ics: list[float] = []
    spreads: list[float] = []
    top_sets: list[set[int]] = []
    for date_index in range(factor_values.shape[1]):
        mask = eligibility[:, date_index]
        if int(mask.sum()) < 2:
            continue
        asset_indices = np.flatnonzero(mask)
        factors = np.asarray(factor_values[mask, date_index], dtype=np.float64)
        targets = np.asarray(target_values[mask, date_index], dtype=np.float64)
        rank_ics.append(_correlation(_average_rank(factors), _average_rank(targets)))
        group_size = max(1, int(len(factors) * 0.33))
        order = np.lexsort((asset_indices, factors))
        bottom = order[:group_size]
        top = order[-group_size:]
        spreads.append(float(targets[top].mean() - targets[bottom].mean()))
        top_sets.append({int(asset_indices[index]) for index in top})
    rank_array = np.asarray(rank_ics, dtype=np.float64)
    rank_mean = float(rank_array.mean()) if rank_ics else 0.0
    rank_std = float(rank_array.std()) if rank_ics else 0.0
    target_eligible = target_available & np.isfinite(target_values)
    turnover_values = [
        1.0 - len(left & right) / max(len(left | right), 1)
        for left, right in zip(top_sets[:-1], top_sets[1:])
    ]
    return {
        "schema_version": "fixed_factor_replay_diagnostics_v1",
        "factor_id": FACTOR_ID,
        "rank_ic_mean": rank_mean,
        "rank_ic_std": rank_std,
        "rank_ic_ir": float(rank_mean / rank_std) if rank_std > 0.0 else rank_mean,
        "rank_ic_t_stat": float(
            rank_mean / (rank_std / math.sqrt(len(rank_ics)) + 1e-12)
        )
        if rank_ics
        else 0.0,
        "rank_ic_positive_ratio": float(np.mean(rank_array > 0.0))
        if rank_ics
        else 0.0,
        "top_bottom_spread": float(np.mean(spreads)) if spreads else 0.0,
        "top_bottom_win_rate": float(np.mean(np.asarray(spreads) > 0.0))
        if spreads
        else 0.0,
        "turnover": float(np.mean(turnover_values)) if turnover_values else 0.0,
        "coverage": float(eligibility.sum() / target_eligible.sum())
        if int(target_eligible.sum())
        else 0.0,
        "valid_observation_count": int(eligibility.sum()),
        "metric_eligibility_count": int(eligibility.sum()),
        "evaluable_date_count": len(rank_ics),
        "target_used_for_signal": False,
        "interpretation": "in_sample_development_diagnostic_not_OOS",
        "promotion_decision": "not_applicable",
    }


def _average_rank(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="stable")
    sorted_values = values[order]
    sorted_ranks = np.empty(len(values), dtype=np.float64)
    start = 0
    while start < len(values):
        end = start + 1
        while end < len(values) and sorted_values[end] == sorted_values[start]:
            end += 1
        sorted_ranks[start:end] = (start + end - 1) / 2.0
        start = end
    ranks = np.empty(len(values), dtype=np.float64)
    ranks[order] = sorted_ranks
    return ranks


def _correlation(left: np.ndarray, right: np.ndarray) -> float:
    centered_left = left - left.mean()
    centered_right = right - right.mean()
    denominator = float(
        np.linalg.norm(centered_left) * np.linalg.norm(centered_right)
    )
    if denominator <= 1e-12:
        return 0.0
    return float(np.dot(centered_left, centered_right) / denominator)


def _validate_diagnostics_contract(value: Mapping[str, Any]) -> None:
    if (
        value.get("schema_version") != "fixed_factor_replay_diagnostics_v1"
        or value.get("factor_id") != FACTOR_ID
        or value.get("target_used_for_signal") is not False
        or value.get("interpretation")
        != "in_sample_development_diagnostic_not_OOS"
        or value.get("promotion_decision") != "not_applicable"
        or int(value.get("valid_observation_count", -1))
        != int(value.get("metric_eligibility_count", -2))
        or int(value.get("valid_observation_count", -1)) < 0
        or int(value.get("evaluable_date_count", -1)) < 0
    ):
        raise FixedFactorReplayError("fixed factor replay diagnostics contract invalid")


def _validate_backtest_summary_contract(value: Mapping[str, Any]) -> None:
    if (
        value.get("schema_version")
        != "fixed_factor_replay_backtest_summary_v1"
        or value.get("interpretation")
        != "strict_ledger_mechanics_non_admissible_development_proxy"
        or value.get("uncached_ab_truth_equal") is not True
        or value.get("uncached_ab_scenario") != "baseline"
        or value.get("scenario_paths_may_differ") is not True
        or value.get("corporate_actions_applied") is not False
        or value.get("benchmark_supported") is not False
        or value.get("formal_research_backtest_eligible") is not False
        or not isinstance(value.get("baseline"), Mapping)
        or not isinstance(value.get("zero_cost"), Mapping)
    ):
        raise FixedFactorReplayError("fixed factor replay backtest contract invalid")


def _validate_unit_receipt_contract(value: Mapping[str, Any]) -> None:
    try:
        numeric = tuple(
            float(value.get(name))
            for name in (
                "crosscheck_ratio_p01",
                "crosscheck_ratio_median",
                "crosscheck_ratio_p99",
            )
        )
    except (TypeError, ValueError) as exc:
        raise FixedFactorReplayError("fixed factor replay unit contract invalid") from exc
    if (
        value.get("schema_version")
        != "fixed_factor_replay_unit_assumption_v1"
        or value.get("source_volume_unit_assumption") != "lots_100_shares"
        or value.get("source_amount_unit_assumption") != "thousand_CNY"
        or value.get("volume_to_shares_multiplier") != VOLUME_TO_SHARES
        or value.get("amount_to_cny_multiplier") != AMOUNT_TO_CNY
        or value.get("crosscheck_status")
        != "consistent_with_legacy_tushare_units"
        or value.get("unit_evidence_grade")
        != "development_assumption_crosschecked_not_governed"
        or value.get("formal_capacity_evidence_eligible") is not False
        or value.get("corporate_action_lineage_proven") is not False
        or int(value.get("crosscheck_observation_count", 0)) < 20
        or int(value.get("adjustment_factor_transition_count", -1)) < 0
        or any(not math.isfinite(item) for item in numeric)
        or not 0.80 <= numeric[1] <= 1.20
    ):
        raise FixedFactorReplayError("fixed factor replay unit contract invalid")


def _prepare_ledger_inputs(
    loader: LocalDevelopmentBundleLoader,
    factor_values: np.ndarray,
    factor_validity: np.ndarray,
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    raw: dict[str, np.ndarray] = {}
    valid: dict[str, np.ndarray] = {}
    for name in (
        "open",
        "close",
        "volume",
        "amount",
        "up_limit",
        "down_limit",
        "adj_factor",
    ):
        raw[name] = np.asarray(
            loader.load_array(f"raw_{name}", dtype=np.float32), dtype=np.float64
        )
        valid[name] = np.asarray(
            loader.load_array(f"raw_{name}_validity", dtype=np.bool_), dtype=bool
        )
    membership = np.asarray(
        loader.load_array("pit_universe_membership", dtype=np.bool_), dtype=bool
    )
    membership_known = np.asarray(
        loader.load_array("membership_known", dtype=np.bool_), dtype=bool
    )
    bar_observed = np.asarray(
        loader.load_array("daily_bars_observed_positions", dtype=np.bool_),
        dtype=bool,
    )
    bar_duplicate = np.asarray(
        loader.load_array("daily_bars_duplicate_positions", dtype=np.bool_),
        dtype=bool,
    )
    limit_observed = np.asarray(
        loader.load_array("daily_limits_observed_positions", dtype=np.bool_),
        dtype=bool,
    )
    limit_duplicate = np.asarray(
        loader.load_array("daily_limits_duplicate_positions", dtype=np.bool_),
        dtype=bool,
    )
    limit_unusable = np.asarray(
        loader.load_array(
            "limit_required_field_unusable_positions", dtype=np.bool_
        ),
        dtype=bool,
    )
    limit_order_violation = np.asarray(
        loader.load_array(
            "positive_limit_order_violation_positions", dtype=np.bool_
        ),
        dtype=bool,
    )
    limit_mismatch = np.asarray(
        loader.load_array(
            "cross_source_pre_close_mismatch_positions", dtype=np.bool_
        ),
        dtype=bool,
    )
    unit_receipt = _unit_assumption_receipt(raw, valid)
    volume_shares = raw["volume"] * VOLUME_TO_SHARES
    adv = _trailing_mean(
        volume_shares.T,
        valid["volume"].T,
        ADV_WINDOW,
        min_periods=ADV_MIN_PERIODS,
    )

    common_open = (
        bar_observed
        & ~bar_duplicate
        & valid["open"]
    )
    common_limit = (
        limit_observed
        & ~limit_duplicate
        & ~limit_unusable
        & ~limit_order_violation
        & ~limit_mismatch
        & valid["up_limit"]
        & valid["down_limit"]
    )
    buy = (
        common_open
        & common_limit
        & membership
        & membership_known
        & (raw["open"] < raw["up_limit"] - 1e-6)
    ).T
    sell = (
        common_open
        & common_limit
        & (raw["open"] > raw["down_limit"] + 1e-6)
    ).T
    selection_source = (
        factor_validity
        & membership
        & membership_known
        & valid["close"]
        & np.isfinite(factor_values)
    )
    top_n = int(contract["scenario_policies"]["baseline"]["top_n"])
    breadth = selection_source.sum(axis=0)
    selection_source[:, breadth < top_n] = False
    selection = selection_source.T
    scores = np.where(factor_validity, factor_values, np.nan).T
    valuation = _causal_valuation_marks(raw, valid, loader.trade_dates)
    adjustment_transitions = (
        valid["adj_factor"][:, 1:]
        & valid["adj_factor"][:, :-1]
        & ~np.isclose(
            raw["adj_factor"][:, 1:],
            raw["adj_factor"][:, :-1],
            rtol=0.0,
            atol=1e-7,
        )
    )
    unit_receipt["adjustment_factor_transition_count"] = int(
        adjustment_transitions.sum()
    )
    unit_receipt["corporate_action_lineage_proven"] = False
    market = {
        "dates": list(loader.trade_dates),
        "assets": list(loader.stock_ids),
        "open": raw["open"].T,
        "close": raw["close"].T,
        "valuation_open": valuation["open"],
        "valuation_close": valuation["close"],
        "adv": adv,
        **valuation["metadata"],
    }
    return {
        "market": market,
        "scores": scores,
        "selection": selection,
        "buy": buy,
        "sell": sell,
        "unit_receipt": unit_receipt,
    }


def _unit_assumption_receipt(
    raw: Mapping[str, np.ndarray],
    validity: Mapping[str, np.ndarray],
) -> dict[str, Any]:
    mask = (
        validity["close"]
        & validity["volume"]
        & validity["amount"]
        & (raw["close"] > 0.0)
        & (raw["volume"] > 0.0)
        & (raw["amount"] > 0.0)
    )
    if int(mask.sum()) < 20:
        raise FixedFactorReplayError("volume/amount unit cross-check evidence insufficient")
    ratio = (
        raw["amount"][mask]
        * AMOUNT_TO_CNY
        / (raw["close"][mask] * raw["volume"][mask] * VOLUME_TO_SHARES)
    )
    quantiles = np.quantile(ratio, [0.01, 0.50, 0.99])
    median = float(quantiles[1])
    if not 0.80 <= median <= 1.20:
        raise FixedFactorReplayError("volume/amount unit assumption inconsistent")
    return {
        "schema_version": "fixed_factor_replay_unit_assumption_v1",
        "source_volume_unit_assumption": "lots_100_shares",
        "source_amount_unit_assumption": "thousand_CNY",
        "volume_to_shares_multiplier": VOLUME_TO_SHARES,
        "amount_to_cny_multiplier": AMOUNT_TO_CNY,
        "crosscheck_formula": "amount_CNY/(close_CNY_per_share*volume_shares)",
        "crosscheck_observation_count": int(mask.sum()),
        "crosscheck_ratio_p01": float(quantiles[0]),
        "crosscheck_ratio_median": median,
        "crosscheck_ratio_p99": float(quantiles[2]),
        "crosscheck_status": "consistent_with_legacy_tushare_units",
        "unit_evidence_grade": "development_assumption_crosschecked_not_governed",
        "formal_capacity_evidence_eligible": False,
    }


def _trailing_mean(
    values: np.ndarray,
    validity: np.ndarray,
    window: int,
    *,
    min_periods: int,
) -> np.ndarray:
    if not 1 <= min_periods <= window:
        raise FixedFactorReplayError("fixed factor replay ADV periods invalid")
    result = np.full(values.shape, np.nan, dtype=np.float64)
    for index in range(values.shape[0]):
        start = max(0, index - window + 1)
        window_values = values[start : index + 1]
        window_valid = (
            validity[start : index + 1]
            & np.isfinite(window_values)
            & (window_values >= 0.0)
        )
        counts = window_valid.sum(axis=0)
        totals = np.where(window_valid, window_values, 0.0).sum(axis=0)
        result[index] = np.divide(
            totals,
            counts,
            out=np.full(values.shape[1], np.nan, dtype=np.float64),
            where=counts >= min_periods,
        )
    return result


def _causal_valuation_marks(
    raw: Mapping[str, np.ndarray],
    validity: Mapping[str, np.ndarray],
    dates: Sequence[str],
) -> dict[str, Any]:
    stock_count, date_count = raw["open"].shape
    open_marks = np.full((date_count, stock_count), np.nan, dtype=np.float64)
    close_marks = np.full((date_count, stock_count), np.nan, dtype=np.float64)
    metadata: dict[str, np.ndarray] = {}
    for point in ("open", "close"):
        metadata[f"valuation_{point}_method"] = np.full(
            (date_count, stock_count), "", dtype=object
        )
        metadata[f"valuation_{point}_source_date"] = np.full(
            (date_count, stock_count), "", dtype=object
        )
        metadata[f"valuation_{point}_stale_age"] = np.full(
            (date_count, stock_count), -1, dtype=np.int32
        )
        metadata[f"valuation_{point}_evidence_id"] = np.full(
            (date_count, stock_count), "", dtype=object
        )
    for asset in range(stock_count):
        last_value = math.nan
        last_date_index = -1
        for date_index, date in enumerate(dates):
            if validity["open"][asset, date_index]:
                open_value = float(raw["open"][asset, date_index])
                open_method = "RAW_OPEN_OBSERVED"
                open_source_index = date_index
            else:
                open_value = last_value
                open_method = "LAST_OBSERVED_CAUSAL_PROXY" if math.isfinite(last_value) else ""
                open_source_index = last_date_index
            if math.isfinite(open_value):
                open_marks[date_index, asset] = open_value
                _set_mark_metadata(
                    metadata,
                    point="open",
                    date_index=date_index,
                    asset_index=asset,
                    method=open_method,
                    source_date=dates[open_source_index],
                    stale_age=date_index - open_source_index,
                )
            if validity["close"][asset, date_index]:
                close_value = float(raw["close"][asset, date_index])
                close_method = "RAW_CLOSE_OBSERVED"
                close_source_index = date_index
                last_value = close_value
                last_date_index = date_index
            else:
                close_value = open_value
                close_method = open_method
                close_source_index = open_source_index
            if math.isfinite(close_value):
                close_marks[date_index, asset] = close_value
                _set_mark_metadata(
                    metadata,
                    point="close",
                    date_index=date_index,
                    asset_index=asset,
                    method=close_method,
                    source_date=dates[close_source_index],
                    stale_age=date_index - close_source_index,
                )
                last_value = close_value
                last_date_index = close_source_index
    return {"open": open_marks, "close": close_marks, "metadata": metadata}


def _set_mark_metadata(
    metadata: Mapping[str, np.ndarray],
    *,
    point: str,
    date_index: int,
    asset_index: int,
    method: str,
    source_date: str,
    stale_age: int,
) -> None:
    metadata[f"valuation_{point}_method"][date_index, asset_index] = method
    metadata[f"valuation_{point}_source_date"][date_index, asset_index] = source_date
    metadata[f"valuation_{point}_stale_age"][date_index, asset_index] = stale_age
    metadata[f"valuation_{point}_evidence_id"][date_index, asset_index] = canonical_hash(
        {
            "point": point,
            "date_index": date_index,
            "asset_index": asset_index,
            "method": method,
            "source_date": source_date,
        }
    )


def _run_scenario(
    prepared: Mapping[str, Any],
    policy_payload: Mapping[str, Any],
) -> SimulationResult:
    try:
        return simulate_event_ledger(
            prepared["market"],
            prepared["scores"],
            masks={
                "buy": prepared["buy"],
                "sell": prepared["sell"],
                "select": prepared["selection"],
            },
            corporate_actions=(),
            policy=ScenarioPolicy(**dict(policy_payload)),
            require_explicit_valuation_marks=True,
        )
    except (SimulationDataBlocker, ValueError) as exc:
        raise FixedFactorReplayError(f"fixed factor strict ledger blocked:{exc}") from exc


def _summarize_result(
    result: SimulationResult,
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    return _summarize_rows(
        nav=[row.to_dict() for row in result.nav],
        fills=[row.to_dict() for row in result.fills],
        orders=[row.to_dict() for row in result.orders],
        rejections=[row.to_dict() for row in result.rejections],
        policy=policy,
    )


def _summarize_rows(
    *,
    nav: Sequence[Mapping[str, Any]],
    fills: Sequence[Mapping[str, Any]],
    orders: Sequence[Mapping[str, Any]],
    rejections: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    if not nav:
        raise FixedFactorReplayError("fixed factor replay NAV missing")
    initial_aum = float(policy["initial_aum"])
    nav_values = [float(row["open_post"]) for row in nav]
    if any(not math.isfinite(value) or value <= 0.0 for value in nav_values):
        raise FixedFactorReplayError("fixed factor replay NAV invalid")
    notionals = [float(row["notional"]) for row in fills]
    costs = {
        name: float(sum(float(row.get(name, 0.0)) for row in fills))
        for name in (
            "commission",
            "stamp_duty",
            "transfer_fee",
            "handling_fee",
            "securities_management_fee",
            "slippage",
            "impact",
            "total_cost",
        )
    }
    average_nav = float(np.mean(nav_values))
    requested_shares = int(sum(int(row["requested_shares"]) for row in orders))
    filled_shares = int(sum(int(row["filled_shares"]) for row in fills))
    partial_count = int(sum(str(row.get("status")) == "PARTIAL" for row in fills))
    capacity_rejections = int(
        sum(str(row.get("reason")) == "lagged_adv_capacity" for row in rejections)
    )
    return {
        "scenario_name": str(policy["name"]),
        "initial_aum": initial_aum,
        "terminal_open_post_nav": nav_values[-1],
        "total_return": float(nav_values[-1] / initial_aum - 1.0),
        "order_count": len(orders),
        "fill_count": len(fills),
        "rejection_count": len(rejections),
        "partial_fill_count": partial_count,
        "capacity_rejection_count": capacity_rejections,
        "requested_shares": requested_shares,
        "filled_shares": filled_shares,
        "share_fill_rate": float(filled_shares / requested_shares)
        if requested_shares
        else 0.0,
        "turnover": float(sum(notionals) / average_nav) if average_nav > 0 else 0.0,
        "total_cost": costs["total_cost"],
        "cost_components": costs,
        "nav_observation_count": len(nav),
        "capacity_evidence_grade": "development_unit_assumption_not_governed",
    }


def _drawdown_evidence(
    nav: Sequence[Mapping[str, Any]],
    *,
    initial_aum: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not nav:
        raise FixedFactorReplayError("fixed factor replay drawdown NAV missing")
    rolling_peak = float(initial_aum)
    peak_index = 0
    active_peak_index = 0
    max_drawdown = -1.0
    trough_index = 0
    underwater: list[dict[str, Any]] = []
    for index, row in enumerate(nav):
        value = float(row["open_post"])
        if not math.isfinite(value) or value <= 0.0:
            raise FixedFactorReplayError("fixed factor replay drawdown NAV invalid")
        if value > rolling_peak:
            rolling_peak = value
            active_peak_index = index
        drawdown = max(0.0, 1.0 - value / rolling_peak)
        if drawdown > max_drawdown:
            max_drawdown = drawdown
            peak_index = active_peak_index
            trough_index = index
        underwater.append(
            {
                "index": index,
                "date": str(row["date"]),
                "open_post_nav": value,
                "rolling_peak_nav": rolling_peak,
                "drawdown": drawdown,
            }
        )
    no_drawdown = max_drawdown <= 1e-15
    if no_drawdown:
        peak_index = 0
        trough_index = 0
        recovery_index: int | None = 0
    else:
        peak_value = float(underwater[trough_index]["rolling_peak_nav"])
        recovery_index = None
        for index in range(trough_index + 1, len(nav)):
            if float(nav[index]["open_post"]) >= peak_value - 1e-9:
                recovery_index = index
                break
    peak_date = str(nav[peak_index]["date"])
    trough_date = str(nav[trough_index]["date"])
    recovery_date = (
        str(nav[recovery_index]["date"]) if recovery_index is not None else None
    )
    end_index = recovery_index if recovery_index is not None else len(nav) - 1
    summary = {
        "schema_version": "fixed_factor_replay_drawdown_v1",
        "max_drawdown": float(max(max_drawdown, 0.0)),
        "peak_date": peak_date,
        "trough_date": trough_date,
        "recovery_date": recovery_date,
        "peak_to_trough_trading_days": int(trough_index - peak_index),
        "underwater_duration_trading_days": int(end_index - peak_index),
        "recovered": recovery_index is not None,
        "underwater_observation_count": len(underwater),
        "underwater_series_root": canonical_hash(underwater),
    }
    return summary, underwater


def _input_lineage(loader: LocalDevelopmentBundleLoader) -> dict[str, Any]:
    manifest = loader.manifest
    return {
        "schema_version": "fixed_factor_replay_input_lineage_v1",
        "generation_id": manifest["generation_id"],
        "content_hash": manifest["content_hash"],
        "artifact_root": manifest["artifact_root"],
        "manifest_sha256": sha256_file(manifest["manifest_path"]),
        "source_evidence_grade": manifest["source_evidence_grade"],
        "source_generation_id": manifest["source_generation_id"],
        "source_content_hash": manifest["source_content_hash"],
        "source_partition_selection_root": manifest[
            "source_partition_selection_root"
        ],
        "stock_axis_hash": canonical_hash(list(loader.stock_ids)),
        "date_axis_hash": canonical_hash(list(loader.trade_dates)),
        "feature_axis_hash": canonical_hash(list(loader.feature_names)),
        "stock_count": len(loader.stock_ids),
        "date_count": len(loader.trade_dates),
        "feature_count": len(loader.feature_names),
        "blockers": list(manifest["blockers"]),
        "data_admission_eligible": False,
        "alpha_search_authorized": False,
        "lifecycle_publication_allowed": False,
    }


def _validate_input_lineage(
    loader: LocalDevelopmentBundleLoader,
    lineage: Mapping[str, Any],
) -> None:
    if _input_lineage(loader) != dict(lineage):
        raise FixedFactorReplayError("fixed factor replay trusted input mismatch")


def _validate_input_lineage_contract(lineage: Mapping[str, Any]) -> None:
    required = {
        "schema_version",
        "generation_id",
        "content_hash",
        "artifact_root",
        "manifest_sha256",
        "source_evidence_grade",
        "source_generation_id",
        "source_content_hash",
        "source_partition_selection_root",
        "stock_axis_hash",
        "date_axis_hash",
        "feature_axis_hash",
        "stock_count",
        "date_count",
        "feature_count",
        "blockers",
        "data_admission_eligible",
        "alpha_search_authorized",
        "lifecycle_publication_allowed",
    }
    grade = str(lineage.get("source_evidence_grade") or "")
    expected_blockers = _BLOCKERS_BY_SOURCE_GRADE.get(grade)
    content_hash = str(lineage.get("content_hash") or "")
    source_content_hash = str(lineage.get("source_content_hash") or "")
    expected_source_generation = (
        f"ashare_source_freeze_{source_content_hash[:24]}"
        if grade == "source_freeze_bound"
        else f"ashare_freeze_{source_content_hash[:24]}"
        if grade == "legacy_unproven"
        else ""
    )
    if (
        set(lineage) != required
        or lineage.get("schema_version")
        != "fixed_factor_replay_input_lineage_v1"
        or any(
            not _sha256_hex(str(lineage.get(name) or ""))
            for name in (
                "content_hash",
                "artifact_root",
                "manifest_sha256",
                "source_content_hash",
                "source_partition_selection_root",
                "stock_axis_hash",
                "date_axis_hash",
                "feature_axis_hash",
            )
        )
        or any(
            isinstance(lineage.get(name), bool) or int(lineage.get(name, 0)) <= 0
            for name in ("stock_count", "date_count", "feature_count")
        )
        or expected_blockers is None
        or lineage.get("blockers") != list(expected_blockers)
        or lineage.get("generation_id")
        != f"local_development_bundle_{content_hash[:24]}"
        or lineage.get("source_generation_id") != expected_source_generation
        or lineage.get("data_admission_eligible") is not False
        or lineage.get("alpha_search_authorized") is not False
        or lineage.get("lifecycle_publication_allowed") is not False
    ):
        raise FixedFactorReplayError("fixed factor replay input lineage contract invalid")


def _simulation_truth_hash(
    *,
    artifacts: Sequence[Mapping[str, Any]],
    factor_diagnostics: Mapping[str, Any],
    backtest_summary: Mapping[str, Any],
    drawdown_summary: Mapping[str, Any],
    replay_contract_hash: str,
) -> str:
    selected = {
        str(row["role"]): str(row["sha256"])
        for row in artifacts
        if str(row["role"])
        in {
            "factor_values",
            "factor_validity",
            "signal_eligibility",
            "buy_execution_proxy",
            "sell_execution_proxy",
            "underwater_series",
            *{
                f"{scenario}_{table}"
                for scenario in _SCENARIOS
                for table in _TABLE_NAMES
            },
        }
    }
    return canonical_hash(
        {
            "replay_contract_hash": replay_contract_hash,
            "truth_artifacts": dict(sorted(selected.items())),
            "factor_diagnostics": dict(factor_diagnostics),
            "backtest_summary": dict(backtest_summary),
            "drawdown_summary": dict(drawdown_summary),
        }
    )


def _validate_persisted_simulation(
    payload: Mapping[str, Any],
    tables: Mapping[str, list[dict[str, Any]]],
) -> None:
    contract = payload["replay_contract"]
    expected_date_count = int(payload["input_bundle"]["date_count"])
    scenario_dates: dict[str, list[str]] = {}
    for scenario in _SCENARIOS:
        nav = tables[f"{scenario}_nav"]
        orders = tables[f"{scenario}_orders"]
        fills = tables[f"{scenario}_fills"]
        rejections = tables[f"{scenario}_rejections"]
        events = tables[f"{scenario}_event_ledger"]
        if tables[f"{scenario}_corporate_actions"]:
            raise FixedFactorReplayError(
                f"fixed factor replay corporate action contract drift:{scenario}"
            )
        if [int(row.get("sequence", 0)) for row in events] != list(
            range(1, len(events) + 1)
        ):
            raise FixedFactorReplayError(
                f"fixed factor replay event sequence invalid:{scenario}"
            )
        nav_dates = [str(row.get("date") or "") for row in nav]
        if (
            len(nav_dates) != expected_date_count
            or nav_dates != sorted(set(nav_dates))
            or any(
                len(date) != 8
                or not date.isdigit()
                or not "20120101" <= date <= "20191231"
                for date in nav_dates
            )
        ):
            raise FixedFactorReplayError(
                f"fixed factor replay governed date axis invalid:{scenario}"
            )
        scenario_dates[scenario] = nav_dates
        order_ids = [str(row.get("order_id")) for row in orders]
        if len(order_ids) != len(set(order_ids)):
            raise FixedFactorReplayError(
                f"fixed factor replay duplicate order:{scenario}"
            )
        order_by_id = {str(row["order_id"]): row for row in orders}
        if any(
            int(row.get("decision_index", -1)) < 0
            or int(row.get("execution_index", -1))
            != int(row.get("decision_index", -1)) + 1
            or int(row.get("execution_index", -1)) >= expected_date_count
            for row in orders
        ):
            raise FixedFactorReplayError(
                f"fixed factor replay order timing invalid:{scenario}"
            )
        rejection_ids = [str(row.get("order_id")) for row in rejections]
        if len(rejection_ids) != len(set(rejection_ids)):
            raise FixedFactorReplayError(
                f"fixed factor replay duplicate rejection:{scenario}"
            )
        fill_ids: set[str] = set()
        for fill in fills:
            fill_id = str(fill.get("fill_id"))
            order = order_by_id.get(str(fill.get("order_id")))
            if (
                not fill_id
                or fill_id in fill_ids
                or order is None
                or int(fill["execution_index"])
                != int(order["decision_index"]) + 1
                or int(fill["filled_shares"]) <= 0
                or int(fill["filled_shares"]) > int(fill["requested_shares"])
                or not math.isclose(
                    float(fill["total_cost"]),
                    sum(
                        float(fill.get(name, 0.0))
                        for name in (
                            "commission",
                            "stamp_duty",
                            "transfer_fee",
                            "handling_fee",
                            "securities_management_fee",
                            "slippage",
                            "impact",
                        )
                    ),
                    rel_tol=0.0,
                    abs_tol=1e-8,
                )
            ):
                raise FixedFactorReplayError(
                    f"fixed factor replay fill semantics invalid:{scenario}"
                )
            fill_ids.add(fill_id)
        filled_order_ids = {str(row.get("order_id")) for row in fills}
        rejected_order_ids = set(rejection_ids)
        if filled_order_ids & rejected_order_ids:
            raise FixedFactorReplayError(
                f"fixed factor replay contradictory order terminal:{scenario}"
            )
        terminal_order_ids = filled_order_ids | rejected_order_ids
        if terminal_order_ids != set(order_ids):
            raise FixedFactorReplayError(
                f"fixed factor replay order closure invalid:{scenario}"
            )
        if [int(row.get("index", -1)) for row in nav] != list(range(len(nav))):
            raise FixedFactorReplayError(
                f"fixed factor replay NAV axis invalid:{scenario}"
            )
        expected = _summarize_rows(
            nav=nav,
            fills=fills,
            orders=orders,
            rejections=rejections,
            policy=contract["scenario_policies"][scenario],
        )
        if expected != payload["backtest_summary"][scenario]:
            raise FixedFactorReplayError(
                f"fixed factor replay summary recomputation mismatch:{scenario}"
            )
    if scenario_dates["baseline"] != scenario_dates["zero_cost"]:
        raise FixedFactorReplayError("fixed factor replay scenario date axis drift")
    baseline = payload["backtest_summary"]["baseline"]
    zero_cost = payload["backtest_summary"]["zero_cost"]
    if not math.isclose(
        float(
            payload["backtest_summary"][
                "modeled_cost_scenario_return_difference"
            ]
        ),
        float(zero_cost["total_return"]) - float(baseline["total_return"]),
        rel_tol=0.0,
        abs_tol=1e-15,
    ):
        raise FixedFactorReplayError(
            "fixed factor replay modeled cost scenario difference invalid"
        )


def _valid_governance_boundary(payload: Mapping[str, Any]) -> bool:
    lineage = payload.get("input_bundle")
    grade = (
        str(lineage.get("source_evidence_grade") or "")
        if isinstance(lineage, Mapping)
        else ""
    )
    expected_blockers = _BLOCKERS_BY_SOURCE_GRADE.get(grade)
    return bool(
        payload.get("mode") == "development_replay"
        and payload.get("terminal_status") == TERMINAL_STATUS
        and payload.get("evidence_flags") == EVIDENCE_FLAGS
        and isinstance(payload.get("blockers"), list)
        and expected_blockers is not None
        and payload.get("blockers") == list(expected_blockers)
        and isinstance(lineage, Mapping)
        and lineage.get("blockers") == payload.get("blockers")
        and payload.get("data_admission_eligible") is False
        and payload.get("alpha_search_authorized") is False
        and payload.get("validation_candidate_eligible") is False
        and payload.get("lifecycle_publication_allowed") is False
        and payload.get("holdout_accessed") is False
        and payload.get("network_accessed") is False
        and payload.get("deterministic_build") is True
        and payload.get("backtest_summary", {}).get(
            "formal_research_backtest_eligible"
        )
        is False
    )


def _artifact_row(root: Path, role: str, relative_path: str) -> dict[str, Any]:
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file() or path.is_symlink():
        raise FixedFactorReplayError(f"fixed factor artifact containment failure:{role}")
    row: dict[str, Any] = {
        "role": role,
        "relative_path": relative_path,
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }
    if path.suffix == ".npy":
        value = np.load(path, mmap_mode="r", allow_pickle=False)
        row["shape"] = list(value.shape)
        row["dtype"] = str(value.dtype)
    return row


def _load_output_array(
    root: Path,
    row: Mapping[str, Any],
    dtype: np.dtype[Any] | type[np.generic],
    shape: tuple[int, ...],
) -> np.ndarray:
    try:
        value = np.load(
            root / str(row["relative_path"]), mmap_mode="r", allow_pickle=False
        )
    except (OSError, ValueError, KeyError) as exc:
        raise FixedFactorReplayError("fixed factor replay array invalid") from exc
    if value.dtype != np.dtype(dtype) or value.shape != shape:
        raise FixedFactorReplayError("fixed factor replay array dtype or shape invalid")
    return value


def _validate_closure(
    root: Path,
    manifest_path: Path,
    artifact_paths: set[str],
) -> None:
    expected_files = set(artifact_paths) | {manifest_path.name}
    observed_files: set[str] = set()
    observed_directories: set[str] = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise FixedFactorReplayError("fixed factor replay symlink forbidden")
        if path.is_file():
            observed_files.add(relative)
        elif path.is_dir():
            observed_directories.add(relative)
        else:
            raise FixedFactorReplayError("fixed factor replay special file forbidden")
        if path.stat().st_mode & 0o222:
            raise FixedFactorReplayError("fixed factor replay generation mutable")
    if observed_files != expected_files or observed_directories:
        raise FixedFactorReplayError("fixed factor replay artifact closure invalid")


def _compatible_current(
    output: Path,
    *,
    loader: LocalDevelopmentBundleLoader,
    contract_hash: str,
    builder_hash: str,
) -> dict[str, Any] | None:
    pointer_path = output / "current.json"
    if not pointer_path.exists():
        return None
    if not pointer_path.is_file() or pointer_path.is_symlink():
        raise FixedFactorReplayError("fixed factor replay current pointer invalid")
    try:
        pointer = read_json(pointer_path)
        relative = Path(str(pointer.get("manifest") or ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise FixedFactorReplayError("fixed factor replay pointer escape")
        manifest_path = (output / relative).resolve()
        if not manifest_path.is_relative_to(output) or manifest_path.name != MANIFEST_NAME:
            raise FixedFactorReplayError("fixed factor replay pointer target invalid")
        validated = validate_fixed_factor_replay_evidence(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise FixedFactorReplayError("fixed factor replay current pointer invalid") from exc
    if (
        pointer.get("schema_version") != "fixed_factor_replay_pointer_v1"
        or pointer.get("generation_id") != validated["generation_id"]
        or pointer.get("content_hash") != validated["content_hash"]
        or pointer.get("mode") != "development_replay"
        or pointer.get("validation_candidate_eligible") is not False
    ):
        raise FixedFactorReplayError("fixed factor replay current pointer drift")
    expected_lineage = _input_lineage(loader)
    if (
        validated.get("input_bundle") == expected_lineage
        and validated.get("replay_contract_hash") == contract_hash
        and validated.get("builder_semantic_hash") == builder_hash
    ):
        return validate_fixed_factor_replay_evidence(
            manifest_path,
            trusted_bundle_manifest=loader.manifest["manifest_path"],
        )
    return None


def _reject_output_overlap(output: Path, bundle_root: Path) -> None:
    if (
        output == bundle_root
        or output in bundle_root.parents
        or bundle_root in output.parents
        or output.is_symlink()
    ):
        raise FixedFactorReplayError("fixed factor replay output overlaps input bundle")


def _has_symlink_component(path: Path) -> bool:
    absolute = path if path.is_absolute() else Path.cwd() / path
    return any(component.is_symlink() for component in (absolute, *absolute.parents))


def _builder_semantic_hash() -> str:
    source_objects = (
        run_fixed_factor_replay,
        LocalDevelopmentBundleLoader,
        StackVM.execute_with_validity,
        execute_operator_with_validity,
        get_operator_spec,
        make_formula_vocab,
        ScenarioPolicy,
        SimulationResult,
        simulate_event_ledger,
        publish_prepared_generation,
    )
    paths = sorted(
        {
            Path(inspect.getsourcefile(value) or "").resolve()
            for value in source_objects
        }
    )
    return canonical_hash(
        {
            "sources": [
                {"path": path.name, "sha256": sha256_file(path)} for path in paths
            ],
            "runtime": {
                "python": (
                    f"{sys.version_info.major}.{sys.version_info.minor}."
                    f"{sys.version_info.micro}"
                ),
                "numpy": np.__version__,
                "torch": torch.__version__,
                "schema": SCHEMA_VERSION,
            },
        }
    )


def _write_json(path: Path, value: Any) -> None:
    payload = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _write_bytes(path, payload)


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    payload = b"".join(
        (
            json.dumps(dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        for row in rows
    )
    _write_bytes(path, payload)


def _write_npy(path: Path, value: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("wb") as handle:
            np.save(handle, value, allow_pickle=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FixedFactorReplayError("fixed factor replay JSON artifact invalid") from exc
    if not isinstance(payload, dict):
        raise FixedFactorReplayError("fixed factor replay JSON object required")
    return payload


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise FixedFactorReplayError(
                        "fixed factor replay JSONL object required"
                    )
                result.append(value)
    except (OSError, json.JSONDecodeError) as exc:
        raise FixedFactorReplayError("fixed factor replay JSONL artifact invalid") from exc
    return result


def _remove_preparation_root(root: Path) -> None:
    if not root.exists():
        return
    for path in sorted(root.rglob("*")):
        try:
            path.chmod(0o700 if path.is_dir() else 0o600)
        except FileNotFoundError:
            continue
    try:
        root.chmod(0o700)
    except FileNotFoundError:
        return
    shutil.rmtree(root, ignore_errors=True)


def _sha256_hex(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="auto-alpha portfolio fixed-replay",
        description="Run or validate the locked development-only factor replay",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--bundle-manifest", required=True)
    build.add_argument("--output-root", required=True)
    build.add_argument("--trusted-source-freeze-manifest")
    build.add_argument("--pretty", action="store_true")
    validate = commands.add_parser("validate")
    validate.add_argument("--manifest", required=True)
    validate.add_argument("--trusted-bundle-manifest")
    validate.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    validation_level = "validated_local_bundle_build"
    try:
        if args.command == "build":
            payload = run_fixed_factor_replay(
                args.bundle_manifest,
                args.output_root,
                trusted_source_freeze_manifest=args.trusted_source_freeze_manifest,
            )
            if args.trusted_source_freeze_manifest:
                validation_level = "trusted_source_replay_build"
        else:
            payload = validate_fixed_factor_replay_evidence(
                args.manifest,
                trusted_bundle_manifest=args.trusted_bundle_manifest,
            )
            validation_level = (
                "trusted_bundle_replay"
                if args.trusted_bundle_manifest
                else "integrity_and_internal_consistency"
            )
    except (FixedFactorReplayError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": "fixed_factor_replay_error",
                    "message": str(exc),
                },
                ensure_ascii=False,
                indent=2 if args.pretty else None,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    summary = {
        key: payload.get(key)
        for key in (
            "schema_version",
            "generation_id",
            "content_hash",
            "manifest_path",
            "mode",
            "terminal_status",
            "simulation_truth_hash",
            "data_admission_eligible",
            "alpha_search_authorized",
            "validation_candidate_eligible",
            "blockers",
            "cache_hit",
        )
        if key in payload
    }
    summary["validation_level"] = validation_level
    print(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
            sort_keys=True,
        )
    )
    return 0


__all__ = [
    "FixedFactorReplayError",
    "FixedFactorReplayEvidence",
    "run_fixed_factor_replay",
    "validate_fixed_factor_replay_evidence",
]


if __name__ == "__main__":
    raise SystemExit(main())

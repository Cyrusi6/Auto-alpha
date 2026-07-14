from __future__ import annotations

import math
from dataclasses import replace

import pytest
import torch

from alpha_factory.models import AlphaCandidateRecord
from alpha_factory.static_checks import run_static_checks
from feature_factory.catalog import FEATURE_SET_V3, build_feature_set_manifest, get_feature_definitions
from feature_factory.semantics import (
    build_feature_semantics_map,
    feature_semantics_contract_hash,
)
from feature_factory.vocab_adapter import make_formula_vocab_from_manifest
from model_core.vm import StackVM


def _v3_manifest():
    return build_feature_set_manifest(
        FEATURE_SET_V3,
        corporate_action_aware=True,
        created_at="2026-07-14T00:00:00Z",
    )


def test_all_95_v3_features_have_machine_readable_recursive_semantics():
    manifest = _v3_manifest()
    semantics = build_feature_semantics_map(manifest)

    assert manifest.feature_count == 95
    assert len(semantics) == 95
    assert len(feature_semantics_contract_hash(semantics)) == 64
    for name, contract in semantics.items():
        assert contract.feature_name == name
        assert contract.raw_dependencies
        assert contract.required_observations == contract.max_raw_lag + 1
        assert contract.longest_dependency_path
        assert contract.longest_dependency_path[-1].cumulative_raw_lag == contract.max_raw_lag
        assert len(contract.feature_implementation_source_hash) == 64
        assert len(contract.operator_implementation_source_hash) == 64
        assert len(contract.semantics_hash) == 64


def test_expanded_and_nested_feature_windows_use_recursive_endpoint_semantics():
    semantics = build_feature_semantics_map(_v3_manifest())

    assert (semantics["INDEX_RETURN_1D"].max_raw_lag, semantics["INDEX_RETURN_1D"].required_observations) == (60, 61)
    assert (semantics["INDEX_RETURN_20D"].max_raw_lag, semantics["INDEX_RETURN_20D"].required_observations) == (79, 80)
    assert (semantics["INDEX_VOLATILITY_20D"].max_raw_lag, semantics["INDEX_VOLATILITY_20D"].required_observations) == (79, 80)
    assert (semantics["BENCHMARK_RELATIVE_RETURN_20D"].max_raw_lag, semantics["BENCHMARK_RELATIVE_RETURN_20D"].required_observations) == (20, 21)
    assert (semantics["INDUSTRY_RELATIVE_RETURN_20D"].max_raw_lag, semantics["INDUSTRY_RELATIVE_RETURN_20D"].required_observations) == (20, 21)
    assert (semantics["INDUSTRY_MOMENTUM"].max_raw_lag, semantics["INDUSTRY_MOMENTUM"].required_observations) == (20, 21)
    assert (semantics["HK_HOLDING_CHANGE_20D"].max_raw_lag, semantics["HK_HOLDING_CHANGE_20D"].required_observations) == (20, 21)
    assert [step.lag_increment for step in semantics["INDEX_VOLATILITY_20D"].longest_dependency_path] == [0, 1, 19, 59]


def test_formula_semantics_composes_feature_and_operator_windows_without_off_by_one():
    manifest = _v3_manifest()
    semantics = build_feature_semantics_map(manifest)
    vocab = make_formula_vocab_from_manifest(manifest)
    vm = StackVM(vocab)

    nested = [
        vocab.encode_name("INDEX_VOLATILITY_20D"),
        vocab.encode_name("TS_MEAN10"),
        vocab.encode_name("DELAY5"),
    ]
    nested_semantics = vm.formula_semantics(nested, semantics)
    assert nested_semantics.max_raw_lag == 79 + 9 + 5
    assert nested_semantics.required_observations == 94
    assert [step.lag_increment for step in nested_semantics.longest_dependency_path[-2:]] == [9, 5]

    binary_rolling = [
        vocab.encode_name("RET_5D"),
        vocab.encode_name("HK_HOLDING_CHANGE_20D"),
        vocab.encode_name("TS_CORR10"),
    ]
    binary_semantics = vm.formula_semantics(binary_rolling, semantics)
    assert binary_semantics.max_raw_lag == 20 + 9
    assert binary_semantics.required_observations == 30


def test_earliest_required_raw_observation_changes_output_but_one_earlier_does_not():
    manifest = _v3_manifest()
    semantics = build_feature_semantics_map(manifest)
    vocab = make_formula_vocab_from_manifest(manifest)
    vm = StackVM(vocab)
    tokens = [vocab.encode_name("RET_5D"), vocab.encode_name("TS_MEAN3")]
    formula_semantics = vm.formula_semantics(tokens, semantics)
    assert formula_semantics.max_raw_lag == 7

    raw = torch.arange(1.0, 13.0).reshape(1, -1)

    def execute(prices: torch.Tensor, raw_validity: torch.Tensor | None = None):
        ret = torch.zeros_like(prices)
        ret[:, 5:] = torch.log(prices[:, 5:] / prices[:, :-5])
        validity = torch.zeros_like(prices, dtype=torch.bool)
        validity[:, 5:] = True
        if raw_validity is not None:
            validity[:, 5:] &= raw_validity[:, 5:] & raw_validity[:, :-5]
        tensor = torch.zeros((1, vocab.feature_count, prices.shape[1]), dtype=prices.dtype)
        tensor_validity = torch.zeros_like(tensor, dtype=torch.bool)
        feature_index = vocab.encode_name("RET_5D")
        tensor[:, feature_index, :] = ret
        tensor_validity[:, feature_index, :] = validity
        result = vm.execute_with_validity(tokens, tensor, tensor_validity)
        assert result is not None
        return result

    baseline_value, baseline_valid = execute(raw)
    earliest = raw.shape[1] - 1 - formula_semantics.max_raw_lag
    changed_required = raw.clone()
    changed_required[:, earliest] *= 10
    required_value, required_valid = execute(changed_required)
    assert baseline_valid[0, -1] and required_valid[0, -1]
    assert not math.isclose(float(baseline_value[0, -1]), float(required_value[0, -1]))

    changed_earlier = raw.clone()
    changed_earlier[:, earliest - 1] *= 10
    earlier_value, earlier_valid = execute(changed_earlier)
    assert torch.equal(baseline_valid[:, -1], earlier_valid[:, -1])
    assert torch.equal(baseline_value[:, -1], earlier_value[:, -1])

    missing_required = torch.ones_like(raw, dtype=torch.bool)
    missing_required[:, earliest] = False
    _, required_missing_valid = execute(raw, missing_required)
    assert not required_missing_valid[0, -1]

    missing_earlier = torch.ones_like(raw, dtype=torch.bool)
    missing_earlier[:, earliest - 1] = False
    _, earlier_missing_valid = execute(raw, missing_earlier)
    assert earlier_missing_valid[0, -1]


def test_missing_feature_contract_fails_formula_semantics_and_static_admission_closed():
    manifest = _v3_manifest()
    semantics = build_feature_semantics_map(manifest)
    vocab = make_formula_vocab_from_manifest(manifest)
    vm = StackVM(vocab)
    tokens = [vocab.encode_name("RET_5D")]
    incomplete = dict(semantics)
    incomplete.pop("RET_5D")

    with pytest.raises(ValueError, match="missing canonical feature contract"):
        vm.formula_semantics(tokens, incomplete)

    candidate = AlphaCandidateRecord(
        alpha_candidate_id="alpha_semantics_probe",
        formula_hash="probe",
        formula_tokens=tokens,
        formula_names=["RET_5D"],
        source="unit",
        source_refs=[],
        feature_set_name=FEATURE_SET_V3,
        feature_version=FEATURE_SET_V3,
        operator_version="ashare_ops_v1",
        complexity=1,
        lookback=1,
        family_tags=["price_return"],
    )
    checked, rows = run_static_checks(
        [candidate],
        max_complexity=10,
        max_lookback=20,
        vocab=vocab,
        feature_semantics=incomplete,
    )
    assert checked[0].status == "rejected"
    assert "missing canonical feature contract" in checked[0].reject_reason
    assert rows[0]["required_observations"] is None


def test_static_admission_uses_canonical_required_observations_not_stored_lookback():
    manifest = _v3_manifest()
    semantics = build_feature_semantics_map(manifest)
    vocab = make_formula_vocab_from_manifest(manifest)
    candidate = AlphaCandidateRecord(
        alpha_candidate_id="alpha_nested",
        formula_hash="nested",
        formula_tokens=[vocab.encode_name("INDEX_VOLATILITY_20D")],
        formula_names=["INDEX_VOLATILITY_20D"],
        source="unit",
        source_refs=[],
        feature_set_name=FEATURE_SET_V3,
        feature_version=FEATURE_SET_V3,
        operator_version="ashare_ops_v1",
        complexity=1,
        lookback=1,
        family_tags=["index_market"],
    )
    checked, rows = run_static_checks(
        [candidate],
        max_complexity=10,
        max_lookback=20,
        vocab=vocab,
        feature_semantics=semantics,
    )
    assert checked[0].status == "rejected"
    assert checked[0].lookback == 80
    assert rows[0]["canonical_max_raw_lag"] == 79
    assert rows[0]["required_observations"] == 80
    assert "lookback_exceeds_limit" in rows[0]["errors"]

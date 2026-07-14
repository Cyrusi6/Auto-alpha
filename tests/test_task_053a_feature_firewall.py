from __future__ import annotations

from types import SimpleNamespace

import torch

from feature_factory.models import FeatureDefinition, FeatureSetManifest
from feature_factory.validity import build_feature_values_and_validity
from model_core.vm import StackVM
from model_core.vocab import FORMULA_VOCAB
from research_firewall import FirewallSentinelDataset, run_research_firewall_sentinel


def test_masked_cross_section_ignores_invalid_extreme_and_handles_ties():
    vm = StackVM()
    feature = FORMULA_VOCAB.encode_name("RET_1D")
    rank = FORMULA_VOCAB.encode_name("CS_RANK")
    values = torch.zeros((4, FORMULA_VOCAB.feature_count, 2), dtype=torch.float32)
    values[:, feature, :] = torch.tensor([[1.0, 2.0], [1.0, 4.0], [3.0, 6.0], [9.0, 8.0]])
    validity = torch.ones_like(values, dtype=torch.bool)
    validity[3, feature, :] = False

    baseline, baseline_validity = vm.execute_with_validity([feature, rank], values, validity)
    values[3, feature, :] = 1_000_000.0
    changed, changed_validity = vm.execute_with_validity([feature, rank], values, validity)

    assert torch.equal(baseline, changed)
    assert torch.equal(baseline_validity, changed_validity)
    assert baseline[0, 0] == baseline[1, 0]
    assert not baseline_validity[3].any()


def test_masked_time_series_is_prefix_invariant_and_requires_full_window():
    vm = StackVM()
    feature = FORMULA_VOCAB.encode_name("RET_1D")
    operator = FORMULA_VOCAB.encode_name("TS_MEAN3")
    values = torch.zeros((2, FORMULA_VOCAB.feature_count, 7), dtype=torch.float32)
    values[:, feature, :] = torch.arange(14, dtype=torch.float32).reshape(2, 7)
    validity = torch.ones_like(values, dtype=torch.bool)
    validity[0, feature, 1] = False

    baseline, mask = vm.execute_with_validity([feature, operator], values, validity)
    changed_values = values.clone()
    changed_values[:, feature, 5:] += 100_000.0
    changed, changed_mask = vm.execute_with_validity([feature, operator], changed_values, validity)

    assert torch.equal(baseline[:, :5], changed[:, :5])
    assert torch.equal(mask, changed_mask)
    assert not mask[0, :4].any()
    assert mask[0, 4]


def test_joint_feature_build_excludes_invalid_cells_before_cross_section_stats():
    definition = FeatureDefinition(
        feature_name="PB",
        feature_version="test",
        family="valuation",
        source_fields=["pb"],
        tensor_key="pb",
        transform="robust_zscore",
    )
    manifest = FeatureSetManifest(
        feature_set_name="test",
        feature_set_version="1",
        feature_version="test",
        operator_version="test",
        feature_count=1,
        feature_definitions=[definition.to_dict()],
        data_freeze_id=None,
        data_freeze_hash=None,
        point_in_time=True,
        corporate_action_aware=False,
        target_return_mode="next_open",
        created_at="2026-07-14T00:00:00Z",
        content_hash="manifest",
    )
    values = torch.tensor([[1.0, 2.0], [2.0, 3.0], [100.0, 100.0]])
    validity = torch.tensor([[True, True], [True, True], [False, False]])
    loader = SimpleNamespace(
        raw_data_cache={"pb": values, "signal_eligible_at_close": torch.ones_like(validity)},
        raw_validity_cache={"pb": validity},
    )

    baseline, baseline_validity, _ = build_feature_values_and_validity(loader, manifest)
    loader.raw_data_cache["pb"][2] = -1_000_000.0
    changed, changed_validity, _ = build_feature_values_and_validity(loader, manifest)

    assert torch.equal(baseline, changed)
    assert torch.equal(baseline_validity, changed_validity)
    assert not baseline_validity[2].any()
    assert torch.equal(baseline[2], torch.zeros_like(baseline[2]))


def test_real_firewall_sentinel_observes_diagnostic_but_not_research_changes(tmp_path):
    dates = ("20240527", "20240528", "20240529", "20240530", "20240531", "20240603", "20240604", "20240605")
    features = torch.arange(3 * 1 * len(dates), dtype=torch.float32).reshape(3, 1, len(dates))
    validity = torch.ones_like(features, dtype=torch.bool)
    target = torch.flip(features[:, 0, :], dims=[0]).clone()
    target_available = torch.ones_like(target, dtype=torch.bool)
    dataset = FirewallSentinelDataset(
        trade_dates=dates,
        feature_values=features,
        feature_validity=validity,
        target=target,
        target_available=target_available,
        formula_tokens=(0,),
        source_fingerprint="same-content",
    )

    result = run_research_firewall_sentinel(dataset, dataset, tmp_path)

    assert result["status"] == "passed"
    assert result["proof"]["post_cutoff_research_change_count"] == 0
    assert result["proof"]["diagnostic_change_count"] == 4
    assert result["proof"]["inside_cutoff_cache_miss_count"] == 4
    assert result["proof"]["raw_matrix_local_scheduler_consistent"] is True
    assert result["proof"]["access_violation_count"] == 0
    assert (tmp_path / "task_053a_research_firewall_sentinel.json").exists()

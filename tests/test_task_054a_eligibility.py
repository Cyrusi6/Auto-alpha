from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pytest
import torch

from alpha_factory.proxy_eval import run_proxy_eval
from research_firewall import DateFirewall, ResearchEligibilityContract
from validation_lab.eligibility import build_common_eligibility
from validation_lab.policy import load_validation_policy


@dataclass(frozen=True)
class _Candidate:
    alpha_candidate_id: str = "candidate"
    formula_hash: str = "formula"
    formula_tokens: tuple[int, ...] = (0,)
    status: str = "pending"
    proxy_score: float | None = None
    reject_reason: str | None = None


class _IdentityVM:
    def __init__(self, _vocab=None):
        pass

    def execute_with_validity(self, _tokens, tensor, validity):
        return tensor[:, 0, :], validity[:, 0, :]


class _Loader:
    def __init__(self, feature: torch.Tensor):
        self.trade_dates = ["20240527", "20240528", "20240529", "20240530", "20240531", "20240603"]
        self.firewall_source_trade_dates = list(self.trade_dates)
        self.date_firewall = DateFirewall("20240530", "20240531", label_horizon=2)
        self.feat_tensor = feature[:, None, :]
        self.feature_validity = torch.ones_like(self.feat_tensor, dtype=torch.bool)
        self.target_ret = torch.tensor([[0.1, -0.2, 0.3, -0.4, 0.5, -0.6], [0.2, -0.1, 0.4, -0.3, 0.6, -0.5]])
        eligible = torch.ones_like(self.target_ret, dtype=torch.bool)
        self.target_available = eligible
        self.raw_data_cache = {"signal_eligible_at_close": eligible}
        self.use_matrix_cache = True
        self.ts_codes = ["a", "b"]


def test_t_plus_two_endpoint_uses_complete_trade_axis_and_excludes_immature_dates():
    dates = ("20240527", "20240528", "20240529", "20240530", "20240531", "20240603")
    contract = ResearchEligibilityContract("20240530", label_horizon=2)
    assert contract.eligible_dates(dates) == ("20240527", "20240528")
    assert contract.endpoint_dates(dates, 1) == ("20240528", "20240529", "20240530")
    assert contract.endpoint_dates(dates, 4) is None
    assert contract.eligible_mask(dates)[-2:] == (False, False)
    lineage = contract.lineage(dates)
    assert lineage["max_eligible_signal_date"] == "20240528"
    assert lineage["max_eligible_endpoint_date"] == "20240530"


def test_eligible_hash_binds_axis_cutoff_and_execution_contract():
    dates = ("20240527", "20240528", "20240529", "20240530")
    base = ResearchEligibilityContract("20240530", 2)
    assert base.eligible_date_hash(dates) != ResearchEligibilityContract("20240529", 2).eligible_date_hash(dates)
    assert base.eligible_date_hash(dates) != ResearchEligibilityContract("20240530", 3).eligible_date_hash(dates)
    assert base.eligible_date_hash(dates) != base.eligible_date_hash(dates + ("20240531",))


def test_common_eligibility_intersects_target_endpoint_contract():
    dates = ["20240527", "20240528", "20240529", "20240530", "20240531"]
    result = build_common_eligibility(
        dates,
        membership_known=np.ones(5),
        snapshot_valid=np.ones(5),
        target_data_valid=np.ones(5),
        structural_gap_free=np.ones(5),
        research_contract=ResearchEligibilityContract("20240530", 2),
    )
    assert result.eligible_mask.tolist() == [True, True, False, False, False]
    assert "research_target_endpoint_unobservable" in result.reasons_by_date[-1]


def test_proxy_post_cutoff_mutation_cannot_change_research_output(monkeypatch):
    monkeypatch.setattr("alpha_factory.proxy_eval.StackVM", _IdentityVM)
    feature = torch.tensor([[1.0, 2.0, 3.0, 4.0, 5.0, 6.0], [2.0, 1.0, 4.0, 3.0, 6.0, 5.0]])
    candidate = _Candidate()
    _, rows, summary = run_proxy_eval([candidate], _Loader(feature.clone()), max_candidates=1, max_dates=63)
    mutated = feature.clone()
    mutated[:, 2:] = torch.tensor([[1e9], [-1e9]])
    _, changed_rows, changed_summary = run_proxy_eval([candidate], _Loader(mutated), max_candidates=1, max_dates=63)
    stable_fields = ("coverage", "cross_sectional_std", "nonzero_ratio", "preliminary_rank_ic", "turnover_proxy", "proxy_score", "status")
    assert {name: rows[0][name] for name in stable_fields} == {name: changed_rows[0][name] for name in stable_fields}
    assert summary["eligible_date_hash"] == changed_summary["eligible_date_hash"]


def test_proxy_research_mutation_changes_output(monkeypatch):
    monkeypatch.setattr("alpha_factory.proxy_eval.StackVM", _IdentityVM)
    feature = torch.tensor([[1.0, 2.0, 3.0, 4.0, 5.0, 6.0], [2.0, 1.0, 4.0, 3.0, 6.0, 5.0]])
    candidate = _Candidate()
    _, rows, _ = run_proxy_eval([candidate], _Loader(feature.clone()), max_candidates=1, max_dates=63)
    feature[0, 0] = 100.0
    _, changed_rows, _ = run_proxy_eval([candidate], _Loader(feature), max_candidates=1, max_dates=63)
    assert rows[0]["cross_sectional_std"] != changed_rows[0]["cross_sectional_std"]


def test_task054_production_policy_window_parameters_are_locked():
    policy = load_validation_policy("task054_production_engineering_v1")
    policy.validate_window_parameters(756, 126, 126, 126)
    with pytest.raises(ValueError, match="production_policy_parameter_override"):
        policy.validate_window_parameters(755, 126, 126, 126)

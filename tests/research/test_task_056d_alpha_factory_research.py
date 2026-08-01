from __future__ import annotations

from dataclasses import replace
import json

import pytest
import torch

from auto_alpha.research.discovery.factory.full_research import run_full_research
from auto_alpha.research.discovery.factory.models import AlphaCandidateRecord
from auto_alpha.research.discovery.factory.proxy_eval import run_proxy_eval
from auto_alpha.research.discovery.factory.research_policy import load_alpha_research_policy
from auto_alpha.research.discovery.factory.scoring import score_candidates
from auto_alpha.research.discovery.factory.trial_ledger import write_trial_ledger
from auto_alpha.research.discovery.evaluation import ObjectiveSpec, normalize_objective_rows
from auto_alpha.research.factors.store import has_positive_oos_evidence
from auto_alpha.research.formulas.runtime.backtest import AShareFactorEvaluator
from auto_alpha.platform.observability.monitoring.checks import check_alpha_factory_campaign


class _IdentityVM:
    def __init__(self, _vocab=None):
        pass

    def execute_with_validity(self, _tokens, tensor, validity):
        return tensor[:, 0, :], validity[:, 0, :]


class _ResearchLoader:
    def __init__(self, *, inverse_target: bool = False):
        stock_count = 40
        date_count = 30
        stock_signal = torch.linspace(-1.0, 1.0, stock_count).unsqueeze(1)
        factor = stock_signal.expand(stock_count, date_count).clone()
        target = 0.01 * factor
        if inverse_target:
            target = -target
        dates = [f"2024{month:02d}{day:02d}" for month in range(1, 4) for day in range(1, 11)]
        market = 0.002 * torch.sin(torch.arange(date_count, dtype=torch.float32) / 2.0)
        loading = torch.linspace(0.5, 1.5, stock_count).unsqueeze(1)
        daily_return = loading * market.unsqueeze(0)
        close = 100.0 * torch.cumprod(1.0 + daily_return, dim=1)
        amount = (100_000_000.0 + torch.arange(stock_count, dtype=torch.float32).unsqueeze(1) * 1_000_000.0).expand_as(factor)
        total_mv = (10_000_000_000.0 + torch.arange(stock_count, dtype=torch.float32).unsqueeze(1) * 100_000_000.0).expand_as(factor)
        industry = (torch.arange(stock_count) % 5).unsqueeze(1).expand(stock_count, date_count)
        target_available = torch.ones_like(target, dtype=torch.bool)
        target_available[:, -2:] = False
        signal_eligible = torch.ones_like(target, dtype=torch.bool)
        validation_common = signal_eligible & target_available
        self.trade_dates = dates
        self.ts_codes = [f"{index:06d}.{'SH' if index < stock_count // 2 else 'SZ'}" for index in range(stock_count)]
        self.feat_tensor = factor[:, None, :]
        self.feature_validity = torch.ones_like(self.feat_tensor, dtype=torch.bool)
        self.target_ret = target
        self.target_available = target_available
        self.label_horizon = 2
        self.production_research = False
        self.amount_semantics = "raw_turnover_CNY"
        self.raw_data_cache = {
            "signal_candidate_cells": signal_eligible,
            "signal_eligible_at_close": signal_eligible,
            "validation_common_cells": validation_common,
            "target_available_mask": target_available,
            "active_mask": signal_eligible,
            "index_member_matrix": signal_eligible,
            "close": close,
            "amount": amount,
            "amount_semantics": "raw_turnover_CNY",
            "total_mv": total_mv,
            "log_mkt_cap": torch.log1p(total_mv),
            "industry_codes": industry,
            "industry_code_matrix": industry,
        }
        self.raw_validity_cache = {
            "close": torch.ones_like(close, dtype=torch.bool),
            "amount": torch.ones_like(amount, dtype=torch.bool),
            "total_mv": torch.ones_like(total_mv, dtype=torch.bool),
            "log_mkt_cap": torch.ones_like(total_mv, dtype=torch.bool),
            "industry_codes": torch.ones_like(industry, dtype=torch.bool),
        }


def _candidate(candidate_id: str = "candidate", formula_hash: str = "f" * 64, lookback: int = 1):
    return AlphaCandidateRecord(
        alpha_candidate_id=candidate_id,
        formula_hash=formula_hash,
        formula_tokens=[0],
        formula_names=["FEATURE"],
        source="template",
        source_refs=[],
        feature_set_name="test",
        feature_version="test",
        operator_version="test",
        complexity=1,
        lookback=lookback,
        family_tags=["price_volume"],
        static_check_status="passed",
        status="proxy_passed",
    )


def test_evaluator_score_is_invariant_to_return_unit_scaling():
    factor = torch.tensor([[1.0, 2.0], [2.0, 1.0], [3.0, 3.0]])
    target = torch.tensor([[0.01, 0.03], [0.02, 0.02], [0.03, 0.01]])
    raw = {"validation_common_cells": torch.ones_like(factor, dtype=torch.bool)}
    evaluator = AShareFactorEvaluator()
    base = evaluator.evaluate(factor, raw, target)
    scaled = evaluator.evaluate(factor, raw, target * 10_000.0)
    assert scaled.top_bottom_spread == pytest.approx(base.top_bottom_spread * 10_000.0)
    assert scaled.score == pytest.approx(base.score)


def test_cohort_normalization_is_dimensionless_and_tie_stable():
    rows = [
        {"id": "a", "ic": 0.01, "turnover": 0.8, "raw_spread": 0.001},
        {"id": "b", "ic": 0.02, "turnover": 0.2, "raw_spread": 1000.0},
    ]
    objectives = (ObjectiveSpec("ic", 1, 1.0), ObjectiveSpec("turnover", -1, 1.0))
    scores, components, reference = normalize_objective_rows(rows, objectives, id_field="id")
    changed = [dict(row, raw_spread=row["raw_spread"] * 1_000_000.0) for row in rows]
    changed_scores, _, changed_reference = normalize_objective_rows(changed, objectives, id_field="id")
    assert scores == changed_scores
    assert components["b"] == {"ic": 1.0, "turnover": 1.0}
    assert reference["reference_hash"] == changed_reference["reference_hash"]


def test_proxy_records_neutralized_metrics_universe_consistency_and_policy_lineage(monkeypatch):
    monkeypatch.setattr("auto_alpha.research.discovery.factory.proxy_eval.StackVM", _IdentityVM)
    loader = _ResearchLoader()
    policy = replace(
        load_alpha_research_policy("alpha_factory_two_stage_smoke_v1"),
        proxy_min_universe_count=3,
        proxy_min_evaluable_dates=5,
    )
    updated, rows, summary = run_proxy_eval(
        [_candidate()],
        loader,
        max_candidates=1,
        max_dates=20,
        policy=policy,
        family_novelty_scores={"candidate": 0.75},
        proxy_context_hash="context",
    )
    assert updated[0].status == "proxy_passed"
    assert rows[0]["evaluable_universe_count"] == 3
    assert rows[0]["universe_direction_consistency"] == 1.0
    assert rows[0]["neutralized_rank_ic_mean"] > 0
    assert rows[0]["normalized_objectives"]
    assert summary["research_policy_hash"] == policy.policy_hash
    assert summary["proxy_context_hash"] == "context"


def test_full_research_uses_lookback_plus_horizon_and_proper_trial_correction(monkeypatch):
    monkeypatch.setattr("auto_alpha.research.discovery.factory.full_research.StackVM", _IdentityVM)
    loader = _ResearchLoader()
    policy = load_alpha_research_policy("alpha_factory_two_stage_smoke_v1")
    candidate = _candidate(lookback=3)
    rows, summary = run_full_research(
        [candidate],
        loader,
        policy=policy,
        vocab=None,
        factor_transform="raw",
        total_trial_count=187,
        seed=17,
    )
    row = rows[0]
    assert row["effective_embargo"] == 5
    assert row["bh_q_value"] <= 1.0
    assert row["selection_adjusted_p_value"] <= 1.0
    assert summary["multiple_testing"]["total_generated_trials"] == 187
    assert row["status"] == "validation_candidate"
    assert row["gate_decision"]["checks"]["oos_evidence_positive"] is True
    payload = {
        "status": row["status"],
        "metadata": {"gate_decision": row["gate_decision"]},
    }
    assert has_positive_oos_evidence(payload)


def test_negative_oos_evidence_never_becomes_validation_candidate(monkeypatch):
    monkeypatch.setattr("auto_alpha.research.discovery.factory.full_research.StackVM", _IdentityVM)
    rows, _ = run_full_research(
        [_candidate()],
        _ResearchLoader(inverse_target=True),
        policy=load_alpha_research_policy("alpha_factory_two_stage_smoke_v1"),
        vocab=None,
        factor_transform="raw",
        total_trial_count=20,
        seed=7,
    )
    assert rows[0]["status"] != "validation_candidate"
    assert "positive_oos_rank_ic_missing" in rows[0]["gate_reasons"]


def test_trial_ledger_preserves_exact_trial_path_and_selection_bias(tmp_path):
    candidate = _candidate()
    paths, summary = write_trial_ledger(
        candidates=[candidate],
        static_rows=[{"alpha_candidate_id": candidate.alpha_candidate_id, "status": "passed"}],
        proxy_rows=[{"alpha_candidate_id": candidate.alpha_candidate_id, "status": "proxy_passed", "proxy_score": 0.0, "sampled_dates": ["20240101"]}],
        full_rows=[{"alpha_candidate_id": candidate.alpha_candidate_id, "formula_hash": candidate.formula_hash, "status": "research_rejected", "score": 0.0, "bh_q_value": 0.2}],
        scored_rows=[{"alpha_candidate_id": candidate.alpha_candidate_id, "status": "research_rejected", "final_score": 0.0}],
        shortlist=[],
        campaign_id="campaign",
        policy_id="policy",
        policy_hash="a" * 64,
        output_dir=tmp_path,
    )
    assert summary["trial_count"] == 1
    assert summary["stage_counts"]["full_research_selected"] == 1
    assert summary["stage_counts"]["shortlisted"] == 0
    assert summary["selection_data_reused"] is True
    assert paths["alpha_trial_ledger_path"].endswith("alpha_trial_ledger.jsonl")


def test_final_scoring_consumes_only_standardized_stage_scores():
    candidate = _candidate()
    proxy = [{"alpha_candidate_id": candidate.alpha_candidate_id, "status": "proxy_passed", "proxy_score": -0.5, "normalized_objectives": {"coverage": 1.0}}]
    full = [{"request": {"formula_hash": candidate.formula_hash}, "status": "validation_candidate", "score": 0.5, "normalized_objectives": {"mean_rank_ic": 1.0}, "raw_spread": 1e12}]
    _, scored = score_candidates([candidate], proxy, full, {candidate.alpha_candidate_id: 1.0})
    assert scored[0]["final_score"] == pytest.approx(0.3)
    full[0]["raw_spread"] = -1e20
    _, changed = score_candidates([candidate], proxy, full, {candidate.alpha_candidate_id: 0.0})
    assert changed[0]["final_score"] == scored[0]["final_score"]


def test_production_research_rejects_smoke_policy():
    try:
        load_alpha_research_policy("alpha_factory_two_stage_smoke_v1", production_research=True)
    except ValueError as exc:
        assert "requires alpha_factory_two_stage_oos_v1" in str(exc)
    else:
        raise AssertionError("production accepted smoke policy")


def test_monitoring_rejects_unscaled_success_report(tmp_path):
    path = tmp_path / "alpha_factory_report.json"
    path.write_text(
        json.dumps({"status": "success", "campaign_id": "c", "summary": {"score_method": "legacy_raw_linear_sum"}}),
        encoding="utf-8",
    )
    check, alerts = check_alpha_factory_campaign(path)
    assert check["alpha_score_method"] == "legacy_raw_linear_sum"
    assert any(alert.check == "alpha_factory_unscaled_score" for alert in alerts)

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from auto_alpha.platform.artifacts.schema.writer import attach_artifact_metadata, write_json_artifact, write_jsonl_artifact
from auto_alpha.platform.artifacts.schema.validator import validate_artifact
from auto_alpha.research.search.models import AlphaCampaignConfig
from auto_alpha.research.search.workflow import AlphaFactoryRunner
from auto_alpha.platform.observability.dashboard.config import DashboardConfig
from auto_alpha.platform.observability.dashboard.data_service import AshareDashboardService
from auto_alpha.research.factors.store import make_factor_id, stable_formula_hash
from auto_alpha.platform.observability.monitoring.checks import check_sealed_holdout_validation
from auto_alpha.validation.walk_forward.red_team_candidate_pool import freeze_candidate_pool
from auto_alpha.validation.walk_forward.red_team_candidate_pool import validate_candidate_pool_manifest
from auto_alpha.validation.walk_forward.red_team_capability import HoldoutCapabilityRegistry
from auto_alpha.validation.walk_forward.red_team_contracts import HoldoutCalibrationProfile
from auto_alpha.validation.walk_forward.red_team_contracts import SealedHoldoutPolicy
from auto_alpha.validation.walk_forward.red_team_contracts import publish_holdout_policy
from auto_alpha.validation.walk_forward.red_team_evaluator import ValidationRedTeamAgent
from auto_alpha.validation.walk_forward.red_team_io import HoldoutContractError
from auto_alpha.validation.walk_forward.red_team_io import atomic_json
from auto_alpha.validation.walk_forward.red_team_io import sha256_file
from auto_alpha.validation.walk_forward.red_team_io import stable_hash
from auto_alpha.validation.walk_forward.red_team_preflight import preflight_source_holdout
from auto_alpha.validation.walk_forward.red_team_verifier import verify_holdout_result
from auto_alpha.validation.walk_forward.red_team_view import VIEW_CORE_FIELDS


def test_candidate_pool_freezes_all_required_selection_evidence(tmp_path):
    campaign, materializations = _campaign_fixture(tmp_path)
    first_path, first = freeze_candidate_pool(campaign, materializations, tmp_path / "pool")
    second_path, second = freeze_candidate_pool(campaign, materializations, tmp_path / "pool")
    assert first_path == second_path
    assert first["content_hash"] == second["content_hash"]
    assert first["candidate_count"] == 2
    assert first["formula_hashes"] == [row["formula_hash"] for row in first["candidates"]]
    assert set(first["factor_value_hashes"]) == set(first["formula_hashes"])
    assert len(first["research_metrics"]) == 2
    assert first["selection_order"][0]["selection_rank"] == 1
    assert first["trial_count"] == 2
    assert len(first["selection_policy_hash"]) == 64
    assert validate_candidate_pool_manifest(first_path)["candidate_identity_root"] == first["candidate_identity_root"]


def test_candidate_freeze_rejects_value_tampering(tmp_path):
    campaign, materializations = _campaign_fixture(tmp_path)
    values_path = Path(materializations[0]).parent / "values.npy"
    values = np.load(values_path)
    values[0, 0] += 1.0
    np.save(values_path, values, allow_pickle=False)
    with pytest.raises(HoldoutContractError, match="materialization_hash_mismatch"):
        freeze_candidate_pool(campaign, materializations, tmp_path / "pool")


def test_candidate_freeze_revalidates_jsonl_schema_sidecars(tmp_path):
    campaign, materializations = _campaign_fixture(tmp_path)
    pool_path, _ = freeze_candidate_pool(campaign, materializations, tmp_path / "pool")
    sidecar = Path(f"{tmp_path / 'campaign' / 'alpha_shortlist.jsonl'}.schema.json")
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    payload["extra"]["record_count"] = 999
    atomic_json(sidecar, payload)
    with pytest.raises(HoldoutContractError, match="candidate_pool_source_drift"):
        validate_candidate_pool_manifest(pool_path, revalidate_sources=True)


def test_contaminated_holdout_never_receives_capability(tmp_path):
    pool_path, pool, policy_path, _, view_path = _sealed_inputs(tmp_path)
    view = json.loads(view_path.read_text(encoding="utf-8"))
    view["untouched"] = False
    view["historically_observed"] = True
    core = {field: view.get(field) for field in VIEW_CORE_FIELDS}
    view["content_hash"] = stable_hash(core)
    atomic_json(view_path, view)
    registry = HoldoutCapabilityRegistry(tmp_path / "registry")
    with pytest.raises(HoldoutContractError, match="contaminated"):
        registry.issue(
            candidate_pool_manifest_path=pool_path,
            holdout_view_manifest_path=view_path,
            holdout_policy_path=policy_path,
            red_team_output_root=tmp_path / "red_team",
        )
    assert pool["holdout_accessed"] is False


def test_one_shot_holdout_passes_positive_rejects_inverse_and_archives(tmp_path):
    pool_path, pool, policy_path, _, view_path = _sealed_inputs(tmp_path)
    registry = HoldoutCapabilityRegistry(tmp_path / "registry")
    capability_path, _ = registry.issue(
        candidate_pool_manifest_path=pool_path,
        holdout_view_manifest_path=view_path,
        holdout_policy_path=policy_path,
        red_team_output_root=tmp_path / "red_team",
    )
    agent = ValidationRedTeamAgent(capability_path, sha256_file(capability_path))
    result_path, result = agent.evaluate()
    assert result["candidate_count"] == 2
    assert result["terminal_count"] == 2
    assert result["status_counts"] == {"sealed_holdout_passed": 1, "sealed_holdout_rejected": 1}
    assert result["formula_hashes"] == pool["formula_hashes"]
    rows = _jsonl(result_path.parent / "sealed_holdout_candidate_results.jsonl")
    assert rows[0]["metrics"]["median_rank_ic"] > 0
    assert rows[0]["metrics"]["positive_rank_ic_window_ratio"] == 1.0
    assert rows[0]["metrics"]["walk_forward_window_pass_ratio"] == 1.0
    assert rows[0]["metrics"]["net_top_bottom_spread"] > 0
    assert rows[0]["metrics"]["double_modeled_cost_net_spread"] > 0
    assert rows[0]["metrics"]["max_existing_factor_correlation"] <= 0.70
    assert rows[1]["status"] == "sealed_holdout_rejected"
    archive = _jsonl(result_path.parent / "candidate_holdout_archive.jsonl")
    assert [row["formula_hash"] for row in archive] == [rows[1]["formula_hash"]]
    assert archive[0]["same_holdout_formula_reuse_forbidden"] is True
    assert verify_holdout_result(result_path)["status"] == "verified"
    with pytest.raises(HoldoutContractError, match="already_consumed"):
        ValidationRedTeamAgent(capability_path, sha256_file(capability_path)).evaluate()


def test_holdout_policy_is_stratum_specific_and_cannot_fallback(tmp_path):
    pool_path, _, policy_path, policy, view_path = _sealed_inputs(tmp_path)
    changed = replace(policy, profile=replace(policy.profile, holding_period_days=5))
    changed_policy_path, _ = publish_holdout_policy(changed, tmp_path / "changed_policy")
    registry = HoldoutCapabilityRegistry(tmp_path / "registry")
    with pytest.raises(HoldoutContractError, match="profile_mismatch"):
        registry.issue(
            candidate_pool_manifest_path=pool_path,
            holdout_view_manifest_path=view_path,
            holdout_policy_path=changed_policy_path,
            red_team_output_root=tmp_path / "red_team",
        )
    assert policy_path.exists()


def test_holdout_result_tampering_is_detected(tmp_path):
    pool_path, _, policy_path, _, view_path = _sealed_inputs(tmp_path)
    registry = HoldoutCapabilityRegistry(tmp_path / "registry")
    capability_path, _ = registry.issue(
        candidate_pool_manifest_path=pool_path,
        holdout_view_manifest_path=view_path,
        holdout_policy_path=policy_path,
        red_team_output_root=tmp_path / "red_team",
    )
    result_path, _ = ValidationRedTeamAgent(capability_path, sha256_file(capability_path)).evaluate()
    rows_path = result_path.parent / "sealed_holdout_candidate_results.jsonl"
    rows = rows_path.read_text(encoding="utf-8").splitlines()
    payload = json.loads(rows[0])
    payload["metrics"]["median_rank_ic"] = -99.0
    rows[0] = json.dumps(payload, sort_keys=True)
    rows_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    with pytest.raises(HoldoutContractError, match="rows_hash_mismatch"):
        verify_holdout_result(result_path)


def test_same_holdout_view_cannot_be_reissued(tmp_path):
    pool_path, _, policy_path, _, view_path = _sealed_inputs(tmp_path)
    registry = HoldoutCapabilityRegistry(tmp_path / "registry")
    registry.issue(
        candidate_pool_manifest_path=pool_path,
        holdout_view_manifest_path=view_path,
        holdout_policy_path=policy_path,
        red_team_output_root=tmp_path / "red_team",
    )
    with pytest.raises(HoldoutContractError, match="holdout_view_already_registered"):
        registry.issue(
            candidate_pool_manifest_path=pool_path,
            holdout_view_manifest_path=view_path,
            holdout_policy_path=policy_path,
            red_team_output_root=tmp_path / "second_red_team",
        )


def test_target_tail_must_be_unavailable_nan(tmp_path):
    pool_path, _, policy_path, _, view_path = _sealed_inputs(tmp_path)
    target_path = view_path.parent / "target_return.npy"
    target = np.load(target_path)
    target[:, -1] = 0.0
    np.save(target_path, target, allow_pickle=False)
    view = json.loads(view_path.read_text(encoding="utf-8"))
    for row in view["artifact_catalog"]:
        if row["role"] == "target_return":
            row["sha256"] = sha256_file(target_path)
    core = {field: view.get(field) for field in VIEW_CORE_FIELDS}
    view["content_hash"] = stable_hash(core)
    atomic_json(view_path, view)
    registry = HoldoutCapabilityRegistry(tmp_path / "registry")
    capability_path, _ = registry.issue(
        candidate_pool_manifest_path=pool_path,
        holdout_view_manifest_path=view_path,
        holdout_policy_path=policy_path,
        red_team_output_root=tmp_path / "red_team",
    )
    with pytest.raises(HoldoutContractError, match="unavailable_target_not_nan"):
        ValidationRedTeamAgent(capability_path, sha256_file(capability_path)).evaluate()


def test_schema_dashboard_monitoring_and_search_feedback_firewall(tmp_path):
    pool_path, _, policy_path, _, view_path = _sealed_inputs(tmp_path)
    registry = HoldoutCapabilityRegistry(tmp_path / "registry")
    capability_path, _ = registry.issue(
        candidate_pool_manifest_path=pool_path,
        holdout_view_manifest_path=view_path,
        holdout_policy_path=policy_path,
        red_team_output_root=tmp_path / "validation_red_team",
    )
    result_path, _ = ValidationRedTeamAgent(capability_path, sha256_file(capability_path)).evaluate()
    paths = [
        pool_path,
        policy_path,
        view_path,
        capability_path,
        result_path,
        result_path.parent / "sealed_holdout_candidate_results.jsonl",
        result_path.parent / "candidate_holdout_archive.jsonl",
        tmp_path / "validation_red_team" / "holdout_feedback_forbidden.json",
    ]
    assert all(validate_artifact(path).valid for path in paths)
    service = AshareDashboardService(
        DashboardConfig(
            report_dir=tmp_path / "reports",
            validation_red_team_dir=tmp_path,
        )
    )
    assert service.load_sealed_holdout_candidate_pool()["candidate_count"] == 2
    assert service.load_sealed_holdout_result_manifest()["terminal_count"] == 2
    assert len(service.load_sealed_holdout_candidate_results()) == 2
    metrics, alerts = check_sealed_holdout_validation(pool_path, result_path)
    assert metrics["sealed_holdout_terminal_count"] == 2
    assert not alerts
    forbidden_corpus = result_path.parent / "copied_holdout_metrics.jsonl"
    forbidden_corpus.write_text("{}\n", encoding="utf-8")
    output_dir = tmp_path / "search_output"
    with pytest.raises(RuntimeError, match="sealed_holdout_feedback_path_forbidden"):
        AlphaFactoryRunner(
            AlphaCampaignConfig(
                campaign_name="feedback_attack",
                data_dir=str(tmp_path / "data"),
                output_dir=str(output_dir),
                factor_store_dir=str(tmp_path / "factor_store"),
                formula_corpus_path=str(forbidden_corpus),
            )
        )
    assert not output_dir.exists()


def test_contaminated_source_freeze_preflight_stays_blocked(tmp_path, monkeypatch):
    freeze_manifest = tmp_path / "source_freeze_manifest.json"
    freeze_manifest.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "auto_alpha.validation.walk_forward.red_team_preflight.validate_source_freeze_generation",
        lambda path: {
            "content_hash": "a" * 64,
            "sealed_holdout": {
                "period": "20250101-20260630",
                "historically_observed": True,
                "untouched": False,
            },
            "certification_ready": False,
        },
    )
    path, payload = preflight_source_holdout(freeze_manifest, tmp_path / "preflight")
    assert payload["status"] == "blocked"
    assert payload["holdout_capability_issuable"] is False
    assert payload["holdout_market_values_read"] is False
    assert "sealed_period_already_observed" in payload["blockers"]
    assert validate_artifact(path).valid


def test_holdout_windows_cannot_select_favorable_subset(tmp_path):
    pool_path, _, policy_path, _, view_path = _sealed_inputs(tmp_path)
    view = json.loads(view_path.read_text(encoding="utf-8"))
    view["windows"] = view["windows"][:2]
    core = {field: view.get(field) for field in VIEW_CORE_FIELDS}
    view["content_hash"] = stable_hash(core)
    atomic_json(view_path, view)
    registry = HoldoutCapabilityRegistry(tmp_path / "registry")
    capability_path, _ = registry.issue(
        candidate_pool_manifest_path=pool_path,
        holdout_view_manifest_path=view_path,
        holdout_policy_path=policy_path,
        red_team_output_root=tmp_path / "red_team",
    )
    with pytest.raises(HoldoutContractError, match="window_count_below_locked_policy"):
        ValidationRedTeamAgent(capability_path, sha256_file(capability_path)).evaluate()


def _sealed_inputs(tmp_path):
    campaign, materializations = _campaign_fixture(tmp_path)
    pool_path, pool = freeze_candidate_pool(campaign, materializations, tmp_path / "pool")
    profile = HoldoutCalibrationProfile(
        universe_name="CSI300_TEST",
        holding_period_days=2,
        neutralization_method="raw",
        rebalance_frequency="daily",
        window_size=4,
        min_cross_section_breadth=10,
        modeled_cost_bps=1.0,
        placebo_trials=8,
        min_placebo_percentile=0.75,
    )
    policy = SealedHoldoutPolicy("sealed_holdout_initial_calibrated_v1", profile)
    policy_path, _ = publish_holdout_policy(policy, tmp_path / "policy")
    view_path = _holdout_view_fixture(tmp_path / "view", pool["content_hash"], profile)
    return pool_path, pool, policy_path, policy, view_path


def _campaign_fixture(tmp_path):
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    feature_version = "holdout_test_features_v1"
    operator_version = "stackvm_v1"
    candidates = []
    materializations = []
    stock_count, research_dates = 40, 8
    signal = np.linspace(-1.0, 1.0, stock_count, dtype=np.float32)[:, None]
    for rank, name in enumerate(("FEATURE_POS", "FEATURE_NEG"), start=1):
        tokens = [rank - 1]
        formula_names = [name]
        formula_hash = stable_formula_hash(tokens, formula_names, feature_version, operator_version)
        factor_id = make_factor_id(formula_hash)
        candidates.append(
            {
                "alpha_candidate_id": f"candidate_{rank}",
                "formula_hash": formula_hash,
                "formula_tokens": tokens,
                "formula_names": formula_names,
                "feature_version": feature_version,
                "operator_version": operator_version,
                "complexity": 1,
                "lookback": 0,
                "family_tags": ["test"],
                "final_score": 1.0 - rank * 0.1,
                "status": "shortlisted",
            }
        )
        materialization_dir = tmp_path / "materializations" / factor_id
        materialization_dir.mkdir(parents=True)
        values = np.broadcast_to(signal if rank == 1 else -signal, (stock_count, research_dates)).copy()
        validity = np.ones_like(values, dtype=np.bool_)
        np.save(materialization_dir / "values.npy", values.astype(np.float32), allow_pickle=False)
        np.save(materialization_dir / "validity.npy", validity, allow_pickle=False)
        manifest = attach_artifact_metadata(
            {
                "factor_id": factor_id,
                "formula": formula_names,
                "formula_tokens": tokens,
                "formula_hash": formula_hash,
                "factor_identity": {"complexity": 1, "effective_lookback": 0, "required_observations": 1},
                "operator_version": operator_version,
                "feature_version": feature_version,
                "transform_method": "raw",
                "materialization_status": "success",
                "shape": list(values.shape),
                "dtype": "float32",
                "validity_dtype": "bool",
                "stock_axis_hash": "a" * 64,
                "date_axis_hash": "b" * 64,
                "value_sha256": sha256_file(materialization_dir / "values.npy"),
                "validity_sha256": sha256_file(materialization_dir / "validity.npy"),
            },
            "factor_materialization_manifest",
            "validation_lab",
        )
        manifest_path = materialization_dir / "materialization_manifest.json"
        atomic_json(manifest_path, manifest)
        materializations.append(manifest_path)
    campaign_manifest = write_json_artifact(
        campaign / "alpha_campaign_manifest.json",
        {"campaign_id": "campaign", "campaign_name": "test", "feature_set_name": feature_version, "generator_budgets": {"total": 2}},
        "alpha_campaign_manifest",
        "alpha_factory",
    )
    selection_policy_hash = "c" * 64
    research_policy = write_json_artifact(
        campaign / "alpha_research_policy.json",
        {"policy_id": "alpha_factory_two_stage_oos_v1", "policy_hash": selection_policy_hash, "policy": {}, "parameters_locked": True},
        "alpha_research_policy",
        "alpha_factory",
    )
    full_rows = [
        {
            "alpha_candidate_id": row["alpha_candidate_id"],
            "factor_id": make_factor_id(row["formula_hash"]),
            "formula_hash": row["formula_hash"],
            "request": {"formula_hash": row["formula_hash"], "formula_tokens": row["formula_tokens"], "formula_names": row["formula_names"]},
            "status": "validation_candidate",
            "score": row["final_score"],
            "validation_summary": {"mean_rank_ic": 0.01},
            "gate_decision": {"passed": True, "checks": {"oos_evidence_positive": True}},
            "placebo": {"percentile": 0.9},
            "regime": {"pass_ratio": 1.0},
            "lineage_hash": "d" * 64,
        }
        for row in candidates
    ]
    full_results = write_jsonl_artifact(campaign / "alpha_full_eval_results.jsonl", full_rows, "alpha_full_eval_results", "alpha_factory")
    full_summary = write_json_artifact(
        campaign / "alpha_full_eval_summary.json",
        {
            "enabled": True,
            "evaluated": 2,
            "research_policy_id": "alpha_factory_two_stage_oos_v1",
            "research_policy_hash": selection_policy_hash,
            "score_method": "dimensionless_cohort_multi_objective_v1",
            "normalization": {},
            "multiple_testing": {},
            "selection_bias": {},
            "certification_ready": False,
        },
        "alpha_full_eval_summary",
        "alpha_factory",
    )
    shortlist = write_jsonl_artifact(campaign / "alpha_shortlist.jsonl", candidates, "alpha_shortlist", "alpha_factory")
    trials = write_jsonl_artifact(
        campaign / "alpha_trial_ledger.jsonl",
        [
            {"trial_ordinal": index, "alpha_candidate_id": row["alpha_candidate_id"], "formula_hash": row["formula_hash"], "final_status": "validation_candidate"}
            for index, row in enumerate(candidates)
        ],
        "alpha_trial_ledger",
        "alpha_factory",
    )
    paths = {
        "alpha_campaign_manifest_path": str(campaign_manifest),
        "alpha_research_policy_path": str(research_policy),
        "alpha_full_eval_results_path": str(full_results),
        "alpha_full_eval_summary_path": str(full_summary),
        "alpha_shortlist_path": str(shortlist),
        "alpha_trial_ledger_path": str(trials),
    }
    report = write_json_artifact(
        campaign / "alpha_factory_report.json",
        {"campaign_id": "campaign", "status": "success", "summary": {"total_trials": 2}, "paths": paths},
        "alpha_factory_report",
        "alpha_factory",
    )
    return report, materializations


def _holdout_view_fixture(root: Path, candidate_pool_root: str, profile: HoldoutCalibrationProfile):
    root.mkdir(parents=True)
    stocks, dates = 40, 14
    trade_dates = [f"20250{month}{day:02d}" for month in (1, 2) for day in range(1, 8)]
    ts_codes = [f"{index:06d}.SH" for index in range(stocks)]
    signal = np.linspace(-1.0, 1.0, stocks, dtype=np.float32)[:, None]
    positive = np.broadcast_to(signal, (stocks, dates)).copy()
    negative = -positive
    features = np.stack([positive, negative], axis=1).astype(np.float32)
    feature_validity = np.ones_like(features, dtype=np.bool_)
    target = (0.01 * positive).astype(np.float32)
    target_available = np.ones((stocks, dates), dtype=np.bool_)
    target_available[:, -2:] = False
    target[:, -2:] = np.nan
    evaluation = np.ones(dates, dtype=np.bool_)
    evaluation[-2:] = False
    common = np.ones((stocks, dates), dtype=np.bool_)
    amount = np.full((stocks, dates), 100_000_000.0, dtype=np.float32)
    regime_masks = np.zeros((2, dates), dtype=np.bool_)
    regime_masks[0, :6] = True
    regime_masks[1, 6:12] = True
    universe_masks = np.ones((2, stocks, dates), dtype=np.bool_)
    feature_manifest_payload = {
        "feature_set_name": "holdout_test_features_v1",
        "feature_set_version": "1",
        "feature_version": "holdout_test_features_v1",
        "operator_version": "stackvm_v1",
        "feature_count": 2,
        "feature_definitions": [
            {"feature_name": "FEATURE_POS", "feature_version": "1", "family": "test", "source_fields": ["close"], "tensor_key": "feature_pos"},
            {"feature_name": "FEATURE_NEG", "feature_version": "1", "family": "test", "source_fields": ["close"], "tensor_key": "feature_neg"},
        ],
        "data_freeze_id": "freeze",
        "data_freeze_hash": "e" * 64,
        "point_in_time": True,
        "corporate_action_aware": True,
        "target_return_mode": "next_open_t1_t2",
        "created_at": "2025-01-01T00:00:00Z",
        "content_hash": "f" * 64,
    }
    (root / "trade_dates.json").write_text(json.dumps(trade_dates), encoding="utf-8")
    (root / "ts_codes.json").write_text(json.dumps(ts_codes), encoding="utf-8")
    (root / "feature_manifest.json").write_text(json.dumps(feature_manifest_payload), encoding="utf-8")
    (root / "regime_names.json").write_text(json.dumps(["early", "late"]), encoding="utf-8")
    (root / "universe_names.json").write_text(json.dumps(["all_a", "all_b"]), encoding="utf-8")
    arrays = {
        "feature_tensor": features,
        "feature_validity": feature_validity,
        "target_return": target,
        "target_available": target_available,
        "signal_candidate_cells": common,
        "membership": common,
        "active": common,
        "evaluation_date_mask": evaluation,
        "amount": amount,
        "regime_date_masks": regime_masks,
        "universe_masks": universe_masks,
    }
    for name, array in arrays.items():
        np.save(root / f"{name}.npy", array, allow_pickle=False)
    catalog = []
    json_roles = {
        "trade_dates": "trade_dates.json",
        "ts_codes": "ts_codes.json",
        "feature_manifest": "feature_manifest.json",
        "regime_names": "regime_names.json",
        "universe_names": "universe_names.json",
    }
    for role, filename in json_roles.items():
        catalog.append({"role": role, "relative_path": filename, "sha256": sha256_file(root / filename)})
    for role, array in arrays.items():
        filename = f"{role}.npy"
        catalog.append({"role": role, "relative_path": filename, "sha256": sha256_file(root / filename), "shape": list(array.shape), "dtype": str(array.dtype)})
    windows = [
        {"window_id": "w1", "start_date": trade_dates[0], "end_date": trade_dates[3]},
        {"window_id": "w2", "start_date": trade_dates[4], "end_date": trade_dates[7]},
        {"window_id": "w3", "start_date": trade_dates[8], "end_date": trade_dates[11]},
    ]
    core = {
        "status": "sealed",
        "view_id": "future_holdout_test",
        "evidence_level": "future_untouched_holdout",
        "untouched": True,
        "historically_observed": False,
        "selection_data_reused": False,
        "candidate_pool_root": candidate_pool_root,
        "observation_boundary_seal_hash": "1" * 64,
        "freeze_content_hash": "2" * 64,
        "holdout_start_date": trade_dates[0],
        "holdout_end_date": trade_dates[-1],
        "max_target_endpoint_date": trade_dates[-1],
        "label_horizon": 2,
        "profile": {
            "universe_name": profile.universe_name,
            "holding_period_days": profile.holding_period_days,
            "neutralization_method": profile.neutralization_method,
            "rebalance_frequency": profile.rebalance_frequency,
        },
        "windows": windows,
        "artifact_catalog": catalog,
        "stock_axis_hash": stable_hash(ts_codes),
        "date_axis_hash": stable_hash(trade_dates),
        "feature_axis_hash": stable_hash(["FEATURE_POS", "FEATURE_NEG"]),
        "search_principal_access_count": 0,
        "feedback_to_search_forbidden": True,
        "pit_validation_status": "passed",
        "leakage_blocker_count": 0,
        "certified_factor_count": 0,
    }
    payload = attach_artifact_metadata({**core, "content_hash": stable_hash(core)}, "sealed_holdout_view", "validation_red_team_fixture")
    path = root / "sealed_holdout_view_manifest.json"
    atomic_json(path, payload)
    return path


def _jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

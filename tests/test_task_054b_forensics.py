from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from artifact_schema.validator import validate_artifact
from factor_store.hash import make_factor_id, stable_formula_hash
from feature_factory.models import FeatureSetManifest
from feature_factory.vocab_adapter import make_formula_vocab_from_manifest
from task_054_b.forensics import ForensicConfig, run_selection_impact_forensic


def test_selection_forensic_reconstructs_frozen_scores_and_publishes_immutable_overlay(tmp_path):
    campaign, feature_manifest, candidates = _campaign_fixture(tmp_path)
    source_store = campaign / "consolidated_factor_store" / "factors.jsonl"
    source_sha = _sha(source_store)
    output = tmp_path / "task054b"

    result = run_selection_impact_forensic(
        ForensicConfig(
            campaign_root=str(campaign),
            feature_manifest_path=str(feature_manifest),
            output_root=str(output),
            expected_unique_candidates=2,
        )
    )

    assert result["unique_candidate_count"] == 2
    assert result["historical_selection_reconstructed"] is True
    assert result["fixed_probe_count"] == 2
    assert result["fixed_probe_selection_policy_violation_count"] == 1
    assert result["shortlist_membership_change_count"] == 2
    assert result["canonical_lookback_changed_count"] == 1
    assert result["score_changed_count"] == 1
    assert result["feature_contract_count"] == 2
    assert result["target_or_outcome_read"] is False
    assert _sha(source_store) == source_sha

    rows = _read_jsonl(Path(result["candidate_artifact_path"]))
    by_id = {row["alpha_candidate_id"]: row for row in rows}
    simple = by_id[candidates[0]["alpha_candidate_id"]]
    nested = by_id[candidates[1]["alpha_candidate_id"]]
    assert simple["canonical_max_raw_lag"] == 1
    assert simple["canonical_required_observations"] == 2
    assert nested["canonical_max_raw_lag"] == 10
    assert nested["canonical_required_observations"] == 11
    assert [step["node"] for step in nested["longest_dependency_path"]][-2:] == ["RET_5D", "DELTA5"]
    assert nested["selection_policy_violation"] is True
    assert nested["policy_terminal_state"] == "selection_policy_violation"
    assert nested["formula_identity_preserved"] is True
    assert nested["selection_identity_preserved"] is False

    overlay = Path(result["normalized_overlay_dir"])
    overlay_rows = _read_jsonl(overlay / "normalized_factors.jsonl")
    assert len(overlay_rows) == 2
    assert {row["formula_hash"] for row in overlay_rows} == {row["formula_hash"] for row in candidates}
    assert all(row["migration_reason"] == "canonical_recursive_dependency_semantics" for row in overlay_rows)
    assert validate_artifact(result["candidate_artifact_path"], strict=True).valid
    assert validate_artifact(result["report_path"], strict=True).valid
    assert validate_artifact(overlay / "overlay_manifest.json", strict=True).valid
    assert validate_artifact(overlay / "normalized_factors.jsonl", strict=True).valid

    replay = run_selection_impact_forensic(
        ForensicConfig(str(campaign), str(feature_manifest), str(output), expected_unique_candidates=2)
    )
    assert replay["normalized_overlay"]["content_hash"] == result["normalized_overlay"]["content_hash"]
    assert replay["normalized_overlay_dir"] == result["normalized_overlay_dir"]


def test_selection_forensic_fails_closed_on_count_or_identity_drift(tmp_path):
    campaign, feature_manifest, _ = _campaign_fixture(tmp_path)
    with pytest.raises(RuntimeError, match="unique_candidate_count_mismatch"):
        run_selection_impact_forensic(
            ForensicConfig(str(campaign), str(feature_manifest), str(tmp_path / "bad-count"), expected_unique_candidates=3)
        )

    candidates_path = campaign / "alpha_factory" / "alpha_candidates.jsonl"
    rows = _read_jsonl(candidates_path)
    rows[0]["formula_hash"] = "f" * 64
    _write_jsonl(candidates_path, rows)
    result = run_selection_impact_forensic(
        ForensicConfig(str(campaign), str(feature_manifest), str(tmp_path / "bad-identity"), expected_unique_candidates=2)
    )
    assert result["status"] == "blocked"
    assert result["formula_identity_preserved_count"] == 1
    assert "formula_identity_mismatch" in result["blockers"]


def _campaign_fixture(root: Path):
    campaign = root / "campaign"
    alpha = campaign / "alpha_factory"
    store = campaign / "consolidated_factor_store"
    alpha.mkdir(parents=True)
    store.mkdir(parents=True)
    feature_manifest_path = root / "feature_set_manifest.json"
    definitions = [
        {
            "feature_name": "RET_1D",
            "feature_version": "ashare_features_v3",
            "source_fields": ["adjusted_close"],
            "lookback": 2,
            "transform": "identity",
            "dependency_graph": {
                "version": "test",
                "feature_name": "RET_1D",
                "source_fields": ["adjusted_close"],
                "dependencies": [{"field": "adjusted_close", "offsets": [0, -1], "history": 2}],
                "effective_lookback": 2,
                "max_raw_lag": 1,
                "required_observations": 2,
            },
        },
        {
            "feature_name": "RET_5D",
            "feature_version": "ashare_features_v3",
            "source_fields": ["adjusted_close"],
            "lookback": 6,
            "transform": "identity",
            "dependency_graph": {
                "version": "test",
                "feature_name": "RET_5D",
                "source_fields": ["adjusted_close"],
                "dependencies": [{"field": "adjusted_close", "offsets": [0, -5], "history": 6}],
                "effective_lookback": 6,
                "max_raw_lag": 5,
                "required_observations": 6,
            },
        },
    ]
    manifest = FeatureSetManifest(
        feature_set_name="ashare_features_v3",
        feature_set_version="3.0",
        feature_version="ashare_features_v3",
        operator_version="ashare_ops_v1",
        feature_count=2,
        feature_definitions=definitions,
        data_freeze_id="freeze_test",
        data_freeze_hash="1" * 64,
        point_in_time=True,
        corporate_action_aware=True,
        target_return_mode="adjusted_open_t1_t2",
        created_at="2026-07-14T00:00:00Z",
        content_hash="2" * 64,
    )
    feature_manifest_path.write_text(json.dumps(manifest.to_dict()), encoding="utf-8")
    vocab = make_formula_vocab_from_manifest(manifest)
    delta5 = vocab.encode_name("DELTA5")
    formulas = [([0], ["RET_1D"], 1, 1), ([1, delta5], ["RET_5D", "DELTA5"], 2, 5)]
    candidates = []
    for tokens, names, complexity, lookback in formulas:
        formula_hash = stable_formula_hash(tokens, names, manifest.feature_version, manifest.operator_version)
        candidates.append(
            {
                "alpha_candidate_id": f"alpha_{formula_hash[:16]}",
                "formula_hash": formula_hash,
                "formula_tokens": tokens,
                "formula_names": names,
                "source": "fixture",
                "source_refs": [],
                "feature_set_name": manifest.feature_set_name,
                "feature_version": manifest.feature_version,
                "operator_version": manifest.operator_version,
                "complexity": complexity,
                "lookback": lookback,
                "family_tags": ["price_return"],
                "status": "generated",
            }
        )
    _write_jsonl(alpha / "alpha_candidates.jsonl", candidates)
    components = [
        {"full_eval_score": 0.5, "proxy_percentile": 0.0, "novelty_score": 0.0, "complexity_penalty": 0.01, "lookback_penalty": 0.002},
        {"full_eval_score": 1.0, "proxy_percentile": 0.0, "novelty_score": 0.0, "complexity_penalty": 0.02, "lookback_penalty": 0.01},
    ]
    scored = []
    for candidate, score_components in zip(candidates, components):
        final = score_components["full_eval_score"] - score_components["complexity_penalty"] - score_components["lookback_penalty"]
        scored.append(candidate | {"final_score": final, "novelty_score": 0.0, "status": "scored", "score_components": score_components})
    _write_jsonl(alpha / "alpha_scored_candidates.jsonl", scored)
    _write_jsonl(
        alpha / "alpha_static_checks.jsonl",
        [{"alpha_candidate_id": row["alpha_candidate_id"], "status": "passed", "errors": []} for row in candidates],
    )
    _write_jsonl(alpha / "alpha_shortlist.jsonl", [scored[1]])
    (alpha / "alpha_generation_stats.json").write_text(json.dumps({"generated": 2}), encoding="utf-8")
    (alpha / "alpha_campaign_manifest.json").write_text(
        json.dumps(
            {
                "campaign_id": "campaign_fixture",
                "config_snapshot": {"max_lookback": 6, "max_complexity": 10, "top_k": 1, "max_per_family": 1, "min_novelty_score": 0.0},
            }
        ),
        encoding="utf-8",
    )
    factor_rows = [
        {
            "factor_id": make_factor_id(row["formula_hash"]),
            "formula": row["formula_names"],
            "formula_tokens": row["formula_tokens"],
            "formula_hash": row["formula_hash"],
            "feature_version": row["feature_version"],
            "operator_version": row["operator_version"],
            "lookback_days": row["lookback"],
            "created_at": "2026-07-14T00:00:00Z",
        }
        for row in candidates
    ]
    _write_jsonl(store / "factors.jsonl", factor_rows)
    catalog_entries = {
        "alpha_campaign_manifest_path": alpha / "alpha_campaign_manifest.json",
        "alpha_generation_stats_path": alpha / "alpha_generation_stats.json",
        "alpha_candidates_path": alpha / "alpha_candidates.jsonl",
        "alpha_scored_candidates_path": alpha / "alpha_scored_candidates.jsonl",
        "alpha_static_checks_path": alpha / "alpha_static_checks.jsonl",
        "alpha_shortlist_path": alpha / "alpha_shortlist.jsonl",
    }
    (alpha / "alpha_campaign_artifact_catalog.json").write_text(
        json.dumps({"entries": [{"name": name, "path": str(path)} for name, path in catalog_entries.items()]}),
        encoding="utf-8",
    )
    return campaign, feature_manifest_path, candidates


def _write_jsonl(path: Path, rows):
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _sha(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

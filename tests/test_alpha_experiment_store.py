import json

from alpha_experiment_store import LocalAlphaExperimentStore, consolidate_factor_stores
from alpha_experiment_store.leaderboard import build_leaderboard_from_factor_store, write_validation_candidate_pool
from alpha_experiment_store.run_store import main as alpha_store_main
from artifact_schema.run_validate import main as validate_artifacts_main
from factor_store import FactorRecord, LocalFactorStore


def _write_factor(store_dir, factor_id, formula_hash, status, score):
    store = LocalFactorStore(store_dir)
    store.save_factor(
        FactorRecord(
            factor_id=factor_id,
            formula=["RET_1D"],
            formula_tokens=[0],
            formula_hash=formula_hash,
            feature_version="ashare_features_v1",
            operator_version="ashare_ops_v1",
            lookback_days=1,
            created_at="2026-07-03T00:00:00Z",
            status=status,
            metrics={"score": score, "coverage": 1.0, "turnover": 0.1},
            metadata={"formula_complexity": 1, "novelty_score": 0.2, "alpha_family_tags": ["return"]},
        )
    )
    store.save_factor_values(factor_id, ["000001.SZ"], ["20240102", "20240103"], [[1.0, 2.0]])


def test_alpha_experiment_store_consolidates_dedupes_and_writes_pool(tmp_path):
    shard_a = tmp_path / "shard_a" / "factor_store"
    shard_b = tmp_path / "shard_b" / "factor_store"
    _write_factor(shard_a, "factor_dup_a", "hash_dup", "candidate", 0.2)
    _write_factor(shard_b, "factor_dup_b", "hash_dup", "approved", 0.8)
    _write_factor(shard_b, "factor_unique", "hash_unique", "approved", 0.5)

    merged = tmp_path / "merged"
    report = consolidate_factor_stores([shard_a, shard_b], merged, experiment_id="exp1", campaign_id="camp1", report_dir=tmp_path / "store")
    assert report["input_shard_count"] == 2
    assert report["input_factor_count"] == 3
    assert report["duplicate_count"] == 1
    assert report["merged_factor_count"] == 2

    factors = LocalFactorStore(merged).load_factors()
    assert len(factors) == 2
    assert any(item.formula_hash == "hash_dup" and item.status == "approved" for item in factors)

    store = LocalAlphaExperimentStore(tmp_path / "store")
    store.write_consolidated_factors(report["consolidated_factors"])
    leaderboard = build_leaderboard_from_factor_store(merged, top_k=10, campaign_id="camp1")
    store.write_leaderboard(leaderboard)
    pool_path, pool_rows = write_validation_candidate_pool(leaderboard, tmp_path / "store", max_candidates=2, factor_store_dir=str(merged))
    store.write_validation_candidate_pool(pool_rows)

    assert len(leaderboard) == 2
    assert leaderboard[0].final_score >= leaderboard[1].final_score
    assert len(pool_rows) == 2
    assert pool_path.exists()
    assert pool_rows[0]["factor_store_dir"] == str(merged)


def test_alpha_experiment_store_cli_smoke_and_schema(tmp_path, capsys):
    exit_code = alpha_store_main(["smoke", "--output-dir", str(tmp_path / "alpha_store"), "--pretty"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["merged_factor_count"] >= 2
    assert payload["leaderboard_count"] >= 2
    assert payload["validation_candidate_count"] >= 1

    schema_exit = validate_artifacts_main(
        [
            "--artifact-dir",
            str(tmp_path / "alpha_store"),
            "--output-dir",
            str(tmp_path / "schema"),
            "--write-manifest",
            "--pretty",
        ]
    )
    schema_payload = json.loads(capsys.readouterr().out)
    assert schema_exit == 0
    assert schema_payload["error_count"] == 0

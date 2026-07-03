import json

from artifact_schema.run_validate import main as artifact_validate_main
from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from factor_certification.run_certify import main as certify_main
from factor_store import FactorRecord, LocalFactorStore, stable_formula_hash
from model_core.data_loader import AShareDataLoader
from monitoring.checks import (
    check_factor_certification_queue,
    check_validation_campaign_leaderboard,
    check_validation_campaign_store,
)
from validation_campaign_store.run_validation_store import main as validation_store_main


def _prepare_candidate_pool(tmp_path):
    data_dir = tmp_path / "data"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True)
    loader = AShareDataLoader(data_dir=data_dir, device="cpu").load_data()
    store = LocalFactorStore(tmp_path / "store")
    rows = []
    for idx, name in enumerate(["RET_1D", "RET_5D"]):
        tokens = [idx]
        formula_hash = stable_formula_hash(tokens, [name], "ashare_features_v1", "ashare_ops_v1")
        factor_id = f"factor_validation_{idx}"
        store.save_factor(
            FactorRecord(
                factor_id=factor_id,
                formula=[name],
                formula_tokens=tokens,
                formula_hash=formula_hash,
                feature_version="ashare_features_v1",
                operator_version="ashare_ops_v1",
                lookback_days=1,
                created_at="2026-06-28T00:00:00Z",
                status="approved",
                metrics={"score": 0.5 - idx * 0.1, "rank_ic": 0.1},
                factor_type="single",
            )
        )
        values = [
            [float(stock_idx + date_idx + idx + 1) for date_idx, _date in enumerate(loader.trade_dates)]
            for stock_idx, _code in enumerate(loader.ts_codes)
        ]
        store.save_factor_values(factor_id, loader.ts_codes, loader.trade_dates, values)
        rows.append(
            {
                "factor_id": factor_id,
                "formula_hash": formula_hash,
                "formula_names": [name],
                "feature_version": "ashare_features_v1",
                "operator_version": "ashare_ops_v1",
                "source_campaign": "alpha_unit",
                "rank": idx + 1,
                "final_score": 1.0 - idx * 0.1,
                "factor_store_dir": str(tmp_path / "store"),
                "factor_values_path": str(tmp_path / "store" / "factor_values" / f"{factor_id}.jsonl"),
                "family": "return" if idx == 0 else "momentum",
            }
        )
    pool_path = tmp_path / "alpha_validation_candidate_pool.jsonl"
    pool_path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8")
    return data_dir, tmp_path / "store", pool_path


def test_validation_campaign_store_end_to_end_and_certification_queue_dry_run(tmp_path, capsys):
    data_dir, store_dir, pool_path = _prepare_candidate_pool(tmp_path)
    campaign_dir = tmp_path / "validation_campaign_store"

    exit_code = validation_store_main(
        [
            "run",
            "--validation-campaign-store-dir",
            str(campaign_dir),
            "--source-candidate-pool-path",
            str(pool_path),
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(store_dir),
            "--output-dir",
            str(campaign_dir),
            "--shard-count",
            "2",
            "--max-candidates",
            "2",
            "--run-placebo",
            "--placebo-trials",
            "2",
            "--top-k-certification-queue",
            "2",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["shard_count"] == 2
    assert payload["candidate_count"] == 2
    assert payload["leaderboard_count"] == 2
    assert payload["certification_queue_count"] >= 1
    assert (campaign_dir / "validation_campaign_registry.json").exists()
    assert (campaign_dir / "validation_candidate_results.jsonl").exists()
    assert (campaign_dir / "validation_leaderboard.jsonl").exists()
    assert (campaign_dir / "factor_certification_queue.jsonl").exists()

    store_summary, store_alerts = check_validation_campaign_store(campaign_dir / "validation_campaign_store_report.json")
    leaderboard_summary, _leaderboard_alerts = check_validation_campaign_leaderboard(campaign_dir / "validation_leaderboard.jsonl")
    queue_summary, _queue_alerts = check_factor_certification_queue(campaign_dir / "factor_certification_queue.jsonl")
    assert store_summary["validation_result_count"] == 2
    assert leaderboard_summary["validation_leaderboard_count"] == 2
    assert queue_summary["certification_queue_count"] >= 1
    assert not [alert for alert in store_alerts if alert.severity == "error"]

    dry_code = certify_main(
        [
            "run",
            "--factor-store-dir",
            str(store_dir),
            "--output-dir",
            str(tmp_path / "certification"),
            "--certification-queue-path",
            str(campaign_dir / "factor_certification_queue.jsonl"),
            "--max-queue-items",
            "1",
            "--dry-run",
            "--pretty",
        ]
    )
    dry_payload = json.loads(capsys.readouterr().out)
    assert dry_code == 0
    assert dry_payload["dry_run"] is True
    assert dry_payload["selected_count"] == 1


def test_validation_campaign_store_schema_validation(tmp_path, capsys):
    _data_dir, _store_dir, pool_path = _prepare_candidate_pool(tmp_path)
    campaign_dir = tmp_path / "validation_campaign_store"
    assert validation_store_main(
        [
            "smoke",
            "--validation-campaign-store-dir",
            str(campaign_dir),
            "--output-dir",
            str(tmp_path / "smoke"),
        ]
    ) == 0
    capsys.readouterr()
    exit_code = artifact_validate_main(
        [
            "--artifact-dir",
            str(campaign_dir),
            "--output-dir",
            str(tmp_path / "schema"),
            "--write-manifest",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["error_count"] == 0
    assert (tmp_path / "schema" / "artifact_validation_report.json").exists()

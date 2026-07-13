import json

from artifact_schema.run_validate import main as artifact_validate_main
from certification_campaign_store.run_certification_campaign import main as certification_campaign_main
from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from factor_store import FactorRecord, LocalFactorStore, stable_formula_hash
from model_core.data_loader import AShareDataLoader
from monitoring.checks import (
    check_certified_factor_pool,
    check_factor_certification_campaign,
    check_optimizer_policy_activation_queue,
    check_portfolio_campaign,
    check_production_candidate_bundle,
)
from portfolio_campaign_store.run_portfolio_campaign import main as portfolio_campaign_main
from validation_campaign_store.run_validation_store import main as validation_campaign_main


def _prepare_validation_queue(tmp_path, capsys):
    data_dir = tmp_path / "data"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True)
    loader = AShareDataLoader(data_dir=data_dir, device="cpu").load_data()
    store_dir = tmp_path / "factor_store"
    store = LocalFactorStore(store_dir)
    rows = []
    for idx, name in enumerate(["RET_1D"]):
        tokens = [idx]
        formula_hash = stable_formula_hash(tokens, [name], "ashare_features_v1", "ashare_ops_v1")
        factor_id = f"factor_cert_campaign_{idx}"
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
                metrics={"score": 0.5, "rank_ic": 0.1},
                factor_type="single",
            )
        )
        values = [[float(stock_idx + date_idx + 1) for date_idx, _ in enumerate(loader.trade_dates)] for stock_idx, _ in enumerate(loader.ts_codes)]
        store.save_factor_values(factor_id, loader.ts_codes, loader.trade_dates, values)
        rows.append(
            {
                "factor_id": factor_id,
                "formula_hash": formula_hash,
                "formula_names": [name],
                "feature_version": "ashare_features_v1",
                "operator_version": "ashare_ops_v1",
                "source_campaign": "alpha_campaign_unit",
                "rank": idx + 1,
                "final_score": 1.0,
                "factor_store_dir": str(store_dir),
                "factor_values_path": str(store_dir / "factor_values" / f"{factor_id}.jsonl"),
                "family": "return",
            }
        )
    pool = tmp_path / "alpha_validation_candidate_pool.jsonl"
    pool.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8")
    validation_dir = tmp_path / "validation_campaign_store"
    assert validation_campaign_main(
        [
            "run",
            "--validation-campaign-store-dir",
            str(validation_dir),
            "--source-candidate-pool-path",
            str(pool),
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(store_dir),
            "--output-dir",
            str(validation_dir),
            "--shard-count",
            "1",
            "--max-candidates",
            "1",
            "--run-placebo",
            "--placebo-trials",
            "2",
            "--top-k-certification-queue",
            "1",
            "--pretty",
        ]
    ) == 0
    capsys.readouterr()
    queue_path = validation_dir / "factor_certification_queue.jsonl"
    assert queue_path.read_text(encoding="utf-8") == ""
    clean_queue = {
        "queue_id": "certq_clean_holdout_1",
        "validation_candidate_id": rows[0]["factor_id"],
        "factor_id": rows[0]["factor_id"],
        "priority": 1,
        "certification_policy_profile": "sample_lenient_certification",
        "validation_result_path": "",
        "factor_store_dir": str(store_dir),
        "status": "queued",
        "reason": "synthetic untouched-holdout certification fixture",
        "metadata": {"selection_data_reused": False, "untouched_holdout": True, "evidence_level": "clean_holdout_test_fixture"},
    }
    queue_path.write_text(json.dumps(clean_queue, sort_keys=True) + "\n", encoding="utf-8")
    return data_dir, store_dir, queue_path


def test_certification_and_portfolio_campaign_store_end_to_end(tmp_path, capsys):
    data_dir, store_dir, queue_path = _prepare_validation_queue(tmp_path, capsys)
    certification_dir = tmp_path / "factor_certification_campaign"
    assert certification_campaign_main(
        [
            "run",
            "--certification-campaign-store-dir",
            str(certification_dir),
            "--factor-certification-queue-path",
            str(queue_path),
            "--output-dir",
            str(certification_dir / "items"),
            "--max-items",
            "1",
            "--pretty",
        ]
    ) == 0
    cert_payload = json.loads(capsys.readouterr().out)
    assert cert_payload["certified_factor_pool_count"] == 0
    assert sum(1 for line in (certification_dir / "certified_factor_pool.jsonl").read_text().splitlines() if line.strip()) == 0

    portfolio_dir = tmp_path / "portfolio_campaign"
    assert portfolio_campaign_main(
        [
            "run",
            "--portfolio-campaign-store-dir",
            str(portfolio_dir),
            "--certified-factor-pool-path",
            str(certification_dir / "certified_factor_pool.jsonl"),
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(store_dir),
            "--output-dir",
            str(portfolio_dir / "items"),
            "--max-items",
            "1",
            "--max-trials",
            "1",
            "--pretty",
        ]
    ) == 0
    portfolio_payload = json.loads(capsys.readouterr().out)
    assert portfolio_payload["production_candidate_bundle_count"] == 0
    assert portfolio_payload["optimizer_policy_activation_queue_count"] == 0

    cert_summary, cert_alerts = check_factor_certification_campaign(certification_dir / "factor_certification_campaign_report.json")
    pool_summary, _ = check_certified_factor_pool(certification_dir / "certified_factor_pool.jsonl")
    portfolio_summary, _ = check_portfolio_campaign(portfolio_dir / "portfolio_certification_campaign_report.json")
    bundle_summary, _ = check_production_candidate_bundle(portfolio_dir / "production_candidate_bundle.jsonl")
    activation_summary, _ = check_optimizer_policy_activation_queue(portfolio_dir / "optimizer_policy_activation_queue.jsonl")
    assert cert_summary["certified_factor_pool_count"] == 0
    assert pool_summary["certified_factor_pool_count"] == 0
    assert portfolio_summary["production_candidate_bundle_count"] == 0
    assert bundle_summary["production_candidate_bundle_count"] == 0
    assert activation_summary["optimizer_policy_activation_queue_count"] == 0
    assert not [alert for alert in cert_alerts if alert.severity == "error"]

    assert artifact_validate_main(
        [
            "--artifact-dir",
            str(certification_dir),
            "--artifact-dir",
            str(portfolio_dir),
            "--output-dir",
            str(tmp_path / "schema"),
            "--write-manifest",
            "--pretty",
        ]
    ) == 0
    schema_payload = json.loads(capsys.readouterr().out)
    assert schema_payload["error_count"] == 0


def test_campaign_store_dry_run_smokes(tmp_path, capsys):
    cert_dir = tmp_path / "cert_smoke"
    assert certification_campaign_main(
        [
            "smoke",
            "--certification-campaign-store-dir",
            str(cert_dir),
            "--output-dir",
            str(tmp_path / "cert_src"),
            "--pretty",
        ]
    ) == 0
    cert_payload = json.loads(capsys.readouterr().out)
    assert cert_payload["item_count"] == 1

    portfolio_dir = tmp_path / "portfolio_smoke"
    assert portfolio_campaign_main(
        [
            "smoke",
            "--portfolio-campaign-store-dir",
            str(portfolio_dir),
            "--output-dir",
            str(tmp_path / "portfolio_src"),
            "--pretty",
        ]
    ) == 0
    portfolio_payload = json.loads(capsys.readouterr().out)
    assert portfolio_payload["item_count"] == 1

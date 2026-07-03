import json

from artifact_schema.run_validate import main as validate_artifacts_main
from alpha_factory import AlphaCampaignConfig, AlphaFactoryRunner
from alpha_factory.run_factory import main as run_factory_main
from dashboard.config import DashboardConfig
from dashboard.data_service import AshareDashboardService
from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from feature_factory import FEATURE_SET_V2, FEATURE_SET_V3, build_feature_set_manifest
from alpha_factory.templates import template_formulas
from formula_search.run_search import main as run_search_main


def _prepare_sample_data(tmp_path):
    data_dir = tmp_path / "data"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True)
    return data_dir


def test_alpha_factory_campaign_generates_multistage_shortlist_and_schema_validates(tmp_path, capsys):
    data_dir = _prepare_sample_data(tmp_path)
    output_dir = tmp_path / "alpha"
    result = AlphaFactoryRunner(
        AlphaCampaignConfig(
            campaign_name="unit_alpha",
            data_dir=str(data_dir),
            output_dir=str(output_dir),
            factor_store_dir=str(tmp_path / "store"),
            report_dir=str(tmp_path / "reports"),
            feature_set_name=FEATURE_SET_V2,
            build_feature_set=True,
            feature_output_dir=str(tmp_path / "features"),
            candidate_budget=12,
            template_budget=3,
            random_budget=3,
            mutation_budget=2,
            crossover_budget=2,
            corpus_budget=0,
            proxy_max_candidates=12,
            proxy_max_dates=3,
            top_k=4,
            max_per_family=2,
            seed=7,
        )
    ).run()

    assert result.status == "success"
    assert result.summary["alpha_campaign_id"] == result.campaign_id
    assert result.summary["candidates_generated"] == 12
    assert result.summary["shortlist_count"] > 0
    assert result.summary["feature_set_name"] == FEATURE_SET_V2
    assert result.summary["feature_count"] > 11
    assert (output_dir / "alpha_candidates.jsonl").exists()
    assert (output_dir / "alpha_static_checks.jsonl").exists()
    assert (output_dir / "alpha_proxy_eval.jsonl").exists()
    assert (output_dir / "alpha_shortlist.jsonl").exists()
    assert (tmp_path / "features" / "feature_set_manifest.json").exists()

    source_distribution = result.summary["source_distribution"]
    assert source_distribution.get("template", 0) > 0
    assert source_distribution.get("random", 0) > 0

    exit_code = validate_artifacts_main(
        [
            "--artifact-dir",
            str(output_dir),
            "--artifact-dir",
            str(tmp_path / "features"),
            "--output-dir",
            str(tmp_path / "schema"),
            "--write-manifest",
            "--pretty",
        ]
    )
    schema_payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert schema_payload["error_count"] == 0


def test_alpha_factory_v3_templates_and_batch_eval_smoke(tmp_path, capsys):
    data_dir = _prepare_sample_data(tmp_path)
    manifest = build_feature_set_manifest(FEATURE_SET_V3, created_at="2026-01-01T00:00:00Z")
    manifest_path = tmp_path / "feature_set_manifest.json"
    manifest_path.write_text(json.dumps(manifest.to_dict(), ensure_ascii=False), encoding="utf-8")
    template_names = {item["name"] for item in template_formulas(FEATURE_SET_V3, manifest)}

    assert "moneyflow_reversal_template" in template_names
    assert "margin_crowding_template" in template_names
    assert "financial_quality_template" in template_names
    assert "holder_concentration_template" not in template_names

    result = AlphaFactoryRunner(
        AlphaCampaignConfig(
            campaign_name="unit_alpha_v3",
            data_dir=str(data_dir),
            output_dir=str(tmp_path / "alpha_v3"),
            factor_store_dir=str(tmp_path / "store_v3"),
            report_dir=str(tmp_path / "reports_v3"),
            feature_set_name=FEATURE_SET_V3,
            feature_set_manifest_path=str(manifest_path),
            candidate_budget=12,
            template_budget=8,
            random_budget=0,
            mutation_budget=0,
            crossover_budget=0,
            corpus_budget=0,
            proxy_max_candidates=12,
            top_k=4,
            use_batch_eval=True,
            batch_eval_dir=str(tmp_path / "batch_eval_v3"),
            batch_eval_device="cpu",
            batch_eval_chunk_size=2,
            factor_transform="winsorize_zscore",
            min_coverage=0.5,
            seed=17,
        )
    ).run()

    assert result.status == "success"
    assert result.summary["feature_set_name"] == FEATURE_SET_V3
    assert result.summary["full_eval_count"] >= 0
    families = result.summary["family_distribution"]
    assert "moneyflow" in families
    assert "financial_statement" in families
    assert (tmp_path / "batch_eval_v3" / "formula_batch_eval_result.json").exists()


def test_alpha_factory_cli_with_full_eval_writes_batch_eval_lineage(tmp_path, capsys):
    data_dir = _prepare_sample_data(tmp_path)
    exit_code = run_factory_main(
        [
            "run",
            "--campaign-name",
            "unit_alpha_eval",
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(tmp_path / "store"),
            "--report-dir",
            str(tmp_path / "reports"),
            "--output-dir",
            str(tmp_path / "alpha_eval"),
            "--feature-set-name",
            FEATURE_SET_V2,
            "--build-feature-set",
            "--candidate-budget",
            "8",
            "--template-budget",
            "2",
            "--random-budget",
            "2",
            "--mutation-budget",
            "1",
            "--crossover-budget",
            "1",
            "--corpus-budget",
            "0",
            "--proxy-max-candidates",
            "8",
            "--top-k",
            "3",
            "--use-batch-eval",
            "--batch-eval-dir",
            str(tmp_path / "batch_eval"),
            "--batch-eval-device",
            "cpu",
            "--batch-eval-chunk-size",
            "2",
            "--register-shortlist",
            "--alpha-experiment-store-dir",
            str(tmp_path / "alpha_store"),
            "--register-experiment",
            "--consolidate-shards",
            "--consolidated-factor-store-dir",
            str(tmp_path / "consolidated_store"),
            "--write-leaderboard",
            "--validation-candidate-pool-dir",
            str(tmp_path / "validation_pool"),
            "--max-validation-candidates",
            "3",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["summary"]["full_eval_count"] >= 0
    assert payload["summary"]["alpha_campaign_id"] == payload["campaign_id"]
    assert (tmp_path / "batch_eval" / "formula_batch_eval_result.json").exists()
    eval_payload = json.loads((tmp_path / "batch_eval" / "formula_batch_eval_result.json").read_text(encoding="utf-8"))
    assert eval_payload["summary"]["total"] == payload["summary"]["full_eval_count"]
    assert (tmp_path / "alpha_eval" / "alpha_full_eval_summary.json").exists()
    assert (tmp_path / "alpha_store" / "alpha_experiment_registry.json").exists()
    assert (tmp_path / "alpha_store" / "alpha_experiment_store_report.json").exists()
    assert "alpha_experiment_store_report_path" in payload["paths"]


def test_alpha_factory_readiness_gate_blocks_without_loading_data(tmp_path, capsys):
    readiness_path = tmp_path / "readiness.json"
    readiness_path.write_text(json.dumps({"status": "blocked", "can_run_core_alpha_factory": False}), encoding="utf-8")
    exit_code = run_factory_main(
        [
            "run",
            "--campaign-name",
            "blocked_alpha",
            "--data-dir",
            str(tmp_path / "missing_data"),
            "--factor-store-dir",
            str(tmp_path / "store"),
            "--output-dir",
            str(tmp_path / "blocked"),
            "--research-readiness-decision-path",
            str(readiness_path),
            "--require-alpha-factory-ready",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["status"] == "blocked"
    assert payload["summary"]["research_readiness"]["ready"] is False
    assert (tmp_path / "blocked" / "alpha_factory_report.json").exists()


def test_formula_search_can_use_alpha_shortlist_as_seed(tmp_path, capsys):
    data_dir = _prepare_sample_data(tmp_path)
    alpha_result = AlphaFactoryRunner(
        AlphaCampaignConfig(
            campaign_name="unit_alpha_seed",
            data_dir=str(data_dir),
            output_dir=str(tmp_path / "alpha_seed"),
            factor_store_dir=str(tmp_path / "store"),
            feature_set_name=FEATURE_SET_V2,
            build_feature_set=True,
            candidate_budget=8,
            template_budget=2,
            random_budget=2,
            mutation_budget=1,
            crossover_budget=1,
            corpus_budget=0,
            proxy_max_candidates=8,
            top_k=3,
            seed=11,
        )
    ).run()
    assert alpha_result.summary["shortlist_count"] > 0

    exit_code = run_search_main(
        [
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(tmp_path / "search_store"),
            "--report-dir",
            str(tmp_path / "search_reports"),
            "--output-dir",
            str(tmp_path / "search"),
            "--population-size",
            "4",
            "--generations",
            "1",
            "--candidate-batch-size",
            "2",
            "--max-formula-len",
            "6",
            "--max-complexity",
            "18",
            "--factor-transform",
            "winsorize_zscore",
            "--min-coverage",
            "0.5",
            "--alpha-candidates-path",
            alpha_result.paths["alpha_shortlist_path"],
            "--alpha-campaign-manifest-path",
            alpha_result.paths["alpha_campaign_manifest_path"],
            "--feature-set-name",
            FEATURE_SET_V2,
            "--feature-set-manifest-path",
            alpha_result.paths["feature_set_manifest_path"],
            "--use-alpha-shortlist-as-seed",
            "--alpha-seed-top-k",
            "2",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["config"]["search_mode"] == "random"
    assert (tmp_path / "search" / "search_candidates.jsonl").exists()
    candidates_text = (tmp_path / "search" / "search_candidates.jsonl").read_text(encoding="utf-8")
    assert "alpha_factory" in candidates_text


def test_dashboard_service_reads_alpha_and_feature_artifacts(tmp_path):
    data_dir = _prepare_sample_data(tmp_path)
    result = AlphaFactoryRunner(
        AlphaCampaignConfig(
            campaign_name="unit_alpha_dashboard",
            data_dir=str(data_dir),
            output_dir=str(tmp_path / "alpha"),
            factor_store_dir=str(tmp_path / "store"),
            feature_set_name=FEATURE_SET_V2,
            build_feature_set=True,
            feature_output_dir=str(tmp_path / "features"),
            candidate_budget=6,
            template_budget=2,
            random_budget=2,
            mutation_budget=1,
            crossover_budget=1,
            corpus_budget=0,
            proxy_max_candidates=6,
            top_k=2,
        )
    ).run()
    service = AshareDashboardService(
        DashboardConfig(
            data_dir=data_dir,
            factor_store_dir=tmp_path / "store",
            report_dir=tmp_path / "reports",
            feature_factory_dir=tmp_path / "features",
            alpha_factory_dir=tmp_path / "alpha",
        )
    )

    assert service.load_feature_set_manifest()["feature_set_name"] == FEATURE_SET_V2
    assert service.load_feature_coverage_report()["feature_count"] > 11
    assert service.load_alpha_campaign_manifest()["campaign_id"] == result.campaign_id
    assert service.load_alpha_factory_report()["summary"]["shortlist_count"] >= 0
    assert not service.load_alpha_candidates().empty
    assert not service.load_alpha_static_checks().empty
    assert not service.load_alpha_proxy_eval().empty
    assert not service.load_alpha_shortlist().empty

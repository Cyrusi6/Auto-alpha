from pathlib import Path

from research_suite import ResearchSuiteConfig, ResearchSuiteRunner


def _suite_config(tmp_path, **overrides):
    root = tmp_path
    payload = dict(
        suite_name="sample_suite",
        provider="sample",
        data_dir=str(root / "data"),
        universe_name="csi300_sample",
        index_code="000300.SH",
        factor_store_dir=str(root / "store"),
        report_dir=str(root / "reports"),
        output_dir=str(root / "suite"),
        backtest_dir=str(root / "backtest"),
        orders_dir=str(root / "orders"),
        as_of_date="20240104",
        factor_transform="winsorize_zscore",
        search_seed=42,
        search_population_size=6,
        search_generations=1,
        search_max_candidates=4,
        top_k=3,
        composite_method="rank_average",
        promote_latest_composite=True,
        walk_forward_train_size=1,
        walk_forward_test_size=1,
        walk_forward_step_size=1,
    )
    payload.update(overrides)
    return ResearchSuiteConfig(**payload)


def test_research_suite_workflow_runs_sample_end_to_end(tmp_path):
    config = _suite_config(tmp_path)
    result = ResearchSuiteRunner(config).run()
    stage_statuses = {stage.name: stage.status for stage in result.stages}

    assert result.status == "success"
    assert result.selected_factor_id
    assert all(status == "success" for status in stage_statuses.values())
    assert stage_statuses["data_sync"] == "success"
    assert stage_statuses["formula_search"] == "success"
    assert stage_statuses["walk_forward"] == "success"
    assert stage_statuses["promotion"] == "success"
    assert (tmp_path / "suite" / "suite_result.json").exists()
    assert (tmp_path / "suite" / "suite_report.md").exists()
    assert (tmp_path / "suite" / "artifact_catalog.json").exists()
    assert (tmp_path / "suite" / "promotion_decision.json").exists()


def test_research_suite_workflow_records_failed_stage(tmp_path):
    config = _suite_config(tmp_path, provider="missing_provider")
    result = ResearchSuiteRunner(config).run()

    assert result.status == "failed"
    assert result.stages[0].name == "data_sync"
    assert result.stages[0].status == "failed"
    assert result.stages[0].error
    assert (tmp_path / "suite" / "suite_result.json").exists()

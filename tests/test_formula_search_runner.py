import json

from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from data_pipeline.ashare.storage import LocalAshareStorage
from factor_store import LocalFactorStore
from formula_search.models import FormulaSearchConfig
from formula_search.search import FormulaSearchRunner
from universe.builder import build_universe_from_storage
from universe.models import UniverseBuildConfig


def _prepare_data(tmp_path):
    data_dir = tmp_path / "data"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True)
    build_universe_from_storage(
        LocalAshareStorage(data_dir),
        UniverseBuildConfig(
            universe_name="csi300_sample",
            as_of_date="20240104",
            min_listed_days=0,
            min_amount=0,
            use_index_members=True,
            index_code="000300.SH",
        ),
    )
    return data_dir


def test_formula_search_runner_writes_reports_and_factor_metadata(tmp_path):
    data_dir = _prepare_data(tmp_path)
    result = FormulaSearchRunner(
        search_config=FormulaSearchConfig(
            seed=42,
            population_size=8,
            generations=2,
            max_formula_len=8,
            max_complexity=24,
            max_lookback=10,
            top_k=3,
        ),
        data_dir=str(data_dir),
        universe_name="csi300_sample",
        universe_file=None,
        factor_store_dir=str(tmp_path / "store"),
        report_dir=str(tmp_path / "reports"),
        output_dir=str(tmp_path / "search"),
        factor_transform="winsorize_zscore",
        enable_gate=True,
        correlation_threshold=0.99,
        min_coverage=0.5,
        composite_method="rank_average",
    ).run()
    store = LocalFactorStore(tmp_path / "store")
    factors = store.load_factors()
    metadata_records = [record for record in factors if record.metadata and record.metadata.get("search_id") == result.search_id]

    assert result.candidates_evaluated > 0
    assert result.candidates_valid > 0
    assert result.composite_factor_id is None or result.composite_factor_id.startswith("factor_")
    assert (tmp_path / "search" / "search_result.json").exists()
    assert (tmp_path / "search" / "search_candidates.jsonl").exists()
    assert (tmp_path / "search" / "search_report.json").exists()
    assert (tmp_path / "search" / "search_report.md").exists()
    assert json.loads((tmp_path / "search" / "search_result.json").read_text(encoding="utf-8"))["search_id"] == result.search_id
    assert metadata_records
    assert {"formula_complexity", "formula_lookback", "formula_source", "generation"} <= set(metadata_records[0].metadata)


def test_formula_search_second_run_skips_duplicate_registration(tmp_path):
    data_dir = _prepare_data(tmp_path)
    kwargs = dict(
        search_config=FormulaSearchConfig(seed=99, population_size=6, generations=1, max_formula_len=8, max_complexity=24),
        data_dir=str(data_dir),
        universe_name="csi300_sample",
        universe_file=None,
        factor_store_dir=str(tmp_path / "store"),
        report_dir=str(tmp_path / "reports"),
        output_dir=str(tmp_path / "search"),
        factor_transform="winsorize_zscore",
        enable_gate=True,
        correlation_threshold=0.99,
        min_coverage=0.5,
    )
    first = FormulaSearchRunner(**kwargs).run()
    factor_count = len(LocalFactorStore(tmp_path / "store").load_factors())
    second = FormulaSearchRunner(**kwargs | {"output_dir": str(tmp_path / "search_2")}).run()

    assert first.candidates_evaluated > 0
    assert any(item.get("status") == "skipped_existing" for item in second.best_candidates) or len(
        LocalFactorStore(tmp_path / "store").load_factors()
    ) == factor_count

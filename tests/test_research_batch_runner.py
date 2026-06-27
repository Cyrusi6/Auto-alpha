import json

from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from research import BatchFactorResearchRunner, BatchResearchConfig, FactorCandidate
from research.candidates import default_candidates
from universe.builder import build_universe_from_storage
from universe.models import UniverseBuildConfig
from data_pipeline.ashare.storage import LocalAshareStorage
from factor_store import LocalFactorStore
from model_core.vocab import FORMULA_VOCAB


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


def _config(tmp_path, data_dir):
    return BatchResearchConfig(
        data_dir=str(data_dir),
        universe_name="csi300_sample",
        universe_file=None,
        factor_store_dir=str(tmp_path / "store"),
        report_dir=str(tmp_path / "reports"),
        output_dir=str(tmp_path / "batch"),
        factor_transform="winsorize_zscore",
        enable_gate=True,
        correlation_threshold=0.99,
        min_coverage=0.5,
        top_k=3,
        composite_method="rank_average",
        continue_on_error=True,
    )


def test_batch_runner_generates_reports_and_skips_duplicate_hashes(tmp_path):
    data_dir = _prepare_data(tmp_path)
    config = _config(tmp_path, data_dir)
    candidates = default_candidates()[:5]

    first = BatchFactorResearchRunner(config=config, candidates=candidates).run()
    store = LocalFactorStore(config.factor_store_dir)
    initial_factor_count = len(store.load_factors())
    second = BatchFactorResearchRunner(config=config, candidates=candidates).run()

    assert (tmp_path / "batch" / "batch_result.json").exists()
    assert (tmp_path / "batch" / "batch_results.jsonl").exists()
    assert (tmp_path / "batch" / "batch_report.json").exists()
    assert (tmp_path / "batch" / "batch_report.md").exists()
    markdown = (tmp_path / "batch" / "batch_report.md").read_text(encoding="utf-8")
    assert "complexity" in markdown
    assert "lookback" in markdown
    assert "generation" in markdown
    assert first.results
    assert any(result.status in {"approved", "rejected"} for result in first.results)
    assert first.composite_factor_id is None or first.composite_factor_id.startswith("factor_")
    assert all(result.status == "skipped_existing" for result in second.results)
    assert len(store.load_factors()) == initial_factor_count

    payload = json.loads((tmp_path / "batch" / "batch_result.json").read_text(encoding="utf-8"))
    assert payload["batch_id"].startswith("batch_")
    record = store.load_factors()[0]
    assert "formula_complexity" in record.metadata
    assert "formula_lookback" in record.metadata
    assert "formula_source" in record.metadata


def test_batch_runner_continues_after_bad_candidate(tmp_path):
    data_dir = _prepare_data(tmp_path)
    config = _config(tmp_path, data_dir)
    bad = FactorCandidate(
        name="bad_arity",
        formula_tokens=[FORMULA_VOCAB.encode_name("ADD")],
        formula_names=["ADD"],
    )
    good = default_candidates()[0]

    result = BatchFactorResearchRunner(config=config, candidates=[bad, good]).run()

    assert result.results[0].status == "error"
    assert result.results[1].status in {"approved", "rejected"}

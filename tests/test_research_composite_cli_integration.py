import json

from backtest import run_backtest
from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from data_pipeline.ashare.storage import LocalAshareStorage
from research import BatchFactorResearchRunner, BatchResearchConfig
from research.candidates import default_candidates
from strategy_manager import runner as strategy_runner
from universe.builder import build_universe_from_storage
from universe.models import UniverseBuildConfig


def _prepare_batch(tmp_path):
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
    config = BatchResearchConfig(
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
    )
    result = BatchFactorResearchRunner(config=config, candidates=default_candidates()[:5]).run()
    assert result.composite_factor_id is not None
    return data_dir, tmp_path / "store", result.composite_factor_id


def test_backtest_can_select_latest_approved_composite(tmp_path, capsys):
    data_dir, store_dir, composite_factor_id = _prepare_batch(tmp_path)
    capsys.readouterr()

    exit_code = run_backtest.main(
        [
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(store_dir),
            "--output-dir",
            str(tmp_path / "backtest"),
            "--latest-approved",
            "--factor-type",
            "composite",
            "--top-n",
            "2",
            "--max-weight",
            "0.10",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["factor_id"] == composite_factor_id
    assert payload["factor_type"] == "composite"
    assert payload["component_factor_ids"]


def test_strategy_can_select_latest_approved_composite(tmp_path, capsys):
    data_dir, store_dir, composite_factor_id = _prepare_batch(tmp_path)
    capsys.readouterr()

    exit_code = strategy_runner.main(
        [
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(store_dir),
            "--output-dir",
            str(tmp_path / "orders"),
            "--latest-approved",
            "--factor-type",
            "composite",
            "--top-n",
            "2",
            "--max-weight",
            "0.10",
            "--portfolio-value",
            "1000000",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["factor_id"] == composite_factor_id
    assert payload["factor_type"] == "composite"
    assert payload["component_factor_ids"]
    assert (tmp_path / "orders" / "paper_fills.jsonl").exists()

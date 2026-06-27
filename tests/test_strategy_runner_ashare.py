import json

from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from model_core import engine
from strategy_manager import runner


def prepare_registered_factor(tmp_path):
    data_dir = tmp_path / "data"
    store_dir = tmp_path / "store"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync()
    engine.main(
        [
            "--dry-run",
            "--register",
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(tmp_path / "out"),
            "--factor-store-dir",
            str(store_dir),
            "--report-dir",
            str(tmp_path / "reports"),
        ]
    )
    return data_dir, store_dir


def test_strategy_runner_generates_targets_orders_and_fills(tmp_path, capsys):
    data_dir, store_dir = prepare_registered_factor(tmp_path)
    capsys.readouterr()
    output_dir = tmp_path / "orders"

    result = runner.main(
        [
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(store_dir),
            "--output-dir",
            str(output_dir),
            "--top-n",
            "2",
            "--max-weight",
            "0.10",
            "--portfolio-value",
            "1000000",
            "--pretty",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["factor_id"].startswith("factor_")
    assert payload["rebalance_date"]
    assert payload["n_targets"] > 0
    assert payload["n_orders"] > 0
    assert (output_dir / "target_positions.csv").exists()
    assert (output_dir / "target_positions.jsonl").exists()
    assert (output_dir / "orders.csv").exists()
    assert (output_dir / "orders.jsonl").exists()
    assert (output_dir / "paper_fills.jsonl").exists()
    assert not (output_dir / "best_meme_strategy.json").exists()

import json
from pathlib import Path

from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from model_core import engine


def write_sample_data(data_dir):
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync()


def test_engine_training_registers_by_default(tmp_path, capsys):
    data_dir = tmp_path / "data"
    output_dir = tmp_path / "out"
    store_dir = tmp_path / "store"
    report_dir = tmp_path / "reports"
    write_sample_data(data_dir)

    result = engine.main(
        [
            "--steps",
            "2",
            "--batch-size",
            "3",
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(output_dir),
            "--factor-store-dir",
            str(store_dir),
            "--report-dir",
            str(report_dir),
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert (output_dir / "best_factor_formula.json").exists()
    assert (output_dir / "training_history.json").exists()
    assert (store_dir / "factors.jsonl").exists()
    assert (store_dir / "experiments.jsonl").exists()
    assert Path(payload["report_json_path"]).exists()
    assert Path(payload["report_md_path"]).exists()
    assert not (output_dir / "best_meme_strategy.json").exists()

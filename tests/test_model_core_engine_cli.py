import json

from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from model_core import engine


def write_sample_data(data_dir):
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync()


def test_engine_dry_run_outputs_metrics(tmp_path, capsys):
    data_dir = tmp_path / "data"
    output_dir = tmp_path / "out"
    write_sample_data(data_dir)

    result = engine.main(["--dry-run", "--data-dir", str(data_dir), "--output-dir", str(output_dir)])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert "metrics" in payload
    assert "formula" in payload
    assert payload["n_stocks"] == 3
    assert payload["n_dates"] > 0
    assert payload["n_features"] > 0
    assert not (output_dir / "best_meme_strategy.json").exists()


def test_engine_minimal_training_writes_outputs(tmp_path, capsys):
    data_dir = tmp_path / "data"
    output_dir = tmp_path / "out"
    write_sample_data(data_dir)

    result = engine.main(
        ["--steps", "1", "--batch-size", "2", "--data-dir", str(data_dir), "--output-dir", str(output_dir)]
    )
    capsys.readouterr()

    assert result == 0
    assert (output_dir / "best_factor_formula.json").exists()
    assert (output_dir / "training_history.json").exists()
    assert not (output_dir / "best_meme_strategy.json").exists()

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
        [
            "--steps",
            "1",
            "--batch-size",
            "2",
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(output_dir),
            "--no-register",
        ]
    )
    capsys.readouterr()

    assert result == 0
    assert (output_dir / "best_factor_formula.json").exists()
    assert (output_dir / "training_history.json").exists()
    assert not (output_dir / "best_meme_strategy.json").exists()


def test_engine_neural_train_mode_writes_neural_history(tmp_path, capsys):
    data_dir = tmp_path / "data"
    output_dir = tmp_path / "neural_out"
    write_sample_data(data_dir)

    result = engine.main(
        [
            "--train-mode",
            "neural",
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(output_dir),
            "--factor-store-dir",
            str(tmp_path / "store"),
            "--report-dir",
            str(tmp_path / "reports"),
            "--batch-size",
            "2",
            "--neural-warmup-steps",
            "1",
            "--neural-policy-steps",
            "1",
            "--neural-samples-per-step",
            "2",
            "--factor-transform",
            "winsorize_zscore",
            "--enable-gate",
            "--min-coverage",
            "0.5",
            "--correlation-threshold",
            "0.99",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["train_mode"] == "neural"
    assert payload["training_history"]
    assert (output_dir / "neural_search_result.json").exists()
    assert (output_dir / "neural_training_history.jsonl").exists()
    assert list((output_dir / "checkpoints").glob("*.pt"))

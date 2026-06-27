from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from model_core import engine


def write_sample_data(data_dir):
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync()


def test_engine_training_no_register_skips_store_and_report(tmp_path, capsys):
    data_dir = tmp_path / "data"
    output_dir = tmp_path / "out"
    store_dir = tmp_path / "store"
    report_dir = tmp_path / "reports"
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
            "--factor-store-dir",
            str(store_dir),
            "--report-dir",
            str(report_dir),
            "--no-register",
        ]
    )
    capsys.readouterr()

    assert result == 0
    assert (output_dir / "best_factor_formula.json").exists()
    assert (output_dir / "training_history.json").exists()
    assert not store_dir.exists()
    assert not report_dir.exists()

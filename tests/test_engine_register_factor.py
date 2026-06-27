import json
from pathlib import Path

from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from model_core import engine


def write_sample_data(data_dir):
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync()


def test_engine_dry_run_register_writes_store_and_report(tmp_path, capsys):
    data_dir = tmp_path / "data"
    output_dir = tmp_path / "out"
    store_dir = tmp_path / "store"
    report_dir = tmp_path / "reports"
    write_sample_data(data_dir)

    result = engine.main(
        [
            "--dry-run",
            "--register",
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
    assert payload["factor_id"].startswith("factor_")
    assert payload["experiment_id"].startswith("exp_")
    assert set(payload["metrics_by_split"]) == {"train", "valid", "test", "all"}
    assert Path(payload["report_json_path"]).exists()
    assert Path(payload["report_md_path"]).exists()
    assert (store_dir / "factors.jsonl").exists()
    assert (store_dir / "experiments.jsonl").exists()
    assert (store_dir / "factor_values" / f"{payload['factor_id']}.jsonl").exists()

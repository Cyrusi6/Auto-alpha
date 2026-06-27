import json

from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from data_pipeline.ashare.storage import LocalAshareStorage
from neural_search import run_neural_search
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


def test_run_neural_search_cli_writes_reports_and_checkpoint(tmp_path, capsys):
    data_dir = _prepare_data(tmp_path)
    exit_code = run_neural_search.main(
        [
            "--data-dir",
            str(data_dir),
            "--universe-name",
            "csi300_sample",
            "--factor-store-dir",
            str(tmp_path / "store"),
            "--report-dir",
            str(tmp_path / "reports"),
            "--output-dir",
            str(tmp_path / "neural"),
            "--seed",
            "42",
            "--warmup-steps",
            "1",
            "--policy-steps",
            "1",
            "--batch-size",
            "2",
            "--samples-per-step",
            "2",
            "--max-formula-len",
            "6",
            "--factor-transform",
            "winsorize_zscore",
            "--enable-gate",
            "--top-k",
            "2",
            "--correlation-threshold",
            "0.99",
            "--min-coverage",
            "0.5",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["search_id"].startswith("neural_42_")
    assert payload["training_history"]
    assert payload["checkpoint_paths"]
    assert (tmp_path / "neural" / "neural_search_result.json").exists()
    assert (tmp_path / "neural" / "neural_training_history.jsonl").exists()
    assert (tmp_path / "neural" / "neural_search_report.md").exists()
    assert list((tmp_path / "neural" / "checkpoints").glob("*.pt"))

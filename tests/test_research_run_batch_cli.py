import json

from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from data_pipeline.ashare.storage import LocalAshareStorage
from research import run_batch
from universe.builder import build_universe_from_storage
from universe.models import UniverseBuildConfig


def test_research_run_batch_cli_writes_batch_outputs(tmp_path, capsys):
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

    result = run_batch.main(
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
            str(tmp_path / "batch"),
            "--factor-transform",
            "winsorize_zscore",
            "--enable-gate",
            "--top-k",
            "3",
            "--max-candidates",
            "4",
            "--composite-method",
            "rank_average",
            "--correlation-threshold",
            "0.99",
            "--min-coverage",
            "0.5",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["batch_id"].startswith("batch_")
    assert payload["summary"]["total_candidates"] == 4
    assert payload["composite_factor_id"] is None or payload["composite_factor_id"].startswith("factor_")
    assert (tmp_path / "batch" / "batch_result.json").exists()
    assert (tmp_path / "batch" / "batch_results.jsonl").exists()
    assert (tmp_path / "batch" / "batch_report.json").exists()
    assert (tmp_path / "batch" / "batch_report.md").exists()

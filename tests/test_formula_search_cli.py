import json

from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from data_pipeline.ashare.storage import LocalAshareStorage
from formula_search import run_search
from universe.builder import build_universe_from_storage
from universe.models import UniverseBuildConfig


def test_formula_search_cli_outputs_json_and_artifacts(tmp_path, capsys):
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

    exit_code = run_search.main(
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
            str(tmp_path / "search"),
            "--seed",
            "42",
            "--population-size",
            "6",
            "--generations",
            "1",
            "--max-formula-len",
            "8",
            "--max-complexity",
            "24",
            "--max-lookback",
            "10",
            "--factor-transform",
            "winsorize_zscore",
            "--enable-gate",
            "--top-k",
            "3",
            "--correlation-threshold",
            "0.99",
            "--min-coverage",
            "0.5",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["search_id"].startswith("search_42_")
    assert payload["candidates_evaluated"] > 0
    assert (tmp_path / "search" / "search_result.json").exists()
    assert (tmp_path / "search" / "search_candidates.jsonl").exists()
    assert (tmp_path / "search" / "search_report.json").exists()
    assert (tmp_path / "search" / "search_report.md").exists()

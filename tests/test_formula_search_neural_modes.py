import json

from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from data_pipeline.ashare.storage import LocalAshareStorage
from formula_search import run_search
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


def _base_args(tmp_path, data_dir, output_name):
    return [
        "--data-dir",
        str(data_dir),
        "--universe-name",
        "csi300_sample",
        "--factor-store-dir",
        str(tmp_path / f"store_{output_name}"),
        "--report-dir",
        str(tmp_path / f"reports_{output_name}"),
        "--output-dir",
        str(tmp_path / output_name),
        "--seed",
        "42",
        "--population-size",
        "4",
        "--generations",
        "1",
        "--max-formula-len",
        "6",
        "--max-complexity",
        "24",
        "--max-lookback",
        "10",
        "--factor-transform",
        "winsorize_zscore",
        "--enable-gate",
        "--top-k",
        "2",
        "--neural-warmup-steps",
        "1",
        "--neural-policy-steps",
        "1",
        "--hybrid-neural-ratio",
        "0.5",
        "--correlation-threshold",
        "0.99",
        "--min-coverage",
        "0.5",
        "--pretty",
    ]


def test_formula_search_neural_mode_delegates_to_neural_runner(tmp_path, capsys):
    data_dir = _prepare_data(tmp_path)
    exit_code = run_search.main(["--search-mode", "neural"] + _base_args(tmp_path, data_dir, "neural_search"))
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["search_mode"] == "neural"
    assert payload["search_id"].startswith("neural_42_")
    assert (tmp_path / "neural_search" / "neural_search_result.json").exists()


def test_formula_search_hybrid_mode_includes_neural_metadata(tmp_path, capsys):
    data_dir = _prepare_data(tmp_path)
    exit_code = run_search.main(["--search-mode", "hybrid"] + _base_args(tmp_path, data_dir, "hybrid_search"))
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["search_mode"] == "hybrid"
    assert payload["search_id"].startswith("search_42_")
    assert payload["neural_metadata"]["search_id"].startswith("neural_42_")
    assert payload["neural_metadata"]["checkpoint_paths"]
    assert (tmp_path / "hybrid_search" / "search_result.json").exists()
    assert (tmp_path / "hybrid_search" / "neural" / "neural_search_result.json").exists()

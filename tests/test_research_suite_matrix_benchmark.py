import json

from research_suite import run_suite


def test_research_suite_builds_matrix_cache_and_benchmark(tmp_path, capsys):
    exit_code = run_suite.main(
        [
            "--suite-name",
            "matrix_suite",
            "--provider",
            "sample",
            "--data-dir",
            str(tmp_path / "data"),
            "--universe-name",
            "csi300_sample",
            "--index-code",
            "000300.SH",
            "--factor-store-dir",
            str(tmp_path / "store"),
            "--report-dir",
            str(tmp_path / "reports"),
            "--output-dir",
            str(tmp_path / "suite"),
            "--backtest-dir",
            str(tmp_path / "backtest"),
            "--orders-dir",
            str(tmp_path / "orders"),
            "--as-of-date",
            "20240104",
            "--build-matrix-cache",
            "--matrix-cache-dir",
            str(tmp_path / "data" / "matrix_cache"),
            "--use-matrix-cache",
            "--benchmark",
            "--benchmark-dir",
            str(tmp_path / "suite_benchmark"),
            "--search-population-size",
            "5",
            "--search-generations",
            "1",
            "--search-max-candidates",
            "3",
            "--top-k",
            "2",
            "--skip-orders",
            "--walk-forward-train-size",
            "1",
            "--walk-forward-test-size",
            "1",
            "--walk-forward-step-size",
            "1",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    stage_names = {stage["name"] for stage in payload["stages"]}

    assert exit_code == 0
    assert payload["status"] == "success"
    assert {"matrix_cache", "benchmark"} <= stage_names
    assert (tmp_path / "data" / "matrix_cache" / "metadata.json").exists()
    assert (tmp_path / "data" / "matrix_cache" / "matrix_validation_report.json").exists()
    assert (tmp_path / "suite_benchmark" / "benchmark_result.json").exists()
    catalog = json.loads((tmp_path / "suite" / "artifact_catalog.json").read_text(encoding="utf-8"))
    names = {entry["name"] for entry in catalog["entries"]}
    assert {"matrix_metadata", "matrix_validation_report", "benchmark_result"} <= names

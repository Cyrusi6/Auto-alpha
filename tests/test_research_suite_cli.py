import json

from research_suite import run_suite


def test_run_suite_cli_sample_workflow(tmp_path, capsys):
    exit_code = run_suite.main(
        [
            "--suite-name",
            "sample_suite",
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
            "--factor-transform",
            "winsorize_zscore",
            "--search-seed",
            "42",
            "--search-population-size",
            "6",
            "--search-generations",
            "1",
            "--search-max-candidates",
            "4",
            "--top-k",
            "3",
            "--promote-latest-composite",
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

    assert exit_code == 0
    assert payload["status"] == "success"
    assert (tmp_path / "suite" / "suite_result.json").exists()


def test_run_suite_write_default_config_and_config_json(tmp_path, capsys):
    config_path = tmp_path / "suite_config.json"
    exit_code = run_suite.main(
        [
            "--write-default-config",
            str(config_path),
            "--output-dir",
            str(tmp_path / "suite"),
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["config_path"] == str(config_path)
    assert config_path.exists()

    config = json.loads(config_path.read_text(encoding="utf-8"))
    config.update(
        {
            "data_dir": str(tmp_path / "data"),
            "factor_store_dir": str(tmp_path / "store"),
            "report_dir": str(tmp_path / "reports"),
            "output_dir": str(tmp_path / "suite_run"),
            "backtest_dir": str(tmp_path / "backtest"),
            "orders_dir": str(tmp_path / "orders"),
            "search_population_size": 5,
            "search_generations": 1,
            "search_max_candidates": 3,
            "top_k": 2,
            "promote_latest_composite": False,
            "skip_orders": True,
        }
    )
    config_path.write_text(json.dumps(config), encoding="utf-8")

    exit_code = run_suite.main(["--config-json", str(config_path)])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "success"
    assert "orders" not in {stage["name"] for stage in payload["stages"]}
    assert "promotion" not in {stage["name"] for stage in payload["stages"]}


def test_run_suite_cli_neural_search_mode(tmp_path, capsys):
    exit_code = run_suite.main(
        [
            "--suite-name",
            "neural_suite",
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
            "--factor-transform",
            "winsorize_zscore",
            "--search-mode",
            "neural",
            "--search-population-size",
            "4",
            "--search-generations",
            "1",
            "--search-max-candidates",
            "2",
            "--neural-warmup-steps",
            "1",
            "--neural-policy-steps",
            "1",
            "--top-k",
            "2",
            "--skip-orders",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    formula_stage = next(stage for stage in payload["stages"] if stage["name"] == "formula_search")

    assert exit_code == 0
    assert payload["status"] == "success"
    assert formula_stage["summary"]["search_mode"] == "neural"
    assert (tmp_path / "suite" / "search" / "neural_search_result.json").exists()

import json

from research_suite import run_suite


def test_research_suite_risk_aware_flow(tmp_path, capsys):
    result = run_suite.main(
        [
            "--suite-name",
            "risk_suite_test",
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
            "--search-population-size",
            "6",
            "--search-generations",
            "1",
            "--search-max-candidates",
            "4",
            "--top-k",
            "3",
            "--portfolio-method",
            "risk_aware",
            "--max-industry-active-weight",
            "0.50",
            "--max-tracking-error",
            "1.00",
            "--use-factor-risk-model",
            "--risk-model-lookback",
            "3",
            "--attribution",
            "--max-active-style-exposure",
            "1.0",
            "--promote-latest-composite",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    catalog = json.loads((tmp_path / "suite" / "artifact_catalog.json").read_text(encoding="utf-8"))
    names = {entry["name"] for entry in catalog["entries"]}

    assert result == 0
    assert payload["status"] == "success"
    assert "risk_report" in names
    assert "optimization_result" in names
    assert "risk_exposures" in names
    assert "risk_decomposition" in names
    assert "return_attribution" in names
    assert payload["promotion_decision"]["checks"]["tracking_error"] >= 0
    assert "max_active_style_exposure_abs" in payload["promotion_decision"]["checks"]

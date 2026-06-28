import json

from backtest import run_backtest
from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from model_core import engine


def prepare_registered_factor(tmp_path):
    data_dir = tmp_path / "data"
    store_dir = tmp_path / "store"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync()
    engine.main(
        [
            "--dry-run",
            "--register",
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(tmp_path / "out"),
            "--factor-store-dir",
            str(store_dir),
            "--report-dir",
            str(tmp_path / "reports"),
        ]
    )
    return data_dir, store_dir


def test_backtest_cli_writes_outputs(tmp_path, capsys):
    data_dir, store_dir = prepare_registered_factor(tmp_path)
    capsys.readouterr()
    output_dir = tmp_path / "backtest"

    result = run_backtest.main(
        [
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(store_dir),
            "--output-dir",
            str(output_dir),
            "--top-n",
            "2",
            "--max-weight",
            "0.10",
            "--pretty",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["factor_id"].startswith("factor_")
    assert "metrics" in payload
    assert payload["n_snapshots"] > 0
    assert (output_dir / "backtest_result.json").exists()
    assert (output_dir / "equity_curve.jsonl").exists()
    assert (output_dir / "trades.jsonl").exists()


def test_backtest_cli_settlement_aware_writes_settlement_artifacts(tmp_path, capsys):
    data_dir, store_dir = prepare_registered_factor(tmp_path)
    capsys.readouterr()
    output_dir = tmp_path / "backtest_settlement"
    settlement_dir = tmp_path / "settlement"

    result = run_backtest.main(
        [
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(store_dir),
            "--output-dir",
            str(output_dir),
            "--top-n",
            "2",
            "--max-weight",
            "0.10",
            "--settlement-aware",
            "--settlement-dir",
            str(settlement_dir),
            "--settlement-profile",
            "cn_ashare_paper_default",
            "--cost-basis-method",
            "fifo",
            "--write-settlement-report",
            "--pretty",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["metrics"]["settlement_aware"] == 1.0
    assert payload["settlement_report_path"].endswith("settlement_report.json")
    assert (settlement_dir / "settlement_report.json").exists()
    assert (settlement_dir / "settlement_events.jsonl").exists()
    assert (settlement_dir / "fee_tax_report.json").exists()

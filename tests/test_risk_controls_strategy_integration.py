import json

from data_pipeline.ashare.manager import AShareDataManager
from data_pipeline.ashare.config import AShareDataConfig
from model_core.engine import main as engine_main
from strategy_manager.runner import main as strategy_main


def _prepare_sample_factor(tmp_path):
    data_dir = tmp_path / "data"
    manager = AShareDataManager(AShareDataConfig(data_dir=data_dir, provider="sample"))
    manager.sync(validate=True)
    store_dir = tmp_path / "store"
    report_dir = tmp_path / "reports"
    out_dir = tmp_path / "engine_out"
    assert (
        engine_main(
            [
                "--dry-run",
                "--register",
                "--data-dir",
                str(data_dir),
                "--output-dir",
                str(out_dir),
                "--factor-store-dir",
                str(store_dir),
                "--report-dir",
                str(report_dir),
            ]
        )
        == 0
    )
    return data_dir, store_dir


def test_strategy_runner_with_risk_controls(tmp_path, capsys):
    data_dir, store_dir = _prepare_sample_factor(tmp_path)
    capsys.readouterr()
    exit_code = strategy_main(
        [
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(store_dir),
            "--output-dir",
            str(tmp_path / "orders"),
            "--rebalance-date",
            "20240104",
            "--top-n",
            "2",
            "--max-weight",
            "0.10",
            "--portfolio-value",
            "1000000",
            "--risk-controls",
            "--risk-control-state-dir",
            str(tmp_path / "risk_state"),
            "--risk-control-output-dir",
            str(tmp_path / "orders" / "risk_controls"),
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["risk_controls"] is True
    assert payload["risk_control_status"] in {"passed", "warning"}
    assert (tmp_path / "orders" / "risk_controls" / "risk_control_report.json").exists()
    assert (tmp_path / "orders" / "risk_controls" / "accepted_orders.jsonl").exists()

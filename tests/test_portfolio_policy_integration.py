import json

from backtest.run_backtest import main as backtest_main
from portfolio_certification.run_portfolio_certify import main as certify_main
from portfolio_lab.run_portfolio_lab import main as lab_main
from strategy_manager.runner import main as strategy_main

from test_portfolio_lab_certification import _prepare_factor


def _certified_policy(tmp_path, capsys):
    data_dir, store_dir, factor_id = _prepare_factor(tmp_path)
    capsys.readouterr()
    assert lab_main(
        [
            "run",
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(store_dir),
            "--factor-id",
            factor_id,
            "--output-dir",
            str(tmp_path / "lab"),
            "--portfolio-methods",
            "risk_aware",
            "--risk-aversions",
            "1.0",
            "--turnover-penalties",
            "0.1",
            "--max-names-values",
            "2",
            "--top-n-values",
            "2",
            "--max-trials",
            "1",
        ]
    ) == 0
    capsys.readouterr()
    assert certify_main(
        [
            "run",
            "--factor-store-dir",
            str(store_dir),
            "--factor-id",
            factor_id,
            "--portfolio-policy-path",
            str(tmp_path / "lab" / "selected_portfolio_policy.json"),
            "--portfolio-lab-report-path",
            str(tmp_path / "lab" / "portfolio_lab_report.json"),
            "--portfolio-robustness-report-path",
            str(tmp_path / "lab" / "portfolio_robustness_report.json"),
            "--output-dir",
            str(tmp_path / "cert"),
        ]
    ) == 0
    capsys.readouterr()
    return data_dir, store_dir, factor_id, tmp_path / "cert" / "certified_portfolio_policy.json"


def test_backtest_accepts_certified_portfolio_policy(tmp_path, capsys):
    data_dir, store_dir, factor_id, policy_path = _certified_policy(tmp_path, capsys)

    assert backtest_main(
        [
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(store_dir),
            "--output-dir",
            str(tmp_path / "backtest"),
            "--factor-id",
            factor_id,
            "--portfolio-policy-path",
            str(policy_path),
            "--require-certified-portfolio-policy",
            "--pretty",
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["portfolio_policy_id"]
    assert payload["portfolio_policy_gate"]["certified"] is True
    result = json.loads((tmp_path / "backtest" / "backtest_result.json").read_text(encoding="utf-8"))
    assert result["portfolio_policy"]["policy"]["policy_id"] == payload["portfolio_policy_id"]


def test_strategy_runner_accepts_certified_portfolio_policy(tmp_path, capsys):
    data_dir, store_dir, factor_id, policy_path = _certified_policy(tmp_path, capsys)

    assert strategy_main(
        [
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(store_dir),
            "--output-dir",
            str(tmp_path / "orders"),
            "--factor-id",
            factor_id,
            "--rebalance-date",
            "20240104",
            "--portfolio-policy-path",
            str(policy_path),
            "--require-certified-portfolio-policy",
            "--portfolio-value",
            "1000000",
            "--pretty",
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["portfolio_policy_id"]
    assert payload["portfolio_policy_gate"]["certified"] is True
    assert (tmp_path / "orders" / "orders.jsonl").exists()

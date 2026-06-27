import json

from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from execution import ExecutionFill, export_fills_jsonl
from factor_store import FactorRecord, LocalFactorStore, stable_formula_hash
from model_core.data_loader import AShareDataLoader
from monitoring import run_monitor
from monitoring.checks import check_quality_report
from paper_account import LocalPaperAccount


def _prepare_monitoring_artifacts(tmp_path):
    data_dir = tmp_path / "data"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True)
    loader = AShareDataLoader(data_dir=data_dir, device="cpu").load_data()
    store = LocalFactorStore(tmp_path / "store")
    factor_id = "factor_monitor"
    formula_tokens = [0]
    formula_names = ["RET_1D"]
    store.save_factor(
        FactorRecord(
            factor_id=factor_id,
            formula=formula_names,
            formula_tokens=formula_tokens,
            formula_hash=stable_formula_hash(formula_tokens, formula_names, "ashare_features_v1", "ashare_ops_v1"),
            feature_version="ashare_features_v1",
            operator_version="ashare_ops_v1",
            lookback_days=1,
            created_at="2026-06-27T00:00:00Z",
            status="production_candidate",
            factor_type="composite",
        )
    )
    store.save_factor_values(
        factor_id,
        loader.ts_codes,
        loader.trade_dates,
        [[float(stock_idx + date_idx) for date_idx, _date in enumerate(loader.trade_dates)] for stock_idx, _code in enumerate(loader.ts_codes)],
    )
    account = LocalPaperAccount(tmp_path / "account")
    account.reset(100000.0)
    account.mark_to_market({"000001.SZ": 10.0}, "20240104")
    orders_dir = tmp_path / "orders"
    export_fills_jsonl(
        [
            ExecutionFill("20240104", "000001.SZ", "BUY", 10.0, 0, 0.0, "REJECTED", reason="limit_up"),
            ExecutionFill("20240104", "600000.SH", "BUY", 10.0, 0, 0.0, "REJECTED", reason="limit_up"),
        ],
        orders_dir / "paper_fills.jsonl",
    )
    (orders_dir / "risk_model_report.json").write_text(
        json.dumps(
            {
                "violations": [],
                "metrics": {"tracking_error": 0.1},
                "factor_risk_contribution": {
                    "factor_risk_share": 0.4,
                    "specific_risk_share": 0.6,
                    "factor_contributions": {"size": 0.2, "value": 0.1},
                },
            }
        ),
        encoding="utf-8",
    )
    (orders_dir / "risk_exposures.jsonl").write_text(
        '{"trade_date":"20240104","max_style_exposure_abs":0.5,"max_active_style_exposure_abs":0.4}\n',
        encoding="utf-8",
    )
    (orders_dir / "risk_decomposition.jsonl").write_text(
        '{"trade_date":"20240104","active":{"total_risk":0.2}}\n',
        encoding="utf-8",
    )
    (orders_dir / "return_attribution.jsonl").write_text(
        '{"trade_date":"20240104","total_active_return":0.01}\n',
        encoding="utf-8",
    )
    return data_dir, tmp_path / "store", tmp_path / "account", orders_dir


def test_monitoring_report_cli_writes_alerts(tmp_path, capsys):
    data_dir, store_dir, account_dir, orders_dir = _prepare_monitoring_artifacts(tmp_path)
    exit_code = run_monitor.main(
        [
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(store_dir),
            "--paper-account-dir",
            str(account_dir),
            "--orders-dir",
            str(orders_dir),
            "--output-dir",
            str(tmp_path / "monitoring"),
            "--as-of-date",
            "20240104",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["checks"]["data_freshness"]["ok"] is True
    assert payload["checks"]["style_exposure_drift"]["exists"] is True
    assert payload["checks"]["active_risk_drift"]["exists"] is True
    assert payload["checks"]["factor_risk_concentration"]["exists"] is True
    assert payload["checks"]["attribution_anomaly"]["exists"] is True
    assert any(alert["check"] == "fill_quality" for alert in payload["alerts"])
    assert (tmp_path / "monitoring" / "monitoring_report.json").exists()
    assert (tmp_path / "monitoring" / "monitoring_report.md").exists()
    assert (tmp_path / "monitoring" / "alerts.jsonl").exists()


def test_monitoring_quality_error_produces_error_alert(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "quality_report.json").write_text(
        '{"total_errors":1,"total_warnings":0}',
        encoding="utf-8",
    )
    payload, alerts = check_quality_report(data_dir)

    assert payload["ok"] is False
    assert alerts[0].severity == "error"

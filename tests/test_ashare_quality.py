import json

from data_pipeline.ashare import AShareDataConfig, AShareDataManager, LocalAshareStorage
from data_pipeline.ashare.quality import (
    DataQualityReport,
    validate_all_datasets,
    validate_dataset,
    write_quality_report,
)


def test_quality_report_for_sample_data_has_no_errors(tmp_path):
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=tmp_path)).sync()
    report = validate_all_datasets(LocalAshareStorage(tmp_path))
    payload = report.to_dict()

    assert payload["has_errors"] is False
    assert payload["total_errors"] == 0
    assert {dataset["dataset"] for dataset in payload["datasets"]} >= {
        "securities",
        "trade_calendar",
        "daily_bars",
    }


def test_quality_detects_invalid_daily_bar_and_duplicate_key():
    records = [
        {
            "ts_code": "000001.SZ",
            "trade_date": "20240102",
            "open": 1.0,
            "high": 0.5,
            "low": 1.0,
            "close": 1.0,
            "pre_close": 1.0,
            "volume": -1.0,
            "amount": 1.0,
        },
        {
            "ts_code": "000001.SZ",
            "trade_date": "20240102",
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 1.0,
            "pre_close": 1.0,
            "volume": 1.0,
            "amount": 1.0,
        },
        {
            "ts_code": "bad",
            "trade_date": "20240102",
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 1.0,
            "pre_close": 1.0,
            "volume": 1.0,
            "amount": 1.0,
        },
    ]

    summary = validate_dataset("daily_bars", records)
    codes = {issue.code for issue in summary.issues}

    assert summary.errors >= 3
    assert "duplicate_primary_key" in codes
    assert "invalid_ts_code" in codes
    assert "high_less_than_low" in codes
    assert "invalid_trade_value" in codes


def test_quality_detects_financial_feature_date_errors():
    summary = validate_dataset(
        "financial_features",
        [
            {
                "ts_code": "000001.SZ",
                "report_period": "20230230",
                "announce_date": "",
            }
        ],
    )

    codes = {issue.code for issue in summary.issues}
    assert "invalid_date" in codes
    assert "missing_announce_date" in codes
    assert summary.errors == 2


def test_write_quality_report_writes_json(tmp_path):
    summary = validate_dataset("securities", [])
    report = DataQualityReport(generated_at="2026-06-27T00:00:00+00:00", datasets=[summary])
    path = write_quality_report(report, tmp_path / "quality_report.json")

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["datasets"][0]["dataset"] == "securities"

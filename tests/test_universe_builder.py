import json
from pathlib import Path

from data_pipeline.ashare import AShareDataConfig, AShareDataManager, LocalAshareStorage
from universe.builder import build_universe_from_storage
from universe.models import UniverseBuildConfig
from universe.run_universe import main as universe_main


def test_sample_data_builds_universe(tmp_path):
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=tmp_path)).sync()
    result = build_universe_from_storage(
        LocalAshareStorage(tmp_path),
        UniverseBuildConfig(
            universe_name="all_a_sample",
            as_of_date="20240104",
            min_listed_days=0,
            min_amount=0,
        ),
    )

    assert result.selected == 3
    assert {member.ts_code for member in result.members} == {"000001.SZ", "600000.SH", "830000.BJ"}
    assert Path(result.output_path).exists()
    assert Path(result.summary_path).exists()
    assert json.loads(Path(result.summary_path).read_text(encoding="utf-8"))["selected"] == 3


def test_universe_filters_st_suspended_listed_days_and_amount(tmp_path):
    storage = LocalAshareStorage(tmp_path)
    storage.write_dataset(
        "securities",
        [
            {
                "ts_code": "000001.SZ",
                "symbol": "000001",
                "name": "valid",
                "exchange": "SZSE",
                "list_date": "20200101",
                "is_st": False,
                "board": "主板",
            },
            {
                "ts_code": "000002.SZ",
                "symbol": "000002",
                "name": "st",
                "exchange": "SZSE",
                "list_date": "20200101",
                "is_st": True,
                "board": "主板",
            },
            {
                "ts_code": "000003.SZ",
                "symbol": "000003",
                "name": "short",
                "exchange": "SZSE",
                "list_date": "20240101",
                "is_st": False,
                "board": "主板",
            },
            {
                "ts_code": "000004.SZ",
                "symbol": "000004",
                "name": "suspended",
                "exchange": "SZSE",
                "list_date": "20200101",
                "is_st": False,
                "board": "主板",
            },
            {
                "ts_code": "000005.SZ",
                "symbol": "000005",
                "name": "low amount",
                "exchange": "SZSE",
                "list_date": "20200101",
                "is_st": False,
                "board": "主板",
            },
        ],
    )
    storage.write_dataset(
        "daily_bars",
        [
            {"ts_code": "000001.SZ", "trade_date": "20240102", "amount": 1000.0, "is_suspended": False},
            {"ts_code": "000003.SZ", "trade_date": "20240102", "amount": 1000.0, "is_suspended": False},
            {"ts_code": "000004.SZ", "trade_date": "20240102", "amount": 1000.0, "is_suspended": True},
            {"ts_code": "000005.SZ", "trade_date": "20240102", "amount": 1.0, "is_suspended": False},
        ],
    )

    result = build_universe_from_storage(
        storage,
        UniverseBuildConfig(
            universe_name="filtered",
            as_of_date="20240102",
            min_listed_days=30,
            min_amount=10.0,
        ),
    )

    assert [member.ts_code for member in result.members] == ["000001.SZ"]
    assert result.rejected["st_security"] == 1
    assert result.rejected["listed_days"] == 1
    assert result.rejected["suspended"] == 1
    assert result.rejected["min_amount"] == 1


def test_universe_cli_outputs_json_summary(tmp_path, capsys):
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=tmp_path)).sync()

    result = universe_main(
        [
            "--data-dir",
            str(tmp_path),
            "--as-of-date",
            "20240104",
            "--universe-name",
            "all_a_sample",
            "--min-listed-days",
            "0",
            "--min-amount",
            "0",
            "--pretty",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["universe_name"] == "all_a_sample"
    assert payload["selected"] == 3
    assert Path(payload["output_path"]).exists()

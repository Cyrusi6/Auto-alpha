import json
from pathlib import Path

from research_data_readiness.dataset_policy import dataset_policy
from research_data_readiness.report import build_research_data_readiness_report, write_research_data_readiness_artifacts
from research_data_readiness.run_readiness import main as readiness_main


def test_missing_core_dataset_is_not_ready(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_dataset(data_dir, "securities", [{"ts_code": "000001.SZ", "list_date": "20200101"}])

    report = build_research_data_readiness_report(data_dir)

    assert report.decision.status == "not_ready"
    assert report.decision.core_ready is False
    assert report.decision.blocker_count > 0


def test_core_complete_alpha_ready_with_expanded_warnings(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_core_datasets(data_dir)
    _write_dataset(
        data_dir,
        "industry_members",
        [{"ts_code": "000001.SZ", "l1_code": "801", "l2_code": "80101", "l3_code": "801010", "in_date": "20200101", "out_date": ""}],
    )
    _write_dataset(
        data_dir,
        "main_business",
        [{"ts_code": "000001.SZ", "end_date": "20231231", "bz_item": "main", "curr_type": "CNY"}],
    )

    report = build_research_data_readiness_report(data_dir)
    paths = write_research_data_readiness_artifacts(report, tmp_path / "out")

    assert report.decision.core_ready is True
    assert report.decision.alpha_ready is True
    assert report.decision.status == "ready_for_alpha_factory"
    assert any(item.dataset == "industry_members" and item.pit_safety == "weak_pit" for item in report.dataset_checks)
    assert any(item.dataset == "main_business" and item.pit_safety == "unsafe_missing_availability" for item in report.dataset_checks)
    assert Path(paths["feature_readiness_catalog_path"]).exists()


def test_readiness_cli_and_pit_contract_policy(tmp_path: Path, capsys):
    data_dir = tmp_path / "data"
    _write_core_datasets(data_dir)

    rc = readiness_main(["assess", "--data-dir", str(data_dir), "--output-dir", str(tmp_path / "out"), "--pretty"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert rc == 0
    assert payload["decision"]["alpha_ready"] is True
    assert dataset_policy("pledge_stat").pit_safety == "unsafe_missing_availability"


def _write_core_datasets(data_dir: Path) -> None:
    _write_dataset(data_dir, "securities", [{"ts_code": "000001.SZ", "list_date": "20200101"}])
    _write_dataset(data_dir, "trade_calendar", [{"trade_date": "20240102", "is_open": True}])
    _write_dataset(data_dir, "daily_bars", [{"ts_code": "000001.SZ", "trade_date": "20240102", "close": 10.0}])
    _write_dataset(data_dir, "daily_basic", [{"ts_code": "000001.SZ", "trade_date": "20240102", "pb": 1.1, "pe_ttm": 10.0}])
    _write_dataset(data_dir, "adjustment_factors", [{"ts_code": "000001.SZ", "trade_date": "20240102", "adj_factor": 1.0}])
    _write_dataset(data_dir, "daily_limits", [{"ts_code": "000001.SZ", "trade_date": "20240102", "up_limit": 11.0, "down_limit": 9.0}])
    _write_dataset(data_dir, "index_members", [{"index_code": "000300.SH", "ts_code": "000001.SZ", "trade_date": "20240102", "weight": 1.0}])
    _write_dataset(
        data_dir,
        "corporate_actions",
        [{"ts_code": "000001.SZ", "ann_date": "20240101", "end_date": "20231231", "ex_date": "20240102", "div_proc": "实施"}],
    )
    _write_dataset(
        data_dir,
        "financial_features",
        [{"ts_code": "000001.SZ", "report_period": "20231231", "announce_date": "20240101", "roe": 0.1}],
    )


def _write_dataset(data_dir: Path, dataset: str, rows: list[dict]) -> None:
    target = data_dir / dataset / "records.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

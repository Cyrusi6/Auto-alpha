import json

from corporate_actions.normalizer import normalize_corporate_action_records
from corporate_actions.report import write_corporate_action_report
from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from model_core.data_loader import AShareDataLoader
from paper_account import LocalPaperAccount
from paper_account.models import PaperPosition


def _prepare_data(tmp_path):
    data_dir = tmp_path / "data"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True)
    return data_dir


def test_normalize_corporate_actions_from_sample(tmp_path):
    data_dir = _prepare_data(tmp_path)
    records = [json.loads(line) for line in (data_dir / "corporate_actions" / "records.jsonl").read_text().splitlines()]

    events = normalize_corporate_action_records(records)

    assert len(events) == 4
    assert any(event.action_type == "cash_dividend" for event in events)
    assert any(event.stock_transfer_ratio > 0 for event in events)
    assert any(event.stock_distribution_ratio > 0 for event in events)
    assert any(event.action_type == "combined_distribution" for event in events)
    assert any(event.action_type == "proposal_only" for event in events)
    assert len({event.action_id for event in events}) == len(events)


def test_write_corporate_action_report_outputs_artifacts(tmp_path):
    data_dir = _prepare_data(tmp_path)
    records = [json.loads(line) for line in (data_dir / "corporate_actions" / "records.jsonl").read_text().splitlines()]
    events = normalize_corporate_action_records(records)

    paths = write_corporate_action_report(data_dir, events, tmp_path / "actions", "20240102", "20240104", reconcile_adjustment=True)

    assert (tmp_path / "actions" / "corporate_actions_report.json").exists()
    assert (tmp_path / "actions" / "total_return_series.jsonl").exists()
    assert (tmp_path / "actions" / "adjustment_factor_reconciliation.json").exists()
    report = json.loads((tmp_path / "actions" / "corporate_actions_report.json").read_text())
    assert report["event_count"] == 4
    assert paths["total_return_report_path"].endswith("total_return_report.json")


def test_paper_account_applies_corporate_actions_idempotently(tmp_path):
    data_dir = _prepare_data(tmp_path)
    records = [json.loads(line) for line in (data_dir / "corporate_actions" / "records.jsonl").read_text().splitlines()]
    events = normalize_corporate_action_records(records)
    account = LocalPaperAccount(tmp_path / "account")
    state = account.reset(1000.0)
    state.positions["000001.SZ"] = PaperPosition(ts_code="000001.SZ", shares=1000, avg_cost=10.0)
    account.save_state(state)

    first_state, first_apps = account.apply_corporate_actions(events, trade_date="20240104", mode="pay_date")
    second_state, second_apps = account.apply_corporate_actions(events, trade_date="20240104", mode="pay_date")

    assert sum(app.status == "APPLIED" for app in first_apps) == 1
    assert sum(app.status == "APPLIED" for app in second_apps) == 1
    assert first_state.cash == second_state.cash
    assert len(second_state.corporate_action_ledger) == 1
    assert (tmp_path / "account" / "corporate_action_ledger.jsonl").exists()


def test_loader_corporate_action_total_return_mode(tmp_path):
    data_dir = _prepare_data(tmp_path)
    records = [json.loads(line) for line in (data_dir / "corporate_actions" / "records.jsonl").read_text().splitlines()]
    events = normalize_corporate_action_records(records)
    write_corporate_action_report(data_dir, events, tmp_path / "actions", "20240102", "20240104")

    loader = AShareDataLoader(
        data_dir,
        corporate_action_aware=True,
        corporate_action_dir=tmp_path / "actions",
        target_return_mode="corporate_action_total_return",
    ).load_data()

    assert len(loader.corporate_action_events) == 4
    assert "total_return_close" in loader.raw_data_cache
    assert "corporate_action_flag" in loader.raw_data_cache
    assert loader.target_ret.shape == loader.raw_data_cache["close"].shape

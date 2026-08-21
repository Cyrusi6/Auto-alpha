import json

from auto_alpha.data.pit.corporate_actions.normalizer import normalize_corporate_action_records
from auto_alpha.data.pit.corporate_actions.reconciliation import (
    derive_causal_adjustment_factor_vintages,
)
from auto_alpha.data.pit.corporate_actions.run_actions import main as actions_main
from auto_alpha.data.pit.corporate_actions.report import write_corporate_action_report
from auto_alpha.data.ingestion.pipeline.ashare import AShareDataConfig, AShareDataManager
from auto_alpha.research.formulas.data_loader import AShareDataLoader
from auto_alpha.execution.trading.paper import LocalPaperAccount
from auto_alpha.execution.trading.paper import PaperPosition


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


def test_causal_adjustment_vintage_uses_only_known_effective_events():
    bars = [
        {"ts_code": "000001.SZ", "trade_date": "20240102", "pre_close": 10},
        {"ts_code": "000001.SZ", "trade_date": "20240103", "pre_close": 10},
        {"ts_code": "000001.SZ", "trade_date": "20240104", "pre_close": 9},
    ]
    events = [
        {
            "event_id": "cash-dividend-2024",
            "event_version_id": "event-v1",
            "ts_code": "000001.SZ",
            "known_at": "20240102",
            "effective_at": "20240103",
            "cash_div_per_share": "1",
            "stock_distribution_ratio": "0",
            "source_document_sha256": "a" * 64,
            "pit_evidence_eligible": True,
        },
        {
            "event_id": "late-cash-dividend-2024",
            "event_version_id": "late-event-v1",
            "ts_code": "000001.SZ",
            "known_at": "20240105",
            "effective_at": "20240104",
            "cash_div_per_share": "1",
            "stock_distribution_ratio": "0",
            "source_document_sha256": "b" * 64,
            "pit_evidence_eligible": True,
        },
    ]

    result = derive_causal_adjustment_factor_vintages(bars, events)

    assert result["derivation_complete"] is False
    assert result["data_admission_eligible"] is False
    assert [row["causal_adj_factor"] for row in result["rows"]] == [
        "1.000000000000",
        "1.111111111111",
        "1.111111111111",
    ]
    assert result["rows"][1]["event_version_ids"] == ["event-v1"]
    assert result["rows"][2]["event_version_ids"] == []
    assert {row["code"] for row in result["blockers"]} == {
        "corporate_action_known_after_effective"
    }


def test_future_corporate_action_version_cannot_rewrite_past_factors():
    bars = [
        {"ts_code": "000001.SZ", "trade_date": "20240102", "pre_close": 10},
        {"ts_code": "000001.SZ", "trade_date": "20240103", "pre_close": 10},
    ]
    baseline = derive_causal_adjustment_factor_vintages(bars, [])
    with_future = derive_causal_adjustment_factor_vintages(
        bars,
        [
            {
                "event_id": "future-cash-dividend-2024",
                "event_version_id": "future-v1",
                "ts_code": "000001.SZ",
                "known_at": "20240201",
                "effective_at": "20240202",
                "cash_div_per_share": "1",
                "stock_distribution_ratio": "0",
                "source_document_sha256": "c" * 64,
                "pit_evidence_eligible": True,
            }
        ],
    )

    assert with_future["rows"] == baseline["rows"]


def test_later_corporate_action_revision_cannot_rewrite_applied_factor():
    bars = [
        {"ts_code": "000001.SZ", "trade_date": "20240102", "pre_close": 10},
        {"ts_code": "000001.SZ", "trade_date": "20240103", "pre_close": 10},
        {"ts_code": "000001.SZ", "trade_date": "20240104", "pre_close": 9},
    ]
    events = [
        {
            "event_id": "cash-dividend-2024",
            "event_version_id": "event-v1",
            "ts_code": "000001.SZ",
            "known_at": "20240102",
            "effective_at": "20240103",
            "cash_div_per_share": "1",
            "stock_distribution_ratio": "0",
            "source_document_sha256": "a" * 64,
            "pit_evidence_eligible": True,
        },
        {
            "event_id": "cash-dividend-2024",
            "event_version_id": "event-v2",
            "ts_code": "000001.SZ",
            "known_at": "20240104",
            "effective_at": "20240103",
            "cash_div_per_share": "2",
            "stock_distribution_ratio": "0",
            "source_document_sha256": "b" * 64,
            "pit_evidence_eligible": True,
        },
    ]

    result = derive_causal_adjustment_factor_vintages(bars, events)

    assert result["derivation_complete"] is True
    assert [row["causal_adj_factor"] for row in result["rows"]] == [
        "1.000000000000",
        "1.111111111111",
        "1.111111111111",
    ]
    assert result["rows"][1]["event_version_ids"] == ["event-v1"]
    assert result["rows"][2]["event_version_ids"] == []


def test_causal_adjustment_vintage_cli_publishes_immutable_blocked_evidence(
    tmp_path,
):
    bars = tmp_path / "bars.jsonl"
    events = tmp_path / "events.jsonl"
    bars.write_text(
        json.dumps(
            {
                "ts_code": "000001.SZ",
                "trade_date": "20240102",
                "pre_close": 10,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    events.write_text("", encoding="utf-8")
    output = tmp_path / "vintages"

    first = actions_main(
        [
            "derive-adjustment-vintage",
            "--daily-bars",
            str(bars),
            "--event-versions",
            str(events),
            "--output-dir",
            str(output),
        ]
    )
    second = actions_main(
        [
            "derive-adjustment-vintage",
            "--daily-bars",
            str(bars),
            "--event-versions",
            str(events),
            "--output-dir",
            str(output),
        ]
    )

    pointer = json.loads((output / "current.json").read_text())
    generation = output / "generations" / pointer["manifest"].split("/")[1]
    assert first == 0
    assert second == 0
    assert (generation / "causal_adjustment_vintage_manifest.json").is_file()
    assert (generation / "causal_adjustment_factors.jsonl").is_file()
    manifest = json.loads(
        (generation / "causal_adjustment_vintage_manifest.json").read_text()
    )
    assert manifest["data_admission_eligible"] is False
    assert all(value is False for value in manifest["safety"].values())

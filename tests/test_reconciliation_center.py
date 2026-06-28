import json

from reconciliation_center.models import ReconciliationMaterialityConfig
from reconciliation_center.eod import run_eod_reconciliation


def test_reconciliation_center_report_roundtrip(tmp_path):
    statement_dir = tmp_path / "statement"
    broker_dir = tmp_path / "broker"
    account_dir = tmp_path / "account"
    output_dir = tmp_path / "eod"
    statement_dir.mkdir()
    broker_dir.mkdir()
    account_dir.mkdir()

    (statement_dir / "broker_statement_manifest.json").write_text(
        json.dumps(
            {
                "statement_id": "stmt_empty",
                "account_id": "paper_ashare",
                "schema_name": "generic_broker_statement",
                "trade_date": "20240104",
                "as_of_date": "20240104",
                "metadata": {"synthetic": True},
            }
        ),
        encoding="utf-8",
    )
    (statement_dir / "broker_statement_validation_report.json").write_text(
        json.dumps({"statement_id": "stmt_empty", "issues": []}),
        encoding="utf-8",
    )
    (statement_dir / "normalized_external_cash.jsonl").write_text(
        json.dumps(
            {
                "account_id": "paper_ashare",
                "broker_name": "local",
                "trade_date": "20240104",
                "as_of_date": "20240104",
                "cash_balance": 100.0,
                "available_cash": 100.0,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (account_dir / "account_state.json").write_text(
        json.dumps(
            {
                "account_id": "paper_ashare",
                "cash": 100.0,
                "available_cash": 100.0,
                "withdrawable_cash": 100.0,
                "positions": {},
                "snapshots": [],
                "trade_ledger": [],
                "cash_ledger": [],
                "settlement_events": [],
            }
        ),
        encoding="utf-8",
    )

    report, _mirror, paths = run_eod_reconciliation(
        statement_dir=statement_dir,
        broker_store_dir=broker_dir,
        paper_account_dir=account_dir,
        output_dir=output_dir,
        account_id="paper_ashare",
        trade_date="20240104",
        as_of_date="20240104",
        materiality=ReconciliationMaterialityConfig(),
    )
    assert report.status == "ok"
    assert report.summary["break_count"] == 0
    assert paths["eod_reconciliation_report_path"].exists()
    assert paths["external_account_mirror_path"].exists()

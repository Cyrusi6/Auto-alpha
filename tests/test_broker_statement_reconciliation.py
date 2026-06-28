import json

from approval import ApprovalStatus, ApprovalType, LocalApprovalStore
from broker_adapter import BrokerOrderRequest, SimulatedBrokerAdapter, broker_fills_to_execution_fills
from broker_statement import default_schema, import_statement, synthesize_statement_from_internal
from broker_statement.run_statement import main as statement_main
from paper_account import LocalPaperAccount
from reconciliation_center import run_eod_reconciliation
from reconciliation_center.adjustments import apply_approved_adjustments, create_adjustment_approval
from reconciliation_center.models import AdjustmentProposal, AdjustmentProposalBatch
from reconciliation_center.run_reconcile import main as reconcile_main


def _prepare_internal(tmp_path):
    broker_dir = tmp_path / "broker"
    account_dir = tmp_path / "account"
    account = LocalPaperAccount(account_dir)
    account.reset(100000.0)
    adapter = SimulatedBrokerAdapter(
        broker_dir,
        prices={"000001.SZ": 10.0},
        volumes={"000001.SZ": 100000.0},
        auto_fill=True,
    )
    request = BrokerOrderRequest(
        client_order_id="child_1",
        batch_id="batch_1",
        trade_date="20240104",
        ts_code="000001.SZ",
        side="BUY",
        shares=100,
        order_value=1000.0,
        price=10.0,
        child_order_id="child_1",
    )
    result = adapter.submit_orders([request], batch_id="batch_1")
    account.apply_fills(broker_fills_to_execution_fills(result.fills), {"000001.SZ": 10.0}, "20240104")
    account.mark_to_market({"000001.SZ": 10.0}, "20240104")
    return broker_dir, account_dir


def test_broker_statement_import_validate_and_qmt_notice(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "external_cash.csv").write_text(
        "account_id,broker_name,trade_date,as_of_date,cash_balance\npaper_ashare,synthetic,20240104,20240104,100000\n",
        encoding="utf-8",
    )
    (source / "external_positions.jsonl").write_text(
        '{"account_id":"paper_ashare","broker_name":"synthetic","trade_date":"20240104","as_of_date":"20240104","ts_code":"000001.SZ","position_shares":100,"available_shares":100,"market_value":1000}\n',
        encoding="utf-8",
    )

    result = import_statement(source, tmp_path / "imported", account_id="paper_ashare", trade_date="20240104", as_of_date="20240104")
    qmt = default_schema("qmt_statement_skeleton")

    assert result.status in {"ok", "warning"}
    assert (tmp_path / "imported" / "broker_statement_manifest.json").exists()
    assert (tmp_path / "imported" / "normalized_external_cash.jsonl").exists()
    assert "skeleton" in qmt.notice.lower()


def test_synthetic_statement_zero_break_and_cash_adjustment_workflow(tmp_path):
    broker_dir, account_dir = _prepare_internal(tmp_path)
    synthesize_statement_from_internal(
        tmp_path / "statement_zero_source",
        broker_store_dir=broker_dir,
        broker_batch_id="batch_1",
        paper_account_dir=account_dir,
        account_id="paper_ashare",
        broker_name="synthetic",
        trade_date="20240104",
        as_of_date="20240104",
    )
    import_statement(tmp_path / "statement_zero_source", tmp_path / "statement_zero", account_id="paper_ashare", trade_date="20240104", as_of_date="20240104")
    report, _mirror, _paths = run_eod_reconciliation(
        tmp_path / "statement_zero",
        tmp_path / "reconcile_zero",
        broker_store_dir=broker_dir,
        broker_batch_id="batch_1",
        paper_account_dir=account_dir,
        account_id="paper_ashare",
        trade_date="20240104",
        as_of_date="20240104",
    )
    assert report.status == "ok"
    assert report.summary["break_count"] == 0

    synthesize_statement_from_internal(
        tmp_path / "statement_diff_source",
        broker_store_dir=broker_dir,
        broker_batch_id="batch_1",
        paper_account_dir=account_dir,
        account_id="paper_ashare",
        broker_name="synthetic",
        trade_date="20240104",
        as_of_date="20240104",
        inject_cash_diff=100.0,
    )
    import_statement(tmp_path / "statement_diff_source", tmp_path / "statement_diff", account_id="paper_ashare", trade_date="20240104", as_of_date="20240104")
    diff_report, _mirror, _paths = run_eod_reconciliation(
        tmp_path / "statement_diff",
        tmp_path / "reconcile_diff",
        broker_store_dir=broker_dir,
        broker_batch_id="batch_1",
        paper_account_dir=account_dir,
        account_id="paper_ashare",
        trade_date="20240104",
        as_of_date="20240104",
        create_adjustment_proposals=True,
    )
    assert diff_report.status == "error"
    assert diff_report.summary["adjustment_proposal_count"] == 1
    assert (tmp_path / "reconcile_diff" / "adjustment_proposals.jsonl").exists()

    batch_payload = json.loads((tmp_path / "reconcile_diff" / "adjustment_proposal_batch.json").read_text(encoding="utf-8"))
    batch = AdjustmentProposalBatch(
        adjustment_batch_id=batch_payload["adjustment_batch_id"],
        account_id=batch_payload["account_id"],
        trade_date=batch_payload["trade_date"],
        as_of_date=batch_payload["as_of_date"],
        proposals=[AdjustmentProposal(**item) for item in batch_payload["proposals"]],
    )
    approval = create_adjustment_approval(
        batch,
        tmp_path / "approvals",
        reconciliation_report_path=str(tmp_path / "reconcile_diff" / "eod_reconciliation_report.json"),
        adjustment_proposals_path=str(tmp_path / "reconcile_diff" / "adjustment_proposals.jsonl"),
        metadata={
            "eod_reconciliation_status": diff_report.status,
            "unresolved_break_count": diff_report.summary["unresolved_break_count"],
            "material_break_count": diff_report.summary["material_break_count"],
        },
    )
    assert approval.approval_type == ApprovalType.account_reconciliation_adjustment
    approved = LocalApprovalStore(tmp_path / "approvals").approve(approval.approval_id, "reviewer", "ok")
    assert approved.status == ApprovalStatus.approved

    applied, _paths = apply_approved_adjustments(
        tmp_path / "approvals",
        approval.approval_id,
        account_dir,
        tmp_path / "apply",
        account_id="paper_ashare",
        trade_date="20240104",
    )
    replay, _paths = apply_approved_adjustments(
        tmp_path / "approvals",
        approval.approval_id,
        account_dir,
        tmp_path / "apply_replay",
        account_id="paper_ashare",
        trade_date="20240104",
    )
    after, _mirror, _paths = run_eod_reconciliation(
        tmp_path / "statement_diff",
        tmp_path / "reconcile_after",
        broker_store_dir=broker_dir,
        broker_batch_id="batch_1",
        paper_account_dir=account_dir,
        account_id="paper_ashare",
        trade_date="20240104",
        as_of_date="20240104",
    )

    assert applied.applied_count == 1
    assert replay.applied_count == 0
    assert replay.skipped_duplicate_count == 1
    assert after.status == "ok"
    assert (account_dir / "adjustment_ledger.jsonl").exists()


def test_statement_and_reconcile_cli(tmp_path):
    broker_dir, account_dir = _prepare_internal(tmp_path)
    assert statement_main(
        [
            "synthesize-from-internal",
            "--output-dir",
            str(tmp_path / "statement_source"),
            "--broker-store-dir",
            str(broker_dir),
            "--broker-batch-id",
            "batch_1",
            "--paper-account-dir",
            str(account_dir),
            "--trade-date",
            "20240104",
            "--as-of-date",
            "20240104",
        ]
    ) == 0
    assert statement_main(
        [
            "import",
            "--source-dir",
            str(tmp_path / "statement_source"),
            "--output-dir",
            str(tmp_path / "statement_import"),
            "--account-id",
            "paper_ashare",
            "--trade-date",
            "20240104",
            "--as-of-date",
            "20240104",
        ]
    ) == 0
    assert reconcile_main(
        [
            "eod",
            "--statement-dir",
            str(tmp_path / "statement_import"),
            "--broker-store-dir",
            str(broker_dir),
            "--broker-batch-id",
            "batch_1",
            "--paper-account-dir",
            str(account_dir),
            "--output-dir",
            str(tmp_path / "reconcile_cli"),
            "--account-id",
            "paper_ashare",
            "--trade-date",
            "20240104",
            "--as-of-date",
            "20240104",
            "--pretty",
        ]
    ) == 0
    assert (tmp_path / "reconcile_cli" / "eod_reconciliation_report.json").exists()

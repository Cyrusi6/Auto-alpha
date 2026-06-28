import json

import pytest

from approval import ApprovalBatch, ApprovalOrder, ApprovalStatus, ApprovalType, LocalApprovalStore


def _batch():
    return ApprovalBatch(
        approval_id="approval_test",
        created_at="2026-06-27T00:00:00Z",
        factor_id="factor_test",
        factor_type="composite",
        rebalance_date="20240104",
        portfolio_method="risk_aware",
        orders=[
            ApprovalOrder(
                trade_date="20240104",
                ts_code="000001.SZ",
                side="BUY",
                target_weight=0.1,
                order_value=10000.0,
            )
        ],
        risk_summary={"n_orders": 1},
        parent_orders=[{"parent_order_id": "parent_1", "ts_code": "000001.SZ"}],
        child_orders=[{"child_order_id": "child_1", "parent_order_id": "parent_1", "bucket": "open"}],
        capacity_summary={"capacity_warning_count": 0},
    )


def test_approval_store_create_list_approve_reject_and_expire(tmp_path):
    store = LocalApprovalStore(tmp_path)
    store.save_batch(_batch())

    assert store.load_batch("approval_test").status == ApprovalStatus.pending
    assert store.load_batch("approval_test").child_orders[0]["child_order_id"] == "child_1"
    assert len(store.list_batches(status=ApprovalStatus.pending)) == 1

    approved = store.approve("approval_test", reviewer="reviewer", comment="ok")
    assert approved.status == ApprovalStatus.approved
    assert approved.decision and approved.decision.comment == "ok"
    with pytest.raises(ValueError):
        store.reject("approval_test", reviewer="reviewer", reason="late")

    second = ApprovalBatch(
        **(_batch().to_dict() | {"approval_id": "approval_second"})
    )
    store.save_batch(second)
    rejected = store.reject("approval_second", reviewer="reviewer", reason="risk")
    assert rejected.status == ApprovalStatus.rejected

    third = ApprovalBatch(
        **(_batch().to_dict() | {"approval_id": "approval_third"})
    )
    store.save_batch(third)
    expired = store.expire_pending(as_of_time="2026-06-27T01:00:00Z")
    assert [batch.approval_id for batch in expired] == ["approval_third"]
    assert (tmp_path / "approval_log.jsonl").exists()
    json.dumps(store.load_batch("approval_test").to_dict())


def test_approval_store_loads_legacy_order_batch_without_lifecycle_fields(tmp_path):
    approvals_dir = tmp_path / "approvals"
    approvals_dir.mkdir()
    (approvals_dir / "legacy_order.json").write_text(
        json.dumps(
            {
                "approval_id": "legacy_order",
                "created_at": "2026-06-27T00:00:00Z",
                "factor_id": "factor_legacy",
                "factor_type": "composite",
                "rebalance_date": "20240104",
                "portfolio_method": "risk_aware",
                "orders": [],
                "risk_summary": {},
                "status": "pending",
                "decision": None,
                "metadata": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    batch = LocalApprovalStore(tmp_path).load_batch("legacy_order")

    assert batch.approval_type == ApprovalType.order_batch
    assert batch.model_version_id is None
    assert batch.lifecycle_summary == {}


def test_approval_store_account_reconciliation_adjustment_fields_roundtrip(tmp_path):
    store = LocalApprovalStore(tmp_path)
    batch = ApprovalBatch(
        approval_id="approval_adjustment",
        created_at="2026-06-27T00:00:00Z",
        factor_id="account_reconciliation",
        factor_type="account_adjustment",
        rebalance_date="20240104",
        portfolio_method="eod_reconciliation",
        orders=[],
        approval_type=ApprovalType.account_reconciliation_adjustment,
        reconciliation_report_path="/tmp/eod_reconciliation_report.json",
        adjustment_proposals_path="/tmp/adjustment_proposals.jsonl",
        adjustment_summary={"proposal_count": 1, "cash_adjustment": 100.0},
        eod_reconciliation_status="error",
        unresolved_break_count=1,
        material_break_count=1,
    )

    store.save_batch(batch)
    approved = store.approve("approval_adjustment", "reviewer", "ok")
    loaded = store.load_batch("approval_adjustment")

    assert approved.approval_type == ApprovalType.account_reconciliation_adjustment
    assert loaded.reconciliation_report_path == "/tmp/eod_reconciliation_report.json"
    assert loaded.adjustment_summary["cash_adjustment"] == 100.0
    assert loaded.material_break_count == 1
    assert loaded.status == ApprovalStatus.approved

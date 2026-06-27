import json

import pytest

from approval import ApprovalBatch, ApprovalOrder, ApprovalStatus, LocalApprovalStore


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
    )


def test_approval_store_create_list_approve_reject_and_expire(tmp_path):
    store = LocalApprovalStore(tmp_path)
    store.save_batch(_batch())

    assert store.load_batch("approval_test").status == ApprovalStatus.pending
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

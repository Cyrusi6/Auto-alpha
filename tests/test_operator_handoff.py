import json
from pathlib import Path

from approval import ApprovalStatus, LocalApprovalStore
from operator_handoff.checklist import required_item_ids
from operator_handoff.run_handoff import main as handoff_main
from operator_handoff.store import LocalOperatorHandoffStore


def test_operator_handoff_smoke_creates_checked_package_and_approval(tmp_path: Path) -> None:
    handoff_dir = tmp_path / "handoff"
    approvals = tmp_path / "approvals"
    code = handoff_main(
        [
            "smoke",
            "--handoff-store-dir",
            str(handoff_dir),
            "--approval-store-dir",
            str(approvals),
            "--output-dir",
            str(handoff_dir),
            "--file-batch-id",
            "file_batch_1",
            "--approval-id",
            "order_approval_1",
        ]
    )

    assert code == 0
    package = LocalOperatorHandoffStore(handoff_dir).load_by_file_batch("file_batch_1")
    assert package is not None
    assert {item.item_id for item in package.checklist} == set(required_item_ids())
    assert all(item.checked for item in package.checklist if item.required)
    report = json.loads((handoff_dir / "operator_handoff_report.json").read_text(encoding="utf-8"))
    assert report["missing_required_items"] == []
    approval = LocalApprovalStore(approvals).load_batch(f"handoff_{package.handoff_id}")
    assert approval.status == ApprovalStatus.approved
    assert approval.approval_type == "broker_file_handoff"

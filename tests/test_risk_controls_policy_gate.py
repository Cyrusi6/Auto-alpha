import json
from pathlib import Path

from approval import LocalApprovalStore
from risk_controls import LocalRiskControlState, evaluate_order_records, load_policy
from risk_controls.kill_switch import activate_kill_switch
from risk_controls.overrides import apply_approved_override, create_override_approval


def test_risk_controls_gate_writes_artifacts(tmp_path):
    orders = [
        {"trade_date": "20240104", "ts_code": "000001.SZ", "side": "BUY", "order_value": 1000.0, "shares": 100},
        {"trade_date": "20240104", "ts_code": "688999.SH", "side": "BUY", "order_value": 2_000_000.0, "shares": 200000},
    ]
    report, split, paths = evaluate_order_records(
        orders,
        policy_profile="strict_paper_gate",
        state_dir=tmp_path / "state",
        output_dir=tmp_path / "out",
        batch_id="batch_1",
        trade_date="20240104",
    )

    assert report.status == "rejected"
    assert report.accepted_orders == 1
    assert report.rejected_orders == 1
    assert split["accepted"][0]["ts_code"] == "000001.SZ"
    assert split["rejected"][0]["ts_code"] == "688999.SH"
    for key in [
        "risk_control_report_path",
        "risk_control_breaches_path",
        "risk_control_decisions_path",
        "accepted_orders_path",
        "rejected_orders_path",
        "kill_switch_state_path",
    ]:
        assert Path(paths[key]).exists()
    payload = json.loads(Path(paths["risk_control_report_path"]).read_text(encoding="utf-8"))
    assert payload["artifact_type"] == "risk_control_report"


def test_kill_switch_blocks_all_orders(tmp_path):
    activate_kill_switch(tmp_path / "state", "manual stop", actor="tester")
    report, split, _paths = evaluate_order_records(
        [{"trade_date": "20240104", "ts_code": "000001.SZ", "side": "BUY", "order_value": 1000.0}],
        policy_profile="cn_ashare_paper_default",
        state_dir=tmp_path / "state",
        output_dir=tmp_path / "out",
        batch_id="batch_ks",
        trade_date="20240104",
    )
    assert report.status == "blocked"
    assert report.rejected_orders == 1
    assert split["accepted"] == []
    assert report.breaches[0].limit_id == "kill_switch_active"


def test_risk_override_approval_apply_deactivates_kill_switch(tmp_path):
    state_dir = tmp_path / "state"
    approval_dir = tmp_path / "approvals"
    activate_kill_switch(state_dir, "manual stop", actor="tester")
    request, batch, request_path = create_override_approval(
        approval_store_dir=approval_dir,
        state_dir=state_dir,
        output_dir=tmp_path / "out",
        scope="global",
        reason="approved local smoke",
        requested_by="tester",
    )
    assert request_path.exists()
    assert request.approval_id == batch.approval_id
    store = LocalApprovalStore(approval_dir)
    store.approve(batch.approval_id, reviewer="reviewer", comment="ok")
    summary = apply_approved_override(
        approval_store_dir=approval_dir,
        approval_id=batch.approval_id,
        state_dir=state_dir,
        actor="tester",
        deactivate_kill_switch=True,
    )
    assert summary.status == "applied"
    assert LocalRiskControlState(state_dir).load_kill_switch().active is False
    assert LocalRiskControlState(state_dir).override_records_path.exists()


def test_default_policy_is_wide_paper_profile():
    policy = load_policy(profile="cn_ashare_paper_default")
    assert policy.profile == "cn_ashare_paper_default"
    assert any(limit.metric == "order_value" for limit in policy.limits)

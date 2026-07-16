import json

from dashboard.config import DashboardConfig
from dashboard.data_service import AshareDashboardService
from monitoring.checks import check_task055g_engineering_baseline


def _report(status: str) -> dict:
    state_counts = {
        "certification_queue": 0,
        "certified_pool": 0,
        "portfolio_campaign": 0,
        "production_candidate": 0,
        "optimizer_activation": 0,
        "paper_registry": 0,
        "live_registry": 0,
    }
    return {
        "status": status,
        "network_accessed": False,
        "network_request_count": 0,
        "prospective_holdout_accessed": False,
        "fee_schedule_v2": {"status": "passed"},
        "semantic_verification": {"status": "passed"},
        "causal_frontier": {"round_one_frontier_count": 2},
        "network_plan": {"status": "sealed_round_one_daily_only"},
        "operational_state": {
            "status": "passed",
            "total_operational_record_count": 0,
            "state_counts": state_counts,
        },
        "queues": {
            "certification": 0,
            "portfolio": 0,
            "paper": 0,
            "live": 0,
        },
        "readiness": {
            "certification_ready": False,
            "portfolio_ready": False,
            "paper_ready": False,
            "live_ready": False,
        },
        "engineering_blockers": [],
    }


def test_task055g_monitoring_accepts_only_fee_aware_waiting_boundary(tmp_path):
    report = tmp_path / "task055g_report.json"
    report.write_text(json.dumps(_report(
        "task055g_fee_aware_frontier_sealed_waiting_for_network_authorization"
    )), encoding="utf-8")

    payload, alerts = check_task055g_engineering_baseline(report)

    assert alerts == []
    assert payload["task055g_boundary_valid"] is True
    assert payload["waiting_for_network_authorization"] is True
    assert payload["downstream_queues_physically_empty"] is True
    assert payload["certification_ready"] is False
    assert payload["portfolio_ready"] is False
    assert payload["paper_ready"] is False
    assert payload["live_ready"] is False


def test_task055g_monitoring_rejects_unrecognized_or_promoted_state(tmp_path):
    report = tmp_path / "task055g_report.json"
    payload = _report("task055g_unrecognized_success")
    report.write_text(json.dumps(payload), encoding="utf-8")
    _, alerts = check_task055g_engineering_baseline(report)
    assert alerts

    payload = _report("task055g_offline_engineering_baseline_blocked")
    payload["readiness"]["portfolio_ready"] = True
    report.write_text(json.dumps(payload), encoding="utf-8")
    _, alerts = check_task055g_engineering_baseline(report)
    assert alerts


def test_task055g_dashboard_reads_content_addressed_artifacts(tmp_path):
    store = tmp_path / "validation_campaign_store"
    generation = store / "task_055_g_run" / "generations" / "report_hash"
    generation.mkdir(parents=True)
    report = _report("task055g_offline_engineering_baseline_blocked")
    (generation / "task055g_report.json").write_text(json.dumps(report), encoding="utf-8")
    fee = {"schema_version": "task055g_fee_schedule_v2", "status": "passed"}
    (generation / "fee_schedule_v2_manifest.json").write_text(json.dumps(fee), encoding="utf-8")
    verification = {"status": "verified_waiting_for_network_authorization"}
    (generation / "task055g_final_verification.json").write_text(
        json.dumps(verification), encoding="utf-8"
    )
    service = AshareDashboardService(DashboardConfig(
        validation_campaign_store_dir=store,
        report_dir=tmp_path / "reports",
    ))

    assert service.load_task_055g_final_report()["status"] == report["status"]
    assert service.load_task_055g_fee_schedule_v2()["status"] == "passed"
    assert service.load_task_055g_final_verification()["status"] == verification["status"]

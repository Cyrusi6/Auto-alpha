import json

from dashboard.config import DashboardConfig
from dashboard.data_service import AshareDashboardService
from monitoring.checks import check_task055h_offline_authorization


READY = "canary_authorization_ready_no_network_executed"
BLOCKED = "task055h_canary_authorization_blocked_no_network_executed"


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _evidence(tmp_path, *, blocked: bool = False):
    status = BLOCKED if blocked else READY
    operational_status = "blocked" if blocked else "passed"
    engineering_blockers = ["operational_state_unproven:runtime_root_missing"] if blocked else []
    report = {
        "status": status,
        "content_hash": "report_hash",
        "authorization_seal_content_hash": "authorization_hash",
        "fee_attestation_content_hash": "fee_hash",
        "operational_seal_content_hash": "operational_hash",
        "frontier_count": 17,
        "frontier_root": "frontier_root",
        "plan_hash": "plan_hash",
        "credential_read_count": 0,
        "tushare_request_count": 0,
        "other_network_request_count": 0,
        "prospective_holdout_accessed": False,
        "resume_authorized": False,
        "engineering_blockers": engineering_blockers,
        "readiness": {
            "canary_authorization_ready": not blocked,
            "certification_ready": False,
            "portfolio_ready": False,
            "paper_ready": False,
            "live_ready": False,
        },
    }
    authorization = {
        "status": status,
        "content_hash": "authorization_hash",
        "ordered_exact_daily_key_count": 17,
        "ordered_exact_daily_keys": [
            {"ordinal": index, "ts_code": f"probe_{index}", "trade_date": "20200102"}
            for index in range(1, 18)
        ],
        "frontier_root": "frontier_root",
        "task055g_plan_hash": "plan_hash",
        "resume_authorized": False,
        "network_execution": {
            "credential_read_count": 0,
            "tushare_request_count": 0,
            "other_network_request_count": 0,
            "prospective_holdout_accessed": False,
        },
    }
    fee = {
        "status": "passed",
        "content_hash": "fee_hash",
        "official_rate_or_statutory_interval_record_count": 28,
        "uncalibrated_modeled_record_count": 12,
    }
    operational = {
        "status": operational_status,
        "content_hash": "operational_hash",
        "blockers": ["runtime_root_missing"] if blocked else [],
        "state_counts": {
            "certification_queue": 0,
            "certified_pool": 0,
            "portfolio_campaign": 0,
            "production_candidate": 0,
            "optimizer_activation": 0,
            "paper_registry": 0,
            "live_registry": 0,
        },
    }
    verification = {
        "status": "blocked_verified" if blocked else "passed",
        "top_status": status,
        "report_content_hash": "report_hash",
        "authorization_seal_content_hash": "authorization_hash",
        "credential_read_count": 0,
        "tushare_request_count": 0,
        "other_network_request_count": 0,
        "prospective_holdout_accessed": False,
    }
    return {
        "report": _write(tmp_path / "task055h_report.json", report),
        "authorization": _write(tmp_path / "authorization_seal.json", authorization),
        "fee": _write(tmp_path / "fee_attestation.json", fee),
        "operational": _write(tmp_path / "operational_seal.json", operational),
        "verification": _write(tmp_path / "task055h_final_verification.json", verification),
    }


def _check(paths):
    return check_task055h_offline_authorization(
        paths["report"],
        authorization_seal_path=paths["authorization"],
        fee_attestation_path=paths["fee"],
        operational_seal_path=paths["operational"],
        final_verification_path=paths["verification"],
    )


def test_task055h_monitoring_accepts_offline_authorization_ready(tmp_path):
    payload, alerts = _check(_evidence(tmp_path))

    assert alerts == []
    assert payload["task055h_boundary_valid"] is True
    assert payload["offline_execution_proven"] is True
    assert payload["credential_read_count"] == 0
    assert payload["network_request_count"] == 0
    assert payload["prospective_holdout_access_count"] == 0
    assert payload["frontier_key_count"] == 17
    assert payload["official_fee_record_count"] == 28
    assert payload["modeled_fee_record_count"] == 12
    assert payload["operational_state_proven"] is True
    assert payload["operational_state_unproven"] is False
    assert payload["canary_authorization_ready"] is True


def test_task055h_monitoring_displays_verified_operational_blocker(tmp_path):
    payload, alerts = _check(_evidence(tmp_path, blocked=True))

    assert alerts == []
    assert payload["task055h_boundary_valid"] is True
    assert payload["offline_blocked"] is True
    assert payload["operational_state_proven"] is False
    assert payload["operational_state_unproven"] is True
    assert payload["downstream_queues_proven_empty"] is False
    assert payload["canary_authorization_ready"] is False


def test_task055h_monitoring_rejects_any_credential_or_network_activity(tmp_path):
    paths = _evidence(tmp_path)
    report = json.loads(paths["report"].read_text(encoding="utf-8"))
    report["credential_read_count"] = 1
    paths["report"].write_text(json.dumps(report), encoding="utf-8")

    payload, alerts = _check(paths)

    assert alerts
    assert payload["offline_execution_proven"] is False
    assert payload["canary_authorization_ready"] is False


def test_task055h_dashboard_reads_content_addressed_evidence(tmp_path):
    store = tmp_path / "validation_campaign_store"
    generation = store / "task_055_h_run" / "generations" / "evidence"
    evidence = _evidence(generation, blocked=True)
    service = AshareDashboardService(DashboardConfig(
        validation_campaign_store_dir=store,
        report_dir=tmp_path / "reports",
    ))

    assert service.load_task_055h_final_report()["status"] == BLOCKED
    assert service.load_task_055h_authorization_seal()["ordered_exact_daily_key_count"] == 17
    assert service.load_task_055h_fee_attestation()["official_rate_or_statutory_interval_record_count"] == 28
    assert service.load_task_055h_operational_seal()["status"] == "blocked"
    assert evidence["verification"].is_file()

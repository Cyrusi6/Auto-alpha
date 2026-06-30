from __future__ import annotations

import json
from pathlib import Path

from dashboard.config import DashboardConfig
from dashboard.data_service import AshareDashboardService
from monitoring.checks import check_live_readiness, check_production_replay, check_shadow_lab


def test_monitoring_and_dashboard_read_replay_shadow_readiness(tmp_path: Path):
    replay_dir = tmp_path / "production_replay"
    shadow_dir = tmp_path / "shadow_lab"
    readiness_dir = tmp_path / "live_readiness"
    replay_dir.mkdir()
    shadow_dir.mkdir()
    readiness_dir.mkdir()
    (replay_dir / "production_replay_report.json").write_text(
        json.dumps({"replay_id": "r1", "status": "success", "summary": {"replay_day_count": 1, "replay_success_day_count": 1}}),
        encoding="utf-8",
    )
    (replay_dir / "production_replay_days.jsonl").write_text(json.dumps({"replay_id": "r1", "trade_date": "20240104", "status": "success"}) + "\n", encoding="utf-8")
    (shadow_dir / "shadow_lab_report.json").write_text(
        json.dumps(
            {
                "status": "ok",
                "performance_summary": {"shadow_day_count": 1, "shadow_average_fill_rate": 1.0},
                "drift_summary": {"shadow_target_weight_drift": 0.0, "shadow_position_weight_drift": 0.0},
                "calibration_suggestions": [],
            }
        ),
        encoding="utf-8",
    )
    (readiness_dir / "live_readiness_decision.json").write_text(
        json.dumps({"status": "ready_for_shadow", "passed": True, "new_status": "ready_for_shadow", "score": 1.0, "required_remediation": []}),
        encoding="utf-8",
    )
    (readiness_dir / "live_readiness_scorecard.json").write_text(
        json.dumps({"status": "ready_for_shadow", "score": 1.0, "checks": [], "summary": {"readiness_failed_check_count": 0}}),
        encoding="utf-8",
    )
    replay_check, replay_alerts = check_production_replay(replay_dir / "production_replay_report.json")
    shadow_check, shadow_alerts = check_shadow_lab(shadow_dir / "shadow_lab_report.json")
    readiness_check, readiness_alerts = check_live_readiness(readiness_dir / "live_readiness_decision.json", readiness_dir / "live_readiness_scorecard.json")
    assert replay_check["replay_day_count"] == 1
    assert shadow_check["shadow_day_count"] == 1
    assert readiness_check["live_readiness_status"] == "ready_for_shadow"
    assert replay_alerts == []
    assert shadow_alerts == []
    assert readiness_alerts == []

    service = AshareDashboardService(
        DashboardConfig(
            data_dir=tmp_path / "data",
            factor_store_dir=tmp_path / "store",
            report_dir=tmp_path / "reports",
            backtest_dir=tmp_path / "backtest",
            orders_dir=tmp_path / "orders",
            production_replay_dir=replay_dir,
            shadow_lab_dir=shadow_dir,
            live_readiness_dir=readiness_dir,
        )
    )
    assert service.load_production_replay_report()["replay_id"] == "r1"
    assert len(service.load_production_replay_days()) == 1
    assert service.load_shadow_lab_report()["status"] == "ok"
    assert service.load_live_readiness_decision()["passed"] is True

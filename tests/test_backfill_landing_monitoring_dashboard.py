from __future__ import annotations

import json
from pathlib import Path

from artifact_schema.run_validate import main as schema_main
from dashboard.config import DashboardConfig
from dashboard.data_service import AshareDashboardService
from monitoring.run_monitor import main as monitor_main


def test_monitoring_dashboard_and_schema_read_new_artifacts(tmp_path: Path) -> None:
    observer = tmp_path / "observer"
    landing = tmp_path / "landing"
    observer.mkdir()
    landing.mkdir()
    (observer / "backfill_observer_report.json").write_text(
        json.dumps(
            {
                "artifact_type": "backfill_observer_report",
                "schema_version": "1.0",
                "observed_run": {"active_dataset": "daily_bars", "status": "running"},
                "datasets": [],
                "eta": {"remaining_jobs": 2, "confidence": "medium"},
                "summary": {"active_backfill_dataset": "daily_bars", "progress_ratio": 0.5, "pending_jobs": 2, "failed_jobs": 0},
            }
        ),
        encoding="utf-8",
    )
    (observer / "backfill_dataset_progress.jsonl").write_text(
        json.dumps({"dataset": "daily_bars", "progress_ratio": 0.5, "failed_jobs": 0, "pending_jobs": 2}) + "\n",
        encoding="utf-8",
    )
    (observer / "backfill_eta_report.json").write_text(json.dumps({"remaining_jobs": 2, "confidence": "medium", "estimated_remaining_minutes": 1.0}), encoding="utf-8")
    (observer / "backfill_postprocess_plan.json").write_text(json.dumps({"plan_id": "p", "steps": [], "blockers": ["pending"]}), encoding="utf-8")
    (landing / "raw_data_landing_report.json").write_text(json.dumps({"data_dir": "x", "datasets": [], "freeze_readiness": {}, "summary": {"raw_landing_status": "warning"}}), encoding="utf-8")
    (landing / "raw_freeze_readiness_decision.json").write_text(json.dumps({"status": "blocked", "blocker_count": 1, "warning_count": 0, "checks": []}), encoding="utf-8")

    monitor_dir = tmp_path / "monitor"
    rc = monitor_main(
        [
            "--data-dir",
            str(tmp_path / "data"),
            "--factor-store-dir",
            str(tmp_path / "store"),
            "--paper-account-dir",
            str(tmp_path / "account"),
            "--orders-dir",
            str(tmp_path / "orders"),
            "--output-dir",
            str(monitor_dir),
            "--as-of-date",
            "20240104",
            "--backfill-observer-report-path",
            str(observer / "backfill_observer_report.json"),
            "--backfill-dataset-progress-path",
            str(observer / "backfill_dataset_progress.jsonl"),
            "--backfill-eta-report-path",
            str(observer / "backfill_eta_report.json"),
            "--backfill-postprocess-plan-path",
            str(observer / "backfill_postprocess_plan.json"),
            "--raw-data-landing-report-path",
            str(landing / "raw_data_landing_report.json"),
            "--raw-freeze-readiness-decision-path",
            str(landing / "raw_freeze_readiness_decision.json"),
        ]
    )
    assert rc in {0, 1}
    monitoring = json.loads((monitor_dir / "monitoring_report.json").read_text(encoding="utf-8"))
    assert monitoring["active_backfill_dataset"] == "daily_bars"
    assert monitoring["raw_freeze_blocker_count"] == 1

    schema_dir = tmp_path / "schema"
    assert schema_main(["--artifact-dir", str(observer), "--artifact-dir", str(landing), "--output-dir", str(schema_dir), "--write-manifest"]) == 0
    validation = json.loads((schema_dir / "artifact_validation_report.json").read_text(encoding="utf-8"))
    assert validation["artifact_count"] >= 4

    service = AshareDashboardService(
        DashboardConfig(
            data_dir=tmp_path / "data",
            backfill_dir=observer,
            report_dir=tmp_path / "reports",
        )
    )
    assert service.load_backfill_observer_report()["observed_run"]["active_dataset"] == "daily_bars"

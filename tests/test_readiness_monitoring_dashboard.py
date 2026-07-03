import json
from pathlib import Path

from artifact_schema.run_validate import main as validate_main
from dashboard.config import DashboardConfig
from dashboard.data_service import AshareDashboardService
from monitoring.run_monitor import main as monitor_main


def test_monitoring_dashboard_and_schema_read_new_readiness_artifacts(tmp_path: Path):
    readiness_dir = tmp_path / "readiness"
    post_dir = tmp_path / "post_download"
    readiness_dir.mkdir()
    post_dir.mkdir()
    (tmp_path / "data" / "trade_calendar").mkdir(parents=True)
    (tmp_path / "data" / "trade_calendar" / "records.jsonl").write_text(
        json.dumps({"trade_date": "20240104", "is_open": True}) + "\n",
        encoding="utf-8",
    )
    _write_json(
        readiness_dir / "research_data_readiness_report.json",
        {
            "artifact_type": "research_data_readiness_report",
            "schema_version": "1.0",
            "decision": {"status": "ready_for_alpha_factory", "blocker_count": 0, "warning_count": 1, "alpha_ready": True},
            "dataset_checks": [],
            "feature_readiness": [],
            "summary": {"research_data_readiness_status": "ready_for_alpha_factory"},
        },
    )
    _write_json(
        readiness_dir / "feature_readiness_catalog.json",
        {"artifact_type": "feature_readiness_catalog", "schema_version": "1.0", "feature_families": [], "summary": {}},
    )
    _write_json(
        readiness_dir / "research_readiness_decision.json",
        {"artifact_type": "research_readiness_decision", "schema_version": "1.0", "status": "ready_for_alpha_factory", "core_ready": True, "alpha_ready": True},
    )
    _write_json(
        post_dir / "post_download_plan.json",
        {"artifact_type": "post_download_plan", "schema_version": "1.0", "plan_id": "p1", "readiness_status": "ready_for_alpha_factory", "steps": []},
    )
    _write_json(
        post_dir / "post_download_run_report.json",
        {"artifact_type": "post_download_run_report", "schema_version": "1.0", "run_id": "r1", "mode": "plan_only", "status": "planned", "plan": {}, "summary": {}},
    )

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
            str(tmp_path / "monitoring"),
            "--as-of-date",
            "20240104",
            "--research-data-readiness-report-path",
            str(readiness_dir / "research_data_readiness_report.json"),
            "--research-readiness-decision-path",
            str(readiness_dir / "research_readiness_decision.json"),
            "--feature-readiness-catalog-path",
            str(readiness_dir / "feature_readiness_catalog.json"),
            "--post-download-plan-path",
            str(post_dir / "post_download_plan.json"),
            "--post-download-run-report-path",
            str(post_dir / "post_download_run_report.json"),
            "--pretty",
        ]
    )
    assert rc == 0
    monitor_payload = json.loads((tmp_path / "monitoring" / "monitoring_report.json").read_text())
    assert monitor_payload["research_data_readiness_status"] == "ready_for_alpha_factory"

    service = AshareDashboardService(
        DashboardConfig(
            data_dir=tmp_path / "data",
            factor_store_dir=tmp_path / "store",
            report_dir=tmp_path / "reports",
            backfill_dir=tmp_path,
        )
    )
    assert service.load_research_data_readiness_report()["decision"]["status"] == "ready_for_alpha_factory"
    assert service.load_post_download_plan()["plan_id"] == "p1"

    assert validate_main(["--artifact-dir", str(readiness_dir), "--artifact-dir", str(post_dir), "--output-dir", str(tmp_path / "schema"), "--fail-on-error"]) == 0


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

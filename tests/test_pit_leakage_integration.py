import json

from artifact_schema.registry import get_definition, infer_artifact_type
from artifact_schema.run_validate import main as validate_main
from dashboard.config import DashboardConfig
from dashboard.data_service import AshareDashboardService
from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from leakage_audit.run_audit import main as leakage_main
from model_core import engine
from point_in_time.run_pit import main as pit_main
from release_manager.inventory import PLATFORM_MODULES


def _prepare_artifacts(tmp_path, capsys):
    data_dir = tmp_path / "data"
    store_dir = tmp_path / "store"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True)
    pit_main(
        [
            "validate",
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(tmp_path / "pit"),
            "--start-date",
            "20240102",
            "--end-date",
            "20240104",
            "--as-of-date",
            "20240104",
        ]
    )
    engine.main(
        [
            "--dry-run",
            "--register",
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(tmp_path / "out"),
            "--factor-store-dir",
            str(store_dir),
            "--report-dir",
            str(tmp_path / "reports"),
        ]
    )
    leakage_main(
        [
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(store_dir),
            "--output-dir",
            str(tmp_path / "leakage"),
            "--as-of-date",
            "20240104",
            "--cutoff-date",
            "20240104",
            "--point-in-time",
            "--run-static-scan",
        ]
    )
    capsys.readouterr()


def test_artifact_schema_dashboard_and_release_inventory_for_pit_leakage(tmp_path, capsys):
    _prepare_artifacts(tmp_path, capsys)
    rc = validate_main(
        [
            "--artifact-dir",
            str(tmp_path / "pit"),
            "--artifact-dir",
            str(tmp_path / "leakage"),
            "--output-dir",
            str(tmp_path / "schema"),
            "--write-manifest",
        ]
    )
    capsys.readouterr()
    report = json.loads((tmp_path / "schema" / "artifact_validation_report.json").read_text())
    service = AshareDashboardService(
        DashboardConfig(
            data_dir=tmp_path / "data",
            factor_store_dir=tmp_path / "store",
            report_dir=tmp_path / "reports",
            backtest_dir=tmp_path / "backtest",
            orders_dir=tmp_path / "orders",
            pit_dir=tmp_path / "pit",
            leakage_dir=tmp_path / "leakage",
        )
    )

    assert rc == 0
    assert report["error_count"] == 0
    assert infer_artifact_type(tmp_path / "pit" / "pit_validation_report.json") == "pit_validation_report"
    assert get_definition("leakage_audit_report") is not None
    assert service.load_pit_validation_report()["warning_count"] >= 0
    assert service.load_survivorship_bias_report()["current_only_security_master"] is True
    assert service.load_leakage_audit_report()["status"] in {"passed", "warning", "failed"}
    assert not service.load_active_security_mask().empty
    assert "point_in_time" in PLATFORM_MODULES
    assert "leakage_audit" in PLATFORM_MODULES

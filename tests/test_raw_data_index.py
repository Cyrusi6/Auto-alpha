from __future__ import annotations

import json
from pathlib import Path

import torch

from artifact_schema.run_validate import main as schema_main
from dashboard.config import DashboardConfig
from dashboard.data_service import AshareDashboardService
from feature_factory import FEATURE_SET_V3, build_feature_set_manifest
from feature_factory.coverage import build_feature_coverage_report
from matrix_refresh.planner import build_matrix_refresh_plan
from monitoring.checks import check_raw_data_index
from post_download_orchestrator.planner import build_post_download_plan
from raw_data_index.run_index import main as index_main
from raw_data_index.scanner import active_run_safety_check, build_raw_data_index
from raw_data_index.validator import validate_raw_data_index
from raw_data_landing.report import build_landing_report


def _write_jsonl(path: Path, rows: list[dict], malformed_tail: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    if malformed_tail:
        lines.append("{bad-json")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _prepare_data(data_dir: Path, *, malformed: bool = False) -> None:
    _write_jsonl(
        data_dir / "securities" / "records.jsonl",
        [
            {"ts_code": "000001.SZ", "name": "Ping An", "list_date": "20200101"},
            {"ts_code": "000002.SZ", "name": "Vanke", "list_date": "20200101"},
        ],
    )
    _write_jsonl(
        data_dir / "trade_calendar" / "records.jsonl",
        [
            {"trade_date": "20240102", "is_open": True},
            {"trade_date": "20240103", "is_open": True},
        ],
    )
    _write_jsonl(
        data_dir / "daily_bars" / "records.jsonl",
        [
            {"ts_code": "000001.SZ", "trade_date": "20240102", "close": 10.0, "amount": 1000.0},
            {"ts_code": "000002.SZ", "trade_date": "20240102", "close": 20.0, "amount": 2000.0},
            {"ts_code": "000001.SZ", "trade_date": "20240103", "close": 10.5, "amount": 1100.0},
        ],
    )
    _write_jsonl(
        data_dir / "daily_basic" / "records.jsonl",
        [
            {"ts_code": "000001.SZ", "trade_date": "20240102", "turnover_rate": 1.0, "total_mv": 100.0},
            {"ts_code": "000002.SZ", "trade_date": "20240102", "turnover_rate": 2.0, "total_mv": 200.0},
        ],
    )
    _write_jsonl(
        data_dir / "moneyflow" / "records.jsonl",
        [{"ts_code": "000001.SZ", "trade_date": "20240102", "net_mf_amount": 10.0}],
        malformed_tail=malformed,
    )


def test_raw_data_index_build_validate_and_schema(tmp_path: Path, capsys) -> None:
    data_dir = tmp_path / "data"
    output_dir = tmp_path / "raw_index"
    _prepare_data(data_dir, malformed=True)

    rc = index_main(
        [
            "build",
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(output_dir),
            "--datasets",
            "securities,trade_calendar,daily_bars,daily_basic,moneyflow",
            "--partition-granularity",
            "monthly",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["status"] == "partial"
    assert payload["total_records"] == 10
    manifest = json.loads((output_dir / "raw_data_index_manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifact_type"] == "raw_data_index_manifest"
    assert manifest["total_parse_errors"] == 1
    partitions = (output_dir / "raw_partitions.jsonl").read_text(encoding="utf-8")
    assert "daily_bars:202401" in partitions

    validation = validate_raw_data_index(output_dir / "raw_data_index_manifest.json", data_dir=data_dir)
    assert validation.status == "partial"
    assert validation.parse_error_count == 1

    schema_rc = schema_main(["--artifact-dir", str(output_dir), "--output-dir", str(tmp_path / "schema"), "--write-manifest"])
    assert schema_rc == 0
    schema_report = json.loads((tmp_path / "schema" / "artifact_validation_report.json").read_text(encoding="utf-8"))
    assert schema_report["error_count"] == 0


def test_raw_data_index_stale_and_active_run_safety(tmp_path: Path, capsys) -> None:
    data_dir = tmp_path / "data"
    output_dir = tmp_path / "raw_index"
    _prepare_data(data_dir)
    assert index_main(["build", "--data-dir", str(data_dir), "--output-dir", str(output_dir), "--datasets", "securities,daily_bars"]) == 0
    capsys.readouterr()

    with (data_dir / "daily_bars" / "records.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"ts_code": "000002.SZ", "trade_date": "20240103", "close": 21.0}) + "\n")
    rc = index_main(["validate", "--data-dir", str(data_dir), "--output-dir", str(output_dir), "--fail-on-stale", "--pretty"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["status"] == "stale"
    assert payload["stale_dataset_count"] >= 1
    assert (output_dir / "raw_dataset_indexes.jsonl").exists()
    assert (output_dir / "raw_dataset_indexes.jsonl").read_text(encoding="utf-8").strip()

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "backfill_state.json").write_text('{"status":"running"}', encoding="utf-8")
    safety = active_run_safety_check(data_dir=data_dir, run_dir=run_dir, selected_datasets=["daily_bars"])
    assert safety["blocked"] is True
    manifest, indexes, partitions, issues, _ = build_raw_data_index(data_dir, datasets=["daily_bars"], run_dir=run_dir)
    assert manifest is None
    assert indexes == []
    assert partitions == []
    assert any(issue["code"] == "active_backfill_state" for issue in issues)


def test_raw_landing_matrix_feature_monitoring_and_dashboard_use_index(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    output_dir = tmp_path / "raw_index"
    _prepare_data(data_dir)
    manifest, indexes, partitions, issues, _ = build_raw_data_index(
        data_dir,
        datasets=["securities", "trade_calendar", "daily_bars", "daily_basic"],
        output_dir=output_dir,
        allow_active_run_index=True,
    )
    assert manifest is not None
    from raw_data_index.report import write_raw_data_index_artifacts

    paths = write_raw_data_index_artifacts(
        manifest=manifest,
        dataset_indexes=indexes,
        partitions=partitions,
        validation=None,
        issues=issues,
        output_dir=output_dir,
        data_dir=data_dir,
        status=manifest.status,
    )
    validation = validate_raw_data_index(paths["raw_data_index_manifest_path"], data_dir=data_dir)
    paths = write_raw_data_index_artifacts(
        manifest=manifest,
        dataset_indexes=indexes,
        partitions=partitions,
        validation=validation,
        issues=validation.issues,
        output_dir=output_dir,
        data_dir=data_dir,
        status=validation.status,
    )

    landing = build_landing_report(
        data_dir=data_dir,
        datasets=["securities", "trade_calendar", "daily_bars", "daily_basic"],
        raw_data_index_manifest_path=paths["raw_data_index_manifest_path"],
    )
    assert landing.summary["index_used"] is True
    assert landing.summary["index_status"] == "fresh"

    plan = build_matrix_refresh_plan(
        data_dir=data_dir,
        matrix_cache_dir=tmp_path / "matrix_cache",
        raw_data_index_manifest_path=paths["raw_data_index_manifest_path"],
    )
    assert plan.raw_data_index_status == "fresh"
    assert plan.raw_data_index_hash == manifest.index_hash
    assert plan.source_dataset_index_count == 4

    feature_manifest = build_feature_set_manifest(FEATURE_SET_V3)
    tensor = torch.zeros((1, feature_manifest.feature_count, 1), dtype=torch.float32)
    coverage = build_feature_coverage_report(
        feature_manifest,
        tensor,
        raw_data_index_summary={"raw_data_index_used": True, "status": "fresh", "dataset_index_count": 4},
    )
    assert coverage.raw_data_index_used is True
    assert coverage.dataset_index_status["status"] == "fresh"

    check, alerts = check_raw_data_index(
        paths["raw_data_index_manifest_path"],
        paths["raw_data_index_report_path"],
        paths["raw_data_index_validation_report_path"],
    )
    assert check["raw_data_index_status"] == "fresh"
    assert alerts == []

    service = AshareDashboardService(
        DashboardConfig(
            data_dir=data_dir,
            raw_data_index_dir=output_dir,
            report_dir=tmp_path / "reports",
        )
    )
    assert service.load_raw_data_index_manifest()["index_hash"] == manifest.index_hash
    assert len(service.load_raw_dataset_indexes()) == 4
    assert not service.load_raw_partitions().empty


def test_post_download_plan_includes_raw_data_index_steps(tmp_path: Path) -> None:
    readiness = tmp_path / "readiness.json"
    readiness.write_text(json.dumps({"decision": {"status": "not_ready", "required_remediations": ["download still running"]}}), encoding="utf-8")
    plan = build_post_download_plan(
        data_dir=tmp_path / "data",
        run_dir=tmp_path / "run",
        staging_dir=tmp_path / "staging",
        output_dir=tmp_path / "post",
        registry_dir=tmp_path / "registry",
        freeze_dir=tmp_path / "freeze",
        matrix_cache_dir=tmp_path / "matrix",
        readiness_report_path=readiness,
        profile_name="unit",
        start_date="20240102",
        end_date="20240104",
    )
    by_id = {step.step_id: step for step in plan.steps}
    assert "raw_data_index_plan" in by_id
    assert "raw_data_index_build" in by_id
    assert "raw_data_index_validate" in by_id
    assert by_id["raw_data_index_plan"].blocked is False
    assert by_id["raw_data_index_build"].blocked is True
    assert "--plan-only" in by_id["raw_data_index_plan"].command

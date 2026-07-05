from __future__ import annotations

import json
from pathlib import Path

from artifact_schema.run_validate import main as schema_main
from dashboard.config import DashboardConfig
from dashboard.data_service import AshareDashboardService
from data_quality_lab.run_quality_lab import main as quality_main
from data_quality_lab.scanner import run_data_quality_scan
from monitoring.checks import check_data_quality_lab
from post_download_orchestrator.planner import build_post_download_plan
from raw_data_index.report import write_raw_data_index_artifacts
from raw_data_index.scanner import build_raw_data_index
from raw_data_index.validator import validate_raw_data_index
from research_data_readiness.report import build_research_data_readiness_report


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n", encoding="utf-8")


def _prepare_bad_data(data_dir: Path) -> None:
    _write_jsonl(
        data_dir / "securities" / "records.jsonl",
        [
            {"ts_code": "000001.SZ", "name": "Ping An", "list_status": "L", "list_date": "20200101"},
            {"ts_code": "000002.SZ", "name": "Vanke", "list_status": "L", "list_date": "20200101"},
        ],
    )
    _write_jsonl(
        data_dir / "trade_calendar" / "records.jsonl",
        [
            {"cal_date": "20240102", "trade_date": "20240102", "is_open": True},
            {"cal_date": "20240103", "trade_date": "20240103", "is_open": False},
        ],
    )
    _write_jsonl(
        data_dir / "daily_bars" / "records.jsonl",
        [
            {
                "ts_code": "000001.SZ",
                "trade_date": "20240102",
                "open": 10.0,
                "high": 9.0,
                "low": 9.5,
                "close": 10.5,
                "pre_close": 10.0,
                "pct_chg": 1.0,
                "volume": 0,
                "amount": 100.0,
            },
            {
                "ts_code": "000001.SZ",
                "trade_date": "20240102",
                "open": 10.0,
                "high": 10.5,
                "low": 9.5,
                "close": 10.0,
                "pre_close": 10.0,
                "pct_chg": 0.0,
                "volume": 100,
                "amount": 1000.0,
            },
            {
                "ts_code": "000002.SZ",
                "trade_date": "20240103",
                "open": 20.0,
                "high": 20.5,
                "low": 19.5,
                "close": 25.0,
                "pre_close": 20.0,
                "pct_chg": 25.0,
                "volume": 100,
                "amount": 2500.0,
            },
        ],
    )
    _write_jsonl(
        data_dir / "daily_basic" / "records.jsonl",
        [{"ts_code": "000001.SZ", "trade_date": "20240102", "turnover_rate": -1.0, "volume_ratio": 1.0, "total_mv": 100.0, "circ_mv": 90.0, "pb": 0.9}],
    )
    _write_jsonl(
        data_dir / "daily_limits" / "records.jsonl",
        [
            {"ts_code": "000001.SZ", "trade_date": "20240102", "up_limit": 11.0, "down_limit": 9.0},
            {"ts_code": "000002.SZ", "trade_date": "20240103", "up_limit": 21.0, "down_limit": 18.0},
        ],
    )
    _write_jsonl(
        data_dir / "adjustment_factors" / "records.jsonl",
        [{"ts_code": "000001.SZ", "trade_date": "20240102", "adj_factor": 0.0}],
    )
    _write_jsonl(
        data_dir / "financial_features" / "records.jsonl",
        [{"ts_code": "000001.SZ", "end_date": "20231231", "ann_date": "", "roe": 0.1}],
    )
    _write_jsonl(
        data_dir / "index_members" / "records.jsonl",
        [{"index_code": "000300.SH", "trade_date": "20240102", "ts_code": "999999.SZ", "weight": 100.0}],
    )
    _write_jsonl(data_dir / "corporate_actions" / "records.jsonl", [{"ts_code": "000001.SZ", "ann_date": "", "ex_date": "20240104", "cash_div": 0.0}])
    _write_jsonl(data_dir / "income_statements" / "records.jsonl", [{"ts_code": "000001.SZ", "end_date": "20231231", "ann_date": ""}])
    _write_jsonl(data_dir / "hk_holdings" / "records.jsonl", [])


def _build_index(data_dir: Path, output_dir: Path) -> dict[str, str]:
    manifest, indexes, partitions, issues, _safety = build_raw_data_index(
        data_dir,
        datasets=["securities", "trade_calendar", "daily_bars", "daily_basic", "daily_limits", "adjustment_factors"],
        output_dir=output_dir,
        allow_active_run_index=True,
    )
    assert manifest is not None
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
    return write_raw_data_index_artifacts(
        manifest=manifest,
        dataset_indexes=indexes,
        partitions=partitions,
        validation=validation,
        issues=[*issues, *validation.issues],
        output_dir=output_dir,
        data_dir=data_dir,
        status=validation.status,
    )


def test_data_quality_lab_detects_semantic_issues_and_blocks_core_gate(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    output_dir = tmp_path / "quality"
    _prepare_bad_data(data_dir)
    index_paths = _build_index(data_dir, tmp_path / "raw_index")

    rc = quality_main(
        [
            "run",
            "--data-dir",
            str(data_dir),
            "--raw-data-index-manifest-path",
            index_paths["raw_data_index_manifest_path"],
            "--output-dir",
            str(output_dir),
            "--datasets",
            "securities,trade_calendar,daily_bars,daily_basic,daily_limits,adjustment_factors,financial_features,index_members,corporate_actions,income_statements,hk_holdings",
            "--use-raw-data-index",
            "--pretty",
        ]
    )
    assert rc == 0
    issues = [json.loads(line) for line in (output_dir / "data_quality_issues.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    rule_ids = {issue["rule_id"] for issue in issues}
    assert "daily_bars.ohlc_order" in rule_ids
    assert "daily_bars.primary_key_unique" in rule_ids
    assert "daily_bars.trade_date_open" in rule_ids
    assert "cross.daily_limit_violation" in rule_ids
    assert "financial_features.pit_ann_date" in rule_ids
    assert "statements.pit_ann_date" in rule_ids
    assert any(issue["dataset"] == "hk_holdings" and issue["severity"] == "info" for issue in issues)

    freeze_gate = json.loads((output_dir / "data_quality_freeze_gate.json").read_text(encoding="utf-8"))
    assert freeze_gate["can_create_freeze"] is False
    assert freeze_gate["can_build_matrix"] is False
    assert freeze_gate["core_blocker_count"] > 0
    suggestions = [json.loads(line) for line in (output_dir / "data_quality_repair_suggestions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert suggestions
    assert all(item["automatic"] is False for item in suggestions)

    check, alerts = check_data_quality_lab(
        output_dir / "data_quality_lab_report.json",
        output_dir / "data_quality_scorecard.json",
        output_dir / "data_quality_freeze_gate.json",
        output_dir / "data_quality_issues.jsonl",
    )
    assert check["data_quality_can_create_freeze"] is False
    assert any(alert.severity == "error" for alert in alerts)

    schema_rc = schema_main(["--artifact-dir", str(output_dir), "--output-dir", str(tmp_path / "schema"), "--write-manifest"])
    assert schema_rc == 0
    schema_report = json.loads((tmp_path / "schema" / "artifact_validation_report.json").read_text(encoding="utf-8"))
    assert schema_report["error_count"] == 0


def test_data_quality_scan_api_and_dashboard_loaders(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    output_dir = tmp_path / "quality"
    _prepare_bad_data(data_dir)
    report, issues, suggestions, rules = run_data_quality_scan(
        data_dir,
        output_dir=output_dir,
        datasets=["securities", "trade_calendar", "daily_bars", "daily_basic", "daily_limits", "adjustment_factors", "hk_holdings"],
    )
    from data_quality_lab.report import write_data_quality_lab_report

    write_data_quality_lab_report(report, issues, suggestions, rules, output_dir)
    service = AshareDashboardService(DashboardConfig(data_dir=data_dir, data_quality_lab_dir=output_dir, report_dir=tmp_path / "reports"))
    assert service.load_data_quality_freeze_gate()["can_create_freeze"] is False
    assert not service.load_data_quality_issues().empty
    assert not service.load_dataset_quality_summary().empty
    assert not service.load_data_quality_repair_suggestions().empty


def test_post_download_plan_and_readiness_consume_data_quality_gate(tmp_path: Path) -> None:
    gate = tmp_path / "data_quality_freeze_gate.json"
    gate.write_text(
        json.dumps(
            {
                "status": "blocked",
                "can_create_freeze": False,
                "can_build_matrix": False,
                "can_run_core_alpha": False,
                "can_run_expanded_alpha": False,
                "blocker_count": 2,
                "core_blocker_count": 2,
                "expanded_blocker_count": 0,
                "recommended_next_action": "repair core semantic blockers before freeze",
            }
        ),
        encoding="utf-8",
    )
    data_dir = tmp_path / "empty_data"
    data_dir.mkdir()
    readiness = build_research_data_readiness_report(
        data_dir,
        data_quality_freeze_gate_path=gate,
        profile_name="unit",
    )
    assert readiness.decision.can_create_freeze is False
    assert readiness.decision.can_build_matrix is False
    assert readiness.summary["data_quality_core_blocker_count"] == 2

    readiness_path = tmp_path / "readiness.json"
    readiness_path.write_text(json.dumps({"decision": {"status": "not_ready", "required_remediations": ["download running"]}}), encoding="utf-8")
    plan = build_post_download_plan(
        data_dir=data_dir,
        run_dir=tmp_path / "run",
        staging_dir=tmp_path / "staging",
        output_dir=tmp_path / "post_download",
        registry_dir=tmp_path / "registry",
        freeze_dir=tmp_path / "freeze",
        matrix_cache_dir=tmp_path / "matrix",
        readiness_report_path=readiness_path,
        profile_name="unit",
        start_date="20240102",
        end_date="20240104",
    )
    step_ids = [step.step_id for step in plan.steps]
    assert "data_quality_plan" in step_ids
    assert "data_quality_run" in step_ids
    assert "data_quality_scorecard" in step_ids
    assert "data_quality_freeze_gate" in step_ids
    plan_step = next(step for step in plan.steps if step.step_id == "data_quality_plan")
    run_step = next(step for step in plan.steps if step.step_id == "data_quality_run")
    assert plan_step.blocked is False
    assert run_step.blocked is True

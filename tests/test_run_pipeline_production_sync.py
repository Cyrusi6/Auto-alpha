import json
from pathlib import Path

from data_pipeline import run_pipeline
from data_pipeline.ashare import LocalAshareStorage


def test_run_pipeline_plan_only_outputs_production_plan(tmp_path, capsys):
    result = run_pipeline.main(
        [
            "--plan-only",
            "--provider",
            "sample",
            "--data-dir",
            str(tmp_path),
            "--start-date",
            "20240102",
            "--end-date",
            "20240104",
            "--datasets",
            "daily_bars,index_members",
            "--index-codes",
            "000300.SH",
            "--chunk-days",
            "1",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["provider"] == "sample"
    assert payload["plan_id"].startswith("plan_")
    assert len(payload["jobs"]) == 6


def test_run_pipeline_sync_use_plan_with_governance_artifacts(tmp_path, capsys):
    result = run_pipeline.main(
        [
            "--sync",
            "--use-plan",
            "--provider",
            "sample",
            "--data-dir",
            str(tmp_path),
            "--start-date",
            "20240102",
            "--end-date",
            "20240104",
            "--index-codes",
            "000300.SH",
            "--chunk-days",
            "1",
            "--validate",
            "--audit",
            "--stats",
            "--compact",
            "--snapshot",
            "--mode",
            "append",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert Path(payload["plan_path"]).exists()
    assert Path(payload["stats_path"]).exists()
    assert Path(payload["quality_report_path"]).exists()
    assert Path(payload["snapshot_path"]).exists()
    assert payload["has_errors"] is False
    assert len(LocalAshareStorage(tmp_path).read_dataset("daily_bars")) == 6


def test_run_pipeline_validate_only_and_fail_on_quality_error(tmp_path, capsys):
    storage = LocalAshareStorage(tmp_path)
    storage.write_dataset("daily_limits", [{"trade_date": "20240102", "ts_code": "000001.SZ", "up_limit": 1, "down_limit": 2, "pre_close": 1}])

    result = run_pipeline.main(
        [
            "--validate-only",
            "--provider",
            "sample",
            "--data-dir",
            str(tmp_path),
            "--fail-on-quality-error",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result != 0
    assert payload["has_errors"] is True
    assert (tmp_path / "quality_report.json").exists()


def test_run_pipeline_compact_stats_snapshot_standalone(tmp_path, capsys):
    run_pipeline.main(["--sync", "--provider", "sample", "--data-dir", str(tmp_path), "--mode", "append"])
    capsys.readouterr()

    result = run_pipeline.main(
        [
            "--provider",
            "sample",
            "--data-dir",
            str(tmp_path),
            "--compact",
            "--stats",
            "--snapshot",
            "--snapshot-name",
            "manual_snap",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["compaction_summary"]
    assert Path(payload["stats_path"]).exists()
    assert payload["snapshot_paths"]
    assert all("manual_snap" in path for path in payload["snapshot_paths"])

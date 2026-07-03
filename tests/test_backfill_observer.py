from __future__ import annotations

import json
from pathlib import Path

from backfill_observer.run_observer import main as observer_main


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def test_backfill_observer_generates_progress_repair_and_postprocess(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    run_dir = tmp_path / "run"
    out_dir = tmp_path / "observer"
    _write_jsonl(
        data_dir / "trade_calendar" / "records.jsonl",
        [
            {"trade_date": "20240102", "is_open": True},
            {"trade_date": "20240103", "is_open": True},
            {"trade_date": "20240104", "is_open": True},
        ],
    )
    _write_jsonl(
        data_dir / "daily_bars" / "records.jsonl",
        [
            {"ts_code": "000001.SZ", "trade_date": "20240102", "close": 10.0},
            {"ts_code": "000001.SZ", "trade_date": "20240103", "close": 10.1},
        ],
    )
    state = {
        "plan_id": "test_plan",
        "jobs": {
            "job_daily_1": {"job_id": "job_daily_1", "dataset": "daily_bars", "status": "success", "records": 2},
            "job_daily_2": {"job_id": "job_daily_2", "dataset": "daily_bars", "status": "failed", "error": "429 rate limit"},
            "job_cal": {"job_id": "job_cal", "dataset": "trade_calendar", "status": "success", "records": 3},
        },
    }
    run_dir.mkdir(parents=True)
    (run_dir / "backfill_state.json").write_text(json.dumps(state), encoding="utf-8")

    rc = observer_main(
        [
            "observe",
            "--run-dir",
            str(run_dir),
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(out_dir),
            "--datasets",
            "trade_calendar,daily_bars",
            "--expected-trade-days",
            "3",
            "--rate-limit-per-minute",
            "150",
            "--pretty",
        ]
    )

    assert rc == 0
    report = json.loads((out_dir / "backfill_observer_report.json").read_text(encoding="utf-8"))
    assert report["artifact_type"] == "backfill_observer_report"
    assert report["summary"]["backfill_failed_jobs"] == 1
    assert report["eta"]["remaining_jobs"] >= 1
    repair = json.loads((out_dir / "backfill_repair_plan.json").read_text(encoding="utf-8"))
    assert "--rate-limit-per-minute 150" in "\n".join(repair["commands"])
    assert "--allow-network" in "\n".join(repair["commands"])
    postprocess = json.loads((out_dir / "backfill_postprocess_plan.json").read_text(encoding="utf-8"))
    assert postprocess["blockers"]


def test_backfill_observer_missing_state_warns_without_crashing(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _write_jsonl(data_dir / "securities" / "records.jsonl", [{"ts_code": "000001.SZ", "list_date": "20200101"}])
    out_dir = tmp_path / "observer"
    assert observer_main(["smoke", "--run-dir", str(tmp_path / "missing_run"), "--data-dir", str(data_dir), "--output-dir", str(out_dir)]) == 0
    report = json.loads((out_dir / "backfill_observer_report.json").read_text(encoding="utf-8"))
    assert report["observed_run"]["metadata"]["warnings"]

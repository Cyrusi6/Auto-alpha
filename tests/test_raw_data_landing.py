from __future__ import annotations

import json
from pathlib import Path

from raw_data_landing.run_landing import main as landing_main


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def test_raw_data_landing_report_and_freeze_gate(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    out_dir = tmp_path / "landing"
    _write_jsonl(data_dir / "securities" / "records.jsonl", [{"ts_code": "000001.SZ", "list_date": "20200101"}])
    _write_jsonl(data_dir / "trade_calendar" / "records.jsonl", [{"trade_date": "20240102", "is_open": True}])
    _write_jsonl(
        data_dir / "daily_bars" / "records.jsonl",
        [
            {"ts_code": "000001.SZ", "trade_date": "20240102", "close": 10.0},
            {"ts_code": "000001.SZ", "trade_date": "20240102", "close": 10.0},
        ],
    )

    rc = landing_main(
        [
            "report",
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(out_dir),
            "--datasets",
            "securities,trade_calendar,daily_bars,daily_basic",
            "--core-datasets",
            "securities,trade_calendar,daily_bars,daily_basic",
            "--expected-trade-days",
            "3",
            "--pretty",
        ]
    )

    assert rc == 0
    report = json.loads((out_dir / "raw_data_landing_report.json").read_text(encoding="utf-8"))
    assert report["artifact_type"] == "raw_data_landing_report"
    decision = json.loads((out_dir / "raw_freeze_readiness_decision.json").read_text(encoding="utf-8"))
    assert decision["status"] == "blocked"
    assert any("daily_basic" in blocker for blocker in decision["blockers"])


def test_raw_data_landing_fail_on_blocker(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    out_dir = tmp_path / "landing"
    _write_jsonl(data_dir / "securities" / "records.jsonl", [{"ts_code": "000001.SZ"}])
    rc = landing_main(
        [
            "freeze-readiness",
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(out_dir),
            "--datasets",
            "securities,trade_calendar",
            "--core-datasets",
            "securities,trade_calendar",
            "--fail-on-blocker",
        ]
    )
    assert rc == 1

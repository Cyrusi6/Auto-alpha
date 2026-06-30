from __future__ import annotations

import json
from pathlib import Path

from live_readiness.run_readiness import main as readiness_main
from shadow_lab.run_shadow_lab import main as shadow_lab_main


def test_shadow_lab_and_readiness_cli(tmp_path: Path):
    replay_dir = tmp_path / "replay"
    shadow_dir = tmp_path / "shadow" / "20240104"
    shadow_dir.mkdir(parents=True)
    shadow_report_path = shadow_dir / "shadow_run_report.json"
    shadow_drift_path = shadow_dir / "shadow_drift_report.json"
    shadow_report_path.write_text(
        json.dumps(
            {
                "production_run_id": "prod_1",
                "trade_date": "20240104",
                "status": "success",
                "summary": {"fill_rate": 1.0, "order_count": 1, "fill_count": 1, "daily_return": 0.01},
                "orders": [{"shadow_order_id": "order_1"}],
                "fills": [{"shadow_fill_id": "fill_1", "status": "FILLED"}],
                "snapshots": [{"equity": 101.0}],
            }
        ),
        encoding="utf-8",
    )
    shadow_drift_path.write_text(
        json.dumps({"summary": {"target_weight_drift": 0.01, "position_weight_drift": 0.01}}),
        encoding="utf-8",
    )
    replay_payload = {
        "replay_id": "replay_1",
        "status": "success",
        "summary": {"replay_day_count": 1, "replay_failed_day_count": 0, "replay_blocked_day_count": 0},
        "day_results": [
            {
                "trade_date": "20240104",
                "status": "success",
                "production_run_id": "prod_1",
                "paths": {
                    "shadow_run_report_path": str(shadow_report_path),
                    "shadow_drift_report_path": str(shadow_drift_path),
                },
            }
        ],
    }
    replay_dir.mkdir()
    (replay_dir / "production_replay_report.json").write_text(json.dumps(replay_payload), encoding="utf-8")

    lab_dir = tmp_path / "shadow_lab"
    code = shadow_lab_main(
        [
            "analyze",
            "--replay-report-path",
            str(replay_dir / "production_replay_report.json"),
            "--output-dir",
            str(lab_dir),
            "--min-shadow-days",
            "1",
            "--pretty",
        ]
    )
    assert code == 0
    lab_report = json.loads((lab_dir / "shadow_lab_report.json").read_text(encoding="utf-8"))
    assert lab_report["artifact_type"] == "shadow_lab_report"
    assert lab_report["performance_summary"]["shadow_day_count"] == 1

    readiness_dir = tmp_path / "readiness"
    code = readiness_main(
        [
            "run",
            "--policy-profile",
            "sample_lenient_readiness",
            "--production-replay-report-path",
            str(replay_dir / "production_replay_report.json"),
            "--shadow-lab-report-path",
            str(lab_dir / "shadow_lab_report.json"),
            "--output-dir",
            str(readiness_dir),
            "--pretty",
        ]
    )
    assert code == 0
    decision = json.loads((readiness_dir / "live_readiness_decision.json").read_text(encoding="utf-8"))
    assert decision["artifact_type"] == "live_readiness_decision"
    assert decision["passed"] is True
    assert decision["status"] == "ready_for_shadow"

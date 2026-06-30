from __future__ import annotations

import json
from pathlib import Path

from production_replay.models import ProductionReplayConfig, ReplayMode
from production_replay.run_replay import main as replay_main
from production_replay.runner import ProductionReplayRunner


def test_production_replay_plan_cli_writes_schema_artifacts(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_calendar(data_dir)
    output_dir = tmp_path / "replay"
    code = replay_main(
        [
            "plan",
            "--replay-name",
            "unit",
            "--replay-mode",
            "shadow_only",
            "--replay-state-dir",
            str(tmp_path / "state"),
            "--output-dir",
            str(output_dir),
            "--start-date",
            "20240102",
            "--end-date",
            "20240104",
            "--data-dir",
            str(data_dir),
        ]
    )
    assert code == 0
    plan = json.loads((output_dir / "production_replay_plan.json").read_text(encoding="utf-8"))
    assert plan["artifact_type"] == "production_replay_plan"
    assert plan["day_count"] == 3


def test_production_replay_runner_auto_approves_and_resumes(monkeypatch, tmp_path: Path):
    calls: list[list[str]] = []

    def fake_production_main(argv):
        calls.append(list(argv))
        command = argv[0]
        if command == "plan-day":
            payload = {"status": "planned", "production_run_id": "prod_20240104", "paths": {"production_run_plan_path": "plan.json"}}
        elif command == "run-day":
            payload = {
                "status": "waiting_approval",
                "production_run_id": "prod_20240104",
                "summary": {"approval_id": "approval_1", "gate_blocker_count": 0},
            }
        elif command == "resume":
            payload = {"status": "success", "production_run_id": "prod_20240104", "summary": {"fill_rate": 1.0}}
        elif command == "close-day":
            payload = {"status": "closed", "production_run_id": "prod_20240104", "summary": {"close_day_status": "closed"}}
        else:
            payload = {"status": "failed"}
        print(json.dumps(payload))
        return 0

    def fake_approval_main(argv):
        print(json.dumps({"approval_id": "approval_1", "status": "approved"}))
        return 0

    monkeypatch.setattr("production_replay.runner.production_main", fake_production_main)
    monkeypatch.setattr("production_replay.runner.approval_main", fake_approval_main)
    cfg = ProductionReplayConfig(
        replay_id="replay_unit",
        replay_name="unit",
        replay_mode=ReplayMode.paper_simulated,
        start_date="20240104",
        end_date="20240104",
        trade_dates=["20240104"],
        data_dir=str(tmp_path / "data"),
        output_dir=str(tmp_path / "out"),
        replay_state_dir=str(tmp_path / "state"),
        factor_store_dir=str(tmp_path / "store"),
        approval_store_dir=str(tmp_path / "approvals"),
        paper_account_dir=str(tmp_path / "account"),
        auto_approve_paper_local=True,
    )
    payload = ProductionReplayRunner(cfg).run()
    assert payload["status"] == "success"
    assert payload["summary"]["replay_success_day_count"] == 1
    assert any(call[0] == "resume" for call in calls)
    days = (tmp_path / "out" / "production_replay_days.jsonl").read_text(encoding="utf-8")
    assert "approval_1" in days


def _write_calendar(data_dir: Path) -> None:
    path = data_dir / "trade_calendar" / "records.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"trade_date": "20240102", "is_open": True},
        {"trade_date": "20240103", "is_open": True},
        {"trade_date": "20240104", "is_open": True},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

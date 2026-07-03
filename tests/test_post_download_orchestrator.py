import json
from pathlib import Path

from post_download_orchestrator.planner import build_post_download_plan
from post_download_orchestrator.run_post_download import main as post_download_main


def test_post_download_plan_only_generates_safe_steps(tmp_path: Path):
    readiness = tmp_path / "readiness.json"
    readiness.write_text(json.dumps({"decision": {"status": "ready_for_alpha_factory", "required_remediations": []}}), encoding="utf-8")

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

    assert plan.blockers == []
    assert any(step.step_id == "matrix_refresh" for step in plan.steps)
    assert all("tushare" not in step.command.lower() for step in plan.steps)


def test_execute_refuses_not_ready(tmp_path: Path, capsys):
    readiness = tmp_path / "readiness.json"
    readiness.write_text(
        json.dumps({"decision": {"status": "not_ready", "required_remediations": ["daily_bars missing"]}}),
        encoding="utf-8",
    )

    rc = post_download_main(
        [
            "plan",
            "--data-dir",
            str(tmp_path / "data"),
            "--run-dir",
            str(tmp_path / "run"),
            "--staging-dir",
            str(tmp_path / "staging"),
            "--output-dir",
            str(tmp_path / "post"),
            "--readiness-report-path",
            str(readiness),
            "--execute",
            "--pretty",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert rc == 1
    assert payload["status"] == "blocked"
    assert Path(tmp_path / "post" / "post_download_plan.json").exists()


def test_execute_complete_fake_sample_writes_state_and_candidate(tmp_path: Path, capsys):
    readiness = tmp_path / "readiness.json"
    readiness.write_text(
        json.dumps(
            {
                "decision": {
                    "status": "ready_for_alpha_factory",
                    "core_ready": True,
                    "alpha_ready": True,
                    "blocker_count": 0,
                    "required_remediations": [],
                    "recommended_next_commands": [],
                },
                "summary": {"weak_pit_dataset_count": 1},
                "dataset_checks": [],
            }
        ),
        encoding="utf-8",
    )

    rc = post_download_main(
        [
            "run",
            "--data-dir",
            str(tmp_path / "data"),
            "--run-dir",
            str(tmp_path / "run"),
            "--staging-dir",
            str(tmp_path / "staging"),
            "--output-dir",
            str(tmp_path / "post"),
            "--readiness-report-path",
            str(readiness),
            "--matrix-cache-dir",
            str(tmp_path / "matrix"),
            "--execute",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["status"] == "success"
    assert (tmp_path / "post" / "post_download_state.json").exists()
    assert (tmp_path / "post" / "post_download_step_runs.jsonl").exists()
    package = json.loads((tmp_path / "post" / "freeze_candidate_package.json").read_text(encoding="utf-8"))
    assert package["status"] == "approved_candidate"

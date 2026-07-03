import json
from pathlib import Path

from backfill_repair.run_repair import main as repair_main


def test_repair_runner_dry_run_execute_and_resume(tmp_path: Path, capsys):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    marker = tmp_path / "marker.txt"
    job_results = tmp_path / "backfill_job_results.jsonl"
    job_results.write_text(
        json.dumps(
            {
                "job_id": "job_1",
                "dataset": "daily_bars",
                "provider": "sample",
                "status": "failed",
                "error": "timeout",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    repair_plan = tmp_path / "backfill_repair_plan.json"
    command = f"python -c \"from pathlib import Path; Path(r'{marker}').write_text('ran')\""
    repair_plan.write_text(json.dumps({"repair_plan_id": "rp1", "commands": [command]}), encoding="utf-8")

    rc = repair_main(
        [
            "dry-run",
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(tmp_path / "repair"),
            "--repair-plan-path",
            str(repair_plan),
            "--job-results-path",
            str(job_results),
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["status"] == "dry_run"
    assert not marker.exists()

    rc = repair_main(
        [
            "execute",
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(tmp_path / "repair"),
            "--repair-plan-path",
            str(repair_plan),
            "--job-results-path",
            str(job_results),
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["status"] == "success"
    assert payload["summary"]["success_jobs"] == 1
    assert (tmp_path / "repair" / "repair_run_state.json").exists()

    rc = repair_main(
        [
            "resume",
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(tmp_path / "repair"),
            "--repair-plan-path",
            str(repair_plan),
            "--job-results-path",
            str(job_results),
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["summary"]["resumed_jobs"] == 1


def test_repair_runner_blocks_real_data_path_without_explicit_allow(tmp_path: Path, capsys):
    repair_plan = tmp_path / "backfill_repair_plan.json"
    repair_plan.write_text(json.dumps({"repair_plan_id": "rp1", "commands": ["echo repair"]}), encoding="utf-8")

    rc = repair_main(
        [
            "execute",
            "--data-dir",
            "/home/lijunsi/data/auto-alpha/ashare_lake/data",
            "--output-dir",
            str(tmp_path / "repair"),
            "--repair-plan-path",
            str(repair_plan),
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["status"] == "blocked"
    assert "real data paths" in payload["summary"]["blocked_reason"]

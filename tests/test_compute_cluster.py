import json
import sys

from compute_cluster import (
    ComputeDeviceType,
    ComputeJobKind,
    ComputeJobSpec,
    ComputeSchedulerConfig,
    LocalComputeJobStore,
    LocalComputeScheduler,
    probe_compute_resources,
)
from compute_cluster.run_compute import main as compute_main


def test_compute_probe_and_cpu_scheduler_are_offline(tmp_path):
    snapshot = probe_compute_resources()
    assert snapshot.cpu_count >= 1
    assert snapshot.devices

    state_dir = tmp_path / "state"
    output_dir = tmp_path / "compute"
    job = ComputeJobSpec(
        job_id="cpu_ok",
        job_kind=ComputeJobKind.SHELL_COMMAND,
        command=[sys.executable, "-c", "print('compute ok')"],
        required_device_type=ComputeDeviceType.CPU,
        env={"LOCAL_SECRET_TOKEN": "redacted"},
    )
    store = LocalComputeJobStore(state_dir)
    result = store.submit_jobs([job, job])
    assert result == {"submitted": 1, "skipped_existing": 1}

    report = LocalComputeScheduler(ComputeSchedulerConfig(state_dir=str(state_dir), output_dir=str(output_dir))).run()
    assert report.status == "success"
    assert report.job_count == 1
    assert report.success_count == 1
    assert report.redacted_env_count == 1
    assert (output_dir / "compute_run_report.json").exists()
    assert (output_dir / "compute_jobs.jsonl").exists()
    assert (output_dir / "compute_job_runs.jsonl").exists()
    assert "compute ok" in (output_dir / "jobs" / "cpu_ok" / "stdout_tail.txt").read_text(encoding="utf-8")


def test_compute_cli_smoke_writes_report(tmp_path, capsys):
    exit_code = compute_main(
        [
            "smoke",
            "--state-dir",
            str(tmp_path / "state"),
            "--output-dir",
            str(tmp_path / "smoke"),
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["status"] == "success"
    assert payload["success_count"] >= 1
    assert (tmp_path / "smoke" / "compute_run_report.json").exists()

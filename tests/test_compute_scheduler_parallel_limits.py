import json
import sys

from compute_cluster import ComputeDeviceType, ComputeJobKind, ComputeJobSpec, ComputeSchedulerConfig, LocalComputeJobStore, LocalComputeScheduler


def test_compute_scheduler_runs_cpu_jobs_with_parallel_limit(tmp_path):
    state_dir = tmp_path / "state"
    output_dir = tmp_path / "compute"
    jobs = [
        ComputeJobSpec(
            job_id=f"cpu_sleep_{idx}",
            job_kind=ComputeJobKind.SHELL_COMMAND,
            command=[sys.executable, "-c", "import time; time.sleep(0.05); print('ok')"],
            required_device_type=ComputeDeviceType.CPU,
        )
        for idx in range(4)
    ]

    store = LocalComputeJobStore(state_dir)
    assert store.submit_jobs(jobs) == {"submitted": 4, "skipped_existing": 0}
    report = LocalComputeScheduler(
        ComputeSchedulerConfig(
            state_dir=str(state_dir),
            output_dir=str(output_dir),
            max_parallel_cpu_jobs=2,
            max_parallel_gpu_jobs=1,
        )
    ).run()

    assert report.status == "success"
    assert report.job_count == 4
    assert report.success_count == 4
    state = json.loads((state_dir / "compute_job_state.json").read_text(encoding="utf-8"))
    assert {row["status"] for row in state["jobs"].values()} == {"success"}
    runs = [json.loads(line) for line in (state_dir / "compute_job_runs.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(runs) == 4
    assert all(run["status"] == "success" for run in runs)
    assert (output_dir / "compute_run_report.json").exists()

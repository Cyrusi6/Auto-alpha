import json

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact
from dashboard.config import DashboardConfig
from dashboard.data_service import AshareDashboardService
from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from monitoring.run_monitor import main as monitor_main


def _write_compute_experiment_artifacts(root):
    compute_dir = root / "compute"
    experiment_dir = root / "experiment"
    benchmark_dir = root / "benchmark"
    compute_dir.mkdir()
    experiment_dir.mkdir()
    benchmark_dir.mkdir()
    write_json_artifact(
        compute_dir / "compute_resource_snapshot.json",
        {
            "captured_at": "2026-06-28T00:00:00Z",
            "cpu_count": 8,
            "memory_total_mb": 1024.0,
            "memory_available_mb": 512.0,
            "torch_version": "test",
            "cuda_available": False,
            "cuda_device_count": 0,
            "devices": [{"device_id": "cpu:0", "device_type": "cpu", "name": "cpu"}],
            "warnings": [],
        },
        "compute_resource_snapshot",
        "test",
    )
    write_json_artifact(
        compute_dir / "compute_run_report.json",
        {
            "run_id": "run_test",
            "status": "success",
            "job_count": 1,
            "success_count": 1,
            "failed_count": 0,
            "skipped_count": 0,
            "resumed_count": 0,
            "timeout_count": 0,
            "total_gpu_allocated_seconds": 0.0,
            "fallback_to_cpu_count": 1,
            "oom_error_count": 0,
        },
        "compute_run_report",
        "test",
    )
    write_jsonl_artifact(
        compute_dir / "compute_jobs.jsonl",
        [{"job_id": "job_1", "job_kind": "shell_command", "command": ["python", "-c", "pass"]}],
        "compute_jobs",
        "test",
    )
    write_jsonl_artifact(
        compute_dir / "compute_job_runs.jsonl",
        [{"job_id": "job_1", "status": "success", "duration_seconds": 0.1}],
        "compute_job_runs",
        "test",
    )
    write_jsonl_artifact(compute_dir / "gpu_leases.jsonl", [], "gpu_leases", "test")
    write_json_artifact(
        experiment_dir / "experiment_plan.json",
        {"experiment_id": "exp_1", "workflow": "full_research_compute_smoke", "compute_jobs": []},
        "experiment_plan",
        "test",
    )
    write_json_artifact(
        experiment_dir / "experiment_run_report.json",
        {"experiment_id": "exp_1", "workflow": "full_research_compute_smoke", "status": "success", "shard_count": 2, "failed_shard_count": 0},
        "experiment_run_report",
        "test",
    )
    write_json_artifact(
        experiment_dir / "experiment_merge_report.json",
        {"status": "success", "shard_count": 2, "missing_shard_count": 0},
        "experiment_merge_report",
        "test",
    )
    write_json_artifact(
        benchmark_dir / "benchmark_result.json",
        {
            "summary": {
                "formula_eval_formulas_per_second_cpu": 10.0,
                "pretrain_samples_per_second_cpu": 5.0,
                "gpu_count_detected": 0,
            },
            "items": [],
        },
        "gpu_benchmark_report",
        "test",
    )
    return compute_dir, experiment_dir, benchmark_dir


def test_dashboard_reads_compute_and_experiment_artifacts(tmp_path):
    compute_dir, experiment_dir, benchmark_dir = _write_compute_experiment_artifacts(tmp_path)
    service = AshareDashboardService(
        DashboardConfig(
            compute_dir=compute_dir,
            experiment_dir=experiment_dir,
            benchmark_dir=benchmark_dir,
        )
    )
    assert service.load_compute_resource_snapshot()["cuda_available"] is False
    assert service.load_compute_run_report()["job_count"] == 1
    assert len(service.load_compute_jobs()) == 1
    assert service.load_experiment_run_report()["status"] == "success"
    assert service.load_experiment_merge_report()["status"] == "success"
    assert service.load_gpu_benchmark_report()["summary"]["pretrain_samples_per_second_cpu"] == 5.0


def test_monitoring_reads_compute_experiment_artifacts(tmp_path, capsys):
    data_dir = tmp_path / "data"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True)
    compute_dir, experiment_dir, benchmark_dir = _write_compute_experiment_artifacts(tmp_path)
    exit_code = monitor_main(
        [
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(tmp_path / "store"),
            "--paper-account-dir",
            str(tmp_path / "account"),
            "--orders-dir",
            str(tmp_path / "orders"),
            "--output-dir",
            str(tmp_path / "monitoring"),
            "--as-of-date",
            "20240104",
            "--compute-run-report-path",
            str(compute_dir / "compute_run_report.json"),
            "--compute-resource-snapshot-path",
            str(compute_dir / "compute_resource_snapshot.json"),
            "--experiment-run-report-path",
            str(experiment_dir / "experiment_run_report.json"),
            "--experiment-merge-report-path",
            str(experiment_dir / "experiment_merge_report.json"),
            "--gpu-benchmark-report-path",
            str(benchmark_dir / "benchmark_result.json"),
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["compute_job_count"] == 1
    assert payload["fallback_to_cpu_count"] == 1
    assert payload["experiment_status"] == "success"
    assert payload["formula_eval_throughput"] == 10.0

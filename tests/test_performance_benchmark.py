import json

from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from matrix_store import build_matrix_cache
from performance_benchmark import run_benchmark
from performance_benchmark.run_benchmark import main as benchmark_main


def test_performance_benchmark_writes_reports(tmp_path, capsys):
    data_dir = tmp_path / "data"
    output_dir = tmp_path / "benchmark"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True)
    build_matrix_cache(data_dir)

    result = run_benchmark(data_dir=data_dir, matrix_cache_dir=data_dir / "matrix_cache", output_dir=output_dir)

    assert (output_dir / "benchmark_result.json").exists()
    assert (output_dir / "benchmark_report.md").exists()
    item_names = {item.name for item in result.items}
    assert {
        "gpu_probe",
        "jsonl_loader_load_data",
        "matrix_loader_load_data",
        "stackvm_execute_default_formulas",
        "scheduler_overhead_smoke",
    } <= item_names
    assert all(item.wall_time_seconds >= 0 for item in result.items if item.success)
    assert "gpu_count_detected" in result.summary
    assert "fallback_to_cpu_count" in result.summary

    exit_code = benchmark_main(
        [
            "--data-dir",
            str(data_dir),
            "--matrix-cache-dir",
            str(data_dir / "matrix_cache"),
            "--output-dir",
            str(tmp_path / "benchmark_cli"),
            "--skip-gpu-if-unavailable",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["summary"]["successful_items"] >= 3

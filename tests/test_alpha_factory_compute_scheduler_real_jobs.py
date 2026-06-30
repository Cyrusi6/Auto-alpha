import json

from alpha_factory.run_factory import main as run_factory_main
from data_pipeline.ashare import AShareDataConfig, AShareDataManager


def test_alpha_factory_compute_scheduler_runs_real_batch_eval_jobs(tmp_path, capsys):
    data_dir = tmp_path / "data"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True)
    compute_state_dir = tmp_path / "compute_state"
    compute_output_dir = tmp_path / "compute_output"
    batch_eval_dir = tmp_path / "batch_eval"

    exit_code = run_factory_main(
        [
            "run",
            "--campaign-name",
            "unit_alpha_compute",
            "--data-dir",
            str(data_dir),
            "--factor-store-dir",
            str(tmp_path / "store"),
            "--report-dir",
            str(tmp_path / "reports"),
            "--output-dir",
            str(tmp_path / "alpha"),
            "--candidate-budget",
            "8",
            "--template-budget",
            "2",
            "--random-budget",
            "2",
            "--mutation-budget",
            "1",
            "--crossover-budget",
            "1",
            "--corpus-budget",
            "0",
            "--proxy-max-candidates",
            "8",
            "--top-k",
            "3",
            "--use-batch-eval",
            "--use-compute-scheduler",
            "--compute-state-dir",
            str(compute_state_dir),
            "--compute-output-dir",
            str(compute_output_dir),
            "--batch-eval-dir",
            str(batch_eval_dir),
            "--batch-eval-device",
            "cpu",
            "--batch-eval-chunk-size",
            "2",
            "--shard-count",
            "2",
            "--max-parallel-cpu-jobs",
            "2",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["summary"]["compute_run_report_path"] == payload["paths"]["compute_run_report_path"]
    assert (compute_state_dir / "compute_jobs.jsonl").exists()
    assert (compute_state_dir / "compute_job_runs.jsonl").exists()
    assert (compute_output_dir / "compute_run_report.json").exists()
    compute_report = json.loads((compute_output_dir / "compute_run_report.json").read_text(encoding="utf-8"))
    assert compute_report["artifact_type"] == "compute_run_report"
    assert compute_report["producer"] == "compute_cluster"
    assert compute_report["job_count"] == 2
    assert compute_report["success_count"] == 2

    for shard_id in range(2):
        shard_output = batch_eval_dir / "shards" / f"shard_{shard_id:04d}" / "output"
        assert shard_output.exists()
        assert (shard_output / "formula_batch_eval_result.json").exists()
        assert (shard_output / "formula_eval_results.jsonl").exists()
        assert (shard_output / "resource_usage.json").exists()
        assert (shard_output / "shard_manifest.json").exists()
    assert (batch_eval_dir / "merged" / "formula_batch_eval_result.json").exists()
    assert payload["paths"]["formula_batch_eval_result_path"] == str(batch_eval_dir / "merged" / "formula_batch_eval_result.json")

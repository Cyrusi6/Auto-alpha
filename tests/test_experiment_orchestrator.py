import json

from data_pipeline.ashare import AShareDataConfig, AShareDataManager
from experiment_orchestrator import create_experiment_plan, run_workflow_smoke
from experiment_orchestrator.run_experiment import main as experiment_main


def test_experiment_plan_shards_formula_corpus(tmp_path):
    data_dir = tmp_path / "data"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True)
    plan = create_experiment_plan(
        {
            "data_dir": str(data_dir),
            "output_dir": str(tmp_path / "experiment"),
            "compute_state_dir": str(tmp_path / "compute_state"),
            "workflow": "full_research_compute_smoke",
            "shard_count": 2,
            "device": "cpu",
        }
    )
    assert plan.workflow == "full_research_compute_smoke"
    assert len(plan.shards) == 2
    assert len(plan.compute_jobs) == 2
    assert (tmp_path / "experiment" / "experiment_plan.json").exists()
    assert (tmp_path / "experiment" / "experiment_shards.jsonl").exists()


def test_experiment_workflow_smoke_runs_compute_jobs(tmp_path):
    data_dir = tmp_path / "data"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True)
    report = run_workflow_smoke(
        {
            "data_dir": str(data_dir),
            "output_dir": str(tmp_path / "experiment"),
            "compute_state_dir": str(tmp_path / "compute_state"),
            "workflow": "full_research_compute_smoke",
            "shard_count": 2,
            "device": "cpu",
            "batch_eval_chunk_size": 1,
            "max_formulas": 1,
        }
    )
    assert report.status == "success"
    assert report.shard_count == 2
    assert (tmp_path / "experiment" / "experiment_run_report.json").exists()
    assert (tmp_path / "experiment" / "merged" / "experiment_merge_report.json").exists()
    assert (tmp_path / "compute_state" / "compute_run_report.json").exists()


def test_experiment_cli_smoke(tmp_path, capsys):
    data_dir = tmp_path / "data"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True)
    exit_code = experiment_main(
        [
            "smoke",
            "--data-dir",
            str(data_dir),
            "--output-dir",
            str(tmp_path / "experiment_cli"),
            "--compute-state-dir",
            str(tmp_path / "compute_cli"),
            "--shard-count",
            "1",
            "--device",
            "cpu",
            "--batch-eval-chunk-size",
            "1",
            "--max-formulas",
            "1",
            "--pretty",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["status"] == "success"


def test_real_data_alpha_factory_large_plan_blocks_when_readiness_not_ready(tmp_path):
    readiness_path = tmp_path / "readiness.json"
    readiness_path.write_text(json.dumps({"status": "blocked", "can_run_core_alpha_factory": False}), encoding="utf-8")
    plan = create_experiment_plan(
        {
            "workflow": "real_data_alpha_factory_large_plan",
            "output_dir": str(tmp_path / "large_plan"),
            "research_readiness_decision_path": str(readiness_path),
            "require_alpha_factory_ready": True,
            "gpu_count": 4,
            "shard_count": 8,
            "candidate_budget": 50000,
        }
    )

    assert plan.metadata["blocked"] is True
    assert plan.compute_jobs == []
    assert plan.resource_plan["gpu_count_requested"] == 4
    assert (tmp_path / "large_plan" / "alpha_large_campaign_plan.json").exists()
    payload = json.loads((tmp_path / "large_plan" / "alpha_large_campaign_plan.json").read_text(encoding="utf-8"))
    assert payload["status"] == "blocked"

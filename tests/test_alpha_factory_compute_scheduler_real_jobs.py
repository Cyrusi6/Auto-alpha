import json
from dataclasses import replace

from alpha_factory.run_factory import main as run_factory_main
from data_pipeline.ashare import AShareDataConfig, AShareDataManager


def test_alpha_factory_compute_scheduler_runs_real_batch_eval_jobs(tmp_path, capsys, monkeypatch):
    data_dir = tmp_path / "data"
    AShareDataManager(AShareDataConfig(provider="sample", data_dir=data_dir)).sync(validate=True)
    compute_state_dir = tmp_path / "compute_state"
    compute_output_dir = tmp_path / "compute_output"
    batch_eval_dir = tmp_path / "batch_eval"

    def fixed_proxy_shortlist(candidates, _loader, **kwargs):
        selected = []
        rows = []
        for candidate in candidates:
            if candidate.status == "rejected":
                selected.append(candidate)
                continue
            status = "proxy_passed" if len(rows) < 4 else "rejected"
            score = 1.0 - 0.1 * len(rows)
            rows.append(
                {
                    "alpha_candidate_id": candidate.alpha_candidate_id,
                    "formula_hash": candidate.formula_hash,
                    "status": status,
                    "proxy_score": score,
                    "normalized_objectives": {"neutralized_rank_ic_mean": score},
                    "sampled_dates": ["20240102"],
                    "proxy_blockers": [] if status == "proxy_passed" else ["fixed_scheduler_fixture_limit"],
                }
            )
            selected.append(
                replace(
                    candidate,
                    proxy_score=score,
                    status=status,
                    reject_reason=None if status == "proxy_passed" else "fixed_scheduler_fixture_limit",
                )
            )
        policy = kwargs["policy"]
        return selected, rows, {
            "attempted": len(rows),
            "passed": sum(row["status"] == "proxy_passed" for row in rows),
            "failed": 0,
            "research_policy_id": policy.policy_id,
            "research_policy_hash": policy.policy_hash,
            "normalization": {"reference_hash": "f" * 64},
            "lineage_hash": "e" * 64,
            "score_method": "dimensionless_cohort_multi_objective_v1",
        }

    def fixed_full_research(candidates, _loader, **kwargs):
        policy = kwargs["policy"]
        rows = [
            {
                "alpha_candidate_id": candidate.alpha_candidate_id,
                "factor_id": f"factor_{candidate.formula_hash[:16]}",
                "formula_hash": candidate.formula_hash,
                "request": {
                    "name": candidate.alpha_candidate_id,
                    "formula_hash": candidate.formula_hash,
                    "formula_tokens": candidate.formula_tokens,
                    "formula_names": candidate.formula_names,
                    "lookback": candidate.lookback,
                    "complexity": candidate.complexity,
                },
                "status": "research_rejected",
                "score": 0.0,
                "gate_reasons": ["scheduler_fixture_postprocessor"],
                "certification_supported": False,
            }
            for candidate in candidates
        ]
        return rows, {
            "enabled": True,
            "evaluated": len(rows),
            "research_policy_id": policy.policy_id,
            "research_policy_hash": policy.policy_hash,
            "score_method": "dimensionless_cohort_multi_objective_v1",
            "normalization": {"reference_hash": "d" * 64},
            "multiple_testing": {
                "method": "benjamini_hochberg_and_holm_v1",
                "total_generated_trials": kwargs["total_trial_count"],
                "full_research_trials": len(rows),
            },
            "selection_bias": {
                "total_trials": kwargs["total_trial_count"],
                "full_research_trials": len(rows),
                "selection_fraction": len(rows) / kwargs["total_trial_count"],
                "selection_data_reused": True,
                "untouched_holdout": False,
            },
            "certification_ready": False,
        }

    monkeypatch.setattr("alpha_factory.runner.run_proxy_eval", fixed_proxy_shortlist)
    monkeypatch.setattr("alpha_factory.runner.run_full_research", fixed_full_research)

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

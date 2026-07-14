import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from compute_cluster import ComputeDeviceRecord, ComputeDeviceType, ComputeResourceSnapshot
from validation_campaign_store.ingest import ingest_candidate_pool
from validation_campaign_store.replay_evidence import compare_replay_evidence, publish_replay_bundle, validate_terminal_outputs
from validation_campaign_store.scheduler import _load_task052a_readiness, run_validation_shards


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _readiness(path: Path, *, engineering_blockers=None) -> Path:
    path.write_text(
        json.dumps(
            {
                "governed_source_ready": True,
                "conservative_tradability_policy_ready": True,
                "immutable_freeze_ready": True,
                "engineering_universe_proxy_ready": True,
                "strict_matrix_built": True,
                "strict_matrix_replay_safe": True,
                "v3_tensor_ready": True,
                "research_firewall_ready": True,
                "retrospective_replay_ready": True,
                "engineering_blockers": engineering_blockers or [],
                "candidate_blockers": [],
                "certification_blockers": [
                    "suspension_timing_semantics_uncertified",
                    "constituent_publication_timing_unknown",
                    "no_future_untouched_holdout",
                    "selection_data_reused",
                ],
                "untouched_holdout_ready": False,
                "certification_ready": False,
                "portfolio_ready": False,
                "paper_ready": False,
                "live_ready": False,
                "gpu_replay_started": False,
            }
        ),
        encoding="utf-8",
    )
    return path


def _campaign(tmp_path: Path):
    pool = tmp_path / "source_pool.jsonl"
    pool.write_text(
        "".join(
            json.dumps(
                {
                    "factor_id": f"factor_{index:02d}",
                    "formula_hash": f"formula_{index:02d}",
                    "formula_names": ["RET_1D"],
                    "feature_version": "ashare_feature_factory_v3",
                    "operator_version": "ashare_formula_ops_v3",
                    "transform_method": "raw",
                    "rank": index + 1,
                    "factor_store_dir": str(tmp_path / "factor_store"),
                }
            ) + "\n"
            for index in range(20)
        ),
        encoding="utf-8",
    )
    store = tmp_path / "store"
    ingest_candidate_pool(store, pool, validation_campaign_id="task053a", shard_count=4)
    inputs = {}
    for name in ("data", "factor_store", "freeze", "matrix"):
        root = tmp_path / name
        root.mkdir()
        (root / "manifest.json").write_text(json.dumps({"name": name}), encoding="utf-8")
        inputs[name] = str(root)
    for name in ("feature_manifest", "feature_tensor", "feature_validity", "snapshot_proof", "campaign_manifest", "promotion_policy", "promotion_allowlist", "promotion_denylist"):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps({"name": name}), encoding="utf-8")
        inputs[name] = str(path)
    inputs["readiness"] = str(_readiness(tmp_path / "readiness.json"))
    return store, inputs


def _snapshot():
    return ComputeResourceSnapshot(
        captured_at="2026-07-14T00:00:00Z",
        cpu_count=8,
        memory_total_mb=1024,
        memory_available_mb=512,
        torch_version="test",
        cuda_available=True,
        cuda_device_count=4,
        devices=[
            ComputeDeviceRecord(
                device_id=f"cuda:{index}",
                device_type=ComputeDeviceType.CUDA,
                name="NVIDIA GeForce RTX 4090",
                index=index,
                uuid=f"GPU-4090-{index}",
                cuda_available=True,
                torch_available=True,
            )
            for index in range(4)
        ],
    )


def _kwargs(tmp_path: Path, store: Path, inputs: dict):
    return {
        "store_dir": store,
        "data_dir": inputs["data"],
        "factor_store_dir": inputs["factor_store"],
        "output_dir": tmp_path / "output",
        "validation_campaign_id": "task053a",
        "shard_count": 4,
        "use_compute_scheduler": True,
        "compute_state_dir": str(tmp_path / "compute"),
        "data_freeze_dir": inputs["freeze"],
        "matrix_cache_dir": inputs["matrix"],
        "feature_manifest_path": inputs["feature_manifest"],
        "feature_tensor_path": inputs["feature_tensor"],
        "feature_validity_tensor_path": inputs["feature_validity"],
        "snapshot_proof_manifest_path": inputs["snapshot_proof"],
        "campaign_manifest_path": inputs["campaign_manifest"],
        "promotion_policy_path": inputs["promotion_policy"],
        "promotion_allowlist_path": inputs["promotion_allowlist"],
        "promotion_denylist_path": inputs["promotion_denylist"],
        "device": "cuda",
        "task_053a_replay": True,
        "replay_readiness_path": inputs["readiness"],
        "force_uncached_replay": True,
    }


def _write_candidate_outputs(job, gpu):
    output = Path(job.output_dir)
    rows = [json.loads(line) for line in Path(job.command[job.command.index("--candidate-pool-path") + 1]).read_text().splitlines() if line.strip()]
    results = []
    for row in rows:
        factor_id = row["factor_id"]
        materialized = output / "materialized_factors" / factor_id
        materialized.mkdir(parents=True, exist_ok=True)
        values = materialized / "values.npy"
        validity = materialized / "validity.npy"
        np.save(values, np.array([[1.0, 2.0]], dtype=np.float32))
        np.save(validity, np.array([[True, True]], dtype=np.bool_))
        manifest = materialized / "materialization_manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "factor_id": factor_id,
                    "formula_hash": row["formula_hash"],
                    "materialization_status": "success",
                    "cache_hit": False,
                    "input_fingerprint": "fingerprint",
                    "value_sha256": _sha(values),
                    "validity_sha256": _sha(validity),
                    "cuda_formula_execution": {
                        "evidence_version": "stackvm_cuda_formula_execution_v1",
                        "factor_id": factor_id,
                        "formula_hash": row["formula_hash"],
                        "physical_gpu": gpu,
                        "torch_device": "cuda:0",
                        "input_tensor_device": "cuda:0",
                        "input_validity_device": "cuda:0",
                        "output_tensor_device": "cuda:0",
                        "output_validity_device": "cuda:0",
                        "cuda_event_elapsed_ms": 1.0,
                        "peak_allocated_bytes": 1024,
                        "input_bytes": 512,
                        "output_bytes": 128,
                    },
                }
            ),
            encoding="utf-8",
        )
        validation_dir = output / "candidate_results" / factor_id
        validation_dir.mkdir(parents=True, exist_ok=True)
        validation_report = validation_dir / "validation_lab_report.json"
        validation_report.write_text(json.dumps({"status": "statistically_rejected", "validation_summary": {"rank_ic": -0.1}}), encoding="utf-8")
        results.append(
            {
                "factor_id": factor_id,
                "status": "statistically_rejected",
                "validation_summary": {"rank_ic": -0.1},
                "paths": {
                    "materialization_manifest_path": str(manifest),
                    "validation_lab_report_path": str(validation_report),
                },
            }
        )
    (output / "validation_candidate_pool_results.jsonl").write_text("".join(json.dumps(row) + "\n" for row in results), encoding="utf-8")
    (output / "validation_candidate_pool_report.json").write_text(json.dumps({"validated_candidate_count": 5, "blocked_count": 0}), encoding="utf-8")


def test_readiness_ignores_certification_blockers_but_not_engineering(tmp_path):
    ready = _readiness(tmp_path / "ready.json")
    assert _load_task052a_readiness(str(ready), task_053a_replay=True)["untouched_holdout_ready"] is False
    blocked = _readiness(tmp_path / "blocked.json", engineering_blockers=["matrix_invalid"])
    with pytest.raises(RuntimeError, match="engineering_blockers"):
        _load_task052a_readiness(str(blocked), task_053a_replay=True)


def test_content_addressed_bundle_and_replay_core_comparison(tmp_path):
    left = tmp_path / "left.txt"
    right = tmp_path / "right.txt"
    left.write_text("same", encoding="utf-8")
    right.write_text("same", encoding="utf-8")
    first = publish_replay_bundle(tmp_path / "a", inputs={"source": left}, extra={"policy": "v2"})
    second = publish_replay_bundle(tmp_path / "b", inputs={"source": right}, extra={"policy": "v2"})
    assert first["bundle_hash"] == second["bundle_hash"]
    evidence = [{"shard_index": index, "terminal_outputs": {"replay_core_hash": f"hash-{index}"}} for index in range(4)]
    assert compare_replay_evidence(evidence, evidence)["deterministic"] is True


def test_task053a_four_by_five_strict_artifacts_and_resume(tmp_path, monkeypatch):
    store, inputs = _campaign(tmp_path)
    monkeypatch.setattr("validation_campaign_store.scheduler.probe_compute_resources", _snapshot)

    class Report:
        failed_count = 0
        fallback_to_cpu_count = 0
        oom_error_count = 0

        def to_dict(self):
            return {"failed_count": 0, "fallback_to_cpu_count": 0, "oom_error_count": 0}

    class Store:
        def __init__(self, root):
            self.root = Path(root)
            self.root.mkdir(parents=True, exist_ok=True)
            self.heartbeats_path = self.root / "heartbeats.jsonl"
            self.runs = []

        def read_runs(self):
            return self.runs

    class Scheduler:
        def __init__(self, config):
            self.store = Store(config.state_dir)
            self.jobs = []

        def submit_jobs(self, jobs):
            self.jobs = jobs

        def run(self):
            heartbeats = []
            for job in self.jobs:
                gpu = {"uuid": f"GPU-4090-{job.shard_id}", "model": "NVIDIA GeForce RTX 4090"}
                _write_candidate_outputs(job, gpu)
                telemetry = Path(job.metadata["telemetry_path"])
                telemetry.write_text(json.dumps({"exit_code": 0, "cuda_available": True, "physical_gpus": [gpu], "candidate_ids": job.metadata["candidate_ids"]}), encoding="utf-8")
                self.store.runs.append({"run_id": f"run-{job.shard_id}", "job_id": job.job_id, "status": "success", "return_code": 0, "attempt": 1, "fallback_to_cpu": False, "error": None, "physical_devices": [gpu]})
                heartbeats.extend([{"job_id": job.job_id, "status": "running"}, {"job_id": job.job_id, "status": "success"}])
            self.store.heartbeats_path.write_text("".join(json.dumps(row) + "\n" for row in heartbeats), encoding="utf-8")
            return Report()

    monkeypatch.setattr("validation_campaign_store.scheduler.LocalComputeScheduler", Scheduler)
    first = run_validation_shards(**_kwargs(tmp_path, store, inputs))
    assert first["execution_mode"] == "first_run"
    assert first["replay_bundle_hash"]
    campaign = json.loads(Path(first["task_053a_replay_evidence_path"]).read_text(encoding="utf-8"))
    assert len(campaign["physical_gpu_uuids"]) == 4
    assert all(len(shard["terminal_outputs"]["candidate_artifacts"]) == 5 for shard in campaign["shards"])

    sibling_kwargs = _kwargs(tmp_path, store, inputs)
    sibling_kwargs["output_dir"] = tmp_path / "sibling_output"
    sibling_kwargs["replay_generation_label"] = "uncached_sibling"
    sibling_kwargs["replay_reference_evidence_path"] = first["task_053a_replay_evidence_path"]
    sibling = run_validation_shards(**sibling_kwargs)
    assert sibling["deterministic_comparison"]["deterministic"] is True
    assert sibling["replay_bundle_hash"] == first["replay_bundle_hash"]

    class ForbiddenScheduler:
        def __init__(self, _config):
            raise AssertionError("4/4 immutable resume must not schedule")

    monkeypatch.setattr("validation_campaign_store.scheduler.LocalComputeScheduler", ForbiddenScheduler)
    resumed = run_validation_shards(**sibling_kwargs, resume=True)
    assert resumed["execution_mode"] == "resume_4_of_4"
    assert resumed["immutable_resume_count"] == 4


def test_strict_terminal_rejects_outer_only_cuda_evidence(tmp_path):
    output = tmp_path / "shard"
    output.mkdir()
    (output / "validation_candidate_pool_report.json").write_text(json.dumps({"validated_candidate_count": 1}), encoding="utf-8")
    (output / "validation_candidate_pool_results.jsonl").write_text(json.dumps({"factor_id": "f", "status": "statistically_rejected"}) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="candidate terminal artifacts missing"):
        validate_terminal_outputs(output, ["f"], require_candidate_artifacts=True, require_cuda_formula_evidence=True)

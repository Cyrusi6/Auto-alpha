import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from compute_cluster import ComputeDeviceRecord, ComputeDeviceType, ComputeResourceSnapshot
from validation_campaign_store.ingest import ingest_candidate_pool
from validation_campaign_store.scheduler import run_validation_shards


def _prepare_campaign(tmp_path: Path, candidate_count: int = 20):
    source_pool = tmp_path / "source" / "pool.jsonl"
    source_pool.parent.mkdir(parents=True)
    source_pool.write_text(
        "".join(
            json.dumps(
                {
                    "factor_id": f"factor_{index:02d}",
                    "formula_hash": f"hash_{index:02d}",
                    "formula_names": ["RET_1D"],
                    "feature_version": "task052a",
                    "rank": index + 1,
                    "final_score": 1.0 - index / 100.0,
                    "factor_store_dir": str(tmp_path / "factor_store"),
                }
            )
            + "\n"
            for index in range(candidate_count)
        ),
        encoding="utf-8",
    )
    store_dir = tmp_path / "validation_store"
    ingest_candidate_pool(store_dir, source_pool, validation_campaign_id="task052a", shard_count=4)
    strict = {}
    for name in (
        "feature_manifest",
        "feature_tensor",
        "feature_validity",
        "snapshot_proof",
        "campaign_manifest",
        "promotion_policy",
        "promotion_allowlist",
        "promotion_denylist",
    ):
        path = tmp_path / "inputs" / f"{name}.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({"name": name}), encoding="utf-8")
        strict[name] = str(path)
    for name in ("data", "factor_store", "freeze", "matrix"):
        path = tmp_path / name
        path.mkdir(exist_ok=True)
        (path / "manifest.json").write_text(json.dumps({"name": name}), encoding="utf-8")
        strict[name] = str(path)
    readiness = tmp_path / "inputs" / "readiness.json"
    readiness.write_text(
        json.dumps(
            {
                "status": "ready_for_retrospective_replay",
                "data_foundation_ready": True,
                "retrospective_replay_ready": True,
                "research_firewall_ready": True,
                "untouched_holdout_ready": True,
                "gpu_replay_started": False,
                "blockers": [],
            }
        ),
        encoding="utf-8",
    )
    strict["readiness"] = str(readiness)
    return store_dir, strict


def _snapshot(cuda_count: int = 4):
    devices = [
        ComputeDeviceRecord(
            device_id=f"cuda:{index}",
            device_type=ComputeDeviceType.CUDA,
            name=f"GPU Model {index}",
            index=index,
            uuid=f"GPU-UUID-{index}",
            cuda_available=True,
            torch_available=True,
        )
        for index in range(cuda_count)
    ]
    return ComputeResourceSnapshot(
        captured_at="2026-07-14T00:00:00Z",
        cpu_count=8,
        memory_total_mb=1024,
        memory_available_mb=512,
        torch_version="test",
        cuda_available=bool(cuda_count),
        cuda_device_count=cuda_count,
        devices=devices,
    )


def _strict_run_kwargs(tmp_path: Path, store_dir: Path, strict: dict):
    return {
        "store_dir": store_dir,
        "data_dir": strict["data"],
        "factor_store_dir": strict["factor_store"],
        "output_dir": tmp_path / "output",
        "validation_campaign_id": "task052a",
        "shard_count": 4,
        "use_compute_scheduler": True,
        "compute_state_dir": str(tmp_path / "compute_state"),
        "data_freeze_dir": strict["freeze"],
        "matrix_cache_dir": strict["matrix"],
        "feature_manifest_path": strict["feature_manifest"],
        "feature_tensor_path": strict["feature_tensor"],
        "feature_validity_tensor_path": strict["feature_validity"],
        "snapshot_proof_manifest_path": strict["snapshot_proof"],
        "campaign_manifest_path": strict["campaign_manifest"],
        "promotion_policy_path": strict["promotion_policy"],
        "promotion_allowlist_path": strict["promotion_allowlist"],
        "promotion_denylist_path": strict["promotion_denylist"],
        "device": "cuda",
        "task_052a_replay": True,
        "replay_readiness_path": strict["readiness"],
    }


def test_task052a_gates_readiness_and_exact_four_by_five_before_scheduler(tmp_path, monkeypatch):
    store_dir, strict = _prepare_campaign(tmp_path, candidate_count=19)
    scheduler_created = False

    class ForbiddenScheduler:
        def __init__(self, _config):
            nonlocal scheduler_created
            scheduler_created = True

    monkeypatch.setattr("validation_campaign_store.scheduler.LocalComputeScheduler", ForbiddenScheduler)
    with pytest.raises(RuntimeError, match="exactly 4 shards x 5"):
        run_validation_shards(**_strict_run_kwargs(tmp_path, store_dir, strict))
    assert scheduler_created is False

    store_dir, strict = _prepare_campaign(tmp_path / "blocked", candidate_count=20)
    readiness = json.loads(Path(strict["readiness"]).read_text(encoding="utf-8"))
    readiness["research_firewall_ready"] = False
    Path(strict["readiness"]).write_text(json.dumps(readiness), encoding="utf-8")
    with pytest.raises(RuntimeError, match="readiness blocked"):
        run_validation_shards(**_strict_run_kwargs(tmp_path / "blocked", store_dir, strict))
    assert scheduler_created is False


def test_task052a_first_run_resume_and_stale_history_rejection(tmp_path, monkeypatch):
    store_dir, strict = _prepare_campaign(tmp_path)
    monkeypatch.setattr("validation_campaign_store.scheduler.probe_compute_resources", lambda: _snapshot())
    submitted_batches = []

    class FakeReport:
        failed_count = 0
        fallback_to_cpu_count = 0
        oom_error_count = 0

        def to_dict(self):
            return {"status": "success", "failed_count": 0, "fallback_to_cpu_count": 0, "oom_error_count": 0}

    class FakeStore:
        def __init__(self, state_dir):
            self.state_dir = Path(state_dir)
            self.state_dir.mkdir(parents=True, exist_ok=True)
            self.heartbeats_path = self.state_dir / "compute_heartbeats.jsonl"
            self._runs = []

        def read_runs(self):
            return list(self._runs)

    class FakeScheduler:
        def __init__(self, config):
            self.store = FakeStore(config.state_dir)
            self.jobs = []

        def submit_jobs(self, jobs):
            self.jobs = list(jobs)
            submitted_batches.append(list(jobs))

        def run(self):
            heartbeat_rows = []
            for job in self.jobs:
                shard_dir = Path(job.output_dir)
                candidate_ids = list(job.metadata["candidate_ids"])
                (shard_dir / "validation_candidate_pool_report.json").write_text(
                    json.dumps({"validated_candidate_count": 5, "blocked_count": 0}), encoding="utf-8"
                )
                (shard_dir / "validation_candidate_pool_results.jsonl").write_text(
                    "".join(json.dumps({"factor_id": candidate_id}) + "\n" for candidate_id in candidate_ids), encoding="utf-8"
                )
                gpu = {"physical_index": job.shard_id, "uuid": f"GPU-UUID-{job.shard_id}", "model": f"GPU Model {job.shard_id}"}
                Path(job.metadata["telemetry_path"]).write_text(
                    json.dumps(
                        {
                            "exit_code": 0,
                            "cuda_available": True,
                            "cuda_visible_devices": str(job.shard_id),
                            "cuda_memory_allocated_start_bytes": 0,
                            "cuda_memory_allocated_end_bytes": 64,
                            "cuda_peak_memory_allocated_bytes": 128,
                            "cuda_kernel_elapsed_ms": 1.25,
                            "physical_gpus": [gpu],
                            "candidate_ids": candidate_ids,
                        }
                    ),
                    encoding="utf-8",
                )
                run = {
                    "run_id": f"run-{job.shard_id}",
                    "job_id": job.job_id,
                    "status": "success",
                    "return_code": 0,
                    "attempt": 1,
                    "fallback_to_cpu": False,
                    "error": None,
                    "physical_devices": [gpu],
                }
                self.store._runs.append(run)
                heartbeat_rows.extend(
                    [
                        {"job_id": job.job_id, "status": "running", "heartbeat_at": "2026-07-14T00:00:00Z"},
                        {"job_id": job.job_id, "status": "success", "heartbeat_at": "2026-07-14T00:00:01Z"},
                    ]
                )
            self.store.heartbeats_path.write_text("".join(json.dumps(row) + "\n" for row in heartbeat_rows), encoding="utf-8")
            return FakeReport()

    monkeypatch.setattr("validation_campaign_store.scheduler.LocalComputeScheduler", FakeScheduler)
    kwargs = _strict_run_kwargs(tmp_path, store_dir, strict)
    first = run_validation_shards(**kwargs)
    assert first["execution_mode"] == "first_run"
    assert len(submitted_batches) == 1 and len(submitted_batches[0]) == 4
    assert all(job.max_retries == 0 for job in submitted_batches[0])
    campaign_evidence = json.loads(Path(first["task_052a_replay_evidence_path"]).read_text(encoding="utf-8"))
    assert campaign_evidence["first_run"] is True
    assert len(campaign_evidence["physical_gpu_uuids"]) == 4

    class ForbiddenScheduler:
        def __init__(self, _config):
            raise AssertionError("valid 4/4 resume must not create a scheduler")

    monkeypatch.setattr("validation_campaign_store.scheduler.LocalComputeScheduler", ForbiddenScheduler)
    resumed = run_validation_shards(**kwargs, resume=True)
    assert resumed["execution_mode"] == "resume_4_of_4"
    assert resumed["immutable_resume_count"] == 4

    report_path = tmp_path / "output" / "validation_shards" / "shard_0000" / "validation_candidate_pool_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["tampered"] = True
    report_path.write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr("validation_campaign_store.scheduler.LocalComputeScheduler", FakeScheduler)
    fresh = run_validation_shards(**kwargs, resume=True)
    assert fresh["execution_mode"] == "first_run"
    assert fresh["stale_history_rejected"] is True
    assert fresh["stale_history_rejections"]["0"] == "terminal_output_hash_mismatch"
    assert len(submitted_batches) == 2 and len(submitted_batches[1]) == 4


def test_replay_worker_real_cpu_subprocess_through_compute_runner(tmp_path):
    module_path = tmp_path / "mini_validation.py"
    module_path.write_text(
        """
import json
from pathlib import Path

def main(argv=None):
    args = list(argv or [])
    output = Path(args[args.index('--output-dir') + 1])
    pool = Path(args[args.index('--validation-candidate-pool-path') + 1])
    rows = [json.loads(line) for line in pool.read_text().splitlines() if line.strip()]
    output.mkdir(parents=True, exist_ok=True)
    (output / 'validation_candidate_pool_report.json').write_text(json.dumps({'validated_candidate_count': len(rows), 'blocked_count': 0}))
    (output / 'validation_candidate_pool_results.jsonl').write_text(''.join(json.dumps({'factor_id': row['factor_id']}) + '\\n' for row in rows))
    return 0
""",
        encoding="utf-8",
    )
    pool = tmp_path / "pool.jsonl"
    pool.write_text("".join(json.dumps({"factor_id": f"factor_{index}"}) + "\n" for index in range(5)), encoding="utf-8")
    output = tmp_path / "worker_output"
    telemetry = tmp_path / "telemetry.json"
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(tmp_path), str(Path.cwd()), env.get("PYTHONPATH", "")])
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "validation_campaign_store.replay_worker",
            "--entrypoint",
            "mini_validation",
            "--telemetry-path",
            str(telemetry),
            "--candidate-pool-path",
            str(pool),
            "--output-dir",
            str(output),
            "--",
            "validate-candidates",
            "--validation-candidate-pool-path",
            str(pool),
            "--output-dir",
            str(output),
        ],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(telemetry.read_text(encoding="utf-8"))
    assert payload["exit_code"] == 0
    assert payload["candidate_ids"] == [f"factor_{index}" for index in range(5)]
    assert payload["terminal_outputs"]["validation_candidate_pool_report.json"]["exists"] is True
    assert payload["terminal_outputs"]["validation_candidate_pool_results.jsonl"]["exists"] is True

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import torch

from backtest.run_backtest import apply_signal_lag
from artifact_schema.validator import validate_artifact
from factor_store.hash import make_factor_id, stable_formula_hash
from factor_store.models import FactorRecord
from feature_factory import FEATURE_SET_V1, build_feature_set_manifest
from risk_model.covariance import estimate_return_covariance
from validation_lab.materialization import FactorMaterializer, MaterializationInputs, load_materialized_factor
from validation_lab.metrics import evaluate_factor_splits
from validation_lab.models import ValidationSplit
from validation_lab.policy import EngineeringRobustnessPolicy, load_validation_policy
from validation_campaign_store.ingest import ingest_candidate_pool
from validation_campaign_store.scheduler import run_validation_shards


def _materialization_fixture(tmp_path: Path, *, zero: bool = False):
    freeze = tmp_path / "freeze"
    matrix = tmp_path / "matrix"
    features = tmp_path / "features"
    freeze.mkdir()
    matrix.mkdir()
    features.mkdir()
    (freeze / "freeze_manifest.json").write_text(json.dumps({"freeze_id": "test"}), encoding="utf-8")
    manifest = build_feature_set_manifest(FEATURE_SET_V1, point_in_time=True, created_at="2026-01-01T00:00:00Z")
    manifest_path = features / "feature_set_manifest.json"
    manifest_path.write_text(json.dumps(manifest.to_dict()), encoding="utf-8")
    stocks, dates = 4, 8
    tensor = np.zeros((stocks, manifest.feature_count, dates), dtype=np.float32)
    if not zero:
        tensor[:, 0, :] = np.arange(stocks * dates, dtype=np.float32).reshape(stocks, dates)
    tensor_path = features / "feature_tensor.npy"
    np.save(tensor_path, tensor)
    validity_path = features / "feature_validity_tensor.npy"
    np.save(validity_path, np.ones_like(tensor, dtype=np.bool_))
    (matrix / "trade_dates.json").write_text(json.dumps([f"202001{i:02d}" for i in range(1, dates + 1)]), encoding="utf-8")
    (matrix / "ts_codes.json").write_text(json.dumps([f"00000{i}.SZ" for i in range(stocks)]), encoding="utf-8")
    for name in ["active_mask", "pit_available_mask", "index_member_matrix"]:
        np.save(matrix / f"{name}.npy", np.ones((stocks, dates), dtype=np.float32))
    np.save(matrix / "membership_known.npy", np.ones(dates, dtype=np.bool_))
    np.save(matrix / "adjusted_close.npy", np.full((stocks, dates), 10.0, dtype=np.float32))
    (matrix / "matrix_version_manifest.json").write_text(json.dumps({"target_return_mode": "adjusted_close", "feature_cutoff_mode": "next_open"}), encoding="utf-8")
    formula_hash = stable_formula_hash([0], ["RET_1D"], manifest.feature_version, manifest.operator_version)
    factor = FactorRecord(
        factor_id=make_factor_id(formula_hash),
        formula=["RET_1D"],
        formula_tokens=[0],
        formula_hash=formula_hash,
        feature_version=manifest.feature_version,
        operator_version=manifest.operator_version,
        lookback_days=1,
        created_at="2026-01-01T00:00:00Z",
        transform_method="raw",
    )
    inputs = MaterializationInputs(str(freeze), str(matrix), str(manifest_path), str(tensor_path), target_return_mode="adjusted_close", feature_validity_tensor_path=str(validity_path))
    return factor, inputs, tensor


def test_metadata_only_materialization_matches_direct_stackvm_and_resumes(tmp_path):
    factor, inputs, tensor = _materialization_fixture(tmp_path)
    materializer = FactorMaterializer(inputs, tmp_path / "materialized", min_coverage=0.01)
    first = materializer.materialize(factor)
    assert first.status == "success"
    values, validity, manifest = load_materialized_factor(first.manifest_path)
    assert torch.equal(values, torch.from_numpy(tensor[:, 0, :]))
    assert bool(validity.all())
    assert manifest["value_sha256"] and manifest["validity_sha256"]
    assert validate_artifact(Path(first.manifest_path), strict=True).valid is True
    pointer = json.loads((tmp_path / "materialized" / factor.factor_id / "current_materialization.json").read_text(encoding="utf-8"))
    assert pointer["generation_id"] == first.input_fingerprint
    assert Path(first.manifest_path).parent.name == first.input_fingerprint
    second = materializer.materialize(factor)
    assert second.cache_hit is True


def test_lineage_or_transform_drift_causes_cache_miss(tmp_path):
    factor, inputs, _ = _materialization_fixture(tmp_path)
    materializer = FactorMaterializer(inputs, tmp_path / "materialized", min_coverage=0.01)
    original = materializer.materialize(factor)
    assert original.cache_hit is False
    changed = FactorRecord(**{**factor.__dict__, "transform_method": "zscore"})
    rerun = materializer.materialize(changed)
    assert rerun.status == "success"
    assert rerun.cache_hit is False
    generations = tmp_path / "materialized" / factor.factor_id / "generations"
    assert materializer.materialize(factor).cache_hit is True
    assert {path.name for path in generations.iterdir()} == {original.input_fingerprint, rerun.input_fingerprint}
    matrix_manifest = Path(inputs.matrix_cache_dir) / "matrix_version_manifest.json"
    matrix_manifest.write_text(json.dumps({"target_return_mode": "adjusted_close", "feature_cutoff_mode": "next_open", "cache_hash": "changed"}), encoding="utf-8")
    drifted = materializer.materialize(changed)
    assert drifted.cache_hit is False


def test_forged_formula_hash_or_factor_id_is_blocked(tmp_path):
    factor, inputs, _ = _materialization_fixture(tmp_path)
    materializer = FactorMaterializer(inputs, tmp_path / "materialized", min_coverage=0.01)
    forged_hash = FactorRecord(**{**factor.__dict__, "formula_hash": "0" * 64})
    assert materializer.materialize(forged_hash).blocker == "formula_hash_mismatch"
    forged_id = FactorRecord(**{**factor.__dict__, "factor_id": "factor_deadbeefdeadbeef"})
    assert materializer.materialize(forged_id).blocker == "factor_id_mismatch"


def test_materialization_quality_and_research_key_use_eligibility_only(tmp_path):
    factor, inputs, tensor = _materialization_fixture(tmp_path)
    eligible_path = Path(inputs.matrix_cache_dir) / "research_eligible_date_mask.npy"
    np.save(eligible_path, np.asarray([True] * 6 + [False, False], dtype=np.bool_))
    governed_inputs = replace(
        inputs,
        research_end_date="20200106",
        label_horizon=2,
        research_eligible_date_mask_path=str(eligible_path),
        eligibility_contract_hash="eligibility-v1",
        validation_policy_hash="policy-v1",
    )
    materializer = FactorMaterializer(governed_inputs, tmp_path / "materialized", min_coverage=0.01)
    first = materializer.materialize(factor)
    first_manifest = json.loads(Path(first.manifest_path).read_text(encoding="utf-8"))
    changed = tensor.copy()
    changed[:, :, -2:] = 1e20
    np.save(inputs.feature_tensor_path, changed)
    second = materializer.materialize(factor)
    second_manifest = json.loads(Path(second.manifest_path).read_text(encoding="utf-8"))
    assert second.cache_hit is False
    assert first_manifest["research_cache_key"] == second_manifest["research_cache_key"]
    assert first_manifest["statistics"] == second_manifest["statistics"]


def test_missing_or_zero_metadata_only_factor_never_zero_passes(tmp_path):
    factor, inputs, _ = _materialization_fixture(tmp_path, zero=True)
    result = FactorMaterializer(inputs, tmp_path / "materialized", min_coverage=0.01).materialize(factor)
    assert result.status == "blocked"
    assert "zero" in str(result.blocker)
    missing = MaterializationInputs(inputs.data_freeze_dir, inputs.matrix_cache_dir, inputs.feature_manifest_path, str(tmp_path / "missing.npy"))
    result = FactorMaterializer(missing, tmp_path / "missing-output").materialize(factor)
    assert result.status == "blocked"


def test_zero_variance_low_breadth_and_insufficient_oos_are_blocked():
    dates = [f"202001{i:02d}" for i in range(1, 5)]
    split = ValidationSplit("s", "rolling_walk_forward", dates[:1], dates[1:2], dates[2:])
    factors = torch.zeros((4, 4))
    targets = torch.arange(16, dtype=torch.float32).reshape(4, 4)
    policy = EngineeringRobustnessPolicy(min_cross_section_breadth=5, min_oos_dates=2, min_mean_rank_ic=-1.0, min_mean_icir=-10.0, min_window_pass_ratio=0.0, min_valid_oos_dates=2, min_evaluable_windows=0, min_cumulative_oos_dates=0)
    _, summary, issues = evaluate_factor_splits(factors, targets, dates, [split], "factor", validity=torch.ones_like(factors, dtype=torch.bool), policy=policy)
    assert summary.status == "data_blocked"
    codes = {issue.code for issue in issues}
    assert "data_blocked_window" in codes


def test_signal_lag_moves_actual_signal_date():
    factors = torch.tensor([[1.0, 2.0, 3.0]])
    shifted = apply_signal_lag(factors, 1)
    assert torch.isnan(shifted[0, 0])
    assert shifted[0, 1:].tolist() == [1.0, 2.0]


def test_covariance_only_reads_history_available_as_of_date():
    class Loader:
        target_ret = torch.tensor([[0.01, 0.02, 9.0], [0.02, 0.01, -9.0]])

    historical = estimate_return_covariance(Loader(), as_of_index=2, shrinkage=0.0)
    changed_future = Loader()
    changed_future.target_ret = Loader.target_ret.clone()
    changed_future.target_ret[:, 2] = torch.tensor([999.0, -999.0])
    assert torch.equal(historical, estimate_return_covariance(changed_future, as_of_index=2, shrinkage=0.0))


def test_task054_production_policy_parameters_cannot_be_overridden():
    policy = load_validation_policy("task054_production_engineering_v1")
    policy.validate_window_parameters(756, 126, 126, 126)
    with pytest.raises(ValueError, match="production_policy_parameter_override"):
        policy.validate_window_parameters(755, 126, 126, 126)


def test_validation_scheduler_flag_creates_four_cuda_shards(tmp_path, monkeypatch):
    pool = tmp_path / "pool.jsonl"
    rows = []
    for idx in range(8):
        rows.append({"factor_id": f"factor_{idx}", "formula_hash": f"hash_{idx}", "formula_names": ["RET_1D"], "feature_version": "v3", "rank": idx + 1, "final_score": 1.0, "factor_store_dir": str(tmp_path / "store")})
    pool.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    store_dir = tmp_path / "validation_store"
    ingest_candidate_pool(store_dir, pool, validation_campaign_id="campaign", shard_count=4)
    captured = []

    class FakeReport:
        failed_count = 0
        gpu_job_count = 4

        def to_dict(self):
            return {"status": "success", "failed_count": 0, "gpu_job_count": 4}

    class FakeJobStore:
        def read_runs(self):
            return [{"run_id": f"run_{job.shard_id}", "job_id": job.job_id, "status": "success", "fallback_to_cpu": False, "device_indices": [job.shard_id]} for job in captured]

    class FakeScheduler:
        def __init__(self, _config):
            self.store = FakeJobStore()

        def submit_jobs(self, jobs):
            captured.extend(jobs)

        def run(self):
            for job in captured:
                output = Path(job.output_dir)
                count = len((output / "candidate_pool.jsonl").read_text(encoding="utf-8").splitlines())
                (output / "validation_candidate_pool_report.json").write_text(json.dumps({"validated_candidate_count": count, "blocked_count": count}), encoding="utf-8")
            return FakeReport()

    monkeypatch.setattr("validation_campaign_store.scheduler.LocalComputeScheduler", FakeScheduler)
    strict = {}
    for name in ["feature_tensor", "feature_validity", "feature_manifest", "snapshot_proof", "promotion_policy", "promotion_allowlist", "promotion_denylist"]:
        path = tmp_path / f"{name}.json" if "tensor" not in name else tmp_path / f"{name}.npy"
        path.write_bytes(b"test")
        strict[name] = str(path)
    freeze = tmp_path / "freeze"; freeze.mkdir()
    matrix = tmp_path / "matrix"; matrix.mkdir()
    result = run_validation_shards(
        store_dir,
        data_dir=str(tmp_path),
        factor_store_dir=str(tmp_path / "store"),
        output_dir=tmp_path / "output",
        validation_campaign_id="campaign",
        shard_count=4,
        use_compute_scheduler=True,
        compute_state_dir=str(tmp_path / "compute"),
        data_freeze_dir=str(freeze), matrix_cache_dir=str(matrix),
        feature_tensor_path=strict["feature_tensor"], feature_validity_tensor_path=strict["feature_validity"],
        feature_manifest_path=strict["feature_manifest"], snapshot_proof_manifest_path=strict["snapshot_proof"],
        promotion_policy_path=strict["promotion_policy"], promotion_allowlist_path=strict["promotion_allowlist"], promotion_denylist_path=strict["promotion_denylist"],
    )
    assert result["status"] == "success"
    assert len(captured) == 4
    assert {job.shard_id for job in captured} == {0, 1, 2, 3}
    assert all(job.required_device_type == "cuda" and job.gpu_count == 1 for job in captured)
    assert all("--train-size" in job.command and "756" in job.command for job in captured)

    class AccumulatedFakeReport(FakeReport):
        gpu_job_count = 8

        def to_dict(self):
            return {"status": "success", "failed_count": 0, "gpu_job_count": 8}

    class AccumulatedFakeScheduler(FakeScheduler):
        def run(self):
            super().run()
            return AccumulatedFakeReport()

    captured.clear()
    monkeypatch.setattr("validation_campaign_store.scheduler.LocalComputeScheduler", AccumulatedFakeScheduler)
    result = run_validation_shards(
        store_dir,
        data_dir=str(tmp_path),
        factor_store_dir=str(tmp_path / "store"),
        output_dir=tmp_path / "output-accumulated",
        validation_campaign_id="campaign",
        shard_count=4,
        use_compute_scheduler=True,
        compute_state_dir=str(tmp_path / "compute-accumulated"),
        data_freeze_dir=str(freeze), matrix_cache_dir=str(matrix),
        feature_tensor_path=strict["feature_tensor"], feature_validity_tensor_path=strict["feature_validity"],
        feature_manifest_path=strict["feature_manifest"], snapshot_proof_manifest_path=strict["snapshot_proof"],
        promotion_policy_path=strict["promotion_policy"], promotion_allowlist_path=strict["promotion_allowlist"], promotion_denylist_path=strict["promotion_denylist"],
    )
    assert result["status"] == "success"

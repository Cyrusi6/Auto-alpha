from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from task_055_a.artifacts import publish_blocked_simulation_run
from task_055_b import inventory
from task_055_b.contracts import GapInventoryConfig


def _json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _matrix(root: Path) -> tuple[list[str], list[str]]:
    stocks = ["600170.SH", "601018.SH"]
    dates = ["20160321", "20160322", "20160323", "20160324", "20160325"]
    shape = (2, 5)
    _json(root / "ts_codes.json", stocks)
    _json(root / "trade_dates.json", dates)
    _json(root / "task_052a_strict_matrix_manifest.json", {"shape": list(shape), "content_hash": "matrix-hash"})
    active = np.ones(shape, dtype=bool)
    listed = np.ones(shape, dtype=bool)
    membership = np.array([[1, 1, 0, 0, 0], [0, 1, 1, 1, 0]], dtype=bool)
    observed = np.ones(shape, dtype=bool)
    observed[0, 2] = False
    observed[0, 3] = False
    gap = np.zeros(shape, dtype=bool)
    gap[0, 2] = True
    suspension = np.zeros(shape, dtype=bool)
    suspension[0, 3] = True
    signal = np.zeros(shape, dtype=bool)
    target = np.zeros(shape, dtype=bool)
    for name, value in {
        "active.npy": active,
        "listed.npy": listed,
        "membership.npy": membership,
        "membership_known.npy": np.ones(shape, dtype=bool),
        "bar_observed.npy": observed,
        "unexplained_data_gap.npy": gap,
        "suspension_event_present.npy": suspension,
        "signal_eligible_at_close.npy": signal,
        "target_available.npy": target,
    }.items():
        np.save(root / name, value, allow_pickle=False)
    for field in ("open", "high", "low", "close", "vol", "amount"):
        values = np.full(shape, 10.0, dtype=np.float32)
        validity = np.ones(shape, dtype=bool)
        values[0, 2] = np.nan
        validity[0, 2] = False
        if field == "open":
            values[0, 3] = 0.0
            validity[0, 3] = False
        np.save(root / f"{field}.npy", values, allow_pickle=False)
        np.save(root / f"{field}_validity.npy", validity, allow_pickle=False)
    return stocks, dates


def _bundle(path: Path, stocks: list[str], dates: list[str]) -> dict:
    _json(path.parent / "ts_codes.json", stocks)
    _json(path.parent / "execution_dates.json", dates)
    payload = {
        "content_hash": "bundle-hash",
        "artifacts": {
            "ts_codes": {"path": "ts_codes.json"},
            "execution_trade_dates": {"path": "execution_dates.json"},
        },
    }
    _json(path, payload)
    return payload


def _config(tmp_path: Path, matrix: Path, bundle: Path, runs: Path, evidence: Path) -> GapInventoryConfig:
    seal = tmp_path / "seal.json"
    _json(seal, {"content_hash": "seal-hash"})
    return GapInventoryConfig(
        observation_seal=seal,
        strict_matrix_root=matrix,
        simulation_bundle_manifest=bundle,
        blocked_run_roots=(runs,),
        output_root=tmp_path / "inventory",
        evidence_roots=(evidence,),
        acquired_at="2026-07-15T08:00:00Z",
        probes=(("600170.SH", "20160323"), ("601018.SH", "20160325")),
    )


def _patch_validators(monkeypatch: pytest.MonkeyPatch, bundle_payload: dict) -> None:
    monkeypatch.setattr(inventory, "validate_observation_boundary_seal", lambda *_args, **_kwargs: {"content_hash": "seal-hash", "observation": {"max_observed_endpoint": "20260630"}})
    monkeypatch.setattr(inventory, "validate_simulation_bundle", lambda *_args, **_kwargs: bundle_payload)


def test_complete_inventory_merges_episodes_and_marks_first_blocker_censoring(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    matrix = tmp_path / "matrix"
    matrix.mkdir()
    stocks, dates = _matrix(matrix)
    bundle_path = tmp_path / "bundle" / "manifest.json"
    bundle = _bundle(bundle_path, stocks, dates)
    runs = tmp_path / "runs"
    publish_blocked_simulation_run(
        output_root=runs / "factor-a" / "baseline",
        spec={"factor_id": "factor-a", "scenario": "baseline"},
        input_lineage={"matrix": "matrix-hash"},
        blocker={"code": "security_date_evidence_insufficient", "detail": "valuation_open_blocked:20160323:0:cannot mark held asset 600170.SH with unknown price"},
    )
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "suspension_coverage_ledger.jsonl").write_text(
        json.dumps({"dataset": "suspensions", "ts_code": "600170.SH", "trade_date": "20160323", "status": "success", "request_hash": "request", "response_hash": "response"}) + "\n",
        encoding="utf-8",
    )
    _patch_validators(monkeypatch, bundle)

    result = inventory.build_gap_inventory(_config(tmp_path, matrix, bundle_path, runs, evidence))

    cells = {(row["ts_code"], row["trade_date"]): row for row in result["cells"]}
    assert ("600170.SH", "20160323") in cells
    assert cells[("600170.SH", "20160323")]["first_blocker_censored_observation"] is True
    assert cells[("600170.SH", "20160323")]["valuation_closure_domain"] is True
    assert cells[("600170.SH", "20160323")]["supporting_evidence"]
    assert cells[("600170.SH", "20160324")]["state"] == "SOURCE_NORMALIZATION_ZERO_FILL"
    assert result["first_blocker_semantics"] == "censored_first_failure_samples_not_inventory_total"
    episode = next(row for row in result["episodes"] if row["ts_code"] == "600170.SH" and row["start_date"] == "20160323")
    assert episode["end_date"] == "20160324"
    assert episode["trade_date_count"] == 2
    assert result["readiness"] == {
        "factor_replay_ready": True,
        "continuous_portfolio_valuation_ready": False,
        "future_research_data_ready": False,
        "blockers": ["continuous_valuation_gap_cells:2", "future_research_gap_cells:2"],
    }
    ledger = json.loads((Path(result["manifest_path"]).parent / result["partitions"]["child_ledger"]["path"]).read_text())
    assert ledger["classification"] == "seal_post_acquisition_retrospective_historical_repair"
    assert ledger["prospective_holdout_boundary_unchanged"] is True
    assert ledger["network_requests_performed"] == 0


def test_membership_exit_remains_in_valuation_closure_domain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    matrix = tmp_path / "matrix"
    matrix.mkdir()
    stocks, dates = _matrix(matrix)
    bundle_path = tmp_path / "bundle" / "manifest.json"
    bundle = _bundle(bundle_path, stocks, dates)
    _patch_validators(monkeypatch, bundle)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    result = inventory.build_gap_inventory(_config(tmp_path, matrix, bundle_path, tmp_path / "none", evidence))
    row = next(row for row in result["cells"] if row["ts_code"] == "600170.SH" and row["trade_date"] == "20160323")
    assert row["membership"] is False
    assert row["valuation_closure_domain"] is True


def test_factor_readiness_blocks_only_when_gap_was_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    matrix = tmp_path / "matrix"
    matrix.mkdir()
    stocks, dates = _matrix(matrix)
    signal = np.load(matrix / "signal_eligible_at_close.npy")
    signal[0, 2] = True
    np.save(matrix / "signal_eligible_at_close.npy", signal, allow_pickle=False)
    bundle_path = tmp_path / "bundle" / "manifest.json"
    bundle = _bundle(bundle_path, stocks, dates)
    _patch_validators(monkeypatch, bundle)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    result = inventory.build_gap_inventory(_config(tmp_path, matrix, bundle_path, tmp_path / "none", evidence))
    assert result["readiness"]["factor_replay_ready"] is False
    assert "factor_replay_gap_cells:1" in result["readiness"]["blockers"]


def test_future_evidence_is_not_ingested_and_manifest_tampering_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    matrix = tmp_path / "matrix"
    matrix.mkdir()
    stocks, dates = _matrix(matrix)
    bundle_path = tmp_path / "bundle" / "manifest.json"
    bundle = _bundle(bundle_path, stocks, dates)
    _patch_validators(monkeypatch, bundle)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "daily.jsonl").write_text(
        json.dumps({"ts_code": "600170.SH", "trade_date": "20260701", "request_hash": "future"}) + "\n",
        encoding="utf-8",
    )
    result = inventory.build_gap_inventory(_config(tmp_path, matrix, bundle_path, tmp_path / "none", evidence))
    assert result["evidence_record_count"] == 0
    manifest_path = Path(result["manifest_path"])
    payload = json.loads(manifest_path.read_text())
    payload["cell_count"] += 1
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(inventory.GapInventoryError, match="content_hash_mismatch"):
        inventory.validate_gap_inventory(manifest_path)


def test_same_inputs_publish_same_generation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    matrix = tmp_path / "matrix"
    matrix.mkdir()
    stocks, dates = _matrix(matrix)
    bundle_path = tmp_path / "bundle" / "manifest.json"
    bundle = _bundle(bundle_path, stocks, dates)
    _patch_validators(monkeypatch, bundle)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    config = _config(tmp_path, matrix, bundle_path, tmp_path / "none", evidence)
    first = inventory.build_gap_inventory(config)
    second = inventory.build_gap_inventory(config)
    assert first["content_hash"] == second["content_hash"]
    assert first["generation_id"] == second["generation_id"]


def test_first_blocker_uses_explicit_asset_not_date_axis_index(tmp_path: Path):
    root = tmp_path / "runs" / "factor" / "generation"
    root.mkdir(parents=True)
    blocker = {
        "code": "security_date_evidence_insufficient",
        "detail": "valuation_open_blocked:20160323:1:cannot mark held asset 600170.SH with unknown price",
    }
    spec = {"factor_id": "factor_probe", "scenario": "baseline"}
    (root / "blocker.json").write_text(json.dumps(blocker), encoding="utf-8")
    (root / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    manifest = {
        "schema_version": "task055a_blocked_simulation_run_v1",
        "content_hash": "a" * 64,
        "partitions": {
            "blocker.json": {"sha256": inventory.sha256_file(root / "blocker.json")},
        },
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    rows = inventory._load_first_blockers([tmp_path / "runs"], ["000001.SZ", "000002.SZ", "600170.SH"])

    assert rows[0]["ts_code"] == "600170.SH"
    assert rows[0]["date_axis_index"] == 1
    assert "stock_index" not in rows[0]

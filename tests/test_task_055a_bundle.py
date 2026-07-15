import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pytest

from task_054_c.validators import canonical_hash, sha256_file
from task_055_a import bundle


def _stock_axis_hash(values: list[str]) -> str:
    return hashlib.sha256("\n".join(values).encode()).hexdigest()


def _date_axis_hash(values: list[str]) -> str:
    return hashlib.sha256("".join(value + "\n" for value in values).encode()).hexdigest()


def _json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n")


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))


def _ready_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    signal_dates = ["20240527", "20240528"]
    full_dates = signal_dates + ["20240529", "20240530", "20240531"]
    stocks = ["000001.SZ", "600000.SH"]
    signal_matrix = tmp_path / "projection" / "matrix"
    full_matrix = tmp_path / "canonical_matrix"
    signal_matrix.mkdir(parents=True)
    full_matrix.mkdir()
    _json(signal_matrix / "trade_dates.json", signal_dates)
    _json(signal_matrix / "ts_codes.json", stocks)
    _json(full_matrix / "trade_dates.json", full_dates)
    _json(full_matrix / "ts_codes.json", stocks)
    _json(
        signal_matrix / "task_052a_strict_matrix_manifest.json",
        {"date_axis_hash": _date_axis_hash(signal_dates), "stock_axis_hash": _stock_axis_hash(stocks)},
    )
    for name in bundle.STRICT_MASKS:
        shape = (2,) if name == "research_eligible_date_mask.npy" else (2, 2)
        np.save(signal_matrix / name, np.ones(shape, dtype=np.bool_), allow_pickle=False)
    for name in bundle.EXECUTION_MASKS:
        np.save(full_matrix / name, np.ones((2, 5), dtype=np.bool_), allow_pickle=False)
    np.save(full_matrix / "weight.npy", np.ones((2, 5), dtype=np.float32), allow_pickle=False)
    np.save(full_matrix / "snapshot_source_date.npy", np.ones(5, dtype=np.int32), allow_pickle=False)
    for offset, field in enumerate(bundle.RAW_FIELDS):
        values = np.arange(10, dtype=np.float32).reshape(2, 5) + offset
        np.save(full_matrix / f"{field}.npy", values, allow_pickle=False)
        np.save(full_matrix / f"{field}_validity.npy", np.ones((2, 5), dtype=np.bool_), allow_pickle=False)

    exact_ids = [f"factor_{index:02d}" for index in range(20)]
    materializations = {}
    for index, factor_id in enumerate(exact_ids):
        factor_dir = tmp_path / "materializations" / factor_id
        factor_dir.mkdir(parents=True)
        values_path = factor_dir / "values.npy"
        validity_path = factor_dir / "validity.npy"
        manifest_path = factor_dir / "materialization_manifest.json"
        np.save(values_path, np.full((2, 2), index, dtype=np.float32), allow_pickle=False)
        np.save(validity_path, np.ones((2, 2), dtype=np.bool_), allow_pickle=False)
        _json(
            manifest_path,
            {
                "factor_id": factor_id,
                "formula_hash": f"formula_{index:02d}",
                "materialization_status": "success",
                "shape": [2, 2],
                "stock_axis_hash": _stock_axis_hash(stocks),
                "date_axis_hash": _date_axis_hash(signal_dates),
                "value_sha256": sha256_file(values_path),
                "validity_sha256": sha256_file(validity_path),
            },
        )
        materializations[factor_id] = {
            "manifest_path": manifest_path,
            "manifest_sha256": sha256_file(manifest_path),
            "values_path": values_path,
            "validity_path": validity_path,
        }

    benchmark = tmp_path / "governed" / "benchmark.jsonl"
    corporate = tmp_path / "governed" / "corporate.jsonl"
    _jsonl(
        benchmark,
        [
            {"trade_date": date, "open": 1.0, "close": 1.1, "vol": 100.0, "amount": 1000.0}
            for date in ("20240528", "20240530", "20240531")
        ],
    )
    _jsonl(corporate, [{"ex_date": "20240530", "ts_code": "000001.SZ"}, {"ex_date": "20240531", "ts_code": "600000.SH"}])
    source_index = tmp_path / "governed" / "source_index.json"
    _json(
        source_index,
        {
            "schema_version": "governed_source_index_v1",
            "entries": {
                "benchmark_index_bars": {
                    "path": "benchmark.jsonl",
                    "sha256": sha256_file(benchmark),
                    "format": "jsonl",
                    "date_field": "trade_date",
                },
                "corporate_actions": {
                    "path": "corporate.jsonl",
                    "sha256": sha256_file(corporate),
                    "format": "jsonl",
                    "date_field": "ex_date",
                },
            },
        },
    )

    source_paths = {}
    for name in ("canonical_bundle", "final_verifier", "pre_gpu_seal", "research_projection"):
        path = tmp_path / "evidence_inputs" / f"{name}.json"
        _json(path, {"name": name})
        source_paths[name] = path
    source_paths["governed_source_index"] = source_index
    normalized_root = tmp_path / "normalized"
    normalized_manifest = normalized_root / "normalized_replay_store_manifest.json"
    _json(normalized_manifest, {"identity_root": "identity-root", "records_file": "factors.jsonl"})
    _jsonl(normalized_root / "factors.jsonl", [{"factor_id": factor_id} for factor_id in exact_ids])

    materialization_root = canonical_hash(
        [{"factor_id": factor_id, "sha256": materializations[factor_id]["manifest_sha256"]} for factor_id in exact_ids]
    )
    source_identity = {
        "canonical_bundle_manifest_sha256": sha256_file(source_paths["canonical_bundle"]),
        "final_verifier_manifest_sha256": sha256_file(source_paths["final_verifier"]),
        "pre_gpu_seal_manifest_sha256": sha256_file(source_paths["pre_gpu_seal"]),
        "research_projection_manifest_sha256": sha256_file(source_paths["research_projection"]),
        "normalized_store_manifest_sha256": sha256_file(normalized_manifest),
        "governed_source_index_sha256": sha256_file(source_index),
        "exact20_identity_root": "identity-root",
        "exact20_ids": exact_ids,
        "materialization_manifest_root": materialization_root,
    }
    context = {
        "exact_ids": exact_ids,
        "signal_matrix": signal_matrix,
        "full_matrix": full_matrix,
        "signal_dates": signal_dates,
        "execution_dates": full_dates[:4],
        "ts_codes": stocks,
        "signal_count": 2,
        "execution_count": 4,
        "materializations": materializations,
        "source_paths": source_paths,
        "normalized_manifest_path": normalized_manifest,
        "axes": {
            "stock_count": 2,
            "signal_date_count": 2,
            "execution_date_count": 4,
            "stock_axis_hash": _stock_axis_hash(stocks),
            "signal_date_axis_hash": _date_axis_hash(signal_dates),
            "execution_date_axis_hash": _date_axis_hash(full_dates[:4]),
        },
        "source_identity": source_identity,
    }
    monkeypatch.setattr(bundle, "_validate_sources", lambda **_: context)
    unit_contract = {
        "schema_version": bundle.UNIT_CONTRACT_SCHEMA,
        "units": bundle.EXPECTED_UNITS,
        "source_units": bundle.EXPECTED_SOURCE_UNITS,
        "multipliers": bundle.EXPECTED_MULTIPLIERS,
        "volume_semantics": "raw_unadjusted_shares",
        "amount_semantics": "raw_turnover_CNY",
    }
    published = bundle.publish_simulation_bundle(
        output_root=tmp_path / "simulation",
        canonical_bundle_manifest=source_paths["canonical_bundle"],
        final_verifier_manifest=source_paths["final_verifier"],
        pre_gpu_seal_manifest=source_paths["pre_gpu_seal"],
        research_projection_manifest=source_paths["research_projection"],
        normalized_store_root=normalized_root,
        materialization_manifests=[row["manifest_path"] for row in materializations.values()],
        governed_source_index=source_index,
        unit_contract=unit_contract,
    )
    return {"published": published, "unit_contract": unit_contract, "source_paths": source_paths, "normalized_root": normalized_root}


def _rewrite_content_addressed_manifest(manifest_path: Path, payload: dict) -> Path:
    semantic = {key: value for key, value in payload.items() if key not in {"generation_id", "content_hash"}}
    payload["content_hash"] = canonical_hash(semantic)
    payload["generation_id"] = f"simulation_bundle_{payload['content_hash'][:24]}"
    old_root = manifest_path.parent
    new_root = old_root.parent / payload["generation_id"]
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(old_root, new_root)
    return new_root / "simulation_bundle_manifest.json"


def test_strict_bundle_loads_exact20_and_physically_isolates_post_cutoff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fixture = _ready_fixture(tmp_path, monkeypatch)
    loaded = bundle.load_simulation_bundle(fixture["published"]["manifest_path"])
    assert len(loaded["factor_values"]) == 20
    assert loaded["trade_dates"][-1] == "20240528"
    assert loaded["execution_dates"][-1] == "20240530"
    assert max(row["trade_date"] for row in loaded["benchmark_index_bars"]) == "20240530"
    assert max(row["ex_date"] for row in loaded["corporate_actions"]) == "20240530"
    assert loaded["raw"]["open"].shape == (2, 4)
    assert loaded["strict_masks"]["signal_candidate_cells"].shape == (2, 2)


def test_authoritative_validator_rejects_factor_tampering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fixture = _ready_fixture(tmp_path, monkeypatch)
    manifest_path = Path(fixture["published"]["manifest_path"])
    manifest = json.loads(manifest_path.read_text())
    factor_id = manifest["exact20_ids"][0]
    values_path = manifest_path.parent / manifest["artifacts"][f"factor:{factor_id}:values"]["path"]
    np.save(values_path, np.full((2, 2), 999.0, dtype=np.float32), allow_pickle=False)
    with pytest.raises(bundle.SimulationBundleError, match="artifact_tampered"):
        bundle.validate_simulation_bundle(manifest_path)


def test_authoritative_validator_rejects_missing_strict_mask(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fixture = _ready_fixture(tmp_path, monkeypatch)
    manifest_path = Path(fixture["published"]["manifest_path"])
    manifest = json.loads(manifest_path.read_text())
    mask_path = manifest_path.parent / manifest["artifacts"]["mask:signal_candidate_cells.npy"]["path"]
    mask_path.unlink()
    with pytest.raises(bundle.SimulationBundleError, match="artifact_tampered"):
        bundle.validate_simulation_bundle(manifest_path)


def test_authoritative_validator_rejects_forged_post_cutoff_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fixture = _ready_fixture(tmp_path, monkeypatch)
    manifest_path = Path(fixture["published"]["manifest_path"])
    manifest = json.loads(manifest_path.read_text())
    entry = manifest["artifacts"]["benchmark_index_bars"]
    snapshot_path = manifest_path.parent / entry["path"]
    with snapshot_path.open("a") as stream:
        stream.write(json.dumps({"trade_date": "20240531", "open": 1, "close": 1, "vol": 1, "amount": 1}) + "\n")
    entry["sha256"] = sha256_file(snapshot_path)
    entry["size_bytes"] = snapshot_path.stat().st_size
    entry["row_count"] += 1
    entry["max_date"] = "20240531"
    forged_manifest = _rewrite_content_addressed_manifest(manifest_path, manifest)
    with pytest.raises(bundle.SimulationBundleError, match="post_cutoff_snapshot"):
        bundle.validate_simulation_bundle(forged_manifest)


def test_missing_critical_evidence_still_publishes_immutable_blocker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source_paths = {}
    for name in ("canonical_bundle", "final_verifier", "pre_gpu_seal", "research_projection", "governed_source_index"):
        path = tmp_path / f"{name}.json"
        _json(path, {"name": name})
        source_paths[name] = path
    normalized_root = tmp_path / "normalized"
    normalized_root.mkdir()
    monkeypatch.setattr(bundle, "_validate_sources", lambda **_: (_ for _ in ()).throw(RuntimeError("missing_strict_mask")))
    unit_contract = {
        "schema_version": bundle.UNIT_CONTRACT_SCHEMA,
        "units": bundle.EXPECTED_UNITS,
        "source_units": bundle.EXPECTED_SOURCE_UNITS,
        "multipliers": bundle.EXPECTED_MULTIPLIERS,
        "volume_semantics": "raw_unadjusted_shares",
        "amount_semantics": "raw_turnover_CNY",
    }
    arguments = dict(
        output_root=tmp_path / "simulation",
        canonical_bundle_manifest=source_paths["canonical_bundle"],
        final_verifier_manifest=source_paths["final_verifier"],
        pre_gpu_seal_manifest=source_paths["pre_gpu_seal"],
        research_projection_manifest=source_paths["research_projection"],
        normalized_store_root=normalized_root,
        materialization_manifests=[],
        governed_source_index=source_paths["governed_source_index"],
        unit_contract=unit_contract,
    )
    first = bundle.publish_simulation_bundle(**arguments)
    second = bundle.publish_simulation_bundle(**arguments)
    assert first["status"] == "blocked"
    assert first["content_hash"] == second["content_hash"]
    assert Path(first["generation_dir"]) == Path(second["generation_dir"])
    assert bundle.validate_simulation_bundle(first["manifest_path"], require_ready=False)["blockers"]
    with pytest.raises(bundle.SimulationBundleError, match="simulation_bundle_blocked"):
        bundle.load_simulation_bundle(first["manifest_path"])

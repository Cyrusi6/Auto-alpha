"""Production orchestration and immutable tensor publication for Task 053-A."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from artifact_schema.writer import attach_artifact_metadata
from data_lake.task052_freeze import validate_task052_governed_freeze
from feature_factory import (
    build_tensor_content_fingerprint,
    feature_semantic_source_hash,
    intersect_candidate_feature_blockers,
)
from model_core.data_loader import AShareDataLoader

from .readiness import derive_task053_readiness


@dataclass(frozen=True)
class Task053ArtifactInputs:
    governed_source_report: str
    freeze_dir: str
    universe_dir: str
    matrix_dir: str
    feature_manifest_path: str
    tensor_dir: str
    firewall_proof_path: str
    replay_evidence_path: str | None = None


def build_v3_tensor_generation(
    *,
    matrix_dir: str | Path,
    feature_manifest_path: str | Path,
    output_root: str | Path,
    candidate_pool_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build values and validity jointly, then publish a content-addressed generation."""
    matrix_root = Path(matrix_dir)
    feature_manifest = Path(feature_manifest_path)
    matrix_manifest = _load_json(matrix_root / "task_052a_strict_matrix_manifest.json")
    loader = AShareDataLoader(
        data_dir=matrix_root,
        matrix_cache_dir=matrix_root,
        use_matrix_cache=True,
        point_in_time=True,
        feature_set_manifest_path=feature_manifest,
        target_return_mode="target_open_t1_t2",
        feature_cutoff_mode="next_trade_day_open",
        label_horizon=2,
        device="cpu",
    ).load_data()
    values = loader.feat_tensor.detach().cpu().numpy().astype(np.float32, copy=False)
    validity = loader.feature_validity.detach().cpu().numpy().astype(np.bool_, copy=False)
    if values.shape != validity.shape or values.ndim != 3:
        raise RuntimeError("v3 values/validity tensor axis mismatch")
    stored_values = np.where(validity, values, 0.0).astype(np.float32, copy=False)
    if np.count_nonzero(stored_values[~validity]):
        raise RuntimeError("invalid tensor cells must be stored as zero")
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".v3_tensor_build.", dir=root))
    _atomic_npy(staging / "feature_tensor.npy", stored_values)
    _atomic_npy(staging / "feature_validity_tensor.npy", validity)
    values_sha256 = _sha256(staging / "feature_tensor.npy")
    validity_sha256 = _sha256(staging / "feature_validity_tensor.npy")
    source = {
        "matrix_content_hash": matrix_manifest.get("content_hash"),
        "matrix_semantic_hash": matrix_manifest.get("semantic_hash"),
        "matrix_manifest_sha256": _sha256(matrix_root / "task_052a_strict_matrix_manifest.json"),
        "feature_manifest_sha256": _sha256(feature_manifest),
        "freeze_content_hash": (matrix_manifest.get("generation_inputs") or {}).get("governed_freeze_content_hash") or (matrix_manifest.get("source") or {}).get("freeze_content_hash"),
        "universe_content_hash": (matrix_manifest.get("generation_inputs") or {}).get("historical_universe_content_hash") or (matrix_manifest.get("source") or {}).get("universe_content_hash"),
        "semantic_source_hash": feature_semantic_source_hash(extra_sources=(Path(__file__),)),
    }
    feature_blockers = sorted(
        str(item["feature_name"])
        for item in (loader.feature_validity_summary or [])
        if item.get("blocker")
    )
    candidates = _read_jsonl(candidate_pool_path) if candidate_pool_path else []
    candidate_blockers = intersect_candidate_feature_blockers(candidates, loader.feature_validity_summary or [])
    core = {
        "schema_version": "task_054a_v3_tensor_v2",
        "shape": list(stored_values.shape),
        "values_dtype": "float32",
        "validity_dtype": "bool",
        "stock_axis_hash": matrix_manifest.get("stock_axis_hash"),
        "date_axis_hash": matrix_manifest.get("date_axis_hash"),
        "feature_count": int(stored_values.shape[1]),
        "feature_axis_hash": _hash_json([
            str(item.get("feature_name"))
            for item in (_load_json(feature_manifest).get("feature_definitions") or [])
            if isinstance(item, dict) and item.get("feature_name")
        ]),
        "values_sha256": values_sha256,
        "validity_sha256": validity_sha256,
        "source": source,
        "feature_summaries": list(loader.feature_validity_summary or []),
        "feature_blockers": feature_blockers,
        "candidate_blockers": candidate_blockers,
        "invalid_values_stored_as_zero": True,
        "target_contract": matrix_manifest.get("target_contract") or {},
        "time_contract": matrix_manifest.get("time_contract") or matrix_manifest.get("firewall") or {},
    }
    content_hash = build_tensor_content_fingerprint(
        values_sha256=values_sha256,
        validity_sha256=validity_sha256,
        matrix_sha256=source["matrix_manifest_sha256"],
        freeze_sha256=str((matrix_manifest.get("generation_inputs") or {}).get("governed_freeze_content_hash") or source.get("freeze_content_hash") or ""),
        universe_sha256=str((matrix_manifest.get("generation_inputs") or {}).get("historical_universe_content_hash") or source.get("universe_content_hash") or ""),
        feature_manifest_sha256=source["feature_manifest_sha256"],
        stock_axis_hash=str(core["stock_axis_hash"] or ""),
        date_axis_hash=str(core["date_axis_hash"] or ""),
        feature_axis_hash=str(core["feature_axis_hash"]),
        target_contract_hash=_hash_json(core["target_contract"]),
        time_contract_hash=_hash_json(core["time_contract"]),
        semantic_source_hash=source["semantic_source_hash"],
    )
    generation_id = f"v3_tensor_054a_{content_hash[:24]}"
    generation = root / generation_id
    if generation.exists():
        manifest = _load_json(generation / "task_053_v3_tensor_manifest.json")
        _validate_tensor_generation(generation, manifest)
        if manifest.get("content_hash") != content_hash or any(manifest.get(key) != core.get(key) for key in core):
            raise RuntimeError("existing v3 tensor generation semantic mismatch")
        _remove_tree(staging)
        _atomic_json(root / "current_v3_tensor.json", {"generation_id": generation_id, "content_hash": content_hash})
        return manifest | {"generation_dir": str(generation), "cache_hit": True}
    try:
        manifest = attach_artifact_metadata(
            {
                **core,
                "artifact_type": "task_053_v3_tensor_manifest",
                "generation_id": generation_id,
                "content_hash": content_hash,
            },
            "task_053_v3_tensor_manifest",
            "task_053_a",
        )
        _atomic_json(staging / "task_053_v3_tensor_manifest.json", manifest)
        os.replace(staging, generation)
    finally:
        if staging.exists():
            _remove_tree(staging)
    _atomic_json(root / "current_v3_tensor.json", {"generation_id": generation_id, "content_hash": content_hash})
    return manifest | {"generation_dir": str(generation), "cache_hit": False}


class Task053Orchestrator:
    """Validate immutable stages in order and derive replay readiness from evidence."""

    stage_order = (
        "governed_source",
        "immutable_freeze",
        "historical_universe",
        "strict_matrix",
        "v3_tensor",
        "research_firewall",
        "four_gpu_replay",
    )

    def run(self, inputs: Task053ArtifactInputs, output_dir: str | Path) -> dict[str, Any]:
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        stages = self._validate_stages(inputs)
        readiness = derive_task053_readiness(stages)
        readiness_payload = attach_artifact_metadata(readiness, "task_053_readiness", "task_053_a")
        readiness_path = output / "task_053_readiness.json"
        _atomic_json(readiness_path, readiness_payload)
        candidate_count = int(stages.get("four_gpu_replay", {}).get("candidate_count", 20))
        report = attach_artifact_metadata(
            {
                "artifact_type": "task_053_orchestrator_report",
                "status": readiness["status"],
                "run_id": _hash_json(asdict(inputs))[:20],
                "source_campaign_id": stages.get("four_gpu_replay", {}).get("source_campaign_id", "historical_20_candidate_pool"),
                "candidate_count": candidate_count,
                "stage_order": list(self.stage_order),
                "stages": stages,
                "readiness": readiness,
                "output_paths": {"readiness": str(readiness_path)},
            },
            "task_053_orchestrator_report",
            "task_053_a",
        )
        report_path = output / "task_053_orchestrator_report.json"
        _atomic_json(report_path, report)
        return report | {"report_path": str(report_path), "readiness_path": str(readiness_path)}

    def _validate_stages(self, inputs: Task053ArtifactInputs) -> dict[str, dict[str, Any]]:
        source_path = Path(inputs.governed_source_report)
        source = _load_json(source_path)
        datasets = source.get("datasets") or {}
        source_ready = all(int((datasets.get(name) or {}).get("covered_stock_count", 0)) == 637 for name in ("suspensions", "st_status_daily", "name_changes"))
        freeze_validation = validate_task052_governed_freeze(inputs.freeze_dir)
        universe_path = Path(inputs.universe_dir) / "task_052a_universe_proof_manifest.json"
        universe = _load_json(universe_path)
        matrix_path = Path(inputs.matrix_dir) / "task_052a_strict_matrix_manifest.json"
        matrix = _load_json(matrix_path)
        tensor_path = Path(inputs.tensor_dir) / "task_053_v3_tensor_manifest.json"
        tensor = _load_json(tensor_path)
        _validate_tensor_generation(Path(inputs.tensor_dir), tensor)
        firewall_path = Path(inputs.firewall_proof_path)
        firewall = _load_json(firewall_path)
        replay: dict[str, Any] = {"ready": False, "proof_paths": [], "blockers": ["four_gpu_replay_not_completed"]}
        if inputs.replay_evidence_path:
            replay_path = Path(inputs.replay_evidence_path)
            evidence = _load_json(replay_path)
            replay_ready = evidence.get("status") == "success" and int(evidence.get("candidate_count", 0)) == 20
            replay = {"ready": replay_ready, "proof_paths": [str(replay_path)], "proof_sha256": _sha256(replay_path), **evidence}
        return {
            "governed_source": {
                "ready": source_ready,
                "conservative_tradability_policy_ready": source_ready,
                "proof_paths": [str(source_path)],
                "proof_sha256": _sha256(source_path),
                "blockers": [] if source_ready else ["governed_source_coverage_incomplete"],
            },
            "immutable_freeze": {"ready": bool(freeze_validation.get("valid")), "proof_paths": [str(freeze_validation["manifest_path"])], "proof_sha256": _sha256(freeze_validation["manifest_path"]), "blockers": freeze_validation.get("blockers", [])},
            "historical_universe": {"ready": bool(universe.get("historical_constituent_proof")), "proof_paths": [str(universe_path)], "proof_sha256": _sha256(universe_path), "blockers": universe.get("blockers", [])},
            "strict_matrix": {"ready": bool((matrix.get("readiness") or {}).get("strict_matrix_replay_safe")), "built": True, "proof_paths": [str(matrix_path)], "proof_sha256": _sha256(matrix_path), "blockers": (matrix.get("readiness") or {}).get("engineering_blockers", []), "quality_warnings": (matrix.get("readiness") or {}).get("quality_warnings", [])},
            "v3_tensor": {"ready": not tensor.get("candidate_blockers"), "proof_paths": [str(tensor_path)], "proof_sha256": _sha256(tensor_path), "candidate_blockers": tensor.get("candidate_blockers", [])},
            "research_firewall": {"ready": firewall.get("status") == "passed" and int((firewall.get("proof") or {}).get("access_violation_count", 1)) == 0, "proof_paths": [str(firewall_path)], "proof_sha256": _sha256(firewall_path), "blockers": firewall.get("blockers", [])},
            "four_gpu_replay": replay,
        }


def _validate_tensor_generation(root: Path, manifest: Mapping[str, Any]) -> None:
    values_path = root / "feature_tensor.npy"
    validity_path = root / "feature_validity_tensor.npy"
    if not values_path.is_file() or not validity_path.is_file():
        raise RuntimeError("v3 tensor generation is incomplete")
    if _sha256(values_path) != manifest.get("values_sha256") or _sha256(validity_path) != manifest.get("validity_sha256"):
        raise RuntimeError("v3 tensor generation hash mismatch")
    values = np.load(values_path, mmap_mode="r")
    validity = np.load(validity_path, mmap_mode="r")
    if list(values.shape) != list(manifest.get("shape") or []) or values.shape != validity.shape:
        raise RuntimeError("v3 tensor generation shape mismatch")
    if values.dtype != np.float32 or validity.dtype != np.bool_:
        raise RuntimeError("v3 tensor generation dtype mismatch")
    source = manifest.get("source") or {}
    expected_content_hash = build_tensor_content_fingerprint(
        values_sha256=str(manifest.get("values_sha256") or ""),
        validity_sha256=str(manifest.get("validity_sha256") or ""),
        matrix_sha256=str(source.get("matrix_manifest_sha256") or ""),
        freeze_sha256=str(source.get("freeze_content_hash") or ""),
        universe_sha256=str(source.get("universe_content_hash") or ""),
        feature_manifest_sha256=str(source.get("feature_manifest_sha256") or ""),
        stock_axis_hash=str(manifest.get("stock_axis_hash") or ""),
        date_axis_hash=str(manifest.get("date_axis_hash") or ""),
        feature_axis_hash=str(manifest.get("feature_axis_hash") or ""),
        target_contract_hash=_hash_json(manifest.get("target_contract") or {}),
        time_contract_hash=_hash_json(manifest.get("time_contract") or {}),
        semantic_source_hash=str(source.get("semantic_source_hash") or ""),
    )
    if manifest.get("content_hash") != expected_content_hash:
        raise RuntimeError("v3 tensor generation content hash mismatch")


def _read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(target)
    return [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]


def _remove_tree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


def _load_json(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(target)
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {target}")
    return payload


def _sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_json(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _atomic_npy(path: Path, values: np.ndarray) -> None:
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(fd)
    try:
        with open(name, "wb") as handle:
            np.save(handle, values, allow_pickle=False)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs-json", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    inputs = Task053ArtifactInputs(**_load_json(args.inputs_json))
    print(json.dumps(Task053Orchestrator().run(inputs, args.output_dir), sort_keys=True))


if __name__ == "__main__":
    main()

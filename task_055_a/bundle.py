"""Strict, content-addressed Task 055-A simulation bundle publication and loading."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import numpy as np

from task_054_c.bundle import validate_bundle as validate_task054c_bundle
from task_054_c.factor_store import validate_normalized_replay_store
from task_054_c.research_view import validate_research_projection
from task_054_c.seal import validate_pre_gpu_seal
from task_054_c.validators import canonical_hash, sha256_file


SIMULATION_BUNDLE_SCHEMA = "task055a_strict_simulation_bundle_v1"
SIGNAL_CUTOFF = "20240528"
EXECUTION_CUTOFF = "20240530"
SIGNAL_MASKS = (
    "signal_candidate_cells.npy",
    "validation_common_cells.npy",
    "research_eligible_date_mask.npy",
    "membership.npy",
    "membership_known.npy",
    "active.npy",
    "listed.npy",
    "st_effective.npy",
    "st_status_known.npy",
    "st_information_available.npy",
    "signal_eligible_at_close.npy",
    "unexplained_data_gap.npy",
)
EXECUTION_MASKS = (
    "membership.npy",
    "membership_known.npy",
    "active.npy",
    "listed.npy",
    "st_effective.npy",
    "st_status_known.npy",
    "st_information_available.npy",
    "open_execution_known.npy",
    "open_execution_value.npy",
    "buyable_at_open.npy",
    "sellable_at_open.npy",
    "suspension_source_covered.npy",
    "suspension_event_present.npy",
    "suspension_associated_bar_absence.npy",
    "conservative_open_excluded.npy",
    "open_at_up_limit.npy",
    "open_at_down_limit.npy",
    "adjustment_validity.npy",
    "bar_observed.npy",
    "unexplained_data_gap.npy",
    "realized_entry_possible.npy",
    "realized_exit_possible.npy",
    "target_available.npy",
)
EXECUTION_METADATA = ("weight.npy", "snapshot_source_date.npy")
DERIVED_EXECUTION_MASKS = ("corporate_action_validity.npy",)
STRICT_MASKS = SIGNAL_MASKS
RAW_FIELDS = ("open", "close", "vol", "amount")
SOURCE_KINDS = ("benchmark_index_bars", "corporate_actions")
UNIT_CONTRACT_SCHEMA = "task055a_simulation_unit_contract_v1"
EXPECTED_UNITS = {
    "factor_values": "dimensionless",
    "raw_open": "CNY_per_share",
    "raw_close": "CNY_per_share",
    "raw_vol": "shares",
    "raw_amount": "CNY",
    "benchmark_open": "index_points",
    "benchmark_close": "index_points",
    "benchmark_vol": "shares",
    "benchmark_amount": "CNY",
    "corporate_action_cash": "CNY_per_share",
    "corporate_action_ratio": "shares_per_share",
}
EXPECTED_SOURCE_UNITS = {
    "raw_vol": "lots_100_shares",
    "raw_amount": "thousand_CNY",
    "benchmark_vol": "lots_100_shares",
    "benchmark_amount": "thousand_CNY",
    "corporate_action_cash": "CNY_per_share",
    "corporate_action_ratio": "shares_per_share",
}
EXPECTED_MULTIPLIERS = {
    "raw_vol_to_shares": 100.0,
    "raw_amount_to_CNY": 1000.0,
    "benchmark_vol_to_shares": 100.0,
    "benchmark_amount_to_CNY": 1000.0,
}


class SimulationBundleError(RuntimeError):
    """Raised when a simulation bundle cannot be trusted or loaded."""


def publish_simulation_bundle(
    *,
    output_root: str | Path,
    canonical_bundle_manifest: str | Path,
    final_verifier_manifest: str | Path,
    pre_gpu_seal_manifest: str | Path,
    research_projection_manifest: str | Path,
    normalized_store_root: str | Path,
    materialization_manifests: Iterable[str | Path],
    governed_source_index: str | Path,
    unit_contract: Mapping[str, Any] | str | Path,
    **forbidden_inputs: Any,
) -> dict[str, Any]:
    """Publish an immutable bundle from governed Task 054-C evidence only.

    Evidence failures become blockers in a content-addressed generation. Generic
    data directories, factor stores, and fallback inputs are never accepted.
    """

    if forbidden_inputs:
        names = ",".join(sorted(forbidden_inputs))
        raise TypeError(f"unsupported_simulation_bundle_inputs:{names}")
    source_paths = {
        "canonical_bundle": Path(canonical_bundle_manifest),
        "final_verifier": Path(final_verifier_manifest),
        "pre_gpu_seal": Path(pre_gpu_seal_manifest),
        "research_projection": Path(research_projection_manifest),
        "governed_source_index": Path(governed_source_index),
    }
    materialization_paths = tuple(Path(path) for path in materialization_manifests)
    normalized_root = Path(normalized_store_root)
    units = _load_unit_contract(unit_contract)
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".simulation_bundle.", dir=root))
    blockers: list[dict[str, str]] = []
    source_identity: dict[str, Any] = {}
    artifact_registry: dict[str, dict[str, Any]] = {}
    axes: dict[str, Any] = {}
    exact_ids: list[str] = []

    try:
        context = _validate_sources(
            source_paths=source_paths,
            normalized_root=normalized_root,
            materialization_paths=materialization_paths,
            unit_contract=units,
        )
        source_identity = context["source_identity"]
        axes = context["axes"]
        exact_ids = context["exact_ids"]
        _write_payloads(
            staging=staging,
            context=context,
            source_index_path=source_paths["governed_source_index"],
            unit_contract=units,
            artifact_registry=artifact_registry,
        )
    except Exception as exc:
        blockers.append({"code": "critical_evidence_insufficient", "detail": str(exc)})
        _write_json(staging / "blockers.json", blockers)
        artifact_registry["blockers"] = _file_entry(staging, staging / "blockers.json", role="blockers")
        source_identity = _best_effort_source_identity(source_paths, normalized_root, materialization_paths)
        exact_ids = list(source_identity.get("exact20_ids") or [])

    semantic = {
        "schema_version": SIMULATION_BUNDLE_SCHEMA,
        "status": "blocked" if blockers else "ready",
        "signal_cutoff": SIGNAL_CUTOFF,
        "execution_cutoff": EXECUTION_CUTOFF,
        "valuation_cutoff": EXECUTION_CUTOFF,
        "physical_signal_view": True,
        "fallback_allowed": False,
        "source_identity": source_identity,
        "exact20_ids": exact_ids,
        "axes": axes,
        "artifacts": artifact_registry,
        "blockers": blockers,
    }
    content_hash = canonical_hash(semantic)
    generation_id = f"simulation_bundle_{content_hash[:24]}"
    manifest = semantic | {"generation_id": generation_id, "content_hash": content_hash}
    _write_json(staging / "simulation_bundle_manifest.json", manifest)
    target = root / "generations" / generation_id
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        existing = validate_simulation_bundle(target / "simulation_bundle_manifest.json", require_ready=False)
        if existing["content_hash"] != content_hash:
            raise SimulationBundleError("immutable_simulation_generation_conflict")
        shutil.rmtree(staging)
    else:
        os.replace(staging, target)
    manifest_path = target / "simulation_bundle_manifest.json"
    validated = validate_simulation_bundle(manifest_path, require_ready=False)
    _atomic_json(
        root / "current.json",
        {
            "generation_id": generation_id,
            "content_hash": content_hash,
            "manifest": f"generations/{generation_id}/simulation_bundle_manifest.json",
            "status": validated["status"],
        },
    )
    return validated | {"manifest_path": str(manifest_path), "generation_dir": str(target)}


def validate_simulation_bundle(
    manifest_path: str | Path,
    *,
    require_ready: bool = True,
) -> dict[str, Any]:
    """Authoritatively validate every copied byte, shape, axis, and cutoff."""

    path = Path(manifest_path)
    manifest = _read_json(path)
    semantic = {key: value for key, value in manifest.items() if key not in {"generation_id", "content_hash"}}
    if manifest.get("schema_version") != SIMULATION_BUNDLE_SCHEMA:
        raise SimulationBundleError("simulation_bundle_schema_invalid")
    if canonical_hash(semantic) != manifest.get("content_hash"):
        raise SimulationBundleError("simulation_bundle_content_hash_mismatch")
    expected_generation = f"simulation_bundle_{manifest['content_hash'][:24]}"
    if manifest.get("generation_id") != expected_generation or path.parent.name != expected_generation:
        raise SimulationBundleError("simulation_bundle_generation_identity_mismatch")
    if manifest.get("signal_cutoff") != SIGNAL_CUTOFF or manifest.get("execution_cutoff") != EXECUTION_CUTOFF:
        raise SimulationBundleError("simulation_bundle_cutoff_contract_mismatch")
    if manifest.get("valuation_cutoff") != EXECUTION_CUTOFF or manifest.get("fallback_allowed") is not False:
        raise SimulationBundleError("simulation_bundle_fallback_or_valuation_contract_invalid")

    artifacts = manifest.get("artifacts") or {}
    if not isinstance(artifacts, Mapping):
        raise SimulationBundleError("simulation_bundle_artifact_registry_invalid")
    for name, entry in artifacts.items():
        artifact = _safe_relative(path.parent, entry.get("path"))
        if not artifact.is_file() or sha256_file(artifact) != entry.get("sha256"):
            raise SimulationBundleError(f"simulation_bundle_artifact_tampered:{name}")
        if artifact.stat().st_size != entry.get("size_bytes"):
            raise SimulationBundleError(f"simulation_bundle_artifact_size_mismatch:{name}")
        if artifact.suffix == ".npy":
            array = np.load(artifact, mmap_mode="r", allow_pickle=False)
            if list(array.shape) != entry.get("shape") or str(array.dtype) != entry.get("dtype"):
                raise SimulationBundleError(f"simulation_bundle_artifact_shape_dtype_mismatch:{name}")

    status = manifest.get("status")
    if status == "blocked":
        if not manifest.get("blockers") or "blockers" not in artifacts:
            raise SimulationBundleError("simulation_bundle_blocker_evidence_missing")
        if require_ready:
            raise SimulationBundleError("simulation_bundle_blocked")
        return manifest
    if status != "ready" or manifest.get("blockers"):
        raise SimulationBundleError("simulation_bundle_status_invalid")
    registered = {str(Path(entry["path"])) for entry in artifacts.values()}
    observed = {
        str(candidate.relative_to(path.parent))
        for candidate in path.parent.rglob("*")
        if candidate.is_file() and candidate != path
    }
    if observed != registered:
        raise SimulationBundleError("simulation_bundle_unregistered_or_missing_file")
    _validate_ready_manifest(path.parent, manifest)
    return manifest


authoritative_validate_simulation_bundle = validate_simulation_bundle


def load_simulation_bundle(manifest_path: str | Path) -> dict[str, Any]:
    """Load only an authoritative ready bundle; no raw-directory fallback exists."""

    path = Path(manifest_path)
    manifest = validate_simulation_bundle(path, require_ready=True)
    root = path.parent
    factor_ids = manifest["exact20_ids"]
    return {
        "manifest": manifest,
        "trade_dates": _read_json(root / manifest["artifacts"]["signal_trade_dates"]["path"]),
        "execution_dates": _read_json(root / manifest["artifacts"]["execution_trade_dates"]["path"]),
        "ts_codes": _read_json(root / manifest["artifacts"]["ts_codes"]["path"]),
        "factor_values": {
            factor_id: np.load(root / manifest["artifacts"][f"factor:{factor_id}:values"]["path"], mmap_mode="r")
            for factor_id in factor_ids
        },
        "factor_validity": {
            factor_id: np.load(root / manifest["artifacts"][f"factor:{factor_id}:validity"]["path"], mmap_mode="r")
            for factor_id in factor_ids
        },
        "strict_masks": {
            Path(name).stem: np.load(root / manifest["artifacts"][f"mask:{name}"]["path"], mmap_mode="r")
            for name in SIGNAL_MASKS
        },
        "execution_masks": {
            Path(name).stem: np.load(root / manifest["artifacts"][f"execution_mask:{name}"]["path"], mmap_mode="r")
            for name in EXECUTION_MASKS + DERIVED_EXECUTION_MASKS
        },
        "execution_metadata": {
            Path(name).stem: np.load(root / manifest["artifacts"][f"execution_metadata:{name}"]["path"], mmap_mode="r")
            for name in EXECUTION_METADATA
        },
        "raw": {
            field: np.load(root / manifest["artifacts"][f"raw:{field}"]["path"], mmap_mode="r")
            for field in RAW_FIELDS
        },
        "raw_validity": {
            field: np.load(root / manifest["artifacts"][f"raw:{field}:validity"]["path"], mmap_mode="r")
            for field in RAW_FIELDS
        },
        "benchmark_index_bars": _read_jsonl(root / manifest["artifacts"]["benchmark_index_bars"]["path"]),
        "corporate_actions": _read_jsonl(root / manifest["artifacts"]["corporate_actions"]["path"]),
        "unit_contract": _read_json(root / manifest["artifacts"]["unit_contract"]["path"]),
    }


load_strict_simulation_bundle = load_simulation_bundle


def _validate_sources(
    *,
    source_paths: Mapping[str, Path],
    normalized_root: Path,
    materialization_paths: tuple[Path, ...],
    unit_contract: dict[str, Any],
) -> dict[str, Any]:
    for name, path in source_paths.items():
        if not path.is_file():
            raise SimulationBundleError(f"required_governed_manifest_missing:{name}")
    canonical = validate_task054c_bundle(source_paths["canonical_bundle"])
    projection = validate_research_projection(source_paths["research_projection"])
    seal = validate_pre_gpu_seal(source_paths["pre_gpu_seal"], bundle_manifest=source_paths["canonical_bundle"])
    final = _validate_final_verifier(source_paths["final_verifier"])
    canonical_store = Path(canonical["artifact_paths"]["normalized_store_root"]).resolve()
    if normalized_root.resolve() != canonical_store:
        raise SimulationBundleError("normalized_store_not_canonical_bundle_store")
    normalized = validate_normalized_replay_store(normalized_root, expected_ids=canonical["exact20_ids"])
    exact_ids = sorted(canonical["exact20_ids"])
    if len(exact_ids) != 20 or len(set(exact_ids)) != 20:
        raise SimulationBundleError("simulation_bundle_exact20_invalid")
    roots = {
        canonical["exact20_identity_root"],
        normalized["identity_root"],
        seal.get("exact20_identity_root"),
        final.get("exact20_identity_root"),
    }
    if len(roots) != 1:
        raise SimulationBundleError("simulation_bundle_identity_root_mismatch")
    if final.get("bundle_hash") != canonical["content_hash"] or seal.get("bundle_hash") != canonical["content_hash"]:
        raise SimulationBundleError("simulation_bundle_task054c_bundle_lineage_mismatch")
    if final.get("seal_hash") != seal.get("seal_hash"):
        raise SimulationBundleError("simulation_bundle_final_seal_lineage_mismatch")
    source_shas = final.get("source_artifact_sha256") or {}
    if source_shas.get("bundle") != sha256_file(source_paths["canonical_bundle"]):
        raise SimulationBundleError("simulation_bundle_final_bundle_sha_mismatch")
    if source_shas.get("seal") != sha256_file(source_paths["pre_gpu_seal"]):
        raise SimulationBundleError("simulation_bundle_final_seal_sha_mismatch")
    if (final.get("replay") or {}).get("verified") is not True:
        raise SimulationBundleError("simulation_bundle_final_replay_not_verified")

    projection_manifest = projection
    projection_stage = (seal.get("stages") or {}).get("research") or {}
    expected_projection = {
        projection_stage.get("baseline_projection_content_hash"),
        projection.get("content_hash"),
    }
    if len(expected_projection) != 1:
        raise SimulationBundleError("simulation_bundle_projection_content_lineage_mismatch")
    if projection_stage.get("baseline_projection_matrix_content_hash") != projection_manifest.get("matrix_content_hash"):
        raise SimulationBundleError("simulation_bundle_projection_matrix_lineage_mismatch")
    if projection_stage.get("baseline_projection_tensor_content_hash") != projection_manifest.get("tensor_content_hash"):
        raise SimulationBundleError("simulation_bundle_projection_tensor_lineage_mismatch")
    if projection_manifest.get("research_date_end") != SIGNAL_CUTOFF:
        raise SimulationBundleError("simulation_bundle_signal_projection_cutoff_invalid")

    projection_root = Path(projection["generation_dir"])
    signal_matrix = projection_root / projection_manifest["matrix_root"]
    full_matrix = Path(canonical["artifact_paths"]["matrix_root"])
    signal_dates = _read_axis(signal_matrix / "trade_dates.json")
    execution_dates_all = _read_axis(full_matrix / "trade_dates.json")
    ts_codes = _read_axis(signal_matrix / "ts_codes.json")
    full_codes = _read_axis(full_matrix / "ts_codes.json")
    signal_indices = _prefix_indices(signal_dates, SIGNAL_CUTOFF, exact_end=True)
    execution_indices = _prefix_indices(execution_dates_all, EXECUTION_CUTOFF, exact_end=True)
    if ts_codes != full_codes:
        raise SimulationBundleError("simulation_bundle_stock_axis_values_mismatch")
    signal_axis_hash = _hash_date_axis(signal_dates)
    stock_axis_hash = _hash_stock_axis(ts_codes)
    projected_matrix_manifest = _read_json(signal_matrix / "task_052a_strict_matrix_manifest.json")
    if signal_axis_hash != projected_matrix_manifest["date_axis_hash"]:
        raise SimulationBundleError("simulation_bundle_signal_date_axis_hash_mismatch")
    if stock_axis_hash != projected_matrix_manifest["stock_axis_hash"]:
        raise SimulationBundleError("simulation_bundle_stock_axis_hash_mismatch")

    records = {record.factor_id: record for record in normalized["records"]}
    materializations = _validate_materializations(
        materialization_paths,
        exact_ids=exact_ids,
        records=records,
        expected_shape=(len(ts_codes), len(signal_dates)),
        stock_axis_hash=stock_axis_hash,
        date_axis_hash=_hash_stock_axis(signal_dates),
    )
    _validate_unit_contract(unit_contract)
    return {
        "canonical": canonical,
        "projection": projection,
        "seal": seal,
        "final": final,
        "normalized": normalized,
        "exact_ids": exact_ids,
        "signal_matrix": signal_matrix,
        "full_matrix": full_matrix,
        "signal_dates": signal_dates,
        "execution_dates": execution_dates_all[: len(execution_indices)],
        "ts_codes": ts_codes,
        "signal_count": len(signal_indices),
        "execution_count": len(execution_indices),
        "materializations": materializations,
        "source_paths": dict(source_paths),
        "normalized_manifest_path": normalized_root / "normalized_replay_store_manifest.json",
        "axes": {
            "stock_count": len(ts_codes),
            "signal_date_count": len(signal_dates),
            "execution_date_count": len(execution_indices),
            "stock_axis_hash": stock_axis_hash,
            "signal_date_axis_hash": signal_axis_hash,
            "execution_date_axis_hash": _hash_date_axis(execution_dates_all[: len(execution_indices)]),
        },
        "source_identity": {
            "canonical_bundle_content_hash": canonical["content_hash"],
            "canonical_bundle_manifest_sha256": sha256_file(source_paths["canonical_bundle"]),
            "final_verifier_content_hash": final["content_hash"],
            "final_verifier_manifest_sha256": sha256_file(source_paths["final_verifier"]),
            "pre_gpu_seal_hash": seal["seal_hash"],
            "pre_gpu_seal_manifest_sha256": sha256_file(source_paths["pre_gpu_seal"]),
            "research_projection_content_hash": projection["content_hash"],
            "research_projection_manifest_sha256": sha256_file(source_paths["research_projection"]),
            "normalized_store_content_hash": normalized["content_hash"],
            "normalized_store_manifest_sha256": sha256_file(normalized_root / "normalized_replay_store_manifest.json"),
            "exact20_identity_root": normalized["identity_root"],
            "exact20_ids": exact_ids,
            "materialization_manifest_root": canonical_hash(
                [{"factor_id": factor_id, "sha256": row["manifest_sha256"]} for factor_id, row in sorted(materializations.items())]
            ),
            "governed_source_index_sha256": sha256_file(source_paths["governed_source_index"]),
        },
    }


def _write_payloads(
    *,
    staging: Path,
    context: dict[str, Any],
    source_index_path: Path,
    unit_contract: dict[str, Any],
    artifact_registry: dict[str, dict[str, Any]],
) -> None:
    axes_dir = staging / "axes"
    _write_json(axes_dir / "trade_dates.json", context["signal_dates"])
    _write_json(axes_dir / "execution_trade_dates.json", context["execution_dates"])
    _write_json(axes_dir / "ts_codes.json", context["ts_codes"])
    artifact_registry["signal_trade_dates"] = _file_entry(staging, axes_dir / "trade_dates.json", role="signal_axis")
    artifact_registry["execution_trade_dates"] = _file_entry(staging, axes_dir / "execution_trade_dates.json", role="execution_axis")
    artifact_registry["ts_codes"] = _file_entry(staging, axes_dir / "ts_codes.json", role="stock_axis")

    evidence_dir = staging / "evidence"
    evidence_sources = {
        "canonical_bundle": context["source_paths"]["canonical_bundle"],
        "final_verifier": context["source_paths"]["final_verifier"],
        "pre_gpu_seal": context["source_paths"]["pre_gpu_seal"],
        "research_projection": context["source_paths"]["research_projection"],
        "normalized_store_manifest": context["normalized_manifest_path"],
        "governed_source_index": context["source_paths"]["governed_source_index"],
    }
    for name, source in evidence_sources.items():
        target = evidence_dir / f"{name}.json"
        _copy_file(source, target)
        artifact_registry[f"evidence:{name}"] = _file_entry(staging, target, role="source_evidence")

    factor_dir = staging / "factors"
    for factor_id, row in sorted(context["materializations"].items()):
        target = factor_dir / factor_id
        _copy_file(row["manifest_path"], target / "materialization_manifest.json")
        _copy_file(row["values_path"], target / "values.npy")
        _copy_file(row["validity_path"], target / "validity.npy")
        artifact_registry[f"factor:{factor_id}:manifest"] = _file_entry(
            staging, target / "materialization_manifest.json", role="materialization_manifest", factor_id=factor_id
        )
        artifact_registry[f"factor:{factor_id}:values"] = _file_entry(staging, target / "values.npy", role="factor_values", factor_id=factor_id)
        artifact_registry[f"factor:{factor_id}:validity"] = _file_entry(staging, target / "validity.npy", role="factor_validity", factor_id=factor_id)

    mask_dir = staging / "strict_masks"
    for name in SIGNAL_MASKS:
        source = context["signal_matrix"] / name
        if not source.is_file():
            raise SimulationBundleError(f"strict_mask_missing:{name}")
        array = np.load(source, mmap_mode="r", allow_pickle=False)
        expected = (len(context["ts_codes"]), context["signal_count"]) if array.ndim == 2 else (context["signal_count"],)
        if tuple(array.shape) != expected or array.dtype != np.bool_:
            raise SimulationBundleError(f"strict_mask_shape_dtype_invalid:{name}")
        _save_npy(mask_dir / name, array)
        artifact_registry[f"mask:{name}"] = _file_entry(staging, mask_dir / name, role="strict_mask")

    execution_mask_dir = staging / "execution_masks"
    full_date_count = len(_read_axis(context["full_matrix"] / "trade_dates.json"))
    expected_full = (len(context["ts_codes"]), full_date_count)
    for name in EXECUTION_MASKS:
        source = context["full_matrix"] / name
        if not source.is_file():
            raise SimulationBundleError(f"execution_mask_missing:{name}")
        array = np.load(source, mmap_mode="r", allow_pickle=False)
        if tuple(array.shape) != expected_full or array.dtype != np.bool_:
            raise SimulationBundleError(f"execution_mask_shape_dtype_invalid:{name}")
        _save_npy(execution_mask_dir / name, array[:, : context["execution_count"]])
        artifact_registry[f"execution_mask:{name}"] = _file_entry(
            staging, execution_mask_dir / name, role="execution_strict_mask"
        )

    metadata_dir = staging / "execution_metadata"
    for name in EXECUTION_METADATA:
        source = context["full_matrix"] / name
        if not source.is_file():
            raise SimulationBundleError(f"execution_metadata_missing:{name}")
        array = np.load(source, mmap_mode="r", allow_pickle=False)
        expected_metadata_shape = (full_date_count,) if name == "snapshot_source_date.npy" else expected_full
        if tuple(array.shape) != expected_metadata_shape:
            raise SimulationBundleError(f"execution_metadata_shape_invalid:{name}")
        selected = array[: context["execution_count"]] if array.ndim == 1 else array[:, : context["execution_count"]]
        _save_npy(metadata_dir / name, selected)
        artifact_registry[f"execution_metadata:{name}"] = _file_entry(
            staging, metadata_dir / name, role="execution_metadata"
        )

    raw_dir = staging / "execution"
    for field in RAW_FIELDS:
        source = context["full_matrix"] / f"{field}.npy"
        validity_source = _resolve_raw_validity(context["full_matrix"], field)
        values = np.load(source, mmap_mode="r", allow_pickle=False)
        validity = np.load(validity_source, mmap_mode="r", allow_pickle=False)
        expected_full = (len(context["ts_codes"]), len(_read_axis(context["full_matrix"] / "trade_dates.json")))
        if tuple(values.shape) != expected_full or tuple(validity.shape) != expected_full:
            raise SimulationBundleError(f"raw_shape_mismatch:{field}")
        if validity.dtype != np.bool_:
            raise SimulationBundleError(f"raw_validity_dtype_invalid:{field}")
        normalized = np.asarray(values[:, : context["execution_count"]], dtype=np.float32)
        if field == "vol":
            normalized = normalized * float(unit_contract["multipliers"]["raw_vol_to_shares"])
        elif field == "amount":
            normalized = normalized * float(unit_contract["multipliers"]["raw_amount_to_CNY"])
        _save_npy(raw_dir / f"{field}.npy", normalized)
        _save_npy(raw_dir / f"{field}_validity.npy", validity[:, : context["execution_count"]])
        artifact_registry[f"raw:{field}"] = _file_entry(staging, raw_dir / f"{field}.npy", role="raw_execution")
        artifact_registry[f"raw:{field}:validity"] = _file_entry(staging, raw_dir / f"{field}_validity.npy", role="raw_validity")

    snapshots = _resolve_governed_snapshots(source_index_path)
    snapshot_dir = staging / "snapshots"
    corporate_action_rows: list[dict[str, Any]] = []
    for kind in SOURCE_KINDS:
        rows = _filter_snapshot_rows(snapshots[kind], cutoff=EXECUTION_CUTOFF)
        if not rows and not snapshots[kind].get("empty_snapshot_attested"):
            raise SimulationBundleError(f"governed_snapshot_empty:{kind}")
        if kind == "benchmark_index_bars" and not rows:
            raise SimulationBundleError("governed_benchmark_snapshot_empty")
        if kind == "benchmark_index_bars" and any(
            not {"open", "close", "vol", "amount"}.issubset(row) for row in rows
        ):
            raise SimulationBundleError("governed_benchmark_bar_fields_missing")
        if kind == "benchmark_index_bars":
            rows = [
                dict(row)
                | {
                    "vol": float(row["vol"]) * float(unit_contract["multipliers"]["benchmark_vol_to_shares"]),
                    "amount": float(row["amount"]) * float(unit_contract["multipliers"]["benchmark_amount_to_CNY"]),
                }
                for row in rows
            ]
        elif kind == "corporate_actions":
            corporate_action_rows = rows
        target = snapshot_dir / f"{kind}.jsonl"
        _write_jsonl(target, rows)
        artifact_registry[kind] = _file_entry(
            staging,
            target,
            role=kind,
            row_count=len(rows),
            source_sha256=snapshots[kind]["sha256"],
            date_field=snapshots[kind].get("date_field"),
            max_date=max((_record_date(row, snapshots[kind].get("date_field")) for row in rows), default=None),
            empty_snapshot_attested=bool(snapshots[kind].get("empty_snapshot_attested")),
            coverage_end=snapshots[kind].get("coverage_end"),
        )

    corporate_validity = _corporate_action_validity(
        corporate_action_rows,
        ts_codes=context["ts_codes"],
        trade_dates=context["execution_dates"],
    )
    corporate_mask_path = execution_mask_dir / "corporate_action_validity.npy"
    _save_npy(corporate_mask_path, corporate_validity)
    artifact_registry["execution_mask:corporate_action_validity.npy"] = _file_entry(
        staging, corporate_mask_path, role="execution_strict_mask"
    )

    _write_json(staging / "unit_contract.json", unit_contract)
    artifact_registry["unit_contract"] = _file_entry(staging, staging / "unit_contract.json", role="unit_contract")


def _validate_ready_manifest(root: Path, manifest: dict[str, Any]) -> None:
    exact_ids = manifest.get("exact20_ids") or []
    if len(exact_ids) != 20 or exact_ids != sorted(set(exact_ids)):
        raise SimulationBundleError("simulation_bundle_exact20_manifest_invalid")
    artifacts = manifest["artifacts"]
    required = {"signal_trade_dates", "execution_trade_dates", "ts_codes", "benchmark_index_bars", "corporate_actions", "unit_contract"}
    required.update(
        f"evidence:{name}"
        for name in (
            "canonical_bundle", "final_verifier", "pre_gpu_seal", "research_projection",
            "normalized_store_manifest", "governed_source_index",
        )
    )
    required.update(f"mask:{name}" for name in SIGNAL_MASKS)
    required.update(f"execution_mask:{name}" for name in EXECUTION_MASKS)
    required.update(f"execution_mask:{name}" for name in DERIVED_EXECUTION_MASKS)
    required.update(f"execution_metadata:{name}" for name in EXECUTION_METADATA)
    required.update(f"raw:{field}" for field in RAW_FIELDS)
    required.update(f"raw:{field}:validity" for field in RAW_FIELDS)
    required.update(f"factor:{factor_id}:{kind}" for factor_id in exact_ids for kind in ("manifest", "values", "validity"))
    missing = sorted(required - set(artifacts))
    if missing:
        raise SimulationBundleError(f"simulation_bundle_required_artifact_missing:{missing}")
    signal_dates = _read_axis(root / artifacts["signal_trade_dates"]["path"])
    execution_dates = _read_axis(root / artifacts["execution_trade_dates"]["path"])
    stocks = _read_axis(root / artifacts["ts_codes"]["path"])
    if not signal_dates or signal_dates[-1] != SIGNAL_CUTOFF or any(date > SIGNAL_CUTOFF for date in signal_dates):
        raise SimulationBundleError("simulation_bundle_post_cutoff_signal_data")
    if not execution_dates or execution_dates[-1] != EXECUTION_CUTOFF or any(date > EXECUTION_CUTOFF for date in execution_dates):
        raise SimulationBundleError("simulation_bundle_post_cutoff_execution_data")
    if signal_dates != sorted(signal_dates) or execution_dates != sorted(execution_dates):
        raise SimulationBundleError("simulation_bundle_date_axis_unsorted")
    axes = manifest.get("axes") or {}
    expected_axes = {
        "stock_count": len(stocks),
        "signal_date_count": len(signal_dates),
        "execution_date_count": len(execution_dates),
        "stock_axis_hash": _hash_stock_axis(stocks),
        "signal_date_axis_hash": _hash_date_axis(signal_dates),
        "execution_date_axis_hash": _hash_date_axis(execution_dates),
    }
    if axes != expected_axes:
        raise SimulationBundleError("simulation_bundle_axes_mismatch")
    source_identity = manifest.get("source_identity") or {}
    evidence_sha_contract = {
        "canonical_bundle": source_identity.get("canonical_bundle_manifest_sha256"),
        "final_verifier": source_identity.get("final_verifier_manifest_sha256"),
        "pre_gpu_seal": source_identity.get("pre_gpu_seal_manifest_sha256"),
        "research_projection": source_identity.get("research_projection_manifest_sha256"),
        "normalized_store_manifest": source_identity.get("normalized_store_manifest_sha256"),
        "governed_source_index": source_identity.get("governed_source_index_sha256"),
    }
    if any(artifacts[f"evidence:{name}"]["sha256"] != digest for name, digest in evidence_sha_contract.items()):
        raise SimulationBundleError("simulation_bundle_source_evidence_lineage_mismatch")
    signal_shape = (len(stocks), len(signal_dates))
    execution_shape = (len(stocks), len(execution_dates))
    materialization_root_rows = []
    for factor_id in exact_ids:
        materialization = _read_json(root / artifacts[f"factor:{factor_id}:manifest"]["path"])
        values = np.load(root / artifacts[f"factor:{factor_id}:values"]["path"], mmap_mode="r", allow_pickle=False)
        validity = np.load(root / artifacts[f"factor:{factor_id}:validity"]["path"], mmap_mode="r", allow_pickle=False)
        if materialization.get("factor_id") != factor_id or materialization.get("materialization_status") != "success":
            raise SimulationBundleError(f"simulation_bundle_materialization_manifest_invalid:{factor_id}")
        if values.shape != signal_shape or validity.shape != signal_shape or values.dtype != np.float32 or validity.dtype != np.bool_:
            raise SimulationBundleError(f"simulation_bundle_factor_shape_invalid:{factor_id}")
        if materialization.get("value_sha256") != artifacts[f"factor:{factor_id}:values"]["sha256"]:
            raise SimulationBundleError(f"simulation_bundle_factor_value_lineage_mismatch:{factor_id}")
        if materialization.get("validity_sha256") != artifacts[f"factor:{factor_id}:validity"]["sha256"]:
            raise SimulationBundleError(f"simulation_bundle_factor_validity_lineage_mismatch:{factor_id}")
        materialization_root_rows.append(
            {"factor_id": factor_id, "sha256": artifacts[f"factor:{factor_id}:manifest"]["sha256"]}
        )
    if canonical_hash(materialization_root_rows) != source_identity.get("materialization_manifest_root"):
        raise SimulationBundleError("simulation_bundle_materialization_manifest_root_mismatch")
    for name in SIGNAL_MASKS:
        mask = np.load(root / artifacts[f"mask:{name}"]["path"], mmap_mode="r", allow_pickle=False)
        expected = signal_shape if mask.ndim == 2 else (len(signal_dates),)
        if mask.shape != expected or mask.dtype != np.bool_:
            raise SimulationBundleError(f"simulation_bundle_mask_invalid:{name}")
    for name in EXECUTION_MASKS + DERIVED_EXECUTION_MASKS:
        mask = np.load(root / artifacts[f"execution_mask:{name}"]["path"], mmap_mode="r", allow_pickle=False)
        if mask.shape != execution_shape or mask.dtype != np.bool_:
            raise SimulationBundleError(f"simulation_bundle_execution_mask_invalid:{name}")
    for name in EXECUTION_METADATA:
        values = np.load(root / artifacts[f"execution_metadata:{name}"]["path"], mmap_mode="r", allow_pickle=False)
        expected = (len(execution_dates),) if name == "snapshot_source_date.npy" else execution_shape
        if values.shape != expected:
            raise SimulationBundleError(f"simulation_bundle_execution_metadata_invalid:{name}")
    for field in RAW_FIELDS:
        values = np.load(root / artifacts[f"raw:{field}"]["path"], mmap_mode="r", allow_pickle=False)
        validity = np.load(root / artifacts[f"raw:{field}:validity"]["path"], mmap_mode="r", allow_pickle=False)
        if values.shape != execution_shape or validity.shape != execution_shape or values.dtype != np.float32 or validity.dtype != np.bool_:
            raise SimulationBundleError(f"simulation_bundle_raw_invalid:{field}")
    for kind in SOURCE_KINDS:
        rows = _read_jsonl(root / artifacts[kind]["path"])
        empty_attested = artifacts[kind].get("empty_snapshot_attested") is True
        coverage_end = str(artifacts[kind].get("coverage_end") or "")
        if not rows and not (kind == "corporate_actions" and empty_attested and coverage_end >= EXECUTION_CUTOFF):
            raise SimulationBundleError(f"simulation_bundle_snapshot_empty:{kind}")
        if any(_record_date(row, artifacts[kind].get("date_field")) > EXECUTION_CUTOFF for row in rows):
            raise SimulationBundleError(f"simulation_bundle_post_cutoff_snapshot:{kind}")
        if artifacts[kind].get("row_count") != len(rows):
            raise SimulationBundleError(f"simulation_bundle_snapshot_count_mismatch:{kind}")
        observed_max = max((_record_date(row, artifacts[kind].get("date_field")) for row in rows), default=None)
        if artifacts[kind].get("max_date") != observed_max:
            raise SimulationBundleError(f"simulation_bundle_snapshot_date_metadata_mismatch:{kind}")
    _validate_unit_contract(_read_json(root / artifacts["unit_contract"]["path"]))


def _validate_materializations(
    paths: tuple[Path, ...],
    *,
    exact_ids: list[str],
    records: Mapping[str, Any],
    expected_shape: tuple[int, int],
    stock_axis_hash: str,
    date_axis_hash: str,
) -> dict[str, dict[str, Any]]:
    if len(paths) != 20:
        raise SimulationBundleError("materialization_manifest_count_not_20")
    rows: dict[str, dict[str, Any]] = {}
    for path in paths:
        payload = _read_json(path)
        factor_id = str(payload.get("factor_id") or "")
        if factor_id in rows:
            raise SimulationBundleError(f"duplicate_materialization_factor:{factor_id}")
        if payload.get("materialization_status") != "success":
            raise SimulationBundleError(f"materialization_not_success:{factor_id}")
        values_path = path.parent / "values.npy"
        validity_path = path.parent / "validity.npy"
        if not values_path.is_file() or not validity_path.is_file():
            raise SimulationBundleError(f"materialization_partitions_missing:{factor_id}")
        if sha256_file(values_path) != payload.get("value_sha256") or sha256_file(validity_path) != payload.get("validity_sha256"):
            raise SimulationBundleError(f"materialization_sha_mismatch:{factor_id}")
        values = np.load(values_path, mmap_mode="r", allow_pickle=False)
        validity = np.load(validity_path, mmap_mode="r", allow_pickle=False)
        if values.shape != expected_shape or validity.shape != expected_shape or list(values.shape) != payload.get("shape"):
            raise SimulationBundleError(f"materialization_shape_mismatch:{factor_id}")
        if values.dtype != np.float32 or validity.dtype != np.bool_:
            raise SimulationBundleError(f"materialization_dtype_mismatch:{factor_id}")
        if payload.get("stock_axis_hash") != stock_axis_hash or payload.get("date_axis_hash") != date_axis_hash:
            raise SimulationBundleError(f"materialization_axis_mismatch:{factor_id}")
        record = records.get(factor_id)
        if record is None or payload.get("formula_hash") != record.formula_hash:
            raise SimulationBundleError(f"materialization_factor_identity_mismatch:{factor_id}")
        rows[factor_id] = {
            "manifest_path": path,
            "manifest_sha256": sha256_file(path),
            "values_path": values_path,
            "validity_path": validity_path,
        }
    if sorted(rows) != exact_ids:
        raise SimulationBundleError("materialization_exact20_identity_mismatch")
    return rows


def _validate_final_verifier(path: Path) -> dict[str, Any]:
    payload = _read_json(path)
    content_hash = payload.get("content_hash")
    semantic = {key: value for key, value in payload.items() if key != "content_hash"}
    if payload.get("schema_version") != "task054c_final_verification_v1" or canonical_hash(semantic) != content_hash:
        raise SimulationBundleError("task054c_final_verifier_invalid")
    return payload


def _resolve_governed_snapshots(index_path: Path) -> dict[str, dict[str, Any]]:
    payload = _read_json(index_path)
    raw_entries = payload.get("entries")
    entries: dict[str, Mapping[str, Any]] = {}
    if isinstance(raw_entries, Mapping):
        entries = {str(key): value for key, value in raw_entries.items() if isinstance(value, Mapping)}
    elif isinstance(raw_entries, list):
        entries = {str(item.get("kind")): item for item in raw_entries if isinstance(item, Mapping)}
    if not set(SOURCE_KINDS).issubset(entries):
        raise SimulationBundleError("governed_source_index_entries_invalid")
    resolved: dict[str, dict[str, Any]] = {}
    for kind in SOURCE_KINDS:
        entry = entries[kind]
        relative = Path(str(entry.get("path") or ""))
        if not relative.parts or relative.is_absolute() or ".." in relative.parts:
            raise SimulationBundleError(f"governed_source_path_invalid:{kind}")
        source = index_path.parent / relative
        if not source.is_file() or sha256_file(source) != entry.get("sha256"):
            raise SimulationBundleError(f"governed_source_sha_mismatch:{kind}")
        resolved[kind] = {
            "path": source,
            "sha256": entry["sha256"],
            "date_field": entry.get("date_field"),
            "format": entry.get("format") or source.suffix.lstrip("."),
            "empty_snapshot_attested": entry.get("empty_snapshot_attested") is True
            and str(entry.get("coverage_end") or "") >= EXECUTION_CUTOFF,
            "coverage_end": entry.get("coverage_end"),
        }
    return resolved


def _filter_snapshot_rows(source: Mapping[str, Any], *, cutoff: str) -> list[dict[str, Any]]:
    path = Path(source["path"])
    rows = _read_jsonl(path) if source.get("format") in {"jsonl", "ndjson"} else _json_rows(_read_json(path))
    selected = [row for row in rows if _record_date(row, source.get("date_field")) <= cutoff]
    return sorted(selected, key=lambda row: (_record_date(row, source.get("date_field")), canonical_hash(row)))


def _record_date(row: Mapping[str, Any], explicit_field: str | None) -> str:
    fields = (explicit_field,) if explicit_field else ("trade_date", "date", "effective_date", "ex_date", "ann_date")
    for field in fields:
        if field and row.get(field) not in (None, ""):
            value = "".join(character for character in str(row[field]) if character.isdigit())[:8]
            if len(value) == 8:
                return value
    raise SimulationBundleError("governed_snapshot_record_date_missing")


def _resolve_raw_validity(root: Path, field: str) -> Path:
    candidates = (root / f"{field}_validity.npy", root / f"{field}_valid_mask.npy")
    matches = [path for path in candidates if path.is_file()]
    if len(matches) != 1:
        raise SimulationBundleError(f"raw_validity_partition_unresolved:{field}")
    return matches[0]


def _validate_unit_contract(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != UNIT_CONTRACT_SCHEMA or payload.get("units") != EXPECTED_UNITS:
        raise SimulationBundleError("simulation_unit_contract_invalid")
    if payload.get("source_units") != EXPECTED_SOURCE_UNITS or payload.get("multipliers") != EXPECTED_MULTIPLIERS:
        raise SimulationBundleError("simulation_unit_conversion_contract_invalid")
    if payload.get("volume_semantics") != "raw_unadjusted_shares" or payload.get("amount_semantics") != "raw_turnover_CNY":
        raise SimulationBundleError("simulation_unit_contract_semantics_invalid")


def _corporate_action_validity(
    rows: Iterable[Mapping[str, Any]], *, ts_codes: list[str], trade_dates: list[str]
) -> np.ndarray:
    validity = np.ones((len(ts_codes), len(trade_dates)), dtype=np.bool_)
    stock_index = {code: index for index, code in enumerate(ts_codes)}
    date_index = {date: index for index, date in enumerate(trade_dates)}
    for row in rows:
        code = str(row.get("ts_code") or row.get("asset") or "")
        event_date = _record_date(row, "ex_date" if row.get("ex_date") else None)
        if code not in stock_index or event_date not in date_index:
            continue
        cash = row.get("cash_dividend_per_share", row.get("cash_div"))
        cash_value = 0.0 if cash in (None, "") else float(cash)
        ratios = (row.get("stk_bo_rate"), row.get("stk_co_rate"), row.get("stock_dividend_ratio"))
        numeric_ratios = [0.0 if value in (None, "") else float(value) for value in ratios]
        implemented = row.get("div_proc") in (None, "", "实施", "implemented")
        valid = (
            implemented
            and bool(row.get("record_date"))
            and (cash_value == 0.0 or bool(row.get("pay_date")))
            and np.isfinite(cash_value)
            and cash_value >= 0.0
            and all(np.isfinite(value) and value >= 0.0 for value in numeric_ratios)
        )
        if not valid:
            validity[stock_index[code], date_index[event_date]] = False
    return validity


def _load_unit_contract(value: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else _read_json(Path(value))


def _best_effort_source_identity(
    source_paths: Mapping[str, Path], normalized_root: Path, materialization_paths: tuple[Path, ...]
) -> dict[str, Any]:
    identity: dict[str, Any] = {
        "input_manifest_sha256": {name: sha256_file(path) for name, path in source_paths.items() if path.is_file()},
        "materialization_manifest_sha256": [sha256_file(path) for path in materialization_paths if path.is_file()],
    }
    manifest = normalized_root / "normalized_replay_store_manifest.json"
    if manifest.is_file():
        payload = _read_json(manifest)
        identity.update(
            {
                "normalized_store_manifest_sha256": sha256_file(manifest),
                "exact20_identity_root": payload.get("identity_root"),
            }
        )
        try:
            identity["exact20_ids"] = sorted(
                str(row["factor_id"]) for row in _read_jsonl(normalized_root / str(payload.get("records_file") or "factors.jsonl"))
            )
        except Exception:
            identity["exact20_ids"] = []
    return identity


def _prefix_indices(dates: list[str], cutoff: str, *, exact_end: bool) -> list[int]:
    indices = [index for index, value in enumerate(dates) if value <= cutoff]
    if not indices or indices != list(range(len(indices))):
        raise SimulationBundleError("simulation_date_axis_not_prefix_sorted")
    selected = dates[: len(indices)]
    if exact_end and selected[-1] != cutoff:
        raise SimulationBundleError(f"simulation_cutoff_date_missing:{cutoff}")
    return indices


def _file_entry(root: Path, path: Path, *, role: str, **extra: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "path": str(path.relative_to(root)),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
        "role": role,
    }
    if path.suffix == ".npy":
        array = np.load(path, mmap_mode="r", allow_pickle=False)
        entry.update({"shape": list(array.shape), "dtype": str(array.dtype)})
    entry.update(extra)
    return entry


def _safe_relative(root: Path, value: Any) -> Path:
    relative = Path(str(value or ""))
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise SimulationBundleError("simulation_bundle_artifact_path_invalid")
    return root / relative


def _hash_stock_axis(values: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(str(value) for value in values).encode("utf-8")).hexdigest()


def _hash_date_axis(values: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for value in values:
        digest.update(str(value).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _read_axis(path: Path) -> list[str]:
    value = _read_json(path)
    if not isinstance(value, list) or not all(isinstance(item, (str, int)) for item in value):
        raise SimulationBundleError(f"axis_invalid:{path.name}")
    result = [str(item) for item in value]
    if len(result) != len(set(result)):
        raise SimulationBundleError(f"axis_duplicate:{path.name}")
    return result


def _json_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list) and all(isinstance(row, Mapping) for row in value):
        return [dict(row) for row in value]
    if isinstance(value, Mapping):
        for key in ("records", "rows", "items", "entries"):
            rows = value.get(key)
            if isinstance(rows, list) and all(isinstance(row, Mapping) for row in rows):
                return [dict(row) for row in rows]
    raise SimulationBundleError("governed_snapshot_rows_invalid")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not all(isinstance(row, dict) for row in rows):
        raise SimulationBundleError("jsonl_rows_invalid")
    return rows


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    _write_json(temporary, payload)
    os.replace(temporary, path)


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(dict(row), sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _save_npy(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.save(path, np.asarray(value), allow_pickle=False)


def _copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)

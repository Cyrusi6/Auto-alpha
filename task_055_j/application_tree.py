from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from data_lake.task052_freeze import validate_task052_governed_freeze
from task_054_c.bundle import validate_bundle
from task_054_c.factor_store import validate_normalized_replay_store
from task_054_c.validators import validate_strict_matrix_generation, validate_v3_tensor_generation
from task_055_a.bundle import validate_simulation_bundle
from task_055_g.causal import validate_fee_aware_causal_frontier
from task_055_g.fees import validate_fee_schedule_v2
from task_055_g.truth import validate_truth_v2
from task_055_h.io import canonical_hash, publish_generation, read_json, sha256_file, validate_generation

from .contracts import APPLICATION_PREFLIGHT_SCHEMA, APPLICATION_TREE_SCHEMA, MAX_DATE


class Task055JApplicationTreeError(RuntimeError):
    pass


def publish_application_preflight(
    *,
    governed_root: str | Path,
    parent_runtime_authority: Mapping[str, Any],
    output_root: str | Path,
) -> dict[str, Any]:
    governed = Path(governed_root).resolve()
    resolved = _resolve_artifacts(governed, parent_runtime_authority)
    native = _native_validate(resolved, parent_runtime_authority)
    tree = publish_application_tree_seal(
        governed_root=governed,
        roles=resolved,
        output_root=Path(output_root) / "artifact_tree",
    )
    semantic = {
        "schema_version": APPLICATION_PREFLIGHT_SCHEMA,
        "status": "passed",
        "max_allowed_date": MAX_DATE,
        "parent_runtime_authority_content_hash": parent_runtime_authority["content_hash"],
        "application_artifact_tree_content_hash": tree["content_hash"],
        "application_artifact_tree_root": tree["tree_root"],
        "native_validation": native,
        "exact20_ids": list(parent_runtime_authority["application_artifacts"]["exact20_ids"]),
        "exact20_identity_root": parent_runtime_authority["application_artifacts"]["exact20_identity_root"],
        "research_cutoff": parent_runtime_authority["application_artifacts"]["research_cutoff"],
        "target_endpoint_horizon": parent_runtime_authority["application_artifacts"]["target_endpoint_horizon"],
        "production_context_parsed": True,
        "max_validated_source_date": native["max_validated_source_date"],
        "network_accessed": False,
        "credential_read_count": 0,
        "prospective_holdout_accessed": False,
    }
    result = publish_generation(
        output_root,
        prefix="task055j_application_preflight",
        manifest_name="application_preflight.json",
        semantic=semantic,
    )
    validate_application_preflight(result["manifest_path"], governed_root=governed)
    return result | {
        "application_tree": tree,
        "resolved": {role: str(path) for role, path in resolved.items()},
    }


def validate_application_preflight(path: str | Path, *, governed_root: str | Path) -> dict[str, Any]:
    payload = validate_generation(
        path,
        schema=APPLICATION_PREFLIGHT_SCHEMA,
        manifest_name="application_preflight.json",
    )
    if payload.get("status") != "passed" or payload.get("production_context_parsed") is not True:
        raise Task055JApplicationTreeError("task055j_application_preflight_status_invalid")
    if payload.get("network_accessed") is not False or int(payload.get("credential_read_count") or 0):
        raise Task055JApplicationTreeError("task055j_application_preflight_offline_boundary_invalid")
    if payload.get("prospective_holdout_accessed") is not False:
        raise Task055JApplicationTreeError("task055j_application_preflight_holdout_boundary_invalid")
    max_validated_source_date = str(payload.get("max_validated_source_date") or "")
    if (
        len(max_validated_source_date) != 8
        or not max_validated_source_date.isdigit()
        or max_validated_source_date > MAX_DATE
    ):
        raise Task055JApplicationTreeError("task055j_application_preflight_future_data_detected")
    tree_path = Path(payload["manifest_path"]).parents[2] / "artifact_tree" / "current.json"
    pointer = read_json(tree_path)
    manifest = tree_path.parent / str(pointer["manifest"])
    tree = validate_application_tree_seal(manifest, governed_root=governed_root)
    if tree["content_hash"] != payload.get("application_artifact_tree_content_hash"):
        raise Task055JApplicationTreeError("task055j_application_preflight_tree_lineage_invalid")
    return payload | {"application_tree": tree}


def publish_application_tree_seal(
    *, governed_root: str | Path, roles: Mapping[str, Path], output_root: str | Path
) -> dict[str, Any]:
    governed = Path(governed_root).resolve()
    catalog: list[dict[str, Any]] = []
    role_roots: dict[str, str] = {}
    for role, path in sorted(roles.items()):
        resolved = path.resolve()
        if governed != resolved and governed not in resolved.parents:
            raise Task055JApplicationTreeError(f"task055j_application_artifact_escape:{role}")
        entries = _catalog_path(governed, resolved, role)
        catalog.extend(entries)
        role_roots[role] = canonical_hash(entries)
    semantic = {
        "schema_version": APPLICATION_TREE_SCHEMA,
        "status": "sealed",
        "catalog": catalog,
        "catalog_count": len(catalog),
        "tree_root": canonical_hash(catalog),
        "role_roots": role_roots,
        "contains_absolute_paths": False,
    }
    return publish_generation(
        output_root,
        prefix="task055j_application_tree",
        manifest_name="application_tree_seal.json",
        semantic=semantic,
    )


def validate_application_tree_seal(path: str | Path, *, governed_root: str | Path) -> dict[str, Any]:
    governed = Path(governed_root).resolve()
    payload = validate_generation(
        path,
        schema=APPLICATION_TREE_SCHEMA,
        manifest_name="application_tree_seal.json",
    )
    catalog = list(payload.get("catalog") or ())
    if len(catalog) != payload.get("catalog_count") or canonical_hash(catalog) != payload.get("tree_root"):
        raise Task055JApplicationTreeError("task055j_application_tree_catalog_invalid")
    roles: dict[str, list[dict[str, Any]]] = {}
    for row in catalog:
        relative = Path(str(row.get("path") or ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise Task055JApplicationTreeError("task055j_application_tree_relative_path_invalid")
        artifact = (governed / relative).resolve()
        if governed not in artifact.parents or not artifact.is_file() or artifact.is_symlink():
            raise Task055JApplicationTreeError("task055j_application_tree_file_missing_or_escape")
        if artifact.stat().st_size != row.get("size_bytes") or sha256_file(artifact) != row.get("sha256"):
            raise Task055JApplicationTreeError(f"task055j_application_tree_file_drift:{relative}")
        roles.setdefault(str(row.get("role")), []).append(row)
    actual_role_roots = {role: canonical_hash(rows) for role, rows in sorted(roles.items())}
    if actual_role_roots != payload.get("role_roots"):
        raise Task055JApplicationTreeError("task055j_application_tree_role_roots_invalid")
    return payload | {"governed_root": str(governed)}


def _resolve_artifacts(governed: Path, authority: Mapping[str, Any]) -> dict[str, Path]:
    catalog = {str(row["role"]): row for row in authority["application_artifacts"]["catalog"]}
    required = {
        "simulation_bundle",
        "canonical_bundle_snapshot",
        "fee_schedule",
        "truth_v2",
        "causal_frontier",
        "freeze_manifest",
        "universe_manifest",
        "matrix_root",
        "tensor_root",
        "normalized_store_root",
        "promotion_policy",
        "feature_manifest",
    }
    if set(catalog) != required:
        raise Task055JApplicationTreeError("task055j_application_catalog_roles_invalid")
    result: dict[str, Path] = {}
    for role, row in catalog.items():
        relative = Path(str(row.get("relative_path") or ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise Task055JApplicationTreeError(f"task055j_application_catalog_path_invalid:{role}")
        path = (governed / relative).resolve()
        if governed not in path.parents:
            raise Task055JApplicationTreeError(f"task055j_application_catalog_escape:{role}")
        result[role] = path
    return result


def _native_validate(paths: Mapping[str, Path], authority: Mapping[str, Any]) -> dict[str, Any]:
    canonical = validate_bundle(paths["canonical_bundle_snapshot"])
    simulation = validate_simulation_bundle(paths["simulation_bundle"], require_ready=True)
    freeze = validate_task052_governed_freeze(paths["freeze_manifest"].parent)
    matrix = validate_strict_matrix_generation(paths["matrix_root"])
    tensor = validate_v3_tensor_generation(paths["tensor_root"], matrix=matrix)
    store = validate_normalized_replay_store(
        paths["normalized_store_root"], expected_ids=list(authority["application_artifacts"]["exact20_ids"])
    )
    fee = validate_fee_schedule_v2(paths["fee_schedule"])
    truth = validate_truth_v2(paths["truth_v2"])
    causal = validate_fee_aware_causal_frontier(paths["causal_frontier"])
    validated_dates = [
        str(simulation["execution_cutoff"]),
        str(simulation["valuation_cutoff"]),
        str(matrix["max_legal_endpoint_date"]),
        str(truth["max_date"]),
    ]
    if any(not value.isdigit() or len(value) != 8 for value in validated_dates):
        raise Task055JApplicationTreeError("task055j_application_native_date_contract_invalid")
    max_validated_source_date = max(validated_dates)
    if max_validated_source_date > MAX_DATE:
        raise Task055JApplicationTreeError("task055j_application_native_future_data_detected")
    if canonical["content_hash"] != authority["application_artifacts"]["canonical_bundle_content_hash"]:
        raise Task055JApplicationTreeError("task055j_canonical_bundle_hash_mismatch")
    if simulation.get("exact20_ids") != list(authority["application_artifacts"]["exact20_ids"]):
        raise Task055JApplicationTreeError("task055j_simulation_bundle_exact20_mismatch")
    if store.get("identity_root") != authority["application_artifacts"]["exact20_identity_root"]:
        raise Task055JApplicationTreeError("task055j_normalized_store_identity_mismatch")
    return {
        "canonical_bundle_content_hash": canonical["content_hash"],
        "simulation_bundle_content_hash": simulation["content_hash"],
        "freeze_content_hash": freeze["content_hash"],
        "matrix_content_hash": matrix["content_hash"],
        "tensor_content_hash": tensor["content_hash"],
        "normalized_store_content_hash": store["content_hash"],
        "fee_schedule_content_hash": fee["content_hash"],
        "truth_content_hash": truth["content_hash"],
        "truth_record_count": truth["record_count"],
        "causal_content_hash": causal["content_hash"],
        "causal_run_count": causal["run_count"],
        "max_validated_source_date": max_validated_source_date,
    }


def _catalog_path(governed: Path, path: Path, role: str) -> list[dict[str, Any]]:
    candidates = [path] if path.is_file() else sorted(candidate for candidate in path.rglob("*") if candidate.is_file())
    if not candidates:
        raise Task055JApplicationTreeError(f"task055j_application_role_empty:{role}")
    entries = []
    for candidate in candidates:
        if candidate.is_symlink():
            raise Task055JApplicationTreeError(f"task055j_application_symlink_forbidden:{role}")
        entries.append(
            {
                "role": role,
                "path": candidate.relative_to(governed).as_posix(),
                "sha256": sha256_file(candidate),
                "size_bytes": candidate.stat().st_size,
            }
        )
    return entries

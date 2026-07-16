"""Authoritative parent resolution from the sealed access catalog."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from task_055_e.run import FINAL_REPORT_SCHEMA as TASK055E_REPORT_SCHEMA

from .access import AccessBroker, AccessPlanError, canonical_hash
from .contracts import (
    TASK055A_BUNDLE_CONTENT_HASH,
    TASK055A_POLICY_SEAL_HASH,
    TASK055E_REPORT_CONTENT_HASH,
    TASK055F_REPORT_CONTENT_HASH,
)


class ParentLineageError(RuntimeError):
    pass


def resolve_and_validate_parent_lineage(
    *, governed_root: str | Path, access_plan: str | Path, broker: AccessBroker
) -> dict[str, Any]:
    entries = list(broker.plan.get("entries") or ())
    by_role: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        by_role.setdefault(str(entry.get("dataset_role") or ""), []).append(dict(entry))

    task055e_report_path = _one(by_role, "task055e_parent_report")
    task055f_report_path = _one(by_role, "task055f_parent_report")
    task055e_report = broker.read_json(task055e_report_path, principal="task055g_parent_validator")
    task055f_report = broker.read_json(task055f_report_path, principal="task055g_parent_validator")
    if task055e_report.get("schema_version") != TASK055E_REPORT_SCHEMA:
        raise ParentLineageError("task055e_report_schema_invalid")
    if task055e_report.get("content_hash") != TASK055E_REPORT_CONTENT_HASH:
        raise ParentLineageError("task055e_report_content_hash_mismatch")
    _verify_self_hash(task055e_report, "task055e_report")
    if task055f_report.get("content_hash") != TASK055F_REPORT_CONTENT_HASH:
        raise ParentLineageError("task055f_report_content_hash_mismatch")
    _verify_self_hash(task055f_report, "task055f_report")

    provenance_path = _one(by_role, "task055e_provenance_manifest")
    provenance = broker.read_json(provenance_path, principal="task055g_parent_validator")
    _verify_self_hash(provenance, "task055e_provenance")
    if provenance.get("content_hash") != task055e_report.get("lineage", {}).get("provenance_content_hash"):
        raise ParentLineageError("task055e_provenance_lineage_mismatch")
    provenance_root = Path(provenance_path).parent
    for name, partition in sorted((provenance.get("partitions") or {}).items()):
        relative = str(provenance_root / str(partition.get("path") or ""))
        data = broker.read_bytes(relative, principal="task055g_parent_validator")
        if broker.rows[-1].get("actual_sha256") != partition.get("sha256"):
            raise ParentLineageError(f"task055e_provenance_partition_mismatch:{name}")
        if int(partition.get("size_bytes") or len(data)) != len(data):
            raise ParentLineageError(f"task055e_provenance_partition_size_mismatch:{name}")

    matrix_manifest_path = _one(by_role, "strict_matrix_manifest")
    matrix_manifest = broker.read_json(matrix_manifest_path, principal="task055g_parent_validator")
    matrix_hash = str(matrix_manifest.get("content_hash") or "")
    if matrix_hash != task055e_report.get("lineage", {}).get("matrix_content_hash"):
        raise ParentLineageError("task055e_matrix_lineage_mismatch")
    _validate_matrix_manifest(matrix_manifest, matrix_manifest_path, broker)
    matrix_root = str(Path(matrix_manifest_path).parent)

    inventory_path = _one(by_role, "security_date_inventory")
    inventory = broker.read_json(inventory_path, principal="task055g_parent_validator")
    _verify_self_hash(inventory, "security_date_inventory")
    task055c_truth_path = _one(by_role, "task055c_truth_lineage")
    task055c_truth = broker.read_json(task055c_truth_path, principal="task055g_parent_validator")
    _verify_self_hash(task055c_truth, "task055c_truth")

    bundle_path = _one(by_role, "task055a_simulation_bundle_manifest")
    bundle = broker.read_json(bundle_path, principal="task055g_parent_validator")
    _verify_self_hash(bundle, "task055a_simulation_bundle")
    if bundle.get("content_hash") != TASK055A_BUNDLE_CONTENT_HASH:
        raise ParentLineageError("task055a_bundle_hash_mismatch")
    policy_path = _one(by_role, "task055a_policy_seal")
    policy = broker.read_json(policy_path, principal="task055g_parent_validator")
    _verify_self_hash(policy, "task055a_policy_seal")
    if policy.get("content_hash") != TASK055A_POLICY_SEAL_HASH:
        raise ParentLineageError("task055a_policy_hash_mismatch")

    suspend_entries = [row for role, rows in by_role.items() if role == "indexed_suspend_cache" for row in rows]
    if not suspend_entries:
        raise ParentLineageError("suspension_cache_catalog_empty")
    cache_parents = {str(Path(row["relative_path"]).parent) for row in suspend_entries}
    if len(cache_parents) != 1:
        raise ParentLineageError("suspension_cache_root_ambiguous")

    resolved = {
        "task055e_report": task055e_report_path,
        "task055f_report": task055f_report_path,
        "task055e_provenance_manifest": provenance_path,
        "inventory_manifest": inventory_path,
        "inventory_cells": _one(by_role, "security_date_inventory_cells"),
        "matrix_root": matrix_root,
        "matrix_manifest": matrix_manifest_path,
        "suspension_coverage_ledger": _one(by_role, "suspension_coverage_ledger"),
        "suspension_cache_root": next(iter(cache_parents)),
        "task055c_truth_manifest": task055c_truth_path,
        "observation_seal": _one(by_role, "observation_boundary_seal"),
        "simulation_bundle": bundle_path,
        "policy_seal": policy_path,
    }
    semantic = {
        "task055e_report_content_hash": task055e_report["content_hash"],
        "task055f_report_content_hash": task055f_report["content_hash"],
        "provenance_content_hash": provenance["content_hash"],
        "matrix_content_hash": matrix_hash,
        "inventory_content_hash": inventory["content_hash"],
        "task055c_truth_content_hash": task055c_truth["content_hash"],
        "simulation_bundle_content_hash": bundle["content_hash"],
        "policy_seal_content_hash": policy["content_hash"],
        "resolved_relative_paths": resolved,
        "access_plan_content_hash": broker.plan["content_hash"],
    }
    return resolved | {
        "content_hash": canonical_hash(semantic),
        "semantic": semantic,
        "manifests": {
            "task055e_report": task055e_report,
            "task055f_report": task055f_report,
            "provenance": provenance,
            "matrix": matrix_manifest,
            "inventory": inventory,
            "task055c_truth": task055c_truth,
            "simulation_bundle": bundle,
            "policy_seal": policy,
        },
    }


def _one(by_role: Mapping[str, list[dict[str, Any]]], role: str) -> str:
    rows = list(by_role.get(role) or ())
    if len(rows) != 1:
        raise ParentLineageError(f"parent_role_cardinality_invalid:{role}:{len(rows)}")
    return str(rows[0]["relative_path"])


def _verify_self_hash(payload: Mapping[str, Any], label: str) -> None:
    if not payload.get("content_hash"):
        raise ParentLineageError(f"{label}_content_hash_missing")
    semantic = {key: value for key, value in payload.items() if key not in {"content_hash", "generation_id"}}
    if canonical_hash(semantic) != payload.get("content_hash"):
        raise ParentLineageError(f"{label}_content_hash_invalid")


def _validate_matrix_manifest(
    manifest: Mapping[str, Any], manifest_path: str, broker: AccessBroker
) -> None:
    if manifest.get("shape") != [637, 6417]:
        raise ParentLineageError("strict_matrix_shape_mismatch")
    if manifest.get("research_holdout_firewall_enabled") is True or manifest.get("research_firewall_ready") is True:
        raise ParentLineageError("strict_matrix_self_attested_firewall")
    if not manifest.get("physical_research_projection") and manifest.get("raw_truncated_before_compute") is True:
        raise ParentLineageError("strict_matrix_false_raw_truncation_attestation")
    required = {
        "signal_candidate_cells.npy",
        "validation_common_cells.npy",
        "target_available.npy",
        "research_eligible_date_mask.npy",
        "trade_dates.json",
        "ts_codes.json",
    }
    partitions = manifest.get("partition_sha256") or {}
    if not required.issubset(partitions):
        raise ParentLineageError("strict_matrix_required_partition_missing")
    root = Path(manifest_path).parent
    for name, digest in sorted(partitions.items()):
        broker.read_bytes(str(root / name), principal="task055g_parent_validator")
        if broker.rows[-1].get("actual_sha256") != digest:
            raise ParentLineageError(f"strict_matrix_partition_mismatch:{name}")

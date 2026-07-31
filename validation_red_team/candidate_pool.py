"""Freeze an Alpha Factory shortlist before any sealed-holdout access."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import numpy as np

from factor_store import make_factor_id, stable_formula_hash

from .io import (
    HoldoutContractError,
    checked_regular_file,
    publish_generation,
    read_json,
    read_jsonl,
    resolve_report_path,
    sha256_file,
    stable_hash,
)


_CORE_FIELDS = (
    "status",
    "campaign_id",
    "candidate_count",
    "formula_hashes",
    "factor_value_hashes",
    "research_metrics",
    "selection_order",
    "trial_count",
    "selection_policy_hash",
    "selection_policy_id",
    "candidate_identity_root",
    "candidates",
    "source_hashes",
    "source_catalog",
    "holdout_accessed",
    "candidate_mutation_forbidden",
    "holdout_feedback_to_search_forbidden",
)


def freeze_candidate_pool(
    campaign_report_path: str | Path,
    materialization_manifest_paths: Iterable[str | Path],
    output_root: str | Path,
) -> tuple[Path, dict[str, Any]]:
    report_path = checked_regular_file(campaign_report_path)
    report = read_json(report_path, artifact_type="alpha_factory_report")
    if report.get("status") != "success":
        raise HoldoutContractError("alpha_campaign_not_successful")
    paths = report.get("paths") if isinstance(report.get("paths"), dict) else {}
    required = {
        "campaign_manifest": ("alpha_campaign_manifest_path", "alpha_campaign_manifest", False),
        "research_policy": ("alpha_research_policy_path", "alpha_research_policy", False),
        "full_results": ("alpha_full_eval_results_path", "alpha_full_eval_results", True),
        "full_summary": ("alpha_full_eval_summary_path", "alpha_full_eval_summary", False),
        "shortlist": ("alpha_shortlist_path", "alpha_shortlist", True),
        "trial_ledger": ("alpha_trial_ledger_path", "alpha_trial_ledger", True),
    }
    resolved: dict[str, Path] = {}
    payloads: dict[str, Any] = {}
    for role, (key, artifact_type, jsonl) in required.items():
        raw_path = paths.get(key)
        if not raw_path:
            raise HoldoutContractError(f"campaign_artifact_missing:{key}")
        resolved[role] = resolve_report_path(report_path, str(raw_path))
        payloads[role] = read_jsonl(resolved[role], artifact_type=artifact_type) if jsonl else read_json(resolved[role], artifact_type=artifact_type)
    shortlist = payloads["shortlist"]
    full_rows = payloads["full_results"]
    trial_rows = payloads["trial_ledger"]
    if not shortlist:
        raise HoldoutContractError("candidate_shortlist_empty")
    if len({str(row.get("formula_hash") or "") for row in shortlist}) != len(shortlist):
        raise HoldoutContractError("shortlist_formula_hash_not_unique")
    full_hashes = [str(row.get("formula_hash") or (row.get("request") or {}).get("formula_hash") or "") for row in full_rows]
    if any(not value for value in full_hashes) or len(full_hashes) != len(set(full_hashes)):
        raise HoldoutContractError("full_eval_formula_hash_duplicate_or_missing")
    full_by_hash = dict(zip(full_hashes, full_rows, strict=True))
    trial_formula_hashes = [str(row.get("formula_hash") or "") for row in trial_rows]
    trial_count = len(trial_rows)
    if trial_count <= 0 or int((report.get("summary") or {}).get("total_trials") or 0) != trial_count:
        raise HoldoutContractError("trial_count_lineage_mismatch")
    policy = payloads["research_policy"]
    policy_hash = str(policy.get("policy_hash") or "")
    if len(policy_hash) != 64 or payloads["full_summary"].get("research_policy_hash") != policy_hash:
        raise HoldoutContractError("selection_policy_hash_mismatch")
    materializations = _materialization_map(materialization_manifest_paths)
    formula_hashes: list[str] = []
    factor_value_hashes: dict[str, Any] = {}
    research_metrics: dict[str, Any] = {}
    selection_order: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    source_catalog = [
        {"role": "alpha_factory_report", "path": str(report_path), "sha256": sha256_file(report_path)},
    ]
    for role, path in sorted(resolved.items()):
        source_catalog.append({"role": role, "path": str(path), "sha256": sha256_file(path)})
        if required[role][2]:
            sidecar = checked_regular_file(f"{path}.schema.json")
            source_catalog.append(
                {"role": f"{role}_schema", "path": str(sidecar), "sha256": sha256_file(sidecar)}
            )
    for rank, shortlist_row in enumerate(shortlist, start=1):
        formula_hash = str(shortlist_row.get("formula_hash") or "")
        full = full_by_hash.get(formula_hash)
        if not full or full.get("status") != "validation_candidate":
            raise HoldoutContractError(f"shortlist_without_positive_oos_gate:{formula_hash}")
        gate = full.get("gate_decision") if isinstance(full.get("gate_decision"), dict) else {}
        gate_checks = gate.get("checks") if isinstance(gate.get("checks"), dict) else {}
        if gate.get("passed") is not True or gate_checks.get("oos_evidence_positive") is not True:
            raise HoldoutContractError(f"shortlist_without_positive_oos_evidence:{formula_hash}")
        if trial_formula_hashes.count(formula_hash) != 1:
            raise HoldoutContractError(f"shortlist_trial_lineage_mismatch:{formula_hash}")
        materialization = materializations.get(formula_hash)
        if materialization is None:
            raise HoldoutContractError(f"materialization_missing:{formula_hash}")
        identity, value_hashes, materialization_sources = _validate_materialization(materialization, shortlist_row)
        formula_hashes.append(formula_hash)
        factor_value_hashes[formula_hash] = value_hashes
        research_metrics[formula_hash] = {
            "score": full.get("score"),
            "validation_summary": full.get("validation_summary") or {},
            "gate_decision": full.get("gate_decision") or {},
            "placebo": full.get("placebo") or {},
            "regime": full.get("regime") or {},
            "time_sensitivity": full.get("time_sensitivity") or {},
            "parameter_sensitivity": full.get("parameter_sensitivity") or {},
            "cost_capacity_stress": full.get("cost_capacity_stress") or {},
            "style_exposures": full.get("style_exposures") or {},
            "raw_p_value": full.get("raw_p_value"),
            "bh_q_value": full.get("bh_q_value"),
            "selection_adjusted_p_value": full.get("selection_adjusted_p_value"),
            "research_lineage_hash": full.get("lineage_hash"),
        }
        selection_order.append(
            {
                "selection_rank": rank,
                "alpha_candidate_id": shortlist_row.get("alpha_candidate_id"),
                "factor_id": identity["factor_id"],
                "formula_hash": formula_hash,
                "research_final_score": shortlist_row.get("final_score"),
            }
        )
        candidates.append(
            {
                "selection_rank": rank,
                "alpha_candidate_id": shortlist_row.get("alpha_candidate_id"),
                **identity,
                "transform_method": materialization.get("transform_method") or "raw",
                "family_tags": list(shortlist_row.get("family_tags") or []),
                "research_materialization_manifest_sha256": sha256_file(materialization["_path"]),
            }
        )
        source_catalog.extend(materialization_sources)
    if set(materializations) != set(formula_hashes):
        raise HoldoutContractError("materialization_candidate_set_mismatch")
    materialization_axes = {
        (
            tuple(row.get("shape") or []),
            row.get("stock_axis_hash"),
            row.get("date_axis_hash"),
        )
        for row in factor_value_hashes.values()
    }
    if len(materialization_axes) != 1:
        raise HoldoutContractError("candidate_materialization_axes_not_common")
    candidate_identity_root = stable_hash(candidates)
    source_hashes = {row["role"]: row["sha256"] for row in source_catalog}
    core = {
        "status": "frozen_before_holdout",
        "campaign_id": report.get("campaign_id"),
        "candidate_count": len(candidates),
        "formula_hashes": formula_hashes,
        "factor_value_hashes": factor_value_hashes,
        "research_metrics": research_metrics,
        "selection_order": selection_order,
        "trial_count": trial_count,
        "selection_policy_hash": policy_hash,
        "selection_policy_id": policy.get("policy_id"),
        "candidate_identity_root": candidate_identity_root,
        "candidates": candidates,
        "source_hashes": source_hashes,
        "source_catalog": source_catalog,
        "holdout_accessed": False,
        "candidate_mutation_forbidden": True,
        "holdout_feedback_to_search_forbidden": True,
    }
    return publish_generation(
        output_root,
        generation_prefix="candidate_pool",
        manifest_name="candidate_pool_manifest.json",
        artifact_type="candidate_pool_manifest",
        producer="validation_red_team",
        core=core,
    )


def validate_candidate_pool_manifest(path: str | Path, *, revalidate_sources: bool = True) -> dict[str, Any]:
    payload = read_json(path, artifact_type="candidate_pool_manifest")
    core = {field: payload.get(field) for field in _CORE_FIELDS}
    if payload.get("content_hash") != stable_hash(core):
        raise HoldoutContractError("candidate_pool_content_hash_mismatch")
    candidates = payload.get("candidates") or []
    formula_hashes = payload.get("formula_hashes") or []
    selection = payload.get("selection_order") or []
    if payload.get("status") != "frozen_before_holdout" or payload.get("holdout_accessed") is not False:
        raise HoldoutContractError("candidate_pool_not_frozen_before_holdout")
    if not candidates or len(candidates) != int(payload.get("candidate_count") or 0):
        raise HoldoutContractError("candidate_pool_count_mismatch")
    if formula_hashes != [row.get("formula_hash") for row in candidates]:
        raise HoldoutContractError("candidate_pool_formula_order_mismatch")
    if [row.get("formula_hash") for row in selection] != formula_hashes:
        raise HoldoutContractError("candidate_pool_selection_order_mismatch")
    if [row.get("selection_rank") for row in selection] != list(range(1, len(selection) + 1)):
        raise HoldoutContractError("candidate_pool_rank_not_contiguous")
    for candidate in candidates:
        canonical_hash = stable_formula_hash(
            [int(value) for value in candidate.get("formula_tokens") or []],
            [str(value) for value in candidate.get("formula_names") or []],
            str(candidate.get("feature_version") or ""),
            str(candidate.get("operator_version") or ""),
        )
        if canonical_hash != candidate.get("formula_hash") or make_factor_id(canonical_hash) != candidate.get("factor_id"):
            raise HoldoutContractError("candidate_pool_formula_identity_mismatch")
    if payload.get("candidate_identity_root") != stable_hash(candidates):
        raise HoldoutContractError("candidate_identity_root_mismatch")
    if set((payload.get("factor_value_hashes") or {})) != set(formula_hashes):
        raise HoldoutContractError("factor_value_hash_set_mismatch")
    if int(payload.get("trial_count") or 0) < len(candidates):
        raise HoldoutContractError("candidate_pool_trial_count_invalid")
    source_catalog = payload.get("source_catalog") or []
    source_roles = [str(row.get("role") or "") for row in source_catalog]
    if not source_catalog or len(source_roles) != len(set(source_roles)) or any(not role for role in source_roles):
        raise HoldoutContractError("candidate_pool_source_catalog_invalid")
    if payload.get("source_hashes") != {row["role"]: row.get("sha256") for row in source_catalog}:
        raise HoldoutContractError("candidate_pool_source_hash_catalog_mismatch")
    if revalidate_sources:
        for source in source_catalog:
            source_path = checked_regular_file(str(source.get("path") or ""))
            if sha256_file(source_path) != source.get("sha256"):
                raise HoldoutContractError(f"candidate_pool_source_drift:{source.get('role')}")
    return payload


def _materialization_map(paths: Iterable[str | Path]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw_path in paths:
        path = checked_regular_file(raw_path)
        payload = read_json(path, artifact_type="factor_materialization_manifest")
        formula_hash = str(payload.get("formula_hash") or "")
        if not formula_hash or formula_hash in result:
            raise HoldoutContractError("materialization_formula_hash_duplicate_or_missing")
        result[formula_hash] = {**payload, "_path": path}
    return result


def _validate_materialization(payload: dict[str, Any], shortlist: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    if payload.get("materialization_status") != "success":
        raise HoldoutContractError("candidate_materialization_not_successful")
    path = Path(payload["_path"])
    values_path = checked_regular_file(path.parent / "values.npy")
    validity_path = checked_regular_file(path.parent / "validity.npy")
    if sha256_file(values_path) != payload.get("value_sha256") or sha256_file(validity_path) != payload.get("validity_sha256"):
        raise HoldoutContractError("candidate_materialization_hash_mismatch")
    values = np.load(values_path, mmap_mode="r")
    validity = np.load(validity_path, mmap_mode="r")
    if list(values.shape) != payload.get("shape") or validity.shape != values.shape:
        raise HoldoutContractError("candidate_materialization_shape_mismatch")
    if str(values.dtype) != "float32" or str(validity.dtype) != "bool":
        raise HoldoutContractError("candidate_materialization_dtype_mismatch")
    formula_names = [str(value) for value in (payload.get("formula") or shortlist.get("formula_names") or [])]
    formula_tokens = [int(value) for value in (payload.get("formula_tokens") or [])]
    feature_version = str(payload.get("feature_version") or shortlist.get("feature_version") or "")
    operator_version = str(payload.get("operator_version") or shortlist.get("operator_version") or "")
    formula_hash = stable_formula_hash(formula_tokens, formula_names, feature_version, operator_version)
    if formula_hash != payload.get("formula_hash") or formula_hash != shortlist.get("formula_hash"):
        raise HoldoutContractError("candidate_materialization_formula_mismatch")
    factor_id = make_factor_id(formula_hash)
    if factor_id != payload.get("factor_id"):
        raise HoldoutContractError("candidate_materialization_factor_id_mismatch")
    factor_identity = payload.get("factor_identity") or {}
    identity = {
        "factor_id": factor_id,
        "formula_hash": formula_hash,
        "formula_tokens": formula_tokens,
        "formula_names": formula_names,
        "feature_version": feature_version,
        "operator_version": operator_version,
        "complexity": int(factor_identity.get("complexity") or shortlist.get("complexity") or 0),
        "effective_lookback": int(factor_identity.get("effective_lookback") or shortlist.get("lookback") or 0),
        "required_observations": int(factor_identity.get("required_observations") or int(shortlist.get("lookback") or 0) + 1),
    }
    hashes = {
        "value_sha256": payload["value_sha256"],
        "validity_sha256": payload["validity_sha256"],
        "shape": list(values.shape),
        "stock_axis_hash": payload.get("stock_axis_hash"),
        "date_axis_hash": payload.get("date_axis_hash"),
        "materialization_manifest_sha256": sha256_file(path),
    }
    sources = [
        {"role": f"materialization_manifest:{formula_hash}", "path": str(path), "sha256": sha256_file(path)},
        {"role": f"research_factor_values:{formula_hash}", "path": str(values_path), "sha256": sha256_file(values_path)},
        {"role": f"research_factor_validity:{formula_hash}", "path": str(validity_path), "sha256": sha256_file(validity_path)},
    ]
    return identity, hashes, sources

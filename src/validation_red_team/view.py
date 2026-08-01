"""Validation of physically sealed, future untouched holdout views."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from .io import HoldoutContractError, checked_regular_file, read_json, sha256_file, stable_hash


REQUIRED_VIEW_ARTIFACTS = {
    "trade_dates",
    "ts_codes",
    "feature_manifest",
    "feature_tensor",
    "feature_validity",
    "target_return",
    "target_available",
    "signal_candidate_cells",
    "membership",
    "active",
    "evaluation_date_mask",
    "amount",
    "regime_date_masks",
    "regime_names",
    "universe_masks",
    "universe_names",
}

VIEW_CORE_FIELDS = (
    "status",
    "view_id",
    "evidence_level",
    "untouched",
    "historically_observed",
    "selection_data_reused",
    "candidate_pool_root",
    "observation_boundary_seal_hash",
    "freeze_content_hash",
    "holdout_start_date",
    "holdout_end_date",
    "max_target_endpoint_date",
    "label_horizon",
    "profile",
    "windows",
    "artifact_catalog",
    "stock_axis_hash",
    "date_axis_hash",
    "feature_axis_hash",
    "search_principal_access_count",
    "feedback_to_search_forbidden",
    "pit_validation_status",
    "leakage_blocker_count",
    "certified_factor_count",
)


def validate_sealed_holdout_view(path: str | Path, *, open_payloads: bool = False) -> dict[str, Any]:
    manifest_path = checked_regular_file(path)
    payload = read_json(manifest_path, artifact_type="sealed_holdout_view")
    core = {field: payload.get(field) for field in VIEW_CORE_FIELDS}
    if payload.get("content_hash") != stable_hash(core):
        raise HoldoutContractError("sealed_holdout_view_content_hash_mismatch")
    if payload.get("status") != "sealed":
        raise HoldoutContractError("holdout_view_not_sealed")
    if payload.get("evidence_level") != "future_untouched_holdout":
        raise HoldoutContractError("holdout_view_not_future_untouched")
    if payload.get("untouched") is not True or payload.get("historically_observed") is not False:
        raise HoldoutContractError("holdout_view_contaminated")
    if payload.get("selection_data_reused") is not False or int(payload.get("search_principal_access_count") or 0) != 0:
        raise HoldoutContractError("holdout_view_search_access_detected")
    if payload.get("feedback_to_search_forbidden") is not True:
        raise HoldoutContractError("holdout_feedback_boundary_missing")
    if payload.get("pit_validation_status") != "passed" or int(payload.get("leakage_blocker_count") or 0) != 0:
        raise HoldoutContractError("holdout_pit_or_leakage_blocked")
    for name in ("candidate_pool_root", "observation_boundary_seal_hash", "freeze_content_hash", "stock_axis_hash", "date_axis_hash", "feature_axis_hash"):
        if len(str(payload.get(name) or "")) != 64:
            raise HoldoutContractError(f"holdout_view_hash_missing:{name}")
    if str(payload.get("max_target_endpoint_date") or "") > str(payload.get("holdout_end_date") or ""):
        raise HoldoutContractError("holdout_endpoint_exceeds_view")
    profile = payload.get("profile") or {}
    required_profile = {"universe_name", "holding_period_days", "neutralization_method", "rebalance_frequency"}
    if not required_profile.issubset(profile):
        raise HoldoutContractError("holdout_view_profile_incomplete")
    windows = payload.get("windows") or []
    if not windows or any(not row.get("start_date") or not row.get("end_date") for row in windows):
        raise HoldoutContractError("holdout_windows_missing")
    catalog = payload.get("artifact_catalog") or []
    by_role = {str(row.get("role") or ""): row for row in catalog}
    if len(by_role) != len(catalog) or not REQUIRED_VIEW_ARTIFACTS.issubset(by_role):
        raise HoldoutContractError("holdout_view_artifact_catalog_incomplete")
    for role, entry in by_role.items():
        relative = Path(str(entry.get("relative_path") or ""))
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise HoldoutContractError(f"holdout_view_artifact_locator_invalid:{role}")
        if len(str(entry.get("sha256") or "")) != 64:
            raise HoldoutContractError(f"holdout_view_artifact_hash_missing:{role}")
    if open_payloads:
        _validate_payloads(manifest_path, payload, by_role)
    return payload


def resolve_view_artifact(manifest_path: str | Path, payload: dict[str, Any], role: str) -> Path:
    manifest = checked_regular_file(manifest_path)
    entry = next((row for row in payload.get("artifact_catalog") or [] if row.get("role") == role), None)
    if not isinstance(entry, dict):
        raise HoldoutContractError(f"holdout_view_artifact_missing:{role}")
    relative = Path(str(entry.get("relative_path") or ""))
    target = checked_regular_file(manifest.parent / relative)
    if not target.is_relative_to(manifest.parent):
        raise HoldoutContractError(f"holdout_view_artifact_escape:{role}")
    if sha256_file(target) != entry.get("sha256"):
        raise HoldoutContractError(f"holdout_view_artifact_hash_mismatch:{role}")
    return target


def _validate_payloads(manifest_path: Path, payload: dict[str, Any], catalog: dict[str, dict[str, Any]]) -> None:
    paths = {role: resolve_view_artifact(manifest_path, payload, role) for role in catalog}
    trade_dates = _json_list(paths["trade_dates"])
    ts_codes = _json_list(paths["ts_codes"])
    if trade_dates != sorted(set(trade_dates)):
        raise HoldoutContractError("holdout_trade_dates_not_unique_sorted")
    if len(ts_codes) != len(set(ts_codes)):
        raise HoldoutContractError("holdout_stock_axis_not_unique")
    feature_manifest = json.loads(paths["feature_manifest"].read_text(encoding="utf-8"))
    feature_names = [
        str(row.get("feature_name"))
        for row in feature_manifest.get("feature_definitions") or []
        if isinstance(row, dict) and row.get("feature_name")
    ]
    if stable_hash(ts_codes) != payload.get("stock_axis_hash"):
        raise HoldoutContractError("holdout_stock_axis_hash_mismatch")
    if stable_hash(trade_dates) != payload.get("date_axis_hash"):
        raise HoldoutContractError("holdout_date_axis_hash_mismatch")
    if stable_hash(feature_names) != payload.get("feature_axis_hash"):
        raise HoldoutContractError("holdout_feature_axis_hash_mismatch")
    stock_count, date_count, feature_count = len(ts_codes), len(trade_dates), len(feature_names)
    expected = {
        "feature_tensor": ((stock_count, feature_count, date_count), "float32"),
        "feature_validity": ((stock_count, feature_count, date_count), "bool"),
        "target_return": ((stock_count, date_count), "float32"),
        "target_available": ((stock_count, date_count), "bool"),
        "signal_candidate_cells": ((stock_count, date_count), "bool"),
        "membership": ((stock_count, date_count), "bool"),
        "active": ((stock_count, date_count), "bool"),
        "evaluation_date_mask": ((date_count,), "bool"),
        "amount": ((stock_count, date_count), "float32"),
    }
    arrays: dict[str, np.ndarray] = {}
    for role, (shape, dtype) in expected.items():
        value = np.load(paths[role], mmap_mode="r")
        arrays[role] = value
        if tuple(value.shape) != shape or str(value.dtype) != dtype:
            raise HoldoutContractError(f"holdout_view_array_contract_mismatch:{role}")
        entry = catalog[role]
        if entry.get("shape") != list(shape) or entry.get("dtype") != dtype:
            raise HoldoutContractError(f"holdout_view_catalog_array_mismatch:{role}")
    optional_arrays = {
        "log_mkt_cap": ((stock_count, date_count), {"float32", "float64"}),
        "log_mkt_cap_validity": ((stock_count, date_count), {"bool"}),
        "industry_codes": ((stock_count, date_count), {"int32", "int64"}),
        "industry_codes_validity": ((stock_count, date_count), {"bool"}),
    }
    for role, (shape, dtypes) in optional_arrays.items():
        if role not in paths:
            continue
        value = np.load(paths[role], mmap_mode="r")
        entry = catalog[role]
        if tuple(value.shape) != shape or str(value.dtype) not in dtypes:
            raise HoldoutContractError(f"holdout_view_optional_array_contract_mismatch:{role}")
        if entry.get("shape") != list(shape) or entry.get("dtype") != str(value.dtype):
            raise HoldoutContractError(f"holdout_view_catalog_array_mismatch:{role}")
    regime_names = _json_list(paths["regime_names"])
    universe_names = _json_list(paths["universe_names"])
    regime_masks = np.load(paths["regime_date_masks"], mmap_mode="r")
    universe_masks = np.load(paths["universe_masks"], mmap_mode="r")
    if len(regime_names) < 2 or regime_masks.shape != (len(regime_names), date_count) or str(regime_masks.dtype) != "bool":
        raise HoldoutContractError("holdout_regime_contract_invalid")
    if not universe_names or universe_masks.shape != (len(universe_names), stock_count, date_count) or str(universe_masks.dtype) != "bool":
        raise HoldoutContractError("holdout_universe_contract_invalid")
    certified_count = int(payload.get("certified_factor_count") or 0)
    if certified_count:
        for role in ("certified_factor_values", "certified_factor_validity"):
            if role not in paths:
                raise HoldoutContractError(f"holdout_view_artifact_missing:{role}")
        certified_values = np.load(paths["certified_factor_values"], mmap_mode="r")
        certified_validity = np.load(paths["certified_factor_validity"], mmap_mode="r")
        expected_certified = (certified_count, stock_count, date_count)
        if certified_values.shape != expected_certified or str(certified_values.dtype) != "float32":
            raise HoldoutContractError("holdout_certified_factor_values_invalid")
        if certified_validity.shape != expected_certified or str(certified_validity.dtype) != "bool":
            raise HoldoutContractError("holdout_certified_factor_validity_invalid")
    horizon = int(payload.get("label_horizon") or 0)
    evaluation = np.asarray(arrays["evaluation_date_mask"], dtype=np.bool_)
    if horizon < 1 or np.any(evaluation[-horizon:]):
        raise HoldoutContractError("holdout_target_endpoint_tail_matured_incorrectly")
    evaluated_indices = np.flatnonzero(evaluation)
    if evaluated_indices.size == 0 or int(evaluated_indices[-1]) + horizon >= date_count:
        raise HoldoutContractError("holdout_target_endpoint_not_on_trade_axis")
    endpoint_date = trade_dates[int(evaluated_indices[-1]) + horizon]
    if endpoint_date != str(payload.get("max_target_endpoint_date") or ""):
        raise HoldoutContractError("holdout_max_target_endpoint_mismatch")
    target_available = np.asarray(arrays["target_available"], dtype=np.bool_)
    if np.any(target_available[:, ~evaluation]):
        raise HoldoutContractError("holdout_target_available_outside_evaluation_axis")
    target_return = np.asarray(arrays["target_return"])
    if np.any(~np.isfinite(target_return[target_available])):
        raise HoldoutContractError("holdout_available_target_not_finite")
    if np.any(np.isfinite(target_return[~target_available])):
        raise HoldoutContractError("holdout_unavailable_target_not_nan")
    evaluated_dates = [trade_dates[index] for index in evaluated_indices]
    if not evaluated_dates:
        raise HoldoutContractError("holdout_evaluation_axis_empty")
    if evaluated_dates[0] < payload.get("holdout_start_date") or evaluated_dates[-1] > payload.get("holdout_end_date"):
        raise HoldoutContractError("holdout_evaluation_dates_outside_contract")


def _json_list(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise HoldoutContractError(f"nonempty_json_list_required:{path.name}")
    return [str(value) for value in payload]

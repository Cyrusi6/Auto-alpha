"""Shared support for the canonical native response-application stages."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from auto_alpha.portfolio.simulation.ledger_bundle import EXECUTION_MASKS
from auto_alpha.portfolio.simulation.ledger_bundle import SIGNAL_MASKS
from auto_alpha.portfolio.simulation.ledger_bundle import load_simulation_bundle
from auto_alpha.portfolio.simulation.ledger_bundle import validate_simulation_bundle
from auto_alpha.research.factors.store_storage import LocalFactorStore
from auto_alpha.validation.firewall.engineering_closure_factor_store import validate_normalized_replay_store
from auto_alpha.validation.firewall.production_sentinel_sentinel import ProductionSentinelConfig
from auto_alpha.validation.firewall.production_sentinel_sentinel import run_task054b_production_sentinel
from auto_alpha.validation.firewall.production_sentinel_sentinel import validate_task054b_production_sentinel

from auto_alpha.platform.artifacts.storage import canonical_hash, publish_generation, read_json, sha256_file
from .transport import evidence_use_identity, transport_identity


class ResponseApplicationError(RuntimeError):
    pass


def _run_production_sentinel(
    *,
    context: Mapping[str, Any],
    freeze_root: Path,
    matrix_root: Path,
    tensor_root: Path,
    stage_root: Path,
    evidence_scope: str,
) -> dict[str, Any]:
    probe = stage_root / "probe_factor.json"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text(
        json.dumps(asdict(context["factors"][0]), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    payload = run_task054b_production_sentinel(
        ProductionSentinelConfig(
            governed_freeze_dir=str(freeze_root),
            universe_dir=str(context["universe_root"]),
            published_matrix_dir=str(matrix_root),
            published_tensor_dir=str(tensor_root),
            feature_manifest_path=str(context["feature_manifest"]),
            probe_factor_path=str(probe),
            promotion_policy_path=str(context["promotion_policy"]),
            output_root=str(stage_root),
            research_end_date=str(context["research_cutoff"]),
            holdout_start_date=str(context["holdout_start_date"]),
            label_horizon=2,
            timeout_seconds=int(context.get("sentinel_timeout_seconds", 1800)),
            evidence_scope=evidence_scope,
        )
    )
    validate_task054b_production_sentinel(
        payload["artifact_path"],
        scheduler_state_dir=stage_root / "scheduler_state",
        expected_evidence_scope=evidence_scope,
    )
    if payload.get("status") != "passed" or payload.get("exact_run_count") != 12:
        raise ResponseApplicationError("task055j_production_sentinel_not_passed")
    return payload | {
        "input_evidence_scope": evidence_scope,
        "production_seal_eligible": evidence_scope == "real_production",
    }


def _successor_bundle(
    context: Mapping[str, Any],
    matrix_root: Path,
    materializations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    parent = load_simulation_bundle(context["simulation_bundle"])
    assets = list(map(str, parent["ts_codes"]))
    execution_dates = list(map(str, parent["execution_dates"]))
    signal_dates = list(map(str, parent["trade_dates"]))
    matrix_assets = _read_list(matrix_root / "ts_codes.json")
    matrix_dates = _read_list(matrix_root / "trade_dates.json")
    asset_positions = [matrix_assets.index(asset) for asset in assets]
    execution_positions = [matrix_dates.index(date) for date in execution_dates]
    signal_positions = [matrix_dates.index(date) for date in signal_dates]
    raw = {
        "open": _slice_matrix(matrix_root / "open.npy", asset_positions, execution_positions),
        "close": _slice_matrix(matrix_root / "close.npy", asset_positions, execution_positions),
        "vol": _slice_matrix(matrix_root / "volume.npy", asset_positions, execution_positions),
        "amount": _slice_matrix(matrix_root / "amount.npy", asset_positions, execution_positions),
    }
    validity = {
        "open": _slice_matrix(
            matrix_root / "open_validity.npy", asset_positions, execution_positions, dtype=bool
        ),
        "close": _slice_matrix(
            matrix_root / "close_validity.npy", asset_positions, execution_positions, dtype=bool
        ),
        "vol": _slice_matrix(
            matrix_root / "volume_validity.npy", asset_positions, execution_positions, dtype=bool
        ),
        "amount": _slice_matrix(
            matrix_root / "amount_validity.npy", asset_positions, execution_positions, dtype=bool
        ),
    }
    strict_masks = {
        Path(name).stem: _slice_matrix(
            matrix_root / name,
            asset_positions,
            signal_positions,
            dtype=bool,
        )
        for name in SIGNAL_MASKS
    }
    execution_masks: dict[str, Any] = {}
    for name in EXECUTION_MASKS:
        key = Path(name).stem
        candidate = matrix_root / name
        execution_masks[key] = (
            _slice_matrix(candidate, asset_positions, execution_positions, dtype=bool)
            if candidate.is_file()
            else np.asarray(parent["execution_masks"][key], dtype=bool)
        )
    execution_masks["corporate_action_validity"] = np.asarray(
        parent["execution_masks"]["corporate_action_validity"],
        dtype=bool,
    )
    by_factor = {str(row["factor_id"]): row for row in materializations}
    factor_values: dict[str, Any] = {}
    factor_validity: dict[str, Any] = {}
    for factor_id in context["exact20_ids"]:
        entry = by_factor.get(factor_id)
        if entry is None:
            raise ResponseApplicationError(
                f"task055j_successor_materialization_missing:{factor_id}"
            )
        factor_values[factor_id] = np.asarray(
            np.load(entry["values_path"], mmap_mode="r", allow_pickle=False)
        )[np.ix_(asset_positions, signal_positions)]
        factor_validity[factor_id] = np.asarray(
            np.load(entry["validity_path"], mmap_mode="r", allow_pickle=False)
        )[np.ix_(asset_positions, signal_positions)]
    return {
        "manifest": dict(parent["manifest"]),
        "trade_dates": signal_dates,
        "execution_dates": execution_dates,
        "ts_codes": assets,
        "factor_values": factor_values,
        "factor_validity": factor_validity,
        "strict_masks": strict_masks,
        "execution_masks": execution_masks,
        "execution_metadata": parent["execution_metadata"],
        "raw": raw,
        "raw_validity": validity,
        "benchmark_index_bars": parent["benchmark_index_bars"],
        "corporate_actions": parent["corporate_actions"],
        "unit_contract": parent["unit_contract"],
    }


def _matrix_marks(
    matrix_root: Path,
    assets: list[str],
    dates: list[str],
) -> dict[str, Any]:
    matrix_assets = _read_list(matrix_root / "ts_codes.json")
    matrix_dates = _read_list(matrix_root / "trade_dates.json")
    asset_positions = [matrix_assets.index(asset) for asset in assets]
    date_positions = [matrix_dates.index(date) for date in dates]
    return {
        "open": _slice_matrix(matrix_root / "open.npy", asset_positions, date_positions).T,
        "open_valid": _slice_matrix(
            matrix_root / "open_validity.npy", asset_positions, date_positions, dtype=bool
        ).T,
        "close": _slice_matrix(matrix_root / "close.npy", asset_positions, date_positions).T,
        "close_valid": _slice_matrix(
            matrix_root / "close_validity.npy", asset_positions, date_positions, dtype=bool
        ).T,
    }


def _production_context(seal: Mapping[str, Any]) -> dict[str, Any]:
    runtime = seal["runtime_authority"]
    governed = Path(seal["governed_root"])
    catalog = {row["role"]: row for row in runtime["application_artifacts"]["catalog"]}

    def resolve(role: str) -> Path:
        row = catalog[role]
        path = (governed / str(row["relative_path"])).resolve()
        if governed not in path.parents or path.is_symlink():
            raise ResponseApplicationError(f"task055j_production_context_escape:{role}")
        if row.get("sha256") and sha256_file(path) != row["sha256"]:
            raise ResponseApplicationError(f"task055j_production_context_sha_drift:{role}")
        return path

    exact_ids = list(runtime["application_artifacts"]["exact20_ids"])
    store_root = resolve("normalized_store_root")
    validate_normalized_replay_store(store_root, expected_ids=exact_ids)
    factors = LocalFactorStore(store_root).load_factors()
    simulation_bundle = resolve("simulation_bundle")
    bundle = validate_simulation_bundle(simulation_bundle, require_ready=True)
    fee = read_json(resolve("fee_schedule"))
    context = {
        "freeze_root": str(resolve("freeze_manifest").parent),
        "universe_root": str(resolve("universe_manifest").parent),
        "matrix_root": str(resolve("matrix_root")),
        "tensor_root": str(resolve("tensor_root")),
        "feature_manifest": str(resolve("feature_manifest")),
        "promotion_policy": str(resolve("promotion_policy")),
        "truth_manifest": str(resolve("truth_v2")),
        "fee_schedule": str(resolve("fee_schedule")),
        "fee_schedule_content_hash": fee["content_hash"],
        "simulation_bundle": str(simulation_bundle),
        "simulation_bundle_content_hash": bundle["content_hash"],
        "factors": factors,
        "exact20_ids": exact_ids,
        "exact20_identity_root": runtime["application_artifacts"]["exact20_identity_root"],
        "parent_materializations": _materializations_from_bundle(simulation_bundle, exact_ids),
        "research_cutoff": runtime["application_artifacts"]["research_cutoff"],
        "holdout_start_date": "20240531",
        "scenarios": [
            "baseline",
            "zero_cost_accounting",
            "double_modeled_cost",
            "participation_5_percent",
            "aum_10_million",
        ],
        "expected_truth_record_count": 35844,
    }
    context["context_root"] = canonical_hash(
        {
            "application_tree_root": runtime["application_tree_root"],
            "exact20_identity_root": context["exact20_identity_root"],
            "truth": read_json(context["truth_manifest"])["content_hash"],
            "fee": context["fee_schedule_content_hash"],
            "bundle": context["simulation_bundle_content_hash"],
        }
    )
    return context


def _publish_dynamic_l2(
    *,
    request: Mapping[str, Any],
    truth: Mapping[str, Any],
    replay: Mapping[str, Any],
    output_root: Path,
) -> dict[str, Any]:
    fields = ["ts_code", "trade_date", "suspend_timing", "suspend_type"]
    params = {"ts_code": request["ts_code"], "trade_date": request["trade_date"]}
    transport_hash = transport_identity("suspend_d", params, fields)
    request_payload = {
        "api_name": "suspend_d",
        "params": params,
        "fields": fields,
        "ts_code": request["ts_code"],
        "trade_date": request["trade_date"],
        "transport_hash": transport_hash,
        "evidence_use_hash": evidence_use_identity(
            stage="task055j_l2_exact",
            parent_plan_hash=truth["content_hash"],
            frontier_root=replay["frontier_union_root"],
            transport_hash=transport_hash,
        ),
    }
    return publish_generation(
        output_root,
        prefix="task055j_dynamic_l2",
        manifest_name="dynamic_l2_plan.json",
        semantic={
            "schema_version": "task055j_dynamic_exact_suspend_l2_v1",
            "status": "sealed_not_authorized",
            "parent_truth_content_hash": truth["content_hash"],
            "parent_replay_content_hash": replay["content_hash"],
            "requests": [request_payload],
            "request_count": 1,
            "network_executed": False,
            "resume_authorized": False,
            "application_support": "unsupported_waiting_for_separate_authority",
            "daily_empty_semantics": "vendor_absence_only_not_full_day_suspension_proof",
        },
    )


def _materializations_from_bundle(
    bundle_manifest: Path,
    exact_ids: list[str],
) -> list[dict[str, Any]]:
    manifest = validate_simulation_bundle(bundle_manifest, require_ready=True)
    root = bundle_manifest.parent
    result = []
    for factor_id in exact_ids:
        values = root / manifest["artifacts"][f"factor:{factor_id}:values"]["path"]
        validity = root / manifest["artifacts"][f"factor:{factor_id}:validity"]["path"]
        result.append(
            {
                "factor_id": factor_id,
                "values_path": str(values),
                "validity_path": str(validity),
                "content_hash": canonical_hash([sha256_file(values), sha256_file(validity)]),
                "manifest_path": str(bundle_manifest),
            }
        )
    return result


def _slice_matrix(
    path: Path,
    asset_positions: list[int],
    date_positions: list[int],
    dtype: Any = None,
) -> np.ndarray:
    array = np.load(path, mmap_mode="r", allow_pickle=False)
    if array.ndim == 1:
        result = np.asarray(array[date_positions])
    elif array.ndim == 2:
        result = np.asarray(array[np.ix_(asset_positions, date_positions)])
    else:
        raise ResponseApplicationError(
            f"task055j_matrix_partition_rank_invalid:{path.name}:{array.ndim}"
        )
    return result.astype(dtype, copy=False) if dtype is not None else result


def _read_list(path: Path) -> list[str]:
    return [str(value) for value in json.loads(path.read_text(encoding="utf-8"))]

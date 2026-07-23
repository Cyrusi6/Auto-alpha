from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from task_055_f.contracts import (
    MAX_STALE_AGE_TRADE_DAYS,
    MODELED_STALE_METHOD,
    OFFICIAL_CLOSE_METHOD,
    OFFICIAL_OPEN_METHOD,
)
from task_055_f.valuation import load_valuation_projection, valuation_surface_from_projection
from task_055_g.truth import validate_truth_v2
from task_055_h.fee import FeeProjectionCalculator
from task_055_h.independent import independently_trace_prepared, prepare_independent_inputs
from task_055_h.io import canonical_hash
from task_055_j.application import _successor_bundle
from task_054_c.validators import validate_strict_matrix_generation

from .application import validate_staged_application
from .application_components import validate_fee_replay
from .broker import AcceptedResponse
from .immutable import write_immutable_generation


VERIFICATION_SCHEMA = "task055kr_independent_application_replay_verification_v2"


class Task055KIndependentError(RuntimeError):
    pass


def independently_verify_application_replay(
    *,
    application_path: str | Path,
    accepted: AcceptedResponse,
    context: Mapping[str, Any],
    output_root: str | Path,
) -> dict[str, Any]:
    application = validate_staged_application(
        application_path,
        accepted=accepted,
        context=context,
    )
    stages = application["stage_payloads"]
    truth_manifest = _absolute(
        application_path,
        stages["truth_successor"]["native_outputs"]["truth_manifest"],
    )
    truth = validate_truth_v2(truth_manifest)
    matrix_root = _matrix_root(application_path, stages, context)
    materializations = _materializations(application_path, stages, context)
    independent = _recompute(
        context=context,
        matrix_root=matrix_root,
        materializations=materializations,
        truth=truth,
        projection_manifest=_absolute(
            application_path,
            stages["valuation"]["native_outputs"]["valuation_manifest"],
        ),
    )
    net = validate_fee_replay(
        _absolute(
            application_path,
            stages["net_replay"]["native_outputs"]["replay_manifest"],
        )
    )
    all_in = validate_fee_replay(
        _absolute(
            application_path,
            stages["all_in_replay"]["native_outputs"]["replay_manifest"],
        )
    )
    _compare_replay("net", independent["net"], net)
    _compare_replay("all_in", independent["all_in"], all_in)
    union = sorted(
        {
            tuple(item)
            for item in independent["net"]["frontier_keys"]
            + independent["all_in"]["frontier_keys"]
        }
    )
    final_outputs = stages["final_publication"]["native_outputs"]
    if canonical_hash(union) != final_outputs.get("frontier_union_root"):
        raise Task055KIndependentError("task055k_independent_frontier_union_mismatch")
    semantic = {
        "schema_version": VERIFICATION_SCHEMA,
        "status": "passed",
        "application_content_hash": application["content_hash"],
        "application_spec_hash": application["application_spec_hash"],
        "truth_content_hash": truth["content_hash"],
        "matrix_content_hash": net["matrix_content_hash"],
        "net_run_rows_root": net["run_rows_root"],
        "net_held_mark_root": net["held_mark_root"],
        "net_terminal_counts": net["terminal_counts"],
        "net_frontier_root": net["frontier_root"],
        "all_in_run_rows_root": all_in["run_rows_root"],
        "all_in_held_mark_root": all_in["held_mark_root"],
        "all_in_terminal_counts": all_in["terminal_counts"],
        "all_in_frontier_root": all_in["frontier_root"],
        "frontier_union_root": canonical_hash(union),
        "producer_consumer_recomputed_from_raw_matrix_truth_fee": True,
        "producer_projection_used_as_input": True,
        "valuation_projection_recomputed_from_truth_matrix": True,
        "independent_causal_implementation": True,
        "valuation_surface_root": independent["valuation_surface_root"],
        "net_terminal_pair_count": len(independent["net"]["run_rows"]),
        "all_in_terminal_pair_count": len(independent["all_in"]["run_rows"]),
    }
    if semantic["net_terminal_pair_count"] != 100 or semantic["all_in_terminal_pair_count"] != 100:
        raise Task055KIndependentError("task055k_independent_exact20_x5_invalid")
    return write_immutable_generation(
        output_root,
        prefix="task055kr_independent_replay",
        manifest_name="independent_replay_verification.json",
        semantic=semantic,
    )


def _recompute(
    *,
    context: Mapping[str, Any],
    matrix_root: Path,
    materializations: list[dict[str, Any]],
    truth: Mapping[str, Any],
    projection_manifest: str | Path,
) -> dict[str, Any]:
    successor = _successor_bundle(context, matrix_root, materializations)
    projection = load_valuation_projection(projection_manifest)
    dates = [str(value) for value in projection["dates"]]
    assets = [str(value) for value in projection["assets"]]
    rebuilt_surface = _independent_valuation_surface(
        truth=truth,
        assets=assets,
        dates=dates,
        matrix=_independent_matrix_marks(matrix_root, assets, dates),
        corporate_actions=list(successor.get("corporate_actions") or ()),
    )
    published_surface = valuation_surface_from_projection(
        projection_manifest,
        dates=dates,
        assets=assets,
    )
    rebuilt_root = _surface_root(rebuilt_surface)
    if rebuilt_root != _surface_root(published_surface):
        raise Task055KIndependentError("task055k_independent_valuation_projection_mismatch")
    independent_prepared = prepare_independent_inputs(successor, projection)
    return {
        "valuation_surface_root": rebuilt_root,
        "net": independently_trace_prepared(
            bundle_manifest={"exact20_ids": list(context["exact20_ids"])},
            prepared=independent_prepared,
            projection=projection,
            calculator=FeeProjectionCalculator(
                context["fee_schedule"], commission_mode="net_commission_3bp"
            ),
        ),
        "all_in": independently_trace_prepared(
            bundle_manifest={"exact20_ids": list(context["exact20_ids"])},
            prepared=independent_prepared,
            projection=projection,
            calculator=FeeProjectionCalculator(
                context["fee_schedule"], commission_mode="all_in_commission_3bp"
            ),
        ),
    }


def _independent_matrix_marks(
    matrix_root: Path, assets: list[str], dates: list[str]
) -> dict[str, np.ndarray]:
    validated = validate_strict_matrix_generation(matrix_root)
    root = Path(validated["manifest_path"]).parent
    matrix_assets = _read_axis(root / "ts_codes.json")
    matrix_dates = _read_axis(root / "trade_dates.json")
    asset_index = {value: index for index, value in enumerate(matrix_assets)}
    date_index = {value: index for index, value in enumerate(matrix_dates)}
    if any(asset not in asset_index for asset in assets) or any(
        date not in date_index for date in dates
    ):
        raise Task055KIndependentError("task055k_independent_matrix_axis_missing")
    asset_positions = np.asarray([asset_index[asset] for asset in assets], dtype=np.int64)
    date_positions = np.asarray([date_index[date] for date in dates], dtype=np.int64)

    def sliced(name: str, *, dtype: Any) -> np.ndarray:
        values = np.load(root / name, mmap_mode="r", allow_pickle=False)
        return np.asarray(values[np.ix_(asset_positions, date_positions)].T, dtype=dtype)

    return {
        "open": sliced("open.npy", dtype=float),
        "open_valid": sliced("open_validity.npy", dtype=bool),
        "close": sliced("close.npy", dtype=float),
        "close_valid": sliced("close_validity.npy", dtype=bool),
    }


def _read_axis(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise Task055KIndependentError("task055k_independent_matrix_axis_invalid")
    return [str(value) for value in payload]


def _independent_valuation_surface(
    *,
    truth: Mapping[str, Any],
    assets: list[str],
    dates: list[str],
    matrix: Mapping[str, np.ndarray],
    corporate_actions: list[Mapping[str, Any]],
) -> dict[str, Any]:
    shape = (len(dates), len(assets))
    values = {
        "open": np.full(shape, np.nan, dtype=float),
        "close": np.full(shape, np.nan, dtype=float),
    }
    metadata = {
        point: {
            "method": np.full(shape, "", dtype=object),
            "source_date": np.full(shape, "", dtype=object),
            "stale_age": np.full(shape, -1, dtype=np.int32),
            "evidence_id": np.full(shape, "", dtype=object),
        }
        for point in ("open", "close")
    }
    blockers: dict[tuple[str, str, str], str] = {}
    truth_by_key = {
        (str(row["ts_code"]), str(row["trade_date"])): row
        for row in truth["records"]
    }
    date_index = {date: index for index, date in enumerate(dates)}
    action_dates: dict[str, set[int]] = {}
    for action in corporate_actions:
        asset = str(action.get("asset", action.get("ts_code", "")))
        raw = action.get("effective_index", action.get("ex_index", action.get("ex_date")))
        index = date_index.get(str(raw), int(raw) if str(raw).isdigit() else -1)
        if 0 <= index < len(dates):
            action_dates.setdefault(asset, set()).add(index)
    last_close_index = np.full(len(assets), -1, dtype=np.int32)
    for day, date in enumerate(dates):
        for asset_position, asset in enumerate(assets):
            open_value = float(matrix["open"][day, asset_position])
            close_value = float(matrix["close"][day, asset_position])
            open_valid = (
                bool(matrix["open_valid"][day, asset_position])
                and math.isfinite(open_value)
                and open_value > 0
            )
            close_valid = (
                bool(matrix["close_valid"][day, asset_position])
                and math.isfinite(close_value)
                and close_value > 0
            )
            if open_valid:
                values["open"][day, asset_position] = open_value
                _set_independent_mark(
                    metadata["open"],
                    day,
                    asset_position,
                    OFFICIAL_OPEN_METHOD,
                    date,
                    0,
                    canonical_hash((asset, date, "open", open_value)),
                )
            if close_valid:
                values["close"][day, asset_position] = close_value
                _set_independent_mark(
                    metadata["close"],
                    day,
                    asset_position,
                    OFFICIAL_CLOSE_METHOD,
                    date,
                    0,
                    canonical_hash((asset, date, "close", close_value)),
                )
            row = truth_by_key.get((asset, date))
            if not open_valid or not close_valid:
                if row is None:
                    reason = "no_truth_v2_row_for_missing_matrix_mark"
                elif row.get("state") == "MATRIX_SOURCE_CONFLICT":
                    reason = "matrix_source_conflict"
                elif not row.get("modeled_stale_candidate"):
                    reason = str(
                        row.get("reason_code") or "truth_v2_not_modeled_candidate"
                    )
                else:
                    source = int(last_close_index[asset_position])
                    age = day - source if source >= 0 else -1
                    if source < 0:
                        reason = "no_prior_finite_positive_close"
                    elif age > MAX_STALE_AGE_TRADE_DAYS:
                        reason = "stale_age_gt_250"
                    elif any(
                        source < action_day <= day
                        for action_day in action_dates.get(asset, ())
                    ):
                        reason = "corporate_action_between_anchor_and_mark"
                    else:
                        stale_value = float(matrix["close"][source, asset_position])
                        source_date = dates[source]
                        evidence_id = str(row["evidence_hash"])
                        if not open_valid:
                            values["open"][day, asset_position] = stale_value
                            _set_independent_mark(
                                metadata["open"],
                                day,
                                asset_position,
                                MODELED_STALE_METHOD,
                                source_date,
                                age,
                                evidence_id,
                            )
                        if not close_valid:
                            values["close"][day, asset_position] = stale_value
                            _set_independent_mark(
                                metadata["close"],
                                day,
                                asset_position,
                                MODELED_STALE_METHOD,
                                source_date,
                                age,
                                evidence_id,
                            )
                        reason = ""
                if reason:
                    if not open_valid:
                        blockers[(asset, date, "open_pretrade")] = reason
                    if not close_valid:
                        blockers[(asset, date, "close")] = reason
            if close_valid:
                last_close_index[asset_position] = day
    return {"values": values, "metadata": metadata, "blockers": blockers}


def _set_independent_mark(
    metadata: Mapping[str, np.ndarray],
    day: int,
    asset_position: int,
    method: str,
    source_date: str,
    stale_age: int,
    evidence_id: str,
) -> None:
    metadata["method"][day, asset_position] = method
    metadata["source_date"][day, asset_position] = source_date
    metadata["stale_age"][day, asset_position] = stale_age
    metadata["evidence_id"][day, asset_position] = evidence_id


def _compare_replay(name: str, computed: Mapping[str, Any], published: Mapping[str, Any]) -> None:
    expected = {
        "run_rows_root": computed["run_rows_root"],
        "held_mark_root": computed["held_mark_root"],
        "frontier_root": computed["frontier_root"],
        "terminal_counts": computed["terminal_counts"],
        "run_count": len(computed["run_rows"]),
    }
    if any(published.get(key) != value for key, value in expected.items()):
        raise Task055KIndependentError(f"task055k_independent_{name}_replay_mismatch")


def _surface_root(surface: Mapping[str, Any]) -> str:
    values = surface["values"]
    metadata = surface["metadata"]
    payload: dict[str, Any] = {
        "values": {
            point: _numeric_array_root(np.asarray(values[point]))
            for point in ("open", "close")
        },
        "metadata": {},
        "blockers": [
            [list(key), value]
            for key, value in sorted((surface.get("blockers") or {}).items())
        ],
    }
    for point in ("open", "close"):
        payload["metadata"][point] = {}
        for name in ("method", "source_date", "stale_age", "evidence_id"):
            array = np.asarray(metadata[point][name])
            if name == "method":
                array = np.where(array == "", "UNRESOLVED", array)
            payload["metadata"][point][name] = canonical_hash(array.tolist())
    return canonical_hash(payload)


def _numeric_array_root(array: np.ndarray) -> str:
    import hashlib

    normalized = np.ascontiguousarray(array)
    return canonical_hash(
        {
            "shape": list(normalized.shape),
            "dtype": str(normalized.dtype),
            "sha256": hashlib.sha256(normalized.tobytes()).hexdigest(),
        }
    )


def _matrix_root(
    application_path: str | Path,
    stages: Mapping[str, Mapping[str, Any]],
    context: Mapping[str, Any],
) -> Path:
    outputs = stages["strict_matrix"]["native_outputs"]
    return (
        Path(context["matrix_root"])
        if outputs.get("parent_reference")
        else _absolute(application_path, outputs["matrix_root"])
    )


def _materializations(
    application_path: str | Path,
    stages: Mapping[str, Mapping[str, Any]],
    context: Mapping[str, Any],
) -> list[dict[str, Any]]:
    parent = {row["factor_id"]: row for row in context["parent_materializations"]}
    result = []
    for row in stages["exact20_materialization"]["native_outputs"]["materializations"]:
        if row.get("parent_reference"):
            result.append(dict(parent[row["factor_id"]]))
        else:
            result.append(
                {
                    "factor_id": row["factor_id"],
                    "content_hash": row["content_hash"],
                    "manifest_path": str(_absolute(application_path, row["manifest_path"])),
                    "values_path": str(_absolute(application_path, row["values_path"])),
                    "validity_path": str(_absolute(application_path, row["validity_path"])),
                }
            )
    if len(result) != 20:
        raise Task055KIndependentError("task055k_independent_materialization_count_invalid")
    return result


def _absolute(application_path: str | Path, relative: str) -> Path:
    manifest = Path(application_path).resolve()
    root = manifest.parents[2]
    candidate = (root / relative).resolve()
    if root not in candidate.parents:
        raise Task055KIndependentError("task055k_independent_artifact_escape")
    return candidate

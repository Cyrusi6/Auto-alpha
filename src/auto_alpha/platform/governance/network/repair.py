"""Immutable source-repair primitives used by native response application."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from auto_alpha.data.lake.store.task052_freeze import (
    create_task052_governed_freeze,
    resolve_task052_governed_freeze_manifest,
)
from auto_alpha.validation.walk_forward.engine_materialization import FactorMaterializer
from auto_alpha.validation.walk_forward.engine_materialization import MaterializationInputs

from auto_alpha.platform.artifacts.storage import canonical_hash, publish_generation, read_json, sha256_file


class NetworkRepairError(RuntimeError):
    pass


def _publish_raw_repair(
    *,
    parent_freeze_root: Path,
    row: Mapping[str, Any],
    request: Mapping[str, Any],
    output_root: Path,
) -> dict[str, Any]:
    manifest = read_json(resolve_task052_governed_freeze_manifest(parent_freeze_root))
    bars = parent_freeze_root / str(manifest["artifacts_by_name"]["daily_bars"]["relative_path"])
    output_root.mkdir(parents=True, exist_ok=True)
    merged = output_root / "daily_bars_repaired.jsonl"
    temporary = merged.with_suffix(".tmp")
    found = False
    with bars.open("r", encoding="utf-8") as source, temporary.open("w", encoding="utf-8") as target:
        for line in source:
            current = json.loads(line)
            if (
                current.get("ts_code") == request["ts_code"]
                and current.get("trade_date") == request["trade_date"]
            ):
                if found:
                    raise NetworkRepairError("task055i_parent_daily_duplicate_key")
                current = dict(row)
                found = True
            target.write(json.dumps(current, sort_keys=True, separators=(",", ":")) + "\n")
        if not found:
            target.write(json.dumps(dict(row), sort_keys=True, separators=(",", ":")) + "\n")
        target.flush()
        os.fsync(target.fileno())
    os.replace(temporary, merged)
    result = publish_generation(
        output_root / "manifest",
        prefix="raw_repair",
        manifest_name="raw_repair.json",
        semantic={
            "schema_version": "task055i_raw_repair_v1",
            "status": "published",
            "parent_freeze_content_hash": manifest["content_hash"],
            "security_date": [request["ts_code"], request["trade_date"]],
            "row_hash": canonical_hash(row),
            "replaced_existing_row": found,
            "merged_daily_bars_sha256": sha256_file(merged),
            "source_transport_hash": request["transport_hash"],
        },
    )
    return result | {"merged_daily_bars_path": str(merged)}


def _build_repaired_freeze(
    *,
    parent_freeze_root: Path,
    raw_repair: Mapping[str, Any],
    output_root: Path,
) -> dict[str, Any]:
    manifest = read_json(resolve_task052_governed_freeze_manifest(parent_freeze_root))
    artifacts = {
        str(item["logical_name"]): parent_freeze_root / str(item["relative_path"])
        for item in manifest["artifacts"]
    }
    artifacts["daily_bars"] = Path(raw_repair["merged_daily_bars_path"])
    lineage = output_root / "source_lineage.json"
    lineage.parent.mkdir(parents=True, exist_ok=True)
    lineage.write_text(
        json.dumps(
            {
                "parent_freeze_content_hash": manifest["content_hash"],
                "raw_repair_content_hash": raw_repair["content_hash"],
                "raw_repair_manifest_sha256": sha256_file(raw_repair["manifest_path"]),
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return create_task052_governed_freeze(
        artifacts,
        output_root / "generations",
        source_lineage_manifest_path=lineage,
    )


def _materialize_exact20(
    *,
    factors: Sequence[Any],
    freeze_root: Path,
    matrix_root: Path,
    tensor_root: Path,
    feature_manifest: Path,
    promotion_policy: Path,
    output_root: Path,
    research_cutoff: str,
) -> list[dict[str, Any]]:
    tensor_manifest = read_json(tensor_root / "task_053_v3_tensor_manifest.json")
    matrix_manifest = read_json(matrix_root / "task_052a_strict_matrix_manifest.json")
    materializer = FactorMaterializer(
        MaterializationInputs(
            data_freeze_dir=str(freeze_root),
            matrix_cache_dir=str(matrix_root),
            feature_manifest_path=str(feature_manifest),
            feature_tensor_path=str(tensor_root / "feature_tensor.npy"),
            feature_validity_tensor_path=str(tensor_root / "feature_validity_tensor.npy"),
            promotion_policy_path=str(promotion_policy),
            target_return_mode="target_open_t1_t2",
            feature_cutoff_mode="next_trade_day_open",
            research_end_date=research_cutoff,
            label_horizon=2,
            research_eligible_date_mask_path=str(matrix_root / "research_eligible_date_mask.npy"),
            eligibility_contract_hash=str(matrix_manifest["eligible_date_hash"]),
            research_computation_identity=canonical_hash(
                [matrix_manifest["eligible_date_hash"], tensor_manifest["content_hash"], research_cutoff]
            ),
        ),
        output_root,
        device="cpu",
        min_coverage=0.0001,
        max_coverage=1.0,
    )
    results: list[dict[str, Any]] = []
    for factor in factors:
        result = materializer.materialize(factor)
        if result.status != "success" or result.cache_hit:
            raise NetworkRepairError(
                f"task055i_exact20_materialization_failed:{factor.factor_id}:{result.blocker}"
            )
        payload = read_json(result.manifest_path)
        results.append(
            {
                "factor_id": factor.factor_id,
                "content_hash": canonical_hash(
                    [payload["input_fingerprint"], payload["value_sha256"], payload["validity_sha256"]]
                ),
                "manifest_path": result.manifest_path,
                "values_path": result.values_path,
                "validity_path": result.validity_path,
            }
        )
    if len(results) != 20 or len({row["factor_id"] for row in results}) != 20:
        raise NetworkRepairError("task055i_materialization_exact20_identity_invalid")
    return results


def _assert_repair_in_matrix(
    matrix_root: Path,
    request: Mapping[str, Any],
    row: Mapping[str, Any],
) -> None:
    codes = _read_list(matrix_root / "ts_codes.json")
    dates = _read_list(matrix_root / "trade_dates.json")
    stock = codes.index(request["ts_code"])
    date = dates.index(request["trade_date"])
    values = np.load(matrix_root / "open.npy", allow_pickle=False)
    validity = np.load(matrix_root / "open_validity.npy", allow_pickle=False)
    if not validity[stock, date] or not math.isclose(
        float(values[stock, date]),
        float(row["open"]),
        rel_tol=0.0,
        abs_tol=1e-6,
    ):
        raise NetworkRepairError("task055i_raw_repair_not_present_in_matrix")


def _read_list(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise NetworkRepairError(f"task055i_axis_not_list:{path.name}")
    return [str(item) for item in payload]

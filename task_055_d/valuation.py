"""Full-axis immutable valuation projection and independent verification."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from data_pipeline.ashare.request_normalization import stable_json_hash
from task_055_c.evidence import MODELED, sha256_file, validate_truth_table

SCHEMA = "task055d_full_axis_valuation_v2"
METHODS = {
    "UNRESOLVED": 0,
    "OFFICIAL_OPEN": 1,
    "OFFICIAL_CLOSE": 2,
    "STALE_OFFICIAL_NON_TRADING": 3,
    "STALE_VENDOR_DAILY_NON_TRADING_MODELED": 4,
    "LIFECYCLE_SETTLEMENT": 5,
}


class Task055DValuationError(RuntimeError):
    pass


def build_full_axis_valuation(
    *, truth_manifest: str | Path, matrix_root: str | Path, output_root: str | Path,
    max_stale_age_trade_days: int = 250,
) -> dict[str, Any]:
    truth = validate_truth_table(truth_manifest)
    matrix = Path(matrix_root)
    matrix_manifest = _validate_matrix(matrix)
    codes = json.loads((matrix / "ts_codes.json").read_text(encoding="utf-8"))
    dates = json.loads((matrix / "trade_dates.json").read_text(encoding="utf-8"))
    shape = (len(codes), len(dates))
    open_values = np.load(matrix / "open.npy", mmap_mode="r", allow_pickle=False)
    close_values = np.load(matrix / "close.npy", mmap_mode="r", allow_pickle=False)
    open_valid = np.load(matrix / "open_validity.npy", mmap_mode="r", allow_pickle=False)
    close_valid = np.load(matrix / "close_validity.npy", mmap_mode="r", allow_pickle=False)
    for array in (open_values, close_values, open_valid, close_valid):
        if array.shape != shape:
            raise Task055DValuationError("matrix_axis_shape_mismatch")

    code_index = {code: index for index, code in enumerate(codes)}
    date_index = {date: index for index, date in enumerate(dates)}
    truth_by_position: dict[tuple[int, int], dict[str, Any]] = {}
    for row in truth["records"]:
        if row["ts_code"] in code_index and row["trade_date"] in date_index:
            truth_by_position[(code_index[row["ts_code"]], date_index[row["trade_date"]])] = row

    values = np.full((len(codes), len(dates), 2), np.nan, dtype=np.float64)
    methods = np.zeros((len(codes), len(dates), 2), dtype=np.uint8)
    source_date = np.full((len(codes), len(dates), 2), -1, dtype=np.int32)
    stale_age = np.zeros((len(codes), len(dates), 2), dtype=np.int32)
    evidence_id = np.zeros((len(codes), len(dates), 2), dtype="S64")
    last_close = np.full(len(codes), np.nan, dtype=np.float64)
    last_close_date = np.full(len(codes), -1, dtype=np.int32)
    lineage_conflicts = 0
    illegal_carry = 0

    for date_pos in range(len(dates)):
        day_open_valid = np.asarray(open_valid[:, date_pos], dtype=bool).copy()
        day_close_valid = np.asarray(close_valid[:, date_pos], dtype=bool).copy()
        valid_open_values = np.asarray(open_values[:, date_pos], dtype=np.float64)
        valid_close_values = np.asarray(close_values[:, date_pos], dtype=np.float64)
        values[day_open_valid, date_pos, 0] = valid_open_values[day_open_valid]
        methods[day_open_valid, date_pos, 0] = METHODS["OFFICIAL_OPEN"]
        source_date[day_open_valid, date_pos, 0] = date_pos
        values[day_close_valid, date_pos, 1] = valid_close_values[day_close_valid]
        methods[day_close_valid, date_pos, 1] = METHODS["OFFICIAL_CLOSE"]
        source_date[day_close_valid, date_pos, 1] = date_pos

        for asset_pos in range(len(codes)):
            row = truth_by_position.get((asset_pos, date_pos))
            if row is None:
                continue
            evidence = str(row["evidence_hash"]).encode("ascii")
            if row["daily_bar"] == "absent" and (day_open_valid[asset_pos] or day_close_valid[asset_pos]):
                lineage_conflicts += 1
                methods[asset_pos, date_pos] = METHODS["UNRESOLVED"]
                values[asset_pos, date_pos] = np.nan
                source_date[asset_pos, date_pos] = -1
                day_open_valid[asset_pos] = False
                day_close_valid[asset_pos] = False
                continue
            if row["state"] != MODELED:
                continue
            age = date_pos - int(last_close_date[asset_pos])
            if np.isfinite(last_close[asset_pos]) and 0 < age <= max_stale_age_trade_days:
                values[asset_pos, date_pos] = last_close[asset_pos]
                methods[asset_pos, date_pos] = METHODS["STALE_VENDOR_DAILY_NON_TRADING_MODELED"]
                source_date[asset_pos, date_pos] = last_close_date[asset_pos]
                stale_age[asset_pos, date_pos] = age
                evidence_id[asset_pos, date_pos] = evidence
            elif np.isfinite(last_close[asset_pos]) and age <= 0:
                illegal_carry += 1

        last_close[day_close_valid] = valid_close_values[day_close_valid]
        last_close_date[day_close_valid] = date_pos

    valuation_positions = [
        (code_index[row["ts_code"]], date_index[row["trade_date"]])
        for row in truth["records"]
        if row.get("valuation_domain_intersection")
        and row["ts_code"] in code_index
        and row["trade_date"] in date_index
    ]
    unresolved = sum(int(np.count_nonzero(methods[asset, date] == 0)) for asset, date in valuation_positions)
    reporting_points = len(valuation_positions) * 2
    semantic = {
        "schema_version": SCHEMA,
        "status": "ready" if unresolved == 0 and lineage_conflicts == 0 and illegal_carry == 0 else "blocked",
        "truth_content_hash": truth["content_hash"],
        "matrix_content_hash": matrix_manifest["content_hash"],
        "shape": [len(codes), len(dates), 2],
        "stock_axis_hash": _hash_lines(codes),
        "date_axis_hash": _hash_lines(dates),
        "reporting_points": reporting_points,
        "covered_reporting_points": reporting_points - unresolved,
        "unresolved_reporting_points": unresolved,
        "lineage_conflict_count": lineage_conflicts,
        "illegal_carry_count": illegal_carry,
        "max_stale_age_trade_days": max_stale_age_trade_days,
        "method_codes": METHODS,
    }
    return _publish(Path(output_root), semantic, values, methods, source_date, stale_age, evidence_id)


def validate_full_axis_valuation(
    path: str | Path, *, truth_manifest: str | Path, matrix_root: str | Path,
) -> dict[str, Any]:
    manifest_path = Path(path)
    if manifest_path.is_dir():
        manifest_path = manifest_path / "valuation_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA:
        raise Task055DValuationError("valuation_schema_invalid")
    root = manifest_path.parent
    arrays = {}
    for name, entry in manifest.get("partitions", {}).items():
        artifact = root / entry["path"]
        if not artifact.is_file() or sha256_file(artifact) != entry["sha256"]:
            raise Task055DValuationError(f"valuation_partition_mismatch:{name}")
        array = np.load(artifact, mmap_mode="r", allow_pickle=False)
        if list(array.shape) != entry["shape"] or str(array.dtype) != entry["dtype"]:
            raise Task055DValuationError(f"valuation_partition_contract_mismatch:{name}")
        arrays[name] = array
    matrix_manifest = _validate_matrix(Path(matrix_root))
    truth = validate_truth_table(truth_manifest)
    if manifest.get("matrix_content_hash") != matrix_manifest.get("content_hash") or manifest.get("truth_content_hash") != truth.get("content_hash"):
        raise Task055DValuationError("valuation_lineage_mismatch")
    codes = json.loads((Path(matrix_root) / "ts_codes.json").read_text(encoding="utf-8"))
    dates = json.loads((Path(matrix_root) / "trade_dates.json").read_text(encoding="utf-8"))
    if manifest.get("stock_axis_hash") != _hash_lines(codes) or manifest.get("date_axis_hash") != _hash_lines(dates):
        raise Task055DValuationError("valuation_axis_hash_mismatch")
    if manifest.get("stock_axis_hash") != matrix_manifest.get("stock_axis_hash") or manifest.get("date_axis_hash") != matrix_manifest.get("date_axis_hash"):
        raise Task055DValuationError("valuation_matrix_axis_hash_mismatch")
    if not manifest.get("stock_axis_hash") or not manifest.get("date_axis_hash"):
        raise Task055DValuationError("valuation_axis_hash_missing")
    _independent_verify_values(
        arrays=arrays,
        truth=truth,
        matrix_root=Path(matrix_root),
        codes=codes,
        dates=dates,
        max_stale_age=int(manifest["max_stale_age_trade_days"]),
    )
    semantic = {key: value for key, value in manifest.items() if key not in {"content_hash", "generation_id", "partitions"}}
    semantic["partitions"] = manifest["partitions"]
    if stable_json_hash(semantic) != manifest.get("content_hash"):
        raise Task055DValuationError("valuation_content_hash_mismatch")
    return manifest | {"manifest_path": str(manifest_path)}


def _independent_verify_values(
    *, arrays: dict[str, np.ndarray], truth: dict[str, Any], matrix_root: Path,
    codes: list[str], dates: list[str], max_stale_age: int,
) -> None:
    open_values = np.load(matrix_root / "open.npy", mmap_mode="r", allow_pickle=False)
    close_values = np.load(matrix_root / "close.npy", mmap_mode="r", allow_pickle=False)
    open_valid = np.load(matrix_root / "open_validity.npy", mmap_mode="r", allow_pickle=False)
    close_valid = np.load(matrix_root / "close_validity.npy", mmap_mode="r", allow_pickle=False)
    code_index = {code: index for index, code in enumerate(codes)}
    date_index = {date: index for index, date in enumerate(dates)}
    truth_by_position = {
        (code_index[row["ts_code"]], date_index[row["trade_date"]]): row
        for row in truth["records"]
        if row["ts_code"] in code_index and row["trade_date"] in date_index
    }
    last_close = np.full(len(codes), np.nan, dtype=np.float64)
    last_date = np.full(len(codes), -1, dtype=np.int32)
    for date_pos in range(len(dates)):
        for asset_pos in range(len(codes)):
            row = truth_by_position.get((asset_pos, date_pos))
            conflict = bool(row and row["daily_bar"] == "absent" and (bool(open_valid[asset_pos, date_pos]) or bool(close_valid[asset_pos, date_pos])))
            if conflict:
                if np.any(arrays["methods"][asset_pos, date_pos] != 0):
                    raise Task055DValuationError("valuation_truth_matrix_conflict_not_blocked")
                continue
            expected_open = METHODS["OFFICIAL_OPEN"] if bool(open_valid[asset_pos, date_pos]) else 0
            expected_close = METHODS["OFFICIAL_CLOSE"] if bool(close_valid[asset_pos, date_pos]) else 0
            if row and row["state"] == MODELED and row["daily_bar"] == "absent":
                age = date_pos - int(last_date[asset_pos])
                if np.isfinite(last_close[asset_pos]) and 0 < age <= max_stale_age:
                    if not np.all(arrays["methods"][asset_pos, date_pos] == METHODS["STALE_VENDOR_DAILY_NON_TRADING_MODELED"]):
                        raise Task055DValuationError("valuation_modeled_stale_method_mismatch")
                    if not np.allclose(arrays["values"][asset_pos, date_pos], last_close[asset_pos], rtol=0, atol=1e-12):
                        raise Task055DValuationError("valuation_modeled_stale_value_mismatch")
                    if not np.all(arrays["source_date"][asset_pos, date_pos] == last_date[asset_pos]) or not np.all(arrays["stale_age"][asset_pos, date_pos] == age):
                        raise Task055DValuationError("valuation_modeled_stale_provenance_mismatch")
                elif np.any(arrays["methods"][asset_pos, date_pos] != 0):
                    raise Task055DValuationError("valuation_illegal_stale_carry")
            else:
                if int(arrays["methods"][asset_pos, date_pos, 0]) != expected_open or int(arrays["methods"][asset_pos, date_pos, 1]) != expected_close:
                    raise Task055DValuationError("valuation_official_method_mismatch")
                if expected_open and float(arrays["values"][asset_pos, date_pos, 0]) != float(open_values[asset_pos, date_pos]):
                    raise Task055DValuationError("valuation_official_open_mismatch")
                if expected_close and float(arrays["values"][asset_pos, date_pos, 1]) != float(close_values[asset_pos, date_pos]):
                    raise Task055DValuationError("valuation_official_close_mismatch")
            if bool(close_valid[asset_pos, date_pos]):
                last_close[asset_pos] = float(close_values[asset_pos, date_pos])
                last_date[asset_pos] = date_pos


def _validate_matrix(root: Path) -> dict[str, Any]:
    path = root / "task_052a_strict_matrix_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not manifest.get("content_hash") or not manifest.get("stock_axis_hash") or not manifest.get("date_axis_hash"):
        raise Task055DValuationError("matrix_lineage_or_axis_hash_missing")
    for name in ("open.npy", "close.npy", "open_validity.npy", "close_validity.npy"):
        expected = (manifest.get("partition_sha256") or {}).get(name)
        if not expected or sha256_file(root / name) != expected:
            raise Task055DValuationError(f"matrix_partition_mismatch:{name}")
    return manifest


def _publish(root: Path, semantic: dict[str, Any], *arrays: np.ndarray) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".task055d_valuation.", dir=root))
    names = ("values", "methods", "source_date", "stale_age", "evidence_id")
    try:
        partitions = {}
        for name, array in zip(names, arrays):
            path = staging / f"{name}.npy"
            np.save(path, array, allow_pickle=False)
            partitions[name] = {
                "path": path.name,
                "sha256": sha256_file(path),
                "shape": list(array.shape),
                "dtype": str(array.dtype),
                "size_bytes": path.stat().st_size,
            }
        payload = semantic | {"partitions": partitions}
        content_hash = stable_json_hash(payload)
        payload |= {"content_hash": content_hash, "generation_id": f"valuation_{content_hash[:24]}"}
        (staging / "valuation_manifest.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        target = root / "generations" / payload["generation_id"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(staging)
        else:
            os.replace(staging, target)
        pointer = root / "current.json"
        temporary = root / ".current.json.tmp"
        temporary.write_text(json.dumps({"generation_id": payload["generation_id"], "content_hash": content_hash, "manifest": f"generations/{payload['generation_id']}/valuation_manifest.json"}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, pointer)
        return payload | {"manifest_path": str(target / "valuation_manifest.json"), "root": str(target)}
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _hash_lines(values: list[str]) -> str:
    import hashlib
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()

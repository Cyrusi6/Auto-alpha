"""Immutable compact valuation projection for Task 055-F replay."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from .contracts import (
    MODELED_STALE_METHOD,
    OFFICIAL_CLOSE_METHOD,
    OFFICIAL_OPEN_METHOD,
)
from .read_ledger import canonical_hash, sha256_file


VALUATION_SCHEMA = "task055f_compact_valuation_projection_v1"
METHOD_CODES = {
    "UNRESOLVED": 0,
    OFFICIAL_OPEN_METHOD: 1,
    OFFICIAL_CLOSE_METHOD: 2,
    MODELED_STALE_METHOD: 3,
}
METHOD_NAMES = {value: key for key, value in METHOD_CODES.items()}


class ValuationProjectionError(RuntimeError):
    pass


def publish_valuation_projection(
    *,
    output_root: str | Path,
    dates: Sequence[str],
    assets: Sequence[str],
    surface: Mapping[str, Any],
    truth_v2_content_hash: str,
    matrix_content_hash: str,
    builder_code_hash: str,
) -> dict[str, Any]:
    date_axis = [str(value) for value in dates]
    stock_axis = [str(value) for value in assets]
    shape = (len(date_axis), len(stock_axis))
    values = np.stack(
        [
            np.asarray(surface["values"]["open"], dtype=np.float64),
            np.asarray(surface["values"]["close"], dtype=np.float64),
        ],
        axis=2,
    )
    methods = np.zeros(shape + (2,), dtype=np.uint8)
    source_date = np.full(shape + (2,), -1, dtype=np.int32)
    stale_age = np.full(shape + (2,), -1, dtype=np.int32)
    evidence_id = np.zeros(shape + (2,), dtype="S64")
    date_index = {date: index for index, date in enumerate(date_axis)}
    for point_index, point in enumerate(("open", "close")):
        metadata = surface["metadata"][point]
        raw_methods = np.asarray(metadata["method"], dtype=object)
        raw_sources = np.asarray(metadata["source_date"], dtype=object)
        raw_ages = np.asarray(metadata["stale_age"], dtype=np.int32)
        raw_evidence = np.asarray(metadata["evidence_id"], dtype=object)
        if any(array.shape != shape for array in (raw_methods, raw_sources, raw_ages, raw_evidence)):
            raise ValuationProjectionError("valuation_surface_metadata_shape_invalid")
        for name, code in METHOD_CODES.items():
            methods[:, :, point_index][raw_methods == name] = code
        source_date[:, :, point_index] = np.vectorize(
            lambda value: date_index.get(str(value), -1), otypes=[np.int32]
        )(raw_sources)
        stale_age[:, :, point_index] = raw_ages
        evidence_id[:, :, point_index] = np.vectorize(
            lambda value: str(value or "").encode("ascii", errors="ignore")[:64],
            otypes=["S64"],
        )(raw_evidence)
    if values.shape != shape + (2,):
        raise ValuationProjectionError("valuation_surface_value_shape_invalid")
    blockers = [
        {
            "ts_code": str(code),
            "trade_date": str(date),
            "reporting_point": str(point),
            "reason": str(reason),
        }
        for (code, date, point), reason in sorted((surface.get("blockers") or {}).items())
    ]
    semantic = {
        "schema_version": VALUATION_SCHEMA,
        "status": "ready" if not blockers else "blocked",
        "shape": list(values.shape),
        "dates": date_axis,
        "assets": stock_axis,
        "date_axis_hash": canonical_hash(date_axis),
        "stock_axis_hash": canonical_hash(stock_axis),
        "truth_v2_content_hash": str(truth_v2_content_hash),
        "matrix_content_hash": str(matrix_content_hash),
        "builder_code_hash": str(builder_code_hash),
        "method_codes": METHOD_CODES,
        "unresolved_reporting_point_count": len(blockers),
        "blocker_root": canonical_hash(blockers),
    }
    return _publish(Path(output_root), semantic, values, methods, source_date, stale_age, evidence_id, blockers)


def validate_valuation_projection(path: str | Path) -> dict[str, Any]:
    manifest_path = _resolve_manifest(path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != VALUATION_SCHEMA or manifest.get("status") not in {"ready", "blocked"}:
        raise ValuationProjectionError("valuation_projection_manifest_invalid")
    root = manifest_path.parent
    arrays: dict[str, np.ndarray] = {}
    for name, entry in (manifest.get("partitions") or {}).items():
        artifact = root / str(entry.get("path") or "")
        if not artifact.is_file() or sha256_file(artifact) != entry.get("sha256"):
            raise ValuationProjectionError(f"valuation_projection_partition_invalid:{name}")
        if name != "blockers":
            array = np.load(artifact, mmap_mode="r", allow_pickle=False)
            if list(array.shape) != entry.get("shape") or str(array.dtype) != entry.get("dtype"):
                raise ValuationProjectionError(f"valuation_projection_array_contract_invalid:{name}")
            arrays[name] = array
    expected_shape = tuple(manifest.get("shape") or ())
    if expected_shape != (len(manifest.get("dates") or ()), len(manifest.get("assets") or ()), 2):
        raise ValuationProjectionError("valuation_projection_axis_shape_invalid")
    if any(tuple(array.shape) != expected_shape for array in arrays.values()):
        raise ValuationProjectionError("valuation_projection_partition_shape_mismatch")
    if canonical_hash(manifest.get("dates") or ()) != manifest.get("date_axis_hash"):
        raise ValuationProjectionError("valuation_projection_date_axis_hash_mismatch")
    if canonical_hash(manifest.get("assets") or ()) != manifest.get("stock_axis_hash"):
        raise ValuationProjectionError("valuation_projection_stock_axis_hash_mismatch")
    blockers = _read_jsonl(root / manifest["partitions"]["blockers"]["path"])
    if canonical_hash(blockers) != manifest.get("blocker_root"):
        raise ValuationProjectionError("valuation_projection_blocker_root_mismatch")
    if len(blockers) != int(manifest.get("unresolved_reporting_point_count") or 0):
        raise ValuationProjectionError("valuation_projection_blocker_count_mismatch")
    semantic = {key: value for key, value in manifest.items() if key not in {"content_hash", "generation_id"}}
    if canonical_hash(semantic) != manifest.get("content_hash"):
        raise ValuationProjectionError("valuation_projection_content_hash_mismatch")
    return manifest | {"manifest_path": str(manifest_path), "root": str(root), "arrays": arrays, "blockers": blockers}


def load_valuation_projection(
    path: str | Path,
    *,
    dates: Sequence[str] | None = None,
    assets: Sequence[str] | None = None,
) -> dict[str, Any]:
    validated = validate_valuation_projection(path)
    if dates is not None and list(map(str, dates)) != list(validated["dates"]):
        raise ValuationProjectionError("valuation_projection_requested_date_axis_mismatch")
    if assets is not None and list(map(str, assets)) != list(validated["assets"]):
        raise ValuationProjectionError("valuation_projection_requested_stock_axis_mismatch")
    arrays = validated["arrays"]
    methods = np.asarray(arrays["methods"])
    source = np.asarray(arrays["source_date"])
    ages = np.asarray(arrays["stale_age"])
    evidence = np.asarray(arrays["evidence_id"])
    return validated | {
        "valuation_open": np.asarray(arrays["values"][:, :, 0], dtype=float),
        "valuation_close": np.asarray(arrays["values"][:, :, 1], dtype=float),
        "open_method": methods[:, :, 0],
        "close_method": methods[:, :, 1],
        "open_source_date": source[:, :, 0],
        "close_source_date": source[:, :, 1],
        "open_stale_age": ages[:, :, 0],
        "close_stale_age": ages[:, :, 1],
        "open_evidence_id": evidence[:, :, 0],
        "close_evidence_id": evidence[:, :, 1],
    }


def valuation_surface_from_projection(
    path: str | Path,
    *,
    dates: Sequence[str] | None = None,
    assets: Sequence[str] | None = None,
) -> dict[str, Any]:
    projection = load_valuation_projection(path, dates=dates, assets=assets)
    date_axis = list(projection["dates"])

    def methods(point: str) -> np.ndarray:
        return np.vectorize(
            lambda value: METHOD_NAMES[int(value)], otypes=[object]
        )(projection[f"{point}_method"])

    def source_dates(point: str) -> np.ndarray:
        return np.vectorize(
            lambda value: date_axis[int(value)] if int(value) >= 0 else "",
            otypes=[object],
        )(projection[f"{point}_source_date"])

    def evidence_ids(point: str) -> np.ndarray:
        return np.vectorize(
            lambda value: bytes(value).decode("ascii"), otypes=[object]
        )(projection[f"{point}_evidence_id"])

    blockers = {
        (str(row["ts_code"]), str(row["trade_date"]), str(row["reporting_point"])): str(
            row["reason"]
        )
        for row in projection["blockers"]
    }
    return {
        "values": {
            "open": projection["valuation_open"],
            "close": projection["valuation_close"],
        },
        "metadata": {
            point: {
                "method": methods(point),
                "source_date": source_dates(point),
                "stale_age": projection[f"{point}_stale_age"],
                "evidence_id": evidence_ids(point),
            }
            for point in ("open", "close")
        },
        "blockers": blockers,
        "projection_content_hash": projection["content_hash"],
    }


def projection_mark_rows(path: str | Path) -> list[dict[str, Any]]:
    """Expand a compact projection for legacy artifact compatibility tests."""

    projection = load_valuation_projection(path)
    values = projection["arrays"]["values"]
    methods = projection["arrays"]["methods"]
    source = projection["arrays"]["source_date"]
    ages = projection["arrays"]["stale_age"]
    evidence = projection["arrays"]["evidence_id"]
    rows: list[dict[str, Any]] = []
    for day, date in enumerate(projection["dates"]):
        for asset_index, asset in enumerate(projection["assets"]):
            for point_index, point in enumerate(("open", "close")):
                method = METHOD_NAMES[int(methods[day, asset_index, point_index])]
                if method == "UNRESOLVED":
                    continue
                source_index = int(source[day, asset_index, point_index])
                row = {
                    "schema_version": "task055b_security_date_mark_evidence_v1",
                    "date": date,
                    "asset": asset,
                    "reporting_point": point,
                    "mark_price": float(values[day, asset_index, point_index]),
                    "mark_method": method,
                    "mark_source_date": projection["dates"][source_index],
                    "stale_age_trade_days": int(ages[day, asset_index, point_index]),
                    "market_session_state": (
                        "VENDOR_DAILY_NON_TRADING_MODELED" if method == MODELED_STALE_METHOD else "TRADED_PRIMARY_BAR"
                    ),
                    "execution_allowed": method == OFFICIAL_OPEN_METHOD,
                    "corporate_action_transform": {"type": "none", "price_multiplier": 1.0},
                    "stale_mark_notional": 0.0,
                    "stale_mark_nav_ratio": 0.0,
                    "evidence": {
                        "projection_content_hash": projection["content_hash"],
                        "evidence_id": bytes(evidence[day, asset_index, point_index]).decode("ascii"),
                    },
                }
                row["evidence_hash"] = canonical_hash(row["evidence"])
                rows.append(row)
    return rows


def _publish(
    root: Path,
    semantic: Mapping[str, Any],
    values: np.ndarray,
    methods: np.ndarray,
    source_date: np.ndarray,
    stale_age: np.ndarray,
    evidence_id: np.ndarray,
    blockers: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".task055f.valuation.", dir=root))
    try:
        partitions: dict[str, dict[str, Any]] = {}
        for name, array in (
            ("values", values),
            ("methods", methods),
            ("source_date", source_date),
            ("stale_age", stale_age),
            ("evidence_id", evidence_id),
        ):
            path = staging / f"{name}.npy"
            np.save(path, array, allow_pickle=False)
            partitions[name] = {
                "path": path.name,
                "sha256": sha256_file(path),
                "shape": list(array.shape),
                "dtype": str(array.dtype),
                "size_bytes": path.stat().st_size,
            }
        blocker_path = staging / "blockers.jsonl"
        blocker_path.write_text(
            "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in blockers),
            encoding="utf-8",
        )
        partitions["blockers"] = {
            "path": blocker_path.name,
            "sha256": sha256_file(blocker_path),
            "size_bytes": blocker_path.stat().st_size,
        }
        payload = dict(semantic) | {"partitions": partitions}
        content_hash = canonical_hash(payload)
        generation_id = f"valuation_projection_{content_hash[:24]}"
        manifest = payload | {"content_hash": content_hash, "generation_id": generation_id}
        (staging / "valuation_projection_manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        target = root / "generations" / generation_id
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(staging)
        else:
            os.replace(staging, target)
        pointer = {
            "generation_id": generation_id,
            "content_hash": content_hash,
            "manifest": f"generations/{generation_id}/valuation_projection_manifest.json",
        }
        temporary = root / ".current.json.tmp"
        temporary.write_text(json.dumps(pointer, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, root / "current.json")
        return manifest | {"manifest_path": str(target / "valuation_projection_manifest.json"), "root": str(target)}
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _resolve_manifest(path: str | Path) -> Path:
    value = Path(path)
    if value.is_file():
        return value
    pointer = value / "current.json"
    if pointer.is_file():
        return value / str(json.loads(pointer.read_text(encoding="utf-8"))["manifest"])
    candidate = value / "valuation_projection_manifest.json"
    if candidate.is_file():
        return candidate
    raise ValuationProjectionError("valuation_projection_manifest_missing")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

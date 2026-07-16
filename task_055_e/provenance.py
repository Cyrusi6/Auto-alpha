"""Offline row-level provenance reconstruction for Task 055-E."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

from data_pipeline.ashare.request_normalization import stable_json_hash
from task_055_c.evidence import sha256_file

from .contracts import (
    DAILY_API_FIELDS,
    DAILY_NORMALIZED_FIELDS,
    MAX_DATE,
    OFFLINE_CLASSIFICATIONS,
    PROVENANCE_SCHEMA,
    RECONCILIATION_SCHEMA,
    SUSPEND_FIELDS,
)


class OfflineProvenanceError(RuntimeError):
    pass


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def discover_raw_daily_source(governed_root: Path) -> dict[str, Any]:
    """Select the raw-index declaration that matches the actual governed file."""

    candidates: list[dict[str, Any]] = []
    for index_path in sorted((governed_root / "reports").glob("**/raw_dataset_indexes.jsonl")):
        try:
            with index_path.open(encoding="utf-8") as handle:
                for line in handle:
                    if '"daily_bars"' not in line:
                        continue
                    row = json.loads(line)
                    if row.get("dataset") != "daily_bars":
                        continue
                    records_path = Path(str(row.get("records_path") or "")).resolve()
                    if not _inside(governed_root, records_path) or not records_path.is_file():
                        continue
                    stat = records_path.stat()
                    candidates.append(
                        {
                            "index_path": index_path,
                            "records_path": records_path,
                            "declared_sha256": row.get("records_sha256"),
                            "declared_size_bytes": row.get("file_size_bytes"),
                            "declared_record_count": row.get("record_count"),
                            "declared_first_date": row.get("first_date"),
                            "declared_last_date": row.get("last_date"),
                            "size_matches": int(row.get("file_size_bytes") or -1) == stat.st_size,
                        }
                    )
        except (OSError, ValueError, TypeError):
            continue
    size_matches = [row for row in candidates if row["size_matches"] and row["declared_sha256"]]
    if not size_matches:
        raise OfflineProvenanceError("no_raw_daily_index_matches_actual_file_size")
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in size_matches:
        groups[(str(row["records_path"]), str(row["declared_sha256"]))].append(row)
    selected_group = sorted(groups.values(), key=lambda rows: (-len(rows), str(rows[0]["records_path"])))[0]
    selected = sorted(selected_group, key=lambda row: str(row["index_path"]))[0]
    return {
        **selected,
        "matching_index_declarations": len(selected_group),
        "stale_or_mismatched_declarations": len(candidates) - len(selected_group),
        "candidate_declaration_count": len(candidates),
    }


def discover_cache_roots(governed_root: Path) -> list[Path]:
    roots: set[Path] = set()
    legacy = governed_root / "cache" / ".cache" / "tushare"
    if legacy.is_dir():
        roots.add(legacy.resolve())
    validation_root = governed_root / "validation_runs"
    for task_root in sorted(validation_root.glob("task_05[2-5]*")):
        if not task_root.is_dir():
            continue
        for candidate in task_root.glob("**/.cache/tushare"):
            if candidate.is_dir():
                roots.add(candidate.resolve())
    return sorted(roots)


def inventory_cache_roots(governed_root: Path, cache_roots: Iterable[Path]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    totals = Counter()
    for root in cache_roots:
        root_counts = Counter()
        for path in root.glob("*.json"):
            root_counts["physical_candidates"] += 1
            try:
                prefix = path.open("rb").read(16_384)
            except OSError:
                root_counts["unreadable"] += 1
                continue
            schema = _extract_json_string(prefix, b"schema_version")
            api = _extract_json_string(prefix, b"api_name")
            if schema:
                root_counts[f"schema:{schema}"] += 1
            else:
                root_counts["schema:legacy_metadata_records"] += 1
            if api:
                root_counts[f"api:{api}"] += 1
            if api in {"daily", "suspend_d"}:
                root_counts["relevant_api_candidates"] += 1
        totals.update(root_counts)
        rows.append({"root": _relative(governed_root, root), "counts": dict(sorted(root_counts.items()))})
    return {
        "roots": rows,
        "root_count": len(rows),
        "totals": dict(sorted(totals.items())),
        "inventory_hash": canonical_hash(rows),
    }


def scan_offline_sources(
    *,
    governed_root: Path,
    freeze_root: Path,
    freeze_manifest: Mapping[str, Any],
    raw_source: Mapping[str, Any],
    matrix_root: Path,
    target_keys: set[tuple[str, str]],
    suspension_coverage_ledger: Path,
    suspension_cache_root: Path,
    output_root: Path,
    builder_code_hash: str,
) -> dict[str, Any]:
    """Build provenance, reconciliation, and an immutable raw-repair delta."""

    if any(date > MAX_DATE for _, date in target_keys):
        raise OfflineProvenanceError("offline_target_date_exceeds_boundary")
    cache_roots = discover_cache_roots(governed_root)
    cache_inventory = inventory_cache_roots(governed_root, cache_roots)
    provenance: list[dict[str, Any]] = []
    observations: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))

    daily_entry = (freeze_manifest.get("datasets") or {}).get("daily_bars") or {}
    freeze_daily = freeze_root / str(daily_entry.get("records_path") or "")
    expected_freeze_sha = str(daily_entry.get("sha256") or "")
    freeze_scan = _scan_normalized_daily_jsonl(
        governed_root=governed_root,
        path=freeze_daily,
        target_keys=target_keys,
        source_kind="immutable_freeze_normalized",
        source_generation=str(freeze_manifest.get("generation_id")),
        expected_sha256=expected_freeze_sha,
    )
    _merge_observations(observations, freeze_scan["observations"], "freeze")
    provenance.extend(freeze_scan["provenance"])

    raw_scan = _scan_normalized_daily_jsonl(
        governed_root=governed_root,
        path=Path(raw_source["records_path"]),
        target_keys=target_keys,
        source_kind="governed_raw_lake_normalized",
        source_generation=_relative(governed_root, Path(raw_source["index_path"]).parent),
        expected_sha256=str(raw_source["declared_sha256"]),
    )
    _merge_observations(observations, raw_scan["observations"], "lake")
    provenance.extend(raw_scan["provenance"])

    matrix_scan = _scan_matrix(matrix_root, target_keys)
    _merge_observations(observations, matrix_scan["observations"], "matrix")

    suspension_scan = _scan_suspension_coverage(
        governed_root=governed_root,
        ledger_path=suspension_coverage_ledger,
        cache_root=suspension_cache_root,
        target_keys=target_keys,
    )
    _merge_observations(observations, suspension_scan["observations"], "suspension")
    provenance.extend(suspension_scan["provenance"])

    formal_daily = _scan_formal_daily_caches(
        governed_root=governed_root,
        cache_roots=cache_roots,
        target_keys=target_keys,
    )
    _merge_observations(observations, formal_daily["observations"], "formal_daily")
    provenance.extend(formal_daily["provenance"])

    legacy_daily = _scan_legacy_daily_cache(
        governed_root=governed_root,
        cache_root=governed_root / "cache" / ".cache" / "tushare",
        target_keys=target_keys,
    )
    _merge_observations(observations, legacy_daily["observations"], "legacy_daily")
    provenance.extend(legacy_daily["provenance"])

    reconciliation_rows: list[dict[str, Any]] = []
    repair_rows: list[dict[str, Any]] = []
    category_counts = Counter()
    for code, date in sorted(target_keys):
        evidence = observations.get((code, date), {})
        category, reason, reusable = _classify(evidence)
        if category not in OFFLINE_CLASSIFICATIONS:
            raise OfflineProvenanceError(f"offline_classification_invalid:{category}")
        category_counts[category] += 1
        row = {
            "ts_code": code,
            "trade_date": date,
            "classification": category,
            "reason_code": reason,
            "source_presence": {name: len(values) for name, values in sorted(evidence.items())},
            "formal_raw_repair_eligible": reusable is not None,
            "evidence_hashes": sorted(
                str(item.get("evidence_hash") or item.get("row_hash") or item.get("proof_hash"))
                for values in evidence.values()
                for item in values
                if item.get("evidence_hash") or item.get("row_hash") or item.get("proof_hash")
            ),
        }
        row["row_hash"] = canonical_hash(row)
        reconciliation_rows.append(row)
        if reusable is not None:
            repair = {
                "ts_code": code,
                "trade_date": date,
                "record": reusable["record"],
                "source_provenance_hash": reusable["evidence_hash"],
                "repair_reason": "validated_raw_cache_bar_missing_from_governed_lake_and_matrix",
            }
            repair["row_hash"] = canonical_hash(repair)
            repair_rows.append(repair)

    source_summaries = {
        "freeze_daily": freeze_scan["summary"],
        "governed_raw_daily": raw_scan["summary"],
        "matrix": matrix_scan["summary"],
        "suspension": suspension_scan["summary"],
        "formal_daily": formal_daily["summary"],
        "legacy_daily": legacy_daily["summary"],
        "cache_inventory": cache_inventory,
    }
    return _publish_provenance_generation(
        output_root=output_root,
        governed_root=governed_root,
        provenance=provenance,
        reconciliation_rows=reconciliation_rows,
        repair_rows=repair_rows,
        category_counts=category_counts,
        source_summaries=source_summaries,
        target_keys=target_keys,
        builder_code_hash=builder_code_hash,
    )


def validate_offline_provenance(
    manifest_path: str | Path,
    *,
    governed_root: str | Path,
) -> dict[str, Any]:
    path = Path(manifest_path)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != PROVENANCE_SCHEMA or manifest.get("status") != "published":
        raise OfflineProvenanceError("offline_provenance_manifest_invalid")
    root = path.parent
    for entry in (manifest.get("partitions") or {}).values():
        artifact = root / str(entry.get("path"))
        if not artifact.is_file() or sha256_file(artifact) != entry.get("sha256"):
            raise OfflineProvenanceError("offline_provenance_partition_sha_mismatch")
    semantic = {key: value for key, value in manifest.items() if key not in {"content_hash", "generation_id"}}
    if canonical_hash(semantic) != manifest.get("content_hash"):
        raise OfflineProvenanceError("offline_provenance_content_hash_mismatch")
    provenance = _read_jsonl(root / manifest["partitions"]["row_provenance"]["path"])
    if len(provenance) != manifest.get("provenance_record_count"):
        raise OfflineProvenanceError("offline_provenance_record_count_mismatch")
    governed = Path(governed_root).resolve()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in provenance:
        grouped[str(row.get("source_relative_path"))].append(row)
    for relative_path, rows in grouped.items():
        source = _safe_relative(governed, relative_path)
        expected_shas = {str(row.get("source_file_sha256")) for row in rows}
        if len(expected_shas) != 1 or not source.is_file() or sha256_file(source) not in expected_shas:
            raise OfflineProvenanceError("provenance_source_file_mismatch")
        payload = None
        if any(row.get("record_kind") != "normalized_row" for row in rows):
            payload = json.loads(source.read_text(encoding="utf-8"))
        for row in rows:
            _verify_provenance_row(row, governed, source=source, payload=payload)
    return manifest | {"manifest_path": str(path)}


def _scan_normalized_daily_jsonl(
    *,
    governed_root: Path,
    path: Path,
    target_keys: set[tuple[str, str]],
    source_kind: str,
    source_generation: str,
    expected_sha256: str,
) -> dict[str, Any]:
    if not path.is_file() or not _inside(governed_root, path.resolve()):
        raise OfflineProvenanceError(f"daily_source_missing_or_outside_root:{source_kind}")
    digest = hashlib.sha256()
    records = 0
    matched = 0
    first_date: str | None = None
    last_date: str | None = None
    observations: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    provenance: list[dict[str, Any]] = []
    with path.open("rb") as handle:
        while True:
            offset = handle.tell()
            line = handle.readline()
            if not line:
                break
            digest.update(line)
            records += 1
            key = _key_from_daily_line(line)
            if key is not None:
                first_date = key[1] if first_date is None or key[1] < first_date else first_date
                last_date = key[1] if last_date is None or key[1] > last_date else last_date
                if key[1] > MAX_DATE:
                    raise OfflineProvenanceError(f"daily_source_contains_post_boundary_data:{source_kind}:{key[1]}")
            if key not in target_keys:
                continue
            row = json.loads(line)
            valid, reason = _validate_normalized_daily(row)
            row_hash = hashlib.sha256(line.rstrip(b"\r\n")).hexdigest()
            evidence = {
                "dataset": "daily_bars",
                "api": "daily",
                "ts_code": key[0],
                "trade_date": key[1],
                "record_kind": "normalized_row",
                "source_kind": source_kind,
                "source_generation": source_generation,
                "source_relative_path": _relative(governed_root, path),
                "source_file_sha256": expected_sha256,
                "byte_offset": offset,
                "byte_length": len(line),
                "request_range": None,
                "response_code": None,
                "response_cache_sha256": None,
                "row_hash": row_hash,
                "provider": "tushare_normalized_lake",
                "source_semantics_hash": None,
                "raw_envelope_validated": False,
                "formal_reuse_eligible": False,
                "record_valid": valid,
                "validation_reason": reason,
            }
            evidence["evidence_hash"] = canonical_hash(evidence)
            observations[key].append(evidence | {"record": row})
            provenance.append(evidence)
            matched += 1
    actual_sha = digest.hexdigest()
    if actual_sha != expected_sha256:
        raise OfflineProvenanceError(f"daily_source_sha_mismatch:{source_kind}:{actual_sha}")
    return {
        "observations": observations,
        "provenance": provenance,
        "summary": {
            "source_kind": source_kind,
            "source_relative_path": _relative(governed_root, path),
            "sha256": actual_sha,
            "record_count": records,
            "first_date": first_date,
            "last_date": last_date,
            "matched_target_rows": matched,
        },
    }


def _scan_matrix(matrix_root: Path, target_keys: set[tuple[str, str]]) -> dict[str, Any]:
    manifest_path = matrix_root / "task_052a_strict_matrix_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    codes = json.loads((matrix_root / "ts_codes.json").read_text(encoding="utf-8"))
    dates = json.loads((matrix_root / "trade_dates.json").read_text(encoding="utf-8"))
    code_index = {value: index for index, value in enumerate(codes)}
    date_index = {value: index for index, value in enumerate(dates)}
    arrays: dict[str, np.ndarray] = {}
    for field in DAILY_NORMALIZED_FIELDS:
        value_name = "volume.npy" if field == "volume" else f"{field}.npy"
        valid_name = "volume_validity.npy" if field == "volume" else f"{field}_validity.npy"
        for name in (value_name, valid_name):
            expected = (manifest.get("partition_sha256") or {}).get(name)
            if not expected or sha256_file(matrix_root / name) != expected:
                raise OfflineProvenanceError(f"matrix_partition_sha_mismatch:{name}")
        arrays[field] = np.load(matrix_root / value_name, mmap_mode="r", allow_pickle=False)
        arrays[f"{field}:valid"] = np.load(matrix_root / valid_name, mmap_mode="r", allow_pickle=False)
    observations: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    valid_count = 0
    for key in target_keys:
        if key[0] not in code_index or key[1] not in date_index:
            continue
        asset, date = code_index[key[0]], date_index[key[1]]
        record = {field: float(arrays[field][asset, date]) for field in DAILY_NORMALIZED_FIELDS}
        validity = {field: bool(arrays[f"{field}:valid"][asset, date]) for field in DAILY_NORMALIZED_FIELDS}
        valid = all(validity.values()) and _valid_daily_numbers(record, volume_name="volume")
        if valid:
            valid_count += 1
        evidence = {
            "record": record,
            "validity": validity,
            "record_valid": valid,
            "matrix_content_hash": manifest.get("content_hash"),
            "row_hash": canonical_hash({"key": key, "record": record, "validity": validity}),
        }
        evidence["evidence_hash"] = canonical_hash(evidence)
        observations[key].append(evidence)
    return {
        "observations": observations,
        "summary": {
            "content_hash": manifest.get("content_hash"),
            "shape": manifest.get("shape"),
            "target_key_count": len(target_keys),
            "complete_target_bars": valid_count,
            "stock_axis_hash": manifest.get("stock_axis_hash"),
            "date_axis_hash": manifest.get("date_axis_hash"),
        },
    }


def _scan_suspension_coverage(
    *,
    governed_root: Path,
    ledger_path: Path,
    cache_root: Path,
    target_keys: set[tuple[str, str]],
) -> dict[str, Any]:
    if not ledger_path.is_file() or not cache_root.is_dir():
        raise OfflineProvenanceError("suspension_coverage_inputs_missing")
    target_by_stock: dict[str, set[str]] = defaultdict(set)
    for code, date in target_keys:
        target_by_stock[code].add(date)
    observations: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    provenance: list[dict[str, Any]] = []
    stats = Counter()
    with ledger_path.open(encoding="utf-8") as handle:
        for line in handle:
            ledger = json.loads(line)
            code = str(ledger.get("ts_code"))
            dates = target_by_stock.get(code)
            if not dates:
                continue
            stats["stocks_considered"] += 1
            for item in ledger.get("slices") or ():
                cache_path = cache_root / str(item.get("cache_key") or "")
                envelope, envelope_sha = _validate_v2_suspend_envelope(cache_path, ledger, item)
                request = envelope["request"]
                params = request["params"]
                start = str(params.get("start_date") or "")
                end = str(params.get("end_date") or "")
                records_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
                for record in envelope.get("records") or ():
                    records_by_date[str(record["trade_date"])].append(dict(record))
                for date in dates:
                    if not start <= date <= end:
                        continue
                    matches = records_by_date.get(date, ())
                    if matches:
                        for record_index, record in enumerate(matches):
                            base = {
                                "dataset": "suspensions",
                                "api": "suspend_d",
                                "ts_code": code,
                                "trade_date": date,
                                "suspend_type": record.get("suspend_type"),
                                "suspend_timing": record.get("suspend_timing"),
                                "record_kind": "positive_event",
                                "source_kind": "task053_governed_v2_envelope",
                                "source_generation": _relative(governed_root, ledger_path.parent),
                                "source_relative_path": _relative(governed_root, cache_path),
                                "source_file_sha256": envelope_sha,
                                "record_index": _record_index(envelope.get("records") or (), record),
                                "request_range": {"start_date": start, "end_date": end},
                                "request_fingerprint": envelope.get("request_fingerprint"),
                                "response_code": (envelope.get("response") or {}).get("code"),
                                "response_cache_sha256": envelope_sha,
                                "row_hash": canonical_hash(record),
                                "provider": str(item.get("endpoint") or ledger.get("endpoint")),
                                "provider_api_version": item.get("provider_api_version") or ledger.get("provider_api_version"),
                                "source_semantics_hash": ledger.get("source_code_hash"),
                                "code_semantic_hash": envelope.get("code_semantic_hash"),
                                "contract_hash": ledger.get("contract_hash"),
                                "raw_envelope_validated": True,
                                "formal_reuse_eligible": True,
                                "record_valid": True,
                            }
                            base["evidence_hash"] = canonical_hash(base)
                            observations[(code, date)].append(base | {"record": record})
                            provenance.append(base)
                            stats["positive_rows"] += 1
                    else:
                        proof = {
                            "dataset": "suspensions",
                            "api": "suspend_d",
                            "ts_code": code,
                            "trade_date": date,
                            "record_kind": "complete_range_response_without_row",
                            "source_kind": "task053_governed_v2_envelope",
                            "source_generation": _relative(governed_root, ledger_path.parent),
                            "source_relative_path": _relative(governed_root, cache_path),
                            "source_file_sha256": envelope_sha,
                            "request_range": {"start_date": start, "end_date": end},
                            "request_fingerprint": envelope.get("request_fingerprint"),
                            "response_cache_sha256": envelope_sha,
                            "response_code": (envelope.get("response") or {}).get("code"),
                            "provider": str(item.get("endpoint") or ledger.get("endpoint")),
                            "provider_api_version": item.get("provider_api_version") or ledger.get("provider_api_version"),
                            "source_semantics_hash": ledger.get("source_code_hash"),
                            "raw_envelope_validated": True,
                            "formal_reuse_eligible": False,
                        }
                        proof["proof_hash"] = canonical_hash({"key": (code, date), **proof})
                        observations[(code, date)].append(proof)
                        provenance.append(proof | {"row_hash": proof["proof_hash"]})
                        stats["complete_range_without_row"] += 1
    stats["target_stocks"] = len(target_by_stock)
    return {
        "observations": observations,
        "provenance": provenance,
        "summary": dict(sorted(stats.items())) | {
            "coverage_ledger_relative_path": _relative(governed_root, ledger_path),
            "coverage_ledger_sha256": sha256_file(ledger_path),
        },
    }


def _scan_legacy_daily_cache(
    *,
    governed_root: Path,
    cache_root: Path,
    target_keys: set[tuple[str, str]],
) -> dict[str, Any]:
    observations: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    provenance: list[dict[str, Any]] = []
    stats = Counter()
    if not cache_root.is_dir():
        return {"observations": observations, "provenance": provenance, "summary": {"cache_root_present": False}}
    target_by_date: dict[str, set[str]] = defaultdict(set)
    for code, date in target_keys:
        target_by_date[date].add(code)
    audit = _load_daily_api_audit(governed_root, set(target_by_date))
    source_hashes = _historical_daily_source_hashes()
    fields = ",".join(DAILY_API_FIELDS)
    for date, codes in sorted(target_by_date.items()):
        params = {"start_date": date, "end_date": date}
        cache_key = _legacy_cache_key("daily", params, fields)
        path = cache_root / f"{cache_key}.json"
        if not path.is_file():
            stats["physical_cache_miss_dates"] += 1
            continue
        stats["physical_cache_hit_dates"] += 1
        payload = json.loads(path.read_text(encoding="utf-8"))
        metadata = payload.get("metadata") or {}
        records = payload.get("records") or []
        cache_sha = sha256_file(path)
        audit_rows = audit.get(date, ())
        audit_ok = any(
            row.get("status") == "success"
            and row.get("api_name") == "daily"
            and int(row.get("records") or -1) == len(records)
            for row in audit_rows
        )
        params_ok = metadata.get("params_hash") == _legacy_stable_hash(params)
        geometry_ok = metadata.get("api_name") == "daily" and all(str(row.get("trade_date")) == date for row in records)
        count_ok = int(metadata.get("records") or -1) == len(records) and len(records) < 6000
        rows_by_code = {str(row.get("ts_code")): row for row in records if str(row.get("ts_code")) in codes}
        composite_valid = bool(audit_ok and params_ok and geometry_ok and count_ok)
        for code in codes:
            record = rows_by_code.get(code)
            if record is not None:
                valid = _validate_api_daily(record)[0]
                evidence = {
                    "dataset": "daily_bars",
                    "api": "daily",
                    "ts_code": code,
                    "trade_date": date,
                    "record_kind": "legacy_cache_positive_row",
                    "source_kind": "legacy_metadata_records_cache",
                    "source_generation": "pre_v2_tushare_cache",
                    "source_relative_path": _relative(governed_root, path),
                    "source_file_sha256": cache_sha,
                    "request_range": params,
                    "request_fingerprint": cache_key,
                    "response_code": 0 if audit_ok else None,
                    "response_cache_sha256": cache_sha,
                    "row_hash": canonical_hash(record),
                    "provider": "historical_tushare_http_client",
                    "source_semantics_hash": source_hashes,
                    "raw_envelope_validated": False,
                    "formal_reuse_eligible": False,
                    "record_valid": bool(valid and composite_valid),
                    "validation_reason": "legacy_response_body_not_preserved",
                }
                evidence["evidence_hash"] = canonical_hash(evidence)
                observations[(code, date)].append(evidence | {"record": record})
                provenance.append(evidence)
                stats["positive_target_rows"] += 1
            elif composite_valid:
                proof = {
                    "dataset": "daily_bars",
                    "api": "daily",
                    "ts_code": code,
                    "trade_date": date,
                    "record_kind": "complete_range_response_without_row",
                    "source_kind": "legacy_metadata_records_cache",
                    "source_generation": "pre_v2_tushare_cache",
                    "source_relative_path": _relative(governed_root, path),
                    "source_file_sha256": cache_sha,
                    "request_range": params,
                    "request_fingerprint": cache_key,
                    "response_cache_sha256": cache_sha,
                    "response_code": 0,
                    "provider": "historical_tushare_http_client",
                    "source_semantics_hash": source_hashes,
                    "raw_envelope_validated": False,
                    "formal_reuse_eligible": False,
                    "proof_limit": "raw_response_envelope_not_preserved",
                }
                proof["proof_hash"] = canonical_hash({"key": (code, date), **proof})
                observations[(code, date)].append(proof)
                provenance.append(proof | {"row_hash": proof["proof_hash"]})
                stats["composite_complete_range_without_row"] += 1
    return {
        "observations": observations,
        "provenance": provenance,
        "summary": dict(sorted(stats.items())) | {
            "cache_root": _relative(governed_root, cache_root),
            "api_audit": audit.get("__summary__", {}),
            "formal_reuse_from_legacy_cache": 0,
        },
    }


def _scan_formal_daily_caches(
    *,
    governed_root: Path,
    cache_roots: Iterable[Path],
    target_keys: set[tuple[str, str]],
) -> dict[str, Any]:
    observations: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    provenance: list[dict[str, Any]] = []
    target_by_stock: dict[str, set[str]] = defaultdict(set)
    for code, date in target_keys:
        target_by_stock[code].add(date)
    stats = Counter()
    seen_physical: set[str] = set()
    for root in cache_roots:
        for path in root.glob("*.json"):
            try:
                prefix = path.open("rb").read(16_384)
            except OSError:
                continue
            if _extract_json_string(prefix, b"api_name") != "daily":
                continue
            schema = _extract_json_string(prefix, b"schema_version")
            if schema not in {"tushare_cache_envelope.v2", "tushare_cache_envelope.v3"}:
                continue
            stats["physical_daily_envelopes"] += 1
            payload = json.loads(path.read_text(encoding="utf-8"))
            physical_sha = sha256_file(path)
            if physical_sha in seen_physical:
                stats["duplicate_physical_content"] += 1
                continue
            seen_physical.add(physical_sha)
            request = payload.get("request") or {}
            response = payload.get("response") or {}
            records = payload.get("records") or []
            params = request.get("params") or {}
            requested_code = str(params.get("ts_code") or "")
            exact = str(params.get("trade_date") or "")
            start = str(params.get("start_date") or exact)
            end = str(params.get("end_date") or exact)
            if requested_code and requested_code not in target_by_stock:
                continue
            intersecting = [
                (code, date)
                for code, date in target_keys
                if (not requested_code or code == requested_code) and start <= date <= end
            ]
            if not intersecting:
                continue
            stats["intersecting_daily_envelopes"] += 1
            integrity_ok = (
                request.get("api_name") == "daily"
                and set(DAILY_API_FIELDS).issubset(request.get("fields") or ())
                and response.get("code") == 0
                and response.get("complete") is True
                and response.get("item_count") == len(records)
                and stable_json_hash(records) == response.get("records_sha256")
                and len(records) < 6000
            )
            provider = payload.get("provider") or payload.get("metadata") or {}
            source_hash = payload.get("source_code_hash") or payload.get("code_semantic_hash")
            formal = bool(schema == "tushare_cache_envelope.v3" and integrity_ok and source_hash and provider)
            rows_by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
            if integrity_ok:
                for record in records:
                    key = (str(record.get("ts_code")), str(record.get("trade_date")))
                    if key not in target_keys or (requested_code and key[0] != requested_code) or not start <= key[1] <= end:
                        continue
                    rows_by_key[key].append(dict(record))
            for key in intersecting:
                matches = rows_by_key.get(key, ())
                if matches:
                    for record in matches:
                        valid, reason = _validate_api_daily(record)
                        row = {
                            "dataset": "daily_bars",
                            "api": "daily",
                            "ts_code": key[0],
                            "trade_date": key[1],
                            "record_kind": "formal_cache_positive_row",
                            "source_kind": schema,
                            "source_generation": _relative(governed_root, root.parent.parent),
                            "source_relative_path": _relative(governed_root, path),
                            "source_file_sha256": physical_sha,
                            "record_index": _record_index(records, record),
                            "request_range": {"start_date": start, "end_date": end},
                            "request_fingerprint": payload.get("request_fingerprint"),
                            "response_code": response.get("code"),
                            "response_cache_sha256": physical_sha,
                            "row_hash": canonical_hash(record),
                            "provider": provider,
                            "source_semantics_hash": source_hash,
                            "raw_envelope_validated": integrity_ok,
                            "formal_reuse_eligible": bool(formal and valid),
                            "record_valid": bool(integrity_ok and valid),
                            "validation_reason": reason if integrity_ok else "formal_envelope_integrity_invalid",
                        }
                        row["evidence_hash"] = canonical_hash(row)
                        observations[key].append(row | {"record": record})
                        provenance.append(row)
                        stats["positive_target_rows"] += 1
                        stats["formal_reusable_rows"] += int(row["formal_reuse_eligible"])
                elif integrity_ok:
                    proof = {
                        "dataset": "daily_bars",
                        "api": "daily",
                        "ts_code": key[0],
                        "trade_date": key[1],
                        "record_kind": "complete_range_response_without_row",
                        "source_kind": schema,
                        "source_generation": _relative(governed_root, root.parent.parent),
                        "source_relative_path": _relative(governed_root, path),
                        "source_file_sha256": physical_sha,
                        "request_range": {"start_date": start, "end_date": end},
                        "request_fingerprint": payload.get("request_fingerprint"),
                        "response_cache_sha256": physical_sha,
                        "response_code": response.get("code"),
                        "provider": provider,
                        "source_semantics_hash": source_hash,
                        "raw_envelope_validated": True,
                        "formal_reuse_eligible": False,
                    }
                    proof["proof_hash"] = canonical_hash({"key": key, **proof})
                    observations[key].append(proof)
                    provenance.append(proof | {"row_hash": proof["proof_hash"]})
                    stats["complete_range_without_row"] += 1
    return {"observations": observations, "provenance": provenance, "summary": dict(sorted(stats.items()))}


def _classify(evidence: Mapping[str, list[dict[str, Any]]]) -> tuple[str, str, dict[str, Any] | None]:
    matrix = evidence.get("matrix") or []
    freeze = evidence.get("freeze") or []
    lake = evidence.get("lake") or []
    suspension = evidence.get("suspension") or []
    formal_daily = evidence.get("formal_daily") or []
    legacy = evidence.get("legacy_daily") or []
    matrix_valid = [row for row in matrix if row.get("record_valid")]
    freeze_valid = [row for row in freeze if row.get("record_valid")]
    lake_valid = [row for row in lake if row.get("record_valid")]
    positive_suspension = [row for row in suspension if row.get("record_kind") == "positive_event"]
    formal_cache_bars = [row for row in formal_daily if row.get("record_valid") and row.get("formal_reuse_eligible")]
    legacy_rows = [row for row in legacy if row.get("record_kind") == "legacy_cache_positive_row"]
    if matrix_valid and (freeze_valid or lake_valid):
        records = matrix_valid + freeze_valid + lake_valid
        if _bars_agree(records):
            return "existing_valid_daily_bar", "matrix_and_governed_daily_bar_agree", None
        return "raw/lake/matrix_conflict", "governed_daily_values_disagree", None
    if freeze_valid or lake_valid or matrix_valid:
        return "raw/lake/matrix_conflict", "daily_bar_presence_differs_across_raw_freeze_matrix", None
    if formal_cache_bars:
        return "lake_missing_but_raw_cache_contains_bar", "formal_raw_envelope_bar_missing_from_lake_matrix", formal_cache_bars[0]
    if legacy_rows:
        return "raw/lake/matrix_conflict", "legacy_cache_bar_exists_without_preserved_raw_envelope", None
    if positive_suspension:
        return "existing_positive_suspend_event", "validated_positive_suspend_event", None
    range_proofs = [
        row
        for group in evidence.values()
        for row in group
        if row.get("record_kind") == "complete_range_response_without_row"
    ]
    if range_proofs:
        return "complete_range_response_without_row", "validated_or_composite_range_has_no_matching_row", None
    return "genuinely_not_found_offline", "no_matching_governed_row_or_range_response", None


def _publish_provenance_generation(
    *,
    output_root: Path,
    governed_root: Path,
    provenance: list[dict[str, Any]],
    reconciliation_rows: list[dict[str, Any]],
    repair_rows: list[dict[str, Any]],
    category_counts: Counter,
    source_summaries: Mapping[str, Any],
    target_keys: set[tuple[str, str]],
    builder_code_hash: str,
) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".task055e.provenance.", dir=output_root))
    try:
        provenance.sort(key=lambda row: (row["ts_code"], row["trade_date"], row["source_kind"], str(row.get("suspend_type"))))
        _write_jsonl(staging / "row_provenance.jsonl", provenance)
        _write_jsonl(staging / "offline_reconciliation.jsonl", reconciliation_rows)
        _write_jsonl(staging / "offline_raw_repair_delta.jsonl", repair_rows)
        partitions = {
            "row_provenance": _partition(staging / "row_provenance.jsonl"),
            "reconciliation": _partition(staging / "offline_reconciliation.jsonl"),
            "raw_repair_delta": _partition(staging / "offline_raw_repair_delta.jsonl"),
        }
        semantic = {
            "schema_version": PROVENANCE_SCHEMA,
            "reconciliation_schema_version": RECONCILIATION_SCHEMA,
            "status": "published",
            "network_accessed": False,
            "prospective_holdout_accessed": False,
            "max_allowed_date": MAX_DATE,
            "target_key_count": len(target_keys),
            "target_key_hash": canonical_hash(sorted(target_keys)),
            "builder_code_hash": builder_code_hash,
            "provenance_record_count": len(provenance),
            "classification_counts": {name: int(category_counts.get(name, 0)) for name in sorted(OFFLINE_CLASSIFICATIONS)},
            "offline_raw_repair_count": len(repair_rows),
            "source_summaries": source_summaries,
            "partitions": partitions,
        }
        content_hash = canonical_hash(semantic)
        generation_id = f"offline_provenance_{content_hash[:24]}"
        manifest = semantic | {"content_hash": content_hash, "generation_id": generation_id}
        _write_json(staging / "provenance_manifest.json", manifest)
        target = output_root / "generations" / generation_id
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(staging)
        else:
            os.replace(staging, target)
        _atomic_json(
            output_root / "current.json",
            {"generation_id": generation_id, "content_hash": content_hash, "manifest": f"generations/{generation_id}/provenance_manifest.json"},
        )
        return manifest | {
            "manifest_path": str(target / "provenance_manifest.json"),
            "generation_dir": str(target),
            "governed_root": str(governed_root),
        }
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _validate_v2_suspend_envelope(cache_path: Path, ledger: Mapping[str, Any], item: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    if not cache_path.is_file():
        raise OfflineProvenanceError("suspension_cache_file_missing")
    actual_sha = sha256_file(cache_path)
    if actual_sha != item.get("cache_sha256"):
        raise OfflineProvenanceError("suspension_cache_sha_mismatch")
    envelope = json.loads(cache_path.read_text(encoding="utf-8"))
    request = envelope.get("request") or {}
    response = envelope.get("response") or {}
    records = envelope.get("records") or []
    if envelope.get("schema_version") not in {"tushare_cache_envelope.v2", "tushare_cache_envelope.v3"}:
        raise OfflineProvenanceError("suspension_cache_schema_invalid")
    if request != item.get("normalized_request") or envelope.get("request_fingerprint") != item.get("request_fingerprint"):
        raise OfflineProvenanceError("suspension_request_identity_mismatch")
    if request.get("api_name") != "suspend_d" or tuple(request.get("fields") or ()) != SUSPEND_FIELDS:
        raise OfflineProvenanceError("suspension_request_contract_invalid")
    if response.get("code") != 0 or response.get("complete") is not True:
        raise OfflineProvenanceError("suspension_response_status_invalid")
    if response.get("item_count") != len(records) or stable_json_hash(records) != response.get("records_sha256"):
        raise OfflineProvenanceError("suspension_response_integrity_invalid")
    if len(records) >= 1000:
        raise OfflineProvenanceError("suspension_response_cap_ambiguous")
    if envelope.get("code_semantic_hash") != ledger.get("code_semantic_hash"):
        raise OfflineProvenanceError("suspension_code_semantic_hash_mismatch")
    params = request.get("params") or {}
    code = str(params.get("ts_code") or "")
    start = str(params.get("start_date") or "")
    end = str(params.get("end_date") or "")
    seen: set[tuple[Any, ...]] = set()
    for record in records:
        date = str(record.get("trade_date") or "")
        kind = record.get("suspend_type")
        timing = record.get("suspend_timing")
        key = (record.get("ts_code"), date, kind, timing)
        if str(record.get("ts_code")) != code or not start <= date <= end or date > MAX_DATE or kind not in {"S", "R"}:
            raise OfflineProvenanceError("suspension_row_outside_request_or_invalid")
        if key in seen:
            raise OfflineProvenanceError("suspension_row_duplicate")
        seen.add(key)
    return envelope, actual_sha


def _load_daily_api_audit(governed_root: Path, target_dates: set[str]) -> dict[str, Any]:
    candidates = sorted((governed_root / "data").glob("**/api_audit.jsonl"))
    result: dict[str, Any] = defaultdict(list)
    summaries = []
    for path in candidates:
        digest = hashlib.sha256()
        row_count = 0
        matched = 0
        with path.open("rb") as handle:
            for line in handle:
                digest.update(line)
                row_count += 1
                if b'"api_name": "daily"' not in line:
                    continue
                row = json.loads(line)
                date = str(row.get("start_date") or "")
                if date in target_dates and str(row.get("end_date") or "") == date:
                    result[date].append(row)
                    matched += 1
        summaries.append({"path": _relative(governed_root, path), "sha256": digest.hexdigest(), "record_count": row_count, "matched": matched})
    result["__summary__"] = {"files": summaries, "matched_rows": sum(item["matched"] for item in summaries)}
    return result


def _historical_daily_source_hashes() -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for name, spec in {
        "cache_source_blob": "0b64ff7:data_pipeline/ashare/cache.py",
        "client_source_blob": "0b64ff7:data_pipeline/ashare/providers/tushare_client.py",
        "provider_source_blob": "0b64ff7:data_pipeline/ashare/providers/tushare.py",
    }.items():
        process = subprocess.run(["git", "rev-parse", spec], text=True, capture_output=True, check=False)
        result[name] = process.stdout.strip() if process.returncode == 0 else None
    return result


def _verify_provenance_row(
    row: Mapping[str, Any],
    governed_root: Path,
    *,
    source: Path | None = None,
    payload: Mapping[str, Any] | None = None,
) -> None:
    date = str(row.get("trade_date") or "")
    if date > MAX_DATE:
        raise OfflineProvenanceError("provenance_date_exceeds_boundary")
    source = source or _safe_relative(governed_root, row.get("source_relative_path"))
    if row.get("record_kind") == "normalized_row":
        with source.open("rb") as handle:
            handle.seek(int(row["byte_offset"]))
            line = handle.read(int(row["byte_length"]))
        if hashlib.sha256(line.rstrip(b"\r\n")).hexdigest() != row.get("row_hash"):
            raise OfflineProvenanceError("provenance_normalized_row_hash_mismatch")
        parsed = json.loads(line)
        if (str(parsed.get("ts_code")), str(parsed.get("trade_date"))) != (row.get("ts_code"), row.get("trade_date")):
            raise OfflineProvenanceError("provenance_normalized_row_key_mismatch")
    elif row.get("record_kind") in {"positive_event", "formal_cache_positive_row"}:
        payload = payload or json.loads(source.read_text(encoding="utf-8"))
        index = int(row.get("record_index"))
        records = payload.get("records") or []
        if index >= len(records) or canonical_hash(records[index]) != row.get("row_hash"):
            raise OfflineProvenanceError("provenance_envelope_row_mismatch")
        if (payload.get("response") or {}).get("code") != 0:
            raise OfflineProvenanceError("provenance_response_code_invalid")
    elif row.get("record_kind") == "complete_range_response_without_row":
        payload = payload or json.loads(source.read_text(encoding="utf-8"))
        records = payload.get("records") or []
        if any(
            str(record.get("ts_code")) == row.get("ts_code")
            and str(record.get("trade_date")) == row.get("trade_date")
            for record in records
        ):
            raise OfflineProvenanceError("provenance_negative_range_contains_matching_row")
        if row.get("raw_envelope_validated"):
            response = payload.get("response") or {}
            if response.get("code") != 0 or response.get("complete") is not True:
                raise OfflineProvenanceError("provenance_negative_response_invalid")
            if response.get("item_count") != len(records) or stable_json_hash(records) != response.get("records_sha256"):
                raise OfflineProvenanceError("provenance_negative_response_hash_invalid")
        else:
            metadata = payload.get("metadata") or {}
            if metadata.get("api_name") != row.get("api") or int(metadata.get("records") or -1) != len(records):
                raise OfflineProvenanceError("provenance_legacy_negative_metadata_invalid")


def _validate_normalized_daily(row: Mapping[str, Any]) -> tuple[bool, str]:
    if not str(row.get("ts_code") or "") or not re.fullmatch(r"\d{8}", str(row.get("trade_date") or "")):
        return False, "key_invalid"
    if str(row.get("trade_date")) > MAX_DATE:
        return False, "date_exceeds_boundary"
    valid = _valid_daily_numbers(row, volume_name="volume")
    return valid, "complete_valid_bar" if valid else "required_field_invalid"


def _validate_api_daily(row: Mapping[str, Any]) -> tuple[bool, str]:
    valid = _valid_daily_numbers(row, volume_name="vol")
    return valid, "complete_valid_bar" if valid else "required_field_invalid"


def _valid_daily_numbers(row: Mapping[str, Any], *, volume_name: str) -> bool:
    values: dict[str, float] = {}
    for field in ("open", "high", "low", "close", "pre_close", volume_name, "amount"):
        try:
            number = float(row.get(field))
        except (TypeError, ValueError):
            return False
        if not math.isfinite(number):
            return False
        values[field] = number
    if any(values[field] <= 0 for field in ("open", "high", "low", "close", "pre_close")):
        return False
    if values[volume_name] < 0 or values["amount"] < 0:
        return False
    return values["high"] >= max(values["open"], values["low"], values["close"]) and values["low"] <= min(values["open"], values["high"], values["close"])


def _bars_agree(rows: Iterable[Mapping[str, Any]]) -> bool:
    normalized = []
    for row in rows:
        record = row.get("record") or {}
        normalized.append(
            tuple(round(float(record.get(field)), 8) for field in ("open", "high", "low", "close", "pre_close", "amount"))
            + (round(float(record.get("volume", record.get("vol"))), 8),)
        )
    return len(set(normalized)) <= 1


def _merge_observations(target: dict, source: Mapping, name: str) -> None:
    for key, rows in source.items():
        target[key][name].extend(rows)


def _key_from_daily_line(line: bytes) -> tuple[str, str] | None:
    date_match = re.search(br'"trade_date":"(\d{8})"', line)
    code_match = re.search(br'"ts_code":"([0-9]{6}\.(?:SH|SZ|BJ))"', line)
    if not date_match or not code_match:
        return None
    return code_match.group(1).decode("ascii"), date_match.group(1).decode("ascii")


def _legacy_cache_key(api_name: str, params: Mapping[str, Any], fields: str) -> str:
    return _legacy_stable_hash({"api_name": api_name, "params": dict(params), "fields": fields})


def _legacy_stable_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _record_index(records: Iterable[Mapping[str, Any]], record: Mapping[str, Any]) -> int:
    expected = canonical_hash(record)
    for index, candidate in enumerate(records):
        if canonical_hash(candidate) == expected:
            return index
    raise OfflineProvenanceError("suspension_record_index_not_found")


def _extract_json_string(prefix: bytes, key: bytes) -> str | None:
    match = re.search(br'"' + re.escape(key) + br'"\s*:\s*"([^"]+)"', prefix)
    return match.group(1).decode("utf-8", errors="replace") if match else None


def _relative(root: Path, path: Path) -> str:
    resolved = path.resolve()
    if not _inside(root.resolve(), resolved):
        raise OfflineProvenanceError("artifact_path_outside_governed_root")
    return str(resolved.relative_to(root.resolve()))


def _safe_relative(root: Path, value: Any) -> Path:
    relative = Path(str(value or ""))
    if relative.is_absolute() or ".." in relative.parts:
        raise OfflineProvenanceError("unsafe_relative_artifact_path")
    path = (root / relative).resolve()
    if not _inside(root.resolve(), path):
        raise OfflineProvenanceError("relative_artifact_path_escaped")
    return path


def _inside(root: Path, path: Path) -> bool:
    return path == root or root in path.parents


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":"), default=str) + "\n")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _partition(path: Path) -> dict[str, Any]:
    return {"path": path.name, "sha256": sha256_file(path), "size_bytes": path.stat().st_size}


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    _write_json(temporary, payload)
    os.replace(temporary, path)

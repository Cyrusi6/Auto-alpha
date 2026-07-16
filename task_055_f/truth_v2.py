"""Independent security-date truth reconstruction for Task 055-F."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

from artifact_schema.writer import write_artifact_sidecar
from data_pipeline.ashare.request_normalization import stable_json_hash

from .contracts import (
    EXPLICIT_FULL_DAY_TIMINGS,
    MATRIX_DAILY_FIELDS,
    MAX_DATE,
    SUSPEND_FIELDS,
    TRUTH_SCHEMA,
    TRUTH_STATES,
)
from .read_ledger import AuditedReader, canonical_hash, sha256_file


class TruthV2Error(RuntimeError):
    pass


def build_truth_v2(
    *,
    governed_root: str | Path,
    inventory_manifest: str | Path,
    matrix_root: str | Path,
    suspension_coverage_ledger: str | Path,
    suspension_cache_root: str | Path,
    task055e_provenance_manifest: str | Path,
    task055c_truth_manifest: str | Path,
    output_root: str | Path,
    reader: AuditedReader,
    builder_code_hash: str,
) -> dict[str, Any]:
    governed = Path(governed_root).resolve()
    inventory, cells = _load_inventory(reader, inventory_manifest)
    matrix = _load_matrix(reader, matrix_root, cells)
    daily_proofs, task055e_provenance_hash = _load_daily_proofs(reader, task055e_provenance_manifest)
    suspension = _load_suspension_sources(
        reader,
        suspension_coverage_ledger,
        suspension_cache_root,
        {(str(row["ts_code"]), str(row["trade_date"])) for row in cells},
    )
    task055c = reader.read_json(
        task055c_truth_manifest,
        component="truth_v2",
        dataset="task055c_truth_lineage",
    )
    if task055c.get("record_count") != inventory.get("cell_count"):
        raise TruthV2Error("task055c_lineage_key_count_mismatch")

    rows: list[dict[str, Any]] = []
    state_counts = Counter()
    cross = Counter()
    event_counts = Counter()
    daily_empty_counts = Counter()
    suspend_empty_counts = Counter()
    keys_seen: set[tuple[str, str]] = set()
    for cell in cells:
        code = str(cell["ts_code"])
        date = str(cell["trade_date"])
        key = (code, date)
        if key in keys_seen:
            raise TruthV2Error("truth_v2_inventory_duplicate_key")
        keys_seen.add(key)
        if date > MAX_DATE:
            raise TruthV2Error("truth_v2_date_exceeds_boundary")
        matrix_bar = matrix["rows"].get(key)
        events = suspension["events"].get(key, [])
        coverage = suspension["coverage"].get(key, [])
        daily_evidence = daily_proofs.get(key, [])
        row = _classify_cell(cell, matrix_bar, events, coverage, daily_evidence)
        rows.append(row)
        state_counts[row["state"]] += 1
        event_counts[row["suspend_type"]] += 1
        cross[
            (
                row["suspend_type"],
                row["suspend_timing_status"],
                row["daily_bar_status"],
                row["suspension_source_coverage"],
                row["state"],
            )
        ] += 1
        for proof in row["daily_response_evidence"]:
            if proof["outcome"] == "no_matching_row":
                daily_empty_counts[(proof["source_kind"], proof["proof_quality"])] += 1
        for proof in row["suspension_response_evidence"]:
            if proof["outcome"] == "no_matching_row":
                suspend_empty_counts[(proof["source_kind"], proof["proof_quality"])] += 1

    if len(rows) != inventory.get("cell_count") or len(keys_seen) != len(rows):
        raise TruthV2Error("truth_v2_key_conservation_failed")
    rows.sort(key=lambda item: (item["ts_code"], item["trade_date"]))
    cross_rows = [
        {
            "suspend_type": key[0],
            "suspend_timing_status": key[1],
            "daily_bar_status": key[2],
            "suspension_source_coverage": key[3],
            "state": key[4],
            "count": count,
        }
        for key, count in sorted(cross.items())
    ]
    summary = {
        "record_count": len(rows),
        "key_root": canonical_hash(sorted(keys_seen)),
        "state_counts": dict(sorted(state_counts.items())),
        "suspend_type_counts": dict(sorted(event_counts.items())),
        "daily_empty_response_counts": _counter_rows(daily_empty_counts),
        "suspend_empty_response_counts": _counter_rows(suspend_empty_counts),
        "valuation_domain_count": sum(bool(row["valuation_domain_intersection"]) for row in rows),
        "modeled_candidate_count": sum(bool(row["modeled_stale_candidate"]) for row in rows),
        "timing_uncertified_candidate_count": sum(
            row["modeled_stale_candidate"] and row["suspend_timing_status"] == "raw_null"
            for row in rows
        ),
    }
    lineage = {
        "inventory_content_hash": inventory.get("content_hash"),
        "matrix_content_hash": matrix["manifest"].get("content_hash"),
        "task055e_provenance_content_hash": task055e_provenance_hash,
        "task055c_truth_lineage_only": task055c.get("content_hash"),
        "suspension_coverage_ledger_sha256": suspension["coverage_ledger_sha256"],
        "builder_code_hash": builder_code_hash,
    }
    return _publish_truth(
        output_root=Path(output_root),
        governed_root=governed,
        rows=rows,
        cross_rows=cross_rows,
        summary=summary,
        lineage=lineage,
    )


def validate_truth_v2(path: str | Path) -> dict[str, Any]:
    manifest_path = _resolve_manifest(path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != TRUTH_SCHEMA or manifest.get("status") != "published":
        raise TruthV2Error("truth_v2_manifest_invalid")
    root = manifest_path.parent
    for entry in (manifest.get("partitions") or {}).values():
        artifact = root / str(entry.get("path"))
        if not artifact.is_file() or sha256_file(artifact) != entry.get("sha256"):
            raise TruthV2Error("truth_v2_partition_mismatch")
    rows = _read_jsonl(root / manifest["partitions"]["rows"]["path"])
    if len(rows) != manifest.get("record_count"):
        raise TruthV2Error("truth_v2_record_count_mismatch")
    keys = [(str(row.get("ts_code")), str(row.get("trade_date"))) for row in rows]
    if len(keys) != len(set(keys)) or canonical_hash(sorted(keys)) != manifest.get("key_root"):
        raise TruthV2Error("truth_v2_key_root_mismatch")
    if any(row.get("state") not in TRUTH_STATES for row in rows):
        raise TruthV2Error("truth_v2_state_invalid")
    counts = dict(sorted(Counter(row["state"] for row in rows).items()))
    if counts != manifest.get("state_counts"):
        raise TruthV2Error("truth_v2_state_counts_mismatch")
    semantic = {key: value for key, value in manifest.items() if key not in {"content_hash", "generation_id"}}
    if canonical_hash(semantic) != manifest.get("content_hash"):
        raise TruthV2Error("truth_v2_content_hash_mismatch")
    return manifest | {"manifest_path": str(manifest_path), "records": rows}


def _load_inventory(reader: AuditedReader, manifest_path: str | Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = reader.read_json(manifest_path, component="truth_v2", dataset="security_date_inventory")
    if manifest.get("schema_version") != "task055b_security_date_gap_inventory_v1":
        raise TruthV2Error("inventory_schema_invalid")
    root = Path(manifest_path).resolve().parent
    cells_entry = (manifest.get("partitions") or {}).get("cells") or {}
    cells_path = root / str(cells_entry.get("path"))
    cells = reader.read_jsonl(cells_path, component="truth_v2", dataset="security_date_inventory_cells")
    if reader.rows[-1]["sha256"] != cells_entry.get("sha256"):
        raise TruthV2Error("inventory_cells_sha_mismatch")
    if len(cells) != manifest.get("cell_count"):
        raise TruthV2Error("inventory_cell_count_mismatch")
    return manifest, cells


def _load_matrix(
    reader: AuditedReader,
    matrix_root: str | Path,
    cells: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    root = Path(matrix_root).resolve()
    manifest_path = root / "task_052a_strict_matrix_manifest.json"
    manifest = reader.read_json(manifest_path, component="truth_v2", dataset="strict_matrix_manifest")
    codes = reader.read_json(root / "ts_codes.json", component="truth_v2", dataset="matrix_stock_axis")
    dates = reader.read_json(root / "trade_dates.json", component="truth_v2", dataset="matrix_date_axis")
    code_index = {str(code): index for index, code in enumerate(codes)}
    date_index = {str(date): index for index, date in enumerate(dates)}
    partitions = manifest.get("partition_sha256") or {}
    arrays: dict[str, np.ndarray] = {}
    for field in MATRIX_DAILY_FIELDS:
        value_name = f"{field}.npy"
        valid_name = f"{field}_validity.npy"
        if hasattr(reader, "load_npy"):
            arrays[field] = reader.load_npy(
                root / value_name,
                component="truth_v2",
                dataset=f"matrix_partition:{value_name}",
            )
            arrays[f"{field}:valid"] = reader.load_npy(
                root / valid_name,
                component="truth_v2",
                dataset=f"matrix_partition:{valid_name}",
            )
            for name in (value_name, valid_name):
                if partitions.get(name) != next(
                    row["sha256"] for row in reversed(reader.rows) if row["relative_path"].endswith(name)
                ):
                    raise TruthV2Error(f"matrix_partition_sha_mismatch:{name}")
        else:
            for name in (value_name, valid_name):
                path = root / name
                reader.record_binary(
                    path,
                    component="truth_v2",
                    dataset=f"matrix_partition:{name}",
                    declared_start=str(dates[0]),
                    declared_end=str(dates[-1]),
                )
                if partitions.get(name) != reader.rows[-1]["sha256"]:
                    raise TruthV2Error(f"matrix_partition_sha_mismatch:{name}")
            arrays[field] = np.load(root / value_name, mmap_mode="r", allow_pickle=False)
            arrays[f"{field}:valid"] = np.load(root / valid_name, mmap_mode="r", allow_pickle=False)
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for cell in cells:
        code = str(cell["ts_code"])
        date = str(cell["trade_date"])
        if code not in code_index or date not in date_index:
            rows[(code, date)] = {"axis_present": False, "complete": False}
            continue
        asset = code_index[code]
        day = date_index[date]
        values = {field: float(arrays[field][asset, day]) for field in MATRIX_DAILY_FIELDS}
        validity = {field: bool(arrays[f"{field}:valid"][asset, day]) for field in MATRIX_DAILY_FIELDS}
        complete = all(validity.values()) and _valid_matrix_bar(values)
        rows[(code, date)] = {
            "axis_present": True,
            "complete": complete,
            "values": values,
            "validity": validity,
            "row_hash": canonical_hash({"code": code, "date": date, "values": values, "validity": validity}),
        }
    return {"manifest": manifest, "codes": codes, "dates": dates, "rows": rows}


def _load_daily_proofs(
    reader: AuditedReader, manifest_path: str | Path
) -> tuple[dict[tuple[str, str], list[dict[str, Any]]], str]:
    manifest = reader.read_json(manifest_path, component="truth_v2", dataset="task055e_provenance_manifest")
    root = Path(manifest_path).resolve().parent
    entry = (manifest.get("partitions") or {}).get("row_provenance") or {}
    source = root / str(entry.get("path"))
    rows = reader.read_jsonl(source, component="truth_v2", dataset="task055e_row_provenance")
    if reader.rows[-1]["sha256"] != entry.get("sha256"):
        raise TruthV2Error("task055e_provenance_partition_mismatch")
    result: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    opened: dict[str, Mapping[str, Any]] = {}
    opened_shas: dict[str, str] = {}
    governed_root = reader.governed_root
    for row in rows:
        if row.get("api") != "daily" or row.get("record_kind") == "normalized_row":
            continue
        relative = str(row.get("source_relative_path") or "")
        if relative not in opened:
            opened[relative] = reader.read_json(
                governed_root / relative,
                component="truth_v2",
                dataset="indexed_daily_cache",
                request_key=str(row.get("request_fingerprint") or ""),
                declared_start=str((row.get("request_range") or {}).get("start_date") or "") or None,
                declared_end=str((row.get("request_range") or {}).get("end_date") or "") or None,
            )
            opened_shas[relative] = str(reader.rows[-1]["sha256"])
        payload = opened[relative]
        if opened_shas[relative] != row.get("source_file_sha256"):
            raise TruthV2Error("indexed_daily_cache_sha_mismatch")
        proof = _verify_daily_provenance_row(row, payload)
        result[(str(row["ts_code"]), str(row["trade_date"]))].append(proof)
    for key, proofs in result.items():
        unique = {str(proof["proof_hash"]): proof for proof in proofs}
        if len(unique) != len(proofs):
            result[key] = [unique[value] for value in sorted(unique)]
        outcomes = {proof["outcome"] for proof in result[key]}
        if len(outcomes) > 1:
            raise TruthV2Error(f"daily_provenance_conflicting_outcomes:{key[0]}:{key[1]}")
    return result, str(manifest.get("content_hash") or "")


def _load_suspension_sources(
    reader: AuditedReader,
    ledger_path: str | Path,
    cache_root: str | Path,
    target_keys: set[tuple[str, str]],
) -> dict[str, Any]:
    ledger_rows = reader.read_jsonl(
        ledger_path,
        component="truth_v2",
        dataset="suspension_coverage_ledger",
    )
    coverage_ledger_sha256 = reader.rows[-1]["sha256"]
    target_dates: dict[str, set[str]] = defaultdict(set)
    for code, date in target_keys:
        target_dates[code].add(date)
    cache = Path(cache_root).resolve()
    events: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    coverage: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    opened_paths: set[Path] = set()
    for ledger in ledger_rows:
        code = str(ledger.get("ts_code") or "")
        dates = target_dates.get(code)
        if not dates:
            continue
        if ledger.get("status") != "success" or ledger.get("api_name") != "suspend_d":
            raise TruthV2Error("suspension_coverage_ledger_status_invalid")
        for slice_row in ledger.get("slices") or ():
            start = str(slice_row.get("start_date") or "")
            end = str(slice_row.get("end_date") or "")
            intersecting = [date for date in dates if start <= date <= end]
            if not intersecting:
                continue
            path = (cache / str(slice_row.get("cache_key") or "")).resolve()
            if path in opened_paths:
                raise TruthV2Error("suspension_cache_slice_reused_by_multiple_ledger_rows")
            opened_paths.add(path)
            payload = reader.read_json(
                path,
                component="truth_v2",
                dataset="indexed_suspend_cache",
                request_key=str(slice_row.get("request_fingerprint") or ""),
                declared_start=start,
                declared_end=end,
            )
            _validate_suspend_envelope(payload, str(reader.rows[-1]["sha256"]), ledger, slice_row)
            records_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for record in payload.get("records") or ():
                records_by_date[str(record["trade_date"])].append(dict(record))
            source_kind = str(payload.get("schema_version"))
            for date in intersecting:
                matches = records_by_date.get(date, [])
                proof = {
                    "api": "suspend_d",
                    "source_kind": source_kind,
                    "proof_quality": "validated_historical_governed_envelope",
                    "outcome": "matching_row" if matches else "no_matching_row",
                    "request_fingerprint": payload.get("request_fingerprint"),
                    "source_sha256": str(slice_row.get("cache_sha256")),
                    "source_relative_path": str(path.relative_to(reader.governed_root)),
                    "request_start": start,
                    "request_end": end,
                    "provider_endpoint": str(slice_row.get("endpoint") or ledger.get("endpoint") or ""),
                    "provider_api_version": str(slice_row.get("provider_api_version") or ledger.get("provider_api_version") or ""),
                }
                proof["proof_hash"] = canonical_hash(proof)
                coverage[(code, date)].append(proof)
                for record in matches:
                    event = {
                        "ts_code": code,
                        "trade_date": date,
                        "suspend_type": str(record.get("suspend_type") or ""),
                        "suspend_timing": record.get("suspend_timing"),
                        "row_hash": canonical_hash(record),
                        "source_proof_hash": proof["proof_hash"],
                    }
                    event["evidence_hash"] = canonical_hash(event)
                    events[(code, date)].append(event)
    return {
        "events": events,
        "coverage": coverage,
        "coverage_ledger_sha256": coverage_ledger_sha256,
    }


def _classify_cell(
    cell: Mapping[str, Any],
    matrix_bar: Mapping[str, Any] | None,
    events: list[dict[str, Any]],
    coverage: list[dict[str, Any]],
    daily_evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    code = str(cell["ts_code"])
    date = str(cell["trade_date"])
    complete_bar = bool(matrix_bar and matrix_bar.get("complete"))
    s_rows = [row for row in events if row.get("suspend_type") == "S"]
    r_rows = [row for row in events if row.get("suspend_type") == "R"]
    timing_statuses = [_timing_status(row.get("suspend_timing")) for row in s_rows]
    timing_status = _aggregate_timing_status(timing_statuses)
    coverage_status = "complete" if coverage else "missing"
    listed = bool((cell.get("lifecycle") or {}).get("listed", False))
    active = bool((cell.get("lifecycle") or {}).get("active", False))
    corporate_action_conflict = bool(cell.get("corporate_action_validity") is False)
    matrix_inventory_conflict = complete_bar and not bool(cell.get("bar_observed"))
    reason = ""
    modeled_candidate = False
    if corporate_action_conflict:
        state = "LIFECYCLE_OR_CORPORATE_ACTION_CONFLICT"
        reason = "corporate_action_evidence_conflict"
    elif complete_bar and (not listed or not active):
        state = "MATRIX_SOURCE_CONFLICT"
        reason = "complete_daily_bar_conflicts_with_lifecycle_or_inventory"
    elif matrix_inventory_conflict:
        state = "MATRIX_SOURCE_CONFLICT"
        reason = "matrix_complete_bar_conflicts_with_inventory_absence"
    elif complete_bar and s_rows:
        state = "MATRIX_SOURCE_CONFLICT"
        reason = "complete_daily_bar_conflicts_with_suspend_event"
    elif complete_bar:
        state = "TRADED_PRIMARY_BAR"
        reason = (
            "strict_matrix_contains_complete_finite_positive_resume_bar"
            if r_rows
            else "strict_matrix_contains_complete_finite_positive_bar"
        )
    elif not listed or not active:
        state = "LIFECYCLE_TERMINATED"
        reason = "outside_verified_active_lifecycle_requires_settlement_evidence"
    elif s_rows and r_rows:
        state = "SUSPENSION_EVENT_CONFLICT"
        reason = "same_date_contains_suspend_and_resume"
    elif len(s_rows) > 1 and len({(row.get("suspend_timing"), row.get("row_hash")) for row in s_rows}) > 1:
        state = "SUSPENSION_EVENT_CONFLICT"
        reason = "multiple_nonidentical_suspend_rows"
    elif r_rows:
        state = "RESUME_EVENT_WITHOUT_SUSPENSION_EVIDENCE"
        reason = "resume_is_not_positive_suspension_evidence"
    elif s_rows and coverage_status != "complete":
        state = "DATA_SOURCE_GAP"
        reason = "positive_suspend_row_without_complete_source_coverage"
    elif s_rows and timing_status in {"raw_null", "explicit_full_day"}:
        state = "VENDOR_DAILY_NON_TRADING_MODELED_CANDIDATE"
        reason = (
            "exact_positive_s_raw_null_conservative_modeled_candidate"
            if timing_status == "raw_null"
            else "exact_positive_s_explicit_full_day_modeled_candidate"
        )
        modeled_candidate = True
    elif s_rows and timing_status == "explicit_intraday":
        state = "SUSPENSION_INTRADAY_UNSUPPORTED"
        reason = "intraday_suspend_timing_cannot_authorize_daily_stale_mark"
    elif s_rows:
        state = "SUSPENSION_TIMING_UNPARSED"
        reason = "blank_or_unparsed_suspend_timing"
    else:
        state = "DATA_SOURCE_GAP"
        reason = "no_complete_bar_and_no_exact_positive_suspend_event"
    if state not in TRUTH_STATES:
        raise TruthV2Error(f"truth_v2_state_invalid:{state}")
    suspend_type = "S+R" if s_rows and r_rows else "S" if s_rows else "R" if r_rows else "none"
    payload = {
        "ts_code": code,
        "trade_date": date,
        "state": state,
        "reason_code": reason,
        "daily_bar_status": "present_complete" if complete_bar else "absent_or_invalid",
        "matrix_bar": matrix_bar,
        "inventory_bar_observed": bool(cell.get("bar_observed")),
        "suspend_type": suspend_type,
        "suspend_timing_status": timing_status,
        "suspension_events": sorted(events, key=lambda row: (row["suspend_type"], str(row.get("suspend_timing")), row["row_hash"])),
        "suspension_source_coverage": coverage_status,
        "suspension_response_evidence": sorted(coverage, key=lambda row: row["proof_hash"]),
        "daily_response_evidence": sorted(daily_evidence, key=lambda row: row["proof_hash"]),
        "listed": listed,
        "active": active,
        "membership": bool(cell.get("membership", False)),
        "membership_known": bool(cell.get("membership_known", False)),
        "corporate_action_validity": cell.get("corporate_action_validity"),
        "valuation_domain_intersection": bool(cell.get("valuation_closure_domain", False)),
        "regression_probe": bool(cell.get("regression_probe", False)),
        "modeled_stale_candidate": modeled_candidate,
        "stale_mark_authorized": False,
        "stale_mark_authorization_note": "truth_v2_never_authorizes_price_without_prior_close_and_stale_policy",
    }
    payload["evidence_hash"] = canonical_hash(payload)
    return payload


def _verify_daily_provenance_row(row: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    records = payload.get("records") or []
    key = (str(row["ts_code"]), str(row["trade_date"]))
    source_kind = str(row.get("source_kind") or "")
    if row.get("raw_envelope_validated"):
        response = payload.get("response") or {}
        request = payload.get("request") or {}
        if response.get("code") != 0 or response.get("complete") is not True:
            raise TruthV2Error("daily_provenance_response_invalid")
        if response.get("item_count") != len(records) or stable_json_hash(records) != response.get("records_sha256"):
            raise TruthV2Error("daily_provenance_response_integrity_invalid")
        request_range = row.get("request_range") or {}
        if request.get("api_name") != "daily" or request.get("params") is None:
            raise TruthV2Error("daily_provenance_request_invalid")
        start = str(request_range.get("start_date") or "")
        end = str(request_range.get("end_date") or "")
        if not start <= key[1] <= end:
            raise TruthV2Error("daily_provenance_key_outside_range")
        quality = "validated_formal_envelope"
    else:
        metadata = payload.get("metadata") or {}
        if metadata.get("api_name") != "daily" or int(metadata.get("records") or -1) != len(records):
            raise TruthV2Error("legacy_daily_composite_metadata_invalid")
        quality = "legacy_composite_untrusted"
    matching = [record for record in records if (str(record.get("ts_code")), str(record.get("trade_date"))) == key]
    expected_positive = row.get("record_kind") in {"formal_cache_positive_row", "legacy_cache_positive_row"}
    if expected_positive != bool(matching):
        raise TruthV2Error("daily_provenance_row_outcome_mismatch")
    proof = {
        "api": "daily",
        "source_kind": source_kind,
        "proof_quality": quality,
        "outcome": "matching_row" if matching else "no_matching_row",
        "request_fingerprint": row.get("request_fingerprint"),
        "source_sha256": row.get("source_file_sha256"),
        "source_relative_path": row.get("source_relative_path"),
    }
    proof["proof_hash"] = canonical_hash(proof)
    return proof


def _validate_suspend_envelope(
    payload: Mapping[str, Any],
    actual_sha256: str,
    ledger: Mapping[str, Any],
    slice_row: Mapping[str, Any],
) -> None:
    if actual_sha256 != slice_row.get("cache_sha256"):
        raise TruthV2Error("suspension_cache_sha_mismatch")
    if payload.get("schema_version") not in {"tushare_cache_envelope.v2", "tushare_cache_envelope.v3"}:
        raise TruthV2Error("suspension_cache_schema_invalid")
    if payload.get("request") != slice_row.get("normalized_request"):
        raise TruthV2Error("suspension_normalized_request_mismatch")
    if payload.get("request_fingerprint") != slice_row.get("request_fingerprint"):
        raise TruthV2Error("suspension_request_fingerprint_mismatch")
    if payload.get("code_semantic_hash") != ledger.get("code_semantic_hash"):
        raise TruthV2Error("suspension_code_semantic_hash_mismatch")
    response = payload.get("response") or {}
    records = payload.get("records") or []
    if response.get("code") != 0 or response.get("complete") is not True:
        raise TruthV2Error("suspension_response_status_invalid")
    if response.get("item_count") != len(records) or stable_json_hash(records) != response.get("records_sha256"):
        raise TruthV2Error("suspension_response_integrity_invalid")
    if len(records) >= 1000:
        raise TruthV2Error("suspension_historical_response_cap_ambiguous")
    request = payload.get("request") or {}
    if tuple(request.get("fields") or ()) != SUSPEND_FIELDS:
        raise TruthV2Error("suspension_fields_invalid")
    params = request.get("params") or {}
    code = str(params.get("ts_code") or "")
    start = str(params.get("start_date") or "")
    end = str(params.get("end_date") or "")
    seen: set[tuple[Any, ...]] = set()
    for record in records:
        date = str(record.get("trade_date") or "")
        kind = str(record.get("suspend_type") or "")
        timing = record.get("suspend_timing")
        primary_key = (str(record.get("ts_code")), date, kind, timing)
        if primary_key in seen:
            raise TruthV2Error("suspension_primary_key_duplicate")
        seen.add(primary_key)
        if primary_key[0] != code or not start <= date <= end or date > MAX_DATE or kind not in {"S", "R"}:
            raise TruthV2Error("suspension_row_geometry_invalid")


def _timing_status(value: Any) -> str:
    if value is None:
        return "raw_null"
    text = str(value)
    stripped = text.strip()
    if not stripped:
        return "blank"
    normalized = stripped.upper().replace(" ", "")
    if normalized in {item.upper().replace(" ", "") for item in EXPLICIT_FULL_DAY_TIMINGS}:
        return "explicit_full_day"
    parts = [part.strip() for part in stripped.split(",")]
    if parts and all(re.fullmatch(r"\d{2}:\d{2}-\d{2}:\d{2}", part) for part in parts):
        return "explicit_intraday"
    return "unparsed"


def _aggregate_timing_status(values: list[str]) -> str:
    if not values:
        return "none"
    unique = set(values)
    return values[0] if len(unique) == 1 else "conflicting"


def _valid_matrix_bar(values: Mapping[str, Any]) -> bool:
    parsed: dict[str, float] = {}
    for field in MATRIX_DAILY_FIELDS:
        try:
            value = float(values[field])
        except (KeyError, TypeError, ValueError):
            return False
        if not math.isfinite(value):
            return False
        parsed[field] = value
    if any(parsed[field] <= 0 for field in ("open", "high", "low", "close", "pre_close")):
        return False
    if parsed["volume"] < 0 or parsed["amount"] < 0:
        return False
    return parsed["high"] >= max(parsed["open"], parsed["low"], parsed["close"]) and parsed["low"] <= min(
        parsed["open"], parsed["high"], parsed["close"]
    )


def _counter_rows(counter: Counter) -> list[dict[str, Any]]:
    return [
        {"source_kind": key[0], "proof_quality": key[1], "count": count}
        for key, count in sorted(counter.items())
    ]


def _publish_truth(
    *,
    output_root: Path,
    governed_root: Path,
    rows: list[dict[str, Any]],
    cross_rows: list[dict[str, Any]],
    summary: Mapping[str, Any],
    lineage: Mapping[str, Any],
) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".task055f.truth.", dir=output_root))
    try:
        rows_path = staging / "truth_v2_rows.jsonl"
        cross_path = staging / "truth_v2_cross_table.json"
        rows_path.write_text(
            "".join(json.dumps(row, sort_keys=True, separators=(",", ":"), default=str) + "\n" for row in rows),
            encoding="utf-8",
        )
        write_artifact_sidecar(
            rows_path,
            {
                "artifact_type": "task055f_truth_v2_rows",
                "schema_version": "1.0",
                "producer": "task_055_f.truth_v2",
                "created_at": "1970-01-01T00:00:00Z",
                "extra": {"record_count": len(rows)},
            },
        )
        cross_path.write_text(json.dumps(cross_rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        partitions = {
            "rows": _partition(rows_path),
            "cross_table": _partition(cross_path),
        }
        semantic = {
            "schema_version": TRUTH_SCHEMA,
            "status": "published",
            "review_version": "task055f_strict_suspend_type_and_timing_v2",
            "max_date": MAX_DATE,
            **dict(summary),
            "lineage": dict(lineage),
            "partitions": partitions,
            "certification_blockers": [
                "suspension_timing_semantics_uncertified",
                "vendor_historical_revision_risk",
            ],
        }
        content_hash = canonical_hash(semantic)
        generation_id = f"truth_v2_{content_hash[:24]}"
        manifest = semantic | {"content_hash": content_hash, "generation_id": generation_id}
        (staging / "truth_v2_manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        target = output_root / "generations" / generation_id
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(staging)
        else:
            os.replace(staging, target)
        _atomic_json(
            output_root / "current.json",
            {
                "generation_id": generation_id,
                "content_hash": content_hash,
                "manifest": f"generations/{generation_id}/truth_v2_manifest.json",
            },
        )
        return manifest | {
            "manifest_path": str(target / "truth_v2_manifest.json"),
            "governed_root_relative_output": str(target.relative_to(governed_root))
            if governed_root in target.resolve().parents
            else None,
        }
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _partition(path: Path) -> dict[str, Any]:
    return {"path": path.name, "sha256": sha256_file(path), "size_bytes": path.stat().st_size}


def _resolve_manifest(path: str | Path) -> Path:
    value = Path(path)
    if value.is_file():
        return value
    pointer = value / "current.json"
    if pointer.is_file():
        payload = json.loads(pointer.read_text(encoding="utf-8"))
        return value / str(payload["manifest"])
    candidate = value / "truth_v2_manifest.json"
    if candidate.is_file():
        return candidate
    raise TruthV2Error("truth_v2_manifest_missing")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)

"""Independent semantic verifier for Task 055-F evidence generations."""

from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from data_pipeline.ashare.request_normalization import stable_json_hash
from task_055_a.run import SCENARIO_NAMES

from .causal import validate_causal_frontier
from .contracts import (
    EXPLICIT_FULL_DAY_TIMINGS,
    MAX_DATE,
    MAX_STALE_AGE_TRADE_DAYS,
    MODELED_STALE_METHOD,
    OFFICIAL_CLOSE_METHOD,
    OFFICIAL_OPEN_METHOD,
    SEMANTIC_VERIFICATION_SCHEMA,
)
from .read_ledger import AuditedReader, canonical_hash, validate_read_ledger
from .truth_v2 import validate_truth_v2


class SemanticVerificationError(RuntimeError):
    pass


def verify_task055f_semantics(
    *,
    truth_v2_manifest: str | Path,
    governed_root: str | Path,
    matrix_root: str | Path,
    read_ledger_manifest: str | Path,
    output_root: str | Path,
    causal_manifest: str | Path | None = None,
) -> dict[str, Any]:
    truth = validate_truth_v2(truth_v2_manifest)
    read_ledger = validate_read_ledger(read_ledger_manifest, governed_root=governed_root)
    governed = Path(governed_root).resolve()
    matrix = Path(matrix_root).resolve()
    verifier_reader = AuditedReader(governed)
    matrix_context = _matrix_rows(matrix, truth["records"], verifier_reader)
    matrix_rows = matrix_context["rows"]
    dates = matrix_context["dates"]
    source_payloads: dict[str, Mapping[str, Any]] = {}
    state_counts = Counter()
    anchor_counts = Counter()
    anchor_rows = []
    for row in truth["records"]:
        key = (str(row["ts_code"]), str(row["trade_date"]))
        expected_bar = matrix_rows[key]
        if row.get("daily_bar_status") == "present_complete" != bool(expected_bar["complete"]):
            raise SemanticVerificationError("independent_matrix_bar_status_mismatch")
        stored_bar = row.get("matrix_bar") or {}
        if (
            stored_bar.get("axis_present") != expected_bar.get("axis_present")
            or stored_bar.get("complete") != expected_bar.get("complete")
            or stored_bar.get("validity") != expected_bar.get("validity")
            or stored_bar.get("row_hash") != expected_bar.get("row_hash")
        ):
            raise SemanticVerificationError("independent_matrix_row_mismatch")
        for proof in list(row.get("daily_response_evidence") or ()) + list(
            row.get("suspension_response_evidence") or ()
        ):
            relative = str(proof.get("source_relative_path") or "")
            if not relative:
                raise SemanticVerificationError("source_proof_relative_path_missing")
            if relative not in source_payloads:
                path = (governed / relative).resolve()
                if governed not in path.parents or not path.is_file():
                    raise SemanticVerificationError("source_proof_file_or_sha_invalid")
                source_payloads[relative] = verifier_reader.read_json(
                    path,
                    component="task055f_independent_verifier",
                    dataset=str(proof.get("api") or "source_envelope"),
                    request_key=str(proof.get("request_fingerprint") or ""),
                    declared_start=str(proof.get("request_start") or "") or None,
                    declared_end=str(proof.get("request_end") or "") or None,
                )
                if verifier_reader.rows[-1]["sha256"] != proof.get("source_sha256"):
                    raise SemanticVerificationError("source_proof_file_or_sha_invalid")
            _verify_source_outcome(row, proof, source_payloads[relative])
        expected_state = _independent_state(row)
        if expected_state != row.get("state"):
            raise SemanticVerificationError(
                f"independent_truth_state_mismatch:{row['ts_code']}:{row['trade_date']}:{expected_state}:{row.get('state')}"
            )
        state_counts[expected_state] += 1
        if row.get("modeled_stale_candidate"):
            anchor = _independent_anchor(row, matrix_context)
            anchor_rows.append(anchor)
            anchor_counts[anchor["status"]] += 1
    if dict(sorted(state_counts.items())) != truth.get("state_counts"):
        raise SemanticVerificationError("independent_truth_state_counts_mismatch")

    causal_summary = None
    if causal_manifest is not None:
        causal_summary = _verify_causal_semantics(causal_manifest, truth, dates)
    verifier_ledger = verifier_reader.publish(Path(output_root) / "verifier_read_ledger")
    validate_read_ledger(verifier_ledger["manifest_path"], governed_root=governed)
    semantic = {
        "schema_version": SEMANTIC_VERIFICATION_SCHEMA,
        "status": "passed",
        "truth_v2_content_hash": truth["content_hash"],
        "read_ledger_content_hash": read_ledger["content_hash"],
        "record_count": truth["record_count"],
        "source_file_count": len(source_payloads),
        "state_counts": dict(sorted(state_counts.items())),
        "anchor_counts": dict(sorted(anchor_counts.items())),
        "anchor_root": canonical_hash(anchor_rows),
        "causal": causal_summary,
        "max_read_date": read_ledger.get("max_read_date"),
        "prospective_holdout_accessed": read_ledger.get("prospective_holdout_accessed"),
        "verifier_read_ledger_content_hash": verifier_ledger["content_hash"],
        "verifier_read_ledger_manifest": str(
            Path(verifier_ledger["manifest_path"]).resolve().relative_to(Path(output_root).resolve())
        ),
        "verifier_max_read_date": verifier_ledger.get("max_read_date"),
        "verifier_prospective_holdout_accessed": verifier_ledger.get("prospective_holdout_accessed"),
    }
    return _publish(Path(output_root), semantic)


def validate_semantic_verification(
    path: str | Path,
    *,
    governed_root: str | Path | None = None,
) -> dict[str, Any]:
    manifest_path = Path(path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SEMANTIC_VERIFICATION_SCHEMA or manifest.get("status") != "passed":
        raise SemanticVerificationError("semantic_verification_manifest_invalid")
    semantic = {key: value for key, value in manifest.items() if key not in {"content_hash", "generation_id"}}
    if canonical_hash(semantic) != manifest.get("content_hash"):
        raise SemanticVerificationError("semantic_verification_content_hash_mismatch")
    if governed_root is not None:
        verification_root = manifest_path.parents[2]
        ledger_path = (verification_root / str(manifest.get("verifier_read_ledger_manifest") or "")).resolve()
        if verification_root.resolve() not in ledger_path.parents:
            raise SemanticVerificationError("semantic_verifier_read_ledger_path_invalid")
        ledger = validate_read_ledger(ledger_path, governed_root=governed_root)
        if (
            ledger.get("content_hash") != manifest.get("verifier_read_ledger_content_hash")
            or ledger.get("max_read_date") != manifest.get("verifier_max_read_date")
            or ledger.get("prospective_holdout_accessed") is not False
            or manifest.get("verifier_prospective_holdout_accessed") is not False
        ):
            raise SemanticVerificationError("semantic_verifier_read_ledger_mismatch")
    return manifest | {"manifest_path": str(manifest_path)}


def _matrix_rows(
    matrix: Path,
    truth_rows: list[Mapping[str, Any]],
    reader: AuditedReader,
) -> dict[str, Any]:
    manifest = reader.read_json(
        matrix / "task_052a_strict_matrix_manifest.json",
        component="task055f_independent_verifier",
        dataset="strict_matrix_manifest",
    )
    codes = reader.read_json(
        matrix / "ts_codes.json",
        component="task055f_independent_verifier",
        dataset="matrix_stock_axis",
    )
    dates = reader.read_json(
        matrix / "trade_dates.json",
        component="task055f_independent_verifier",
        dataset="matrix_date_axis",
    )
    code_index = {code: index for index, code in enumerate(codes)}
    date_index = {date: index for index, date in enumerate(dates)}
    partitions = manifest.get("partition_sha256") or {}
    arrays = {}
    for field in ("open", "high", "low", "close", "pre_close", "volume", "amount"):
        for name in (f"{field}.npy", f"{field}_validity.npy"):
            reader.record_binary(
                matrix / name,
                component="task055f_independent_verifier",
                dataset=f"matrix_partition:{name}",
                declared_start=str(dates[0]),
                declared_end=str(dates[-1]),
            )
            if reader.rows[-1]["sha256"] != partitions.get(name):
                raise SemanticVerificationError(f"independent_matrix_partition_mismatch:{name}")
        arrays[field] = np.load(matrix / f"{field}.npy", mmap_mode="r", allow_pickle=False)
        arrays[f"{field}:valid"] = np.load(matrix / f"{field}_validity.npy", mmap_mode="r", allow_pickle=False)
    result = {}
    for row in truth_rows:
        code, date = str(row["ts_code"]), str(row["trade_date"])
        if code not in code_index or date not in date_index:
            result[(code, date)] = {"axis_present": False, "complete": False}
            continue
        asset, day = code_index[code], date_index[date]
        values = {field: float(arrays[field][asset, day]) for field in ("open", "high", "low", "close", "pre_close", "volume", "amount")}
        validity = {field: bool(arrays[f"{field}:valid"][asset, day]) for field in values}
        complete = all(validity.values()) and _bar_valid(values)
        result[(code, date)] = {
            "axis_present": True,
            "complete": complete,
            "values": values,
            "validity": validity,
            "row_hash": canonical_hash({"code": code, "date": date, "values": values, "validity": validity}),
        }
    prior_close = np.full(arrays["close:valid"].shape, -1, dtype=np.int32)
    last = np.full(len(codes), -1, dtype=np.int32)
    for day in range(len(dates)):
        prior_close[:, day] = last
        valid = np.asarray(arrays["close:valid"][:, day], dtype=bool)
        finite_positive = np.isfinite(np.asarray(arrays["close"][:, day], dtype=float)) & (
            np.asarray(arrays["close"][:, day], dtype=float) > 0
        )
        last[valid & finite_positive] = day
    return {
        "rows": result,
        "codes": codes,
        "dates": dates,
        "code_index": code_index,
        "date_index": date_index,
        "close": arrays["close"],
        "close_valid": arrays["close:valid"],
        "prior_close": prior_close,
    }


def _verify_source_outcome(row: Mapping[str, Any], proof: Mapping[str, Any], payload: Mapping[str, Any]) -> None:
    records = payload.get("records") or []
    response = payload.get("response") or {}
    if proof.get("proof_quality") != "legacy_composite_untrusted":
        if response.get("code") != 0 or response.get("complete") is not True:
            raise SemanticVerificationError("source_response_status_invalid")
        if response.get("item_count") != len(records) or stable_json_hash(records) != response.get("records_sha256"):
            raise SemanticVerificationError("source_response_integrity_invalid")
    key = (str(row["ts_code"]), str(row["trade_date"]))
    matches = [record for record in records if (str(record.get("ts_code")), str(record.get("trade_date"))) == key]
    expected = proof.get("outcome") == "matching_row"
    if bool(matches) != expected:
        raise SemanticVerificationError("source_proof_outcome_mismatch")
    if proof.get("api") == "suspend_d" and matches:
        event_root = sorted(
            (str(item.get("suspend_type")), item.get("suspend_timing"), canonical_hash(item)) for item in matches
        )
        stored_root = sorted(
            (str(item.get("suspend_type")), item.get("suspend_timing"), item.get("row_hash"))
            for item in row.get("suspension_events") or ()
        )
        if event_root != stored_root:
            raise SemanticVerificationError("source_suspend_event_mismatch")


def _independent_state(row: Mapping[str, Any]) -> str:
    complete_bar = row.get("daily_bar_status") == "present_complete"
    events = list(row.get("suspension_events") or ())
    s_rows = [event for event in events if event.get("suspend_type") == "S"]
    r_rows = [event for event in events if event.get("suspend_type") == "R"]
    timing = {_timing(event.get("suspend_timing")) for event in s_rows}
    if row.get("corporate_action_validity") is False:
        return "LIFECYCLE_OR_CORPORATE_ACTION_CONFLICT"
    if not row.get("listed") or not row.get("active"):
        return "LIFECYCLE_TERMINATED"
    if complete_bar and (events or row.get("inventory_bar_observed") is False):
        return "MATRIX_SOURCE_CONFLICT"
    if complete_bar:
        return "TRADED_PRIMARY_BAR"
    if s_rows and r_rows:
        return "SUSPENSION_EVENT_CONFLICT"
    if len(s_rows) > 1 and len({(item.get("suspend_timing"), item.get("row_hash")) for item in s_rows}) > 1:
        return "SUSPENSION_EVENT_CONFLICT"
    if r_rows:
        return "RESUME_EVENT_WITHOUT_SUSPENSION_EVIDENCE"
    if s_rows and row.get("suspension_source_coverage") != "complete":
        return "DATA_SOURCE_GAP"
    if s_rows and timing <= {"raw_null", "explicit_full_day"} and len(timing) == 1:
        return "VENDOR_DAILY_NON_TRADING_MODELED_CANDIDATE"
    if s_rows and timing == {"explicit_intraday"}:
        return "SUSPENSION_INTRADAY_UNSUPPORTED"
    if s_rows:
        return "SUSPENSION_TIMING_UNPARSED"
    return "DATA_SOURCE_GAP"


def _independent_anchor(row: Mapping[str, Any], matrix: Mapping[str, Any]) -> dict[str, Any]:
    dates = matrix["dates"]
    date_index = matrix["date_index"]
    code_index = matrix["code_index"]
    code, date = str(row["ts_code"]), str(row["trade_date"])
    day = date_index.get(date, -1)
    asset = code_index.get(code, -1)
    source = -1
    source_value = None
    if asset >= 0:
        source = int(matrix["prior_close"][asset, day])
        if source >= 0:
            source_value = float(matrix["close"][asset, source])
    if source < 0:
        status = "no_prior_finite_positive_close"
    elif day - source > MAX_STALE_AGE_TRADE_DAYS:
        status = "stale_age_gt_250"
    else:
        status = "anchor_available_within_policy"
    return {
        "ts_code": code,
        "trade_date": date,
        "status": status,
        "source_date": dates[source] if source >= 0 else None,
        "source_value": source_value,
        "stale_age_trade_days": day - source if source >= 0 else None,
        "evidence_id": row.get("evidence_hash"),
    }


def _verify_causal_semantics(path: str | Path, truth: Mapping[str, Any], dates: list[str]) -> dict[str, Any]:
    causal = validate_causal_frontier(path)
    exact_ids = list(causal["exact20_ids"])
    expected_pairs = sorted((factor_id, scenario) for factor_id in exact_ids for scenario in SCENARIO_NAMES)
    actual_pairs = sorted((row["factor_id"], row["scenario"]) for row in causal["run_rows"])
    if len(exact_ids) != 20 or actual_pairs != expected_pairs or len(actual_pairs) != len(set(actual_pairs)):
        raise SemanticVerificationError("independent_causal_cartesian_invalid")
    truth_by_key = {(row["ts_code"], row["trade_date"]): row for row in truth["records"]}
    date_index = {date: index for index, date in enumerate(dates)}
    modeled_used = 0
    for mark in causal["held_marks"]:
        date = str(mark["trade_date"])
        source = str(mark["source_date"])
        method = str(mark["method"])
        if int(mark["shares"]) <= 0 or date not in date_index or source not in date_index or source > date:
            raise SemanticVerificationError("independent_held_mark_axis_or_shares_invalid")
        if method == MODELED_STALE_METHOD:
            row = truth_by_key.get((str(mark["ts_code"]), date))
            if not row or not row.get("modeled_stale_candidate") or mark.get("evidence_id") != row.get("evidence_hash"):
                raise SemanticVerificationError("independent_modeled_held_mark_unauthorized")
            age = date_index[date] - date_index[source]
            if age != int(mark["stale_age_trade_days"]) or not 0 < age <= MAX_STALE_AGE_TRADE_DAYS:
                raise SemanticVerificationError("independent_modeled_held_mark_age_invalid")
            modeled_used += 1
        elif method in {OFFICIAL_OPEN_METHOD, OFFICIAL_CLOSE_METHOD}:
            if source != date or int(mark["stale_age_trade_days"]) != 0:
                raise SemanticVerificationError("independent_official_held_mark_metadata_invalid")
        else:
            raise SemanticVerificationError("independent_held_mark_method_invalid")
    missing = sorted(
        {
            (row["blocker"]["ts_code"], row["blocker"]["trade_date"])
            for row in causal["run_rows"]
            if (row.get("blocker") or {}).get("code") == "held_position_mark_unavailable"
        }
    )
    if canonical_hash(missing) != causal.get("missing_key_root"):
        raise SemanticVerificationError("independent_missing_key_root_mismatch")
    if modeled_used != causal.get("authorized_modeled_held_mark_count"):
        raise SemanticVerificationError("independent_modeled_held_mark_count_mismatch")
    return {
        "content_hash": causal["content_hash"],
        "run_count": causal["run_count"],
        "held_mark_root": causal["held_mark_root"],
        "missing_key_root": causal["missing_key_root"],
        "round_one_frontier_count": causal["round_one_frontier_count"],
        "authorized_modeled_held_mark_count": modeled_used,
    }


def _timing(value: Any) -> str:
    if value is None:
        return "raw_null"
    text = str(value).strip()
    if not text:
        return "blank"
    normalized = text.upper().replace(" ", "")
    full_day = {item.upper().replace(" ", "") for item in EXPLICIT_FULL_DAY_TIMINGS}
    if normalized in full_day:
        return "explicit_full_day"
    if all(__import__("re").fullmatch(r"\d{2}:\d{2}-\d{2}:\d{2}", part.strip()) for part in text.split(",")):
        return "explicit_intraday"
    return "unparsed"


def _bar_valid(values: Mapping[str, float]) -> bool:
    if any(not math.isfinite(value) for value in values.values()):
        return False
    if any(values[field] <= 0 for field in ("open", "high", "low", "close", "pre_close")):
        return False
    if values["volume"] < 0 or values["amount"] < 0:
        return False
    return values["high"] >= max(values["open"], values["low"], values["close"]) and values["low"] <= min(
        values["open"], values["high"], values["close"]
    )


def _publish(root: Path, semantic: Mapping[str, Any]) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    content_hash = canonical_hash(semantic)
    generation_id = f"semantic_verification_{content_hash[:24]}"
    manifest = dict(semantic) | {"content_hash": content_hash, "generation_id": generation_id}
    staging = Path(tempfile.mkdtemp(prefix=".task055f.verify.", dir=root))
    try:
        (staging / "semantic_verification.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        target = root / "generations" / generation_id
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(staging)
        else:
            os.replace(staging, target)
        return manifest | {"manifest_path": str(target / "semantic_verification.json")}
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

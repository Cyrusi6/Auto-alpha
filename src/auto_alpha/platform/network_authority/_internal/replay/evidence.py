"""Authoritative Task 055-C security-date truth reconstruction."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from data_pipeline.ashare.request_normalization import stable_json_hash
from data_pipeline.ashare.validators import is_valid_ts_code, is_valid_yyyymmdd

SCHEMA = "task055c_security_date_truth_table_v1"
POINTER_SCHEMA = "task055c_security_date_truth_pointer_v1"
MAX_DATE = "20260630"
DAILY_REQUIRED = ("ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "vol", "amount")
SUSPEND_REQUIRED = ("ts_code", "trade_date", "suspend_timing", "suspend_type")
MODELED = "VENDOR_DAILY_NON_TRADING_MODELED"
UNRESOLVED = "DATA_SOURCE_GAP"


class Task055CEvidenceError(RuntimeError):
    pass


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def build_truth_table(
    *, inventory_manifest: str | Path, suspension_records: str | Path,
    suspension_coverage_ledger: str | Path, suspension_cache_root: str | Path,
    output_root: str | Path, trade_dates: Iterable[str] | None = None,
    review_version: str = "task055c_vendor_semantics_v1",
) -> dict[str, Any]:
    inventory_path = Path(inventory_manifest)
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    cells_path = inventory_path.parent / inventory["partitions"]["cells"]["path"]
    if sha256_file(cells_path) != inventory["partitions"]["cells"]["sha256"]:
        raise Task055CEvidenceError("inventory_cells_sha_mismatch")
    suspensions = _load_suspensions(Path(suspension_records))
    coverage = _load_and_verify_coverage(Path(suspension_coverage_ledger), Path(suspension_cache_root))
    rows: list[dict[str, Any]] = []
    cross = Counter()
    state_counts = Counter()
    residual_reasons = Counter()
    for cell in _read_jsonl(cells_path):
        key = (str(cell["ts_code"]), str(cell["trade_date"]))
        events = suspensions.get(key, ())
        event_type = _event_type(events)
        timing = _timing_class(events)
        bar_state = _bar_state(cell)
        source = coverage.get(key[0], {"coverage_state": "incomplete"})
        lifecycle_conflict = _lifecycle_conflict(cell)
        valuation = bool(cell.get("valuation_closure_domain"))
        state, reason = _classify(event_type, timing, bar_state, source["coverage_state"], lifecycle_conflict)
        row = {
            "ts_code": key[0], "trade_date": key[1], "suspend_type": event_type,
            "suspend_timing": timing, "daily_bar": bar_state,
            "source_coverage": source["coverage_state"], "valuation_domain_intersection": valuation,
            "lifecycle_corporate_action_conflict": lifecycle_conflict,
            "state": state, "reason_code": reason,
            "suspension_rows": [dict(item) for item in events],
            "transport_proofs": list(source.get("proofs", ())),
            "previous_legal_bar": cell.get("previous_legal_bar"),
            "next_legal_bar": cell.get("next_legal_bar"),
            "raw_bar": cell.get("raw_bar"), "raw_field_validity": cell.get("raw_field_validity"),
            "membership": bool(cell.get("membership")), "membership_known": bool(cell.get("membership_known")),
            "listed": bool((cell.get("lifecycle") or {}).get("listed")),
            "active": bool((cell.get("lifecycle") or {}).get("active")),
            "corporate_action": cell.get("corporate_action_validity"),
            "inventory_reasons": list(cell.get("reasons") or ()),
            "regression_probe": bool(cell.get("regression_probe")),
        }
        row["evidence_hash"] = canonical_hash({key: value for key, value in row.items() if key != "evidence_hash"})
        rows.append(row)
        cross[(event_type, timing, bar_state, source["coverage_state"], valuation, lifecycle_conflict)] += 1
        state_counts[state] += 1
        if not events:
            residual_reasons["no_event_unexplained_gap" if "matrix_unexplained_data_gap" in row["inventory_reasons"] else "valuation_closure_missing_bar_without_event"] += 1
    rows.sort(key=lambda row: (row["ts_code"], row["trade_date"]))
    if len(rows) != int(inventory.get("cell_count", -1)) or sum(cross.values()) != len(rows):
        raise Task055CEvidenceError(f"truth_table_total_mismatch:{len(rows)}")
    explanation = {
        "suspension_event_missing_bar": sum(1 for row in rows if row["suspend_type"] != "none" and row["daily_bar"] == "absent"),
        "no_event_unexplained_gap": residual_reasons["no_event_unexplained_gap"],
        "residual_2773": residual_reasons["valuation_closure_missing_bar_without_event"],
        "residual_explanation": "valuation closure cells added for continuous holdings after membership exit; no suspension event and no observed bar",
    }
    if sum((explanation["suspension_event_missing_bar"], explanation["no_event_unexplained_gap"], explanation["residual_2773"])) != len(rows):
        raise Task055CEvidenceError(f"parent_key_set_conservation_failed:{explanation}")
    calendar = sorted(str(value) for value in (trade_dates or ()))
    episodes = _episodes(rows, {value: index for index, value in enumerate(calendar)})
    return _publish(Path(output_root), rows, episodes, cross, state_counts, explanation, inventory, review_version)


def validate_truth_table(path: str | Path, *, reclassify: bool = True) -> dict[str, Any]:
    manifest_path = _resolve(Path(path))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA or manifest.get("status") != "published":
        raise Task055CEvidenceError("truth_manifest_invalid")
    for entry in manifest["partitions"].values():
        artifact = manifest_path.parent / entry["path"]
        if not artifact.is_file() or sha256_file(artifact) != entry["sha256"]:
            raise Task055CEvidenceError("truth_partition_sha_mismatch")
    rows = _read_jsonl(manifest_path.parent / manifest["partitions"]["rows"]["path"])
    if len(rows) != manifest["record_count"] or canonical_hash([(r["ts_code"], r["trade_date"]) for r in rows]) != manifest["key_hash"]:
        raise Task055CEvidenceError("truth_key_inventory_mismatch")
    if reclassify:
        for row in rows:
            state, reason = _classify(row["suspend_type"], row["suspend_timing"], row["daily_bar"], row["source_coverage"], row["lifecycle_corporate_action_conflict"])
            if state != row["state"] or reason != row["reason_code"]:
                raise Task055CEvidenceError(f"truth_reclassification_mismatch:{row['ts_code']}:{row['trade_date']}")
            expected = canonical_hash({key: value for key, value in row.items() if key != "evidence_hash"})
            if expected != row["evidence_hash"]:
                raise Task055CEvidenceError("truth_row_evidence_hash_mismatch")
    semantic = {key: value for key, value in manifest.items() if key not in {"content_hash", "generation_id"}}
    if canonical_hash(semantic) != manifest["content_hash"]:
        raise Task055CEvidenceError("truth_content_hash_mismatch")
    return manifest | {"manifest_path": str(manifest_path), "records": rows}


def _load_suspensions(path: Path) -> dict[tuple[str, str], tuple[dict[str, Any], ...]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    seen = set()
    for row in _read_jsonl(path):
        code, date, kind = str(row.get("ts_code")), str(row.get("trade_date")), str(row.get("suspend_type"))
        if not is_valid_ts_code(code) or not is_valid_yyyymmdd(date) or date > MAX_DATE or kind not in {"S", "R"}:
            raise Task055CEvidenceError("suspension_record_contract_invalid")
        timing = row.get("suspend_timing")
        pk = (code, date, kind, timing)
        if pk in seen:
            raise Task055CEvidenceError("suspension_primary_key_duplicate")
        seen.add(pk); grouped[(code, date)].append({"ts_code": code, "trade_date": date, "suspend_type": kind, "suspend_timing": timing})
    return {key: tuple(sorted(value, key=lambda row: (row["suspend_type"], str(row["suspend_timing"])))) for key, value in grouped.items()}


def _load_and_verify_coverage(path: Path, cache_root: Path) -> dict[str, dict[str, Any]]:
    result = {}
    for row in _read_jsonl(path):
        code = str(row.get("ts_code")); proofs=[]; complete = row.get("status") == "success"
        for item in row.get("slices") or ():
            cache_path = cache_root / str(item.get("cache_key"))
            if not cache_path.is_file() or sha256_file(cache_path) != item.get("cache_sha256"):
                complete = False; continue
            envelope = json.loads(cache_path.read_text(encoding="utf-8"))
            request = envelope.get("request") or {}
            response = envelope.get("response") or {}
            records = envelope.get("records") or []
            normalized = item.get("normalized_request") or {}
            if request != normalized or envelope.get("request_fingerprint") != item.get("request_fingerprint"):
                complete = False
            if request.get("api_name") != "suspend_d" or str((request.get("params") or {}).get("ts_code")) != code:
                complete = False
            if tuple(request.get("fields") or ()) != SUSPEND_REQUIRED or set(response.get("fields") or ()) != set(SUSPEND_REQUIRED):
                complete = False
            if response.get("item_count") != len(records) or stable_json_hash(records) != response.get("records_sha256"):
                complete = False
            if len(records) >= 1000 or response.get("complete") is not True:
                complete = False
            proofs.append({"cache_sha256": item.get("cache_sha256"), "transport_hash": envelope.get("request_fingerprint"), "item_count": len(records)})
        if str(row.get("requested_end_date")) != MAX_DATE or not proofs:
            complete = False
        result[code] = {"coverage_state": "complete" if complete else "incomplete", "proofs": proofs}
    return result


def _event_type(events: Iterable[Mapping[str, Any]]) -> str:
    kinds = {str(row.get("suspend_type")) for row in events}
    return "S+R" if kinds == {"S", "R"} else next(iter(kinds), "none")


def _timing_class(events: Iterable[Mapping[str, Any]]) -> str:
    values = [row.get("suspend_timing") for row in events]
    if not values: return "none"
    if all(value is None for value in values): return "null"
    if any(isinstance(value, str) and not value.strip() for value in values): return "blank"
    text = "|".join(str(value).lower() for value in values)
    if any(token in text for token in ("全天", "full", "all day")): return "explicit-full-day"
    if any(token in text for token in ("盘中", "morning", "afternoon", "intraday", "开市后")): return "explicit-intraday"
    return "unparsed"


def _bar_state(cell: Mapping[str, Any]) -> str:
    raw = cell.get("raw_bar") or {}; validity = cell.get("raw_field_validity") or {}
    present = any(raw.get(field) is not None for field in ("open", "high", "low", "close", "vol", "amount"))
    complete = all(bool(validity.get(field)) for field in ("open", "high", "low", "close", "vol", "amount"))
    return "present-complete" if present and complete else "present-invalid" if present else "absent"


def _lifecycle_conflict(cell: Mapping[str, Any]) -> bool:
    return bool(cell.get("conflicting_evidence")) or cell.get("trade_calendar_session") is not True


def _classify(event_type: str, timing: str, bar: str, coverage: str, conflict: bool) -> tuple[str, str]:
    if conflict: return "CONFLICT", "lifecycle_or_corporate_action_conflict"
    if bar == "present-complete" and event_type == "none": return "TRADED_PRIMARY_BAR", "complete_primary_bar"
    if bar != "absent": return "TRADED_SOURCE_CONFLICT", "bar_or_suspension_conflict"
    if coverage == "complete" and event_type == "S" and timing in {"null", "explicit-full-day"}:
        return MODELED, "exact_positive_s_without_bar_conservative_modeled_no_trade"
    if event_type == "R": return UNRESOLVED, "resume_only_without_bar"
    if event_type == "S+R": return "CONFLICT", "same_day_suspend_resume_conflict"
    if event_type == "S": return UNRESOLVED, f"suspension_semantics_not_eligible:{timing}:{coverage}"
    return UNRESOLVED, "no_bar_no_positive_s_evidence"


def _episodes(rows: list[dict[str, Any]], date_index: Mapping[str, int]) -> list[dict[str, Any]]:
    grouped=defaultdict(list)
    for row in rows: grouped[row["ts_code"]].append(row)
    result=[]
    for code, items in grouped.items():
        current=[]; previous_index=None; signature=None
        for row in sorted(items,key=lambda x:x["trade_date"]):
            sig=(row["state"],row["reason_code"],row["valuation_domain_intersection"])
            position=date_index.get(row["trade_date"])
            contiguous=previous_index is not None and position is not None and position == previous_index + 1
            if current and (sig != signature or not contiguous):
                result.append(_episode(code,current,signature)); current=[]
            current.append(row); previous_index=position; signature=sig
        if current: result.append(_episode(code,current,signature))
    return result


def _episode(code, rows, signature):
    payload={"ts_code":code,"start_date":rows[0]["trade_date"],"end_date":rows[-1]["trade_date"],"cell_count":len(rows),"state":signature[0],"reason_code":signature[1],"valuation_domain_intersection":signature[2]}
    return payload | {"episode_id":canonical_hash(payload)[:24]}


def _publish(root, rows, episodes, cross, states, explanation, inventory, review_version):
    root.mkdir(parents=True,exist_ok=True); staging=Path(tempfile.mkdtemp(prefix=".task055c_truth.",dir=root))
    try:
        _write_jsonl(staging/"truth_rows.jsonl",rows); _write_jsonl(staging/"episodes.jsonl",episodes)
        cross_rows=[{"suspend_type":k[0],"suspend_timing":k[1],"daily_bar":k[2],"source_coverage":k[3],"valuation_domain_intersection":k[4],"lifecycle_corporate_action_conflict":k[5],"count":v} for k,v in sorted(cross.items(),key=str)]
        _write_json(staging/"cross_table.json",cross_rows)
        partitions={name:{"path":name,"sha256":sha256_file(staging/name),"size_bytes":(staging/name).stat().st_size} for name in ("truth_rows.jsonl","episodes.jsonl","cross_table.json")}
        semantic={"schema_version":SCHEMA,"status":"published","review_version":review_version,"record_count":len(rows),"episode_count":len(episodes),"key_hash":canonical_hash([(r["ts_code"],r["trade_date"]) for r in rows]),"state_counts":dict(sorted(states.items())),"reconciliation":explanation,"valuation_domain_count":sum(r["valuation_domain_intersection"] for r in rows),"inventory_content_hash":inventory["content_hash"],"partitions":{"rows":partitions["truth_rows.jsonl"],"episodes":partitions["episodes.jsonl"],"cross_table":partitions["cross_table.json"]}}
        content=canonical_hash(semantic); generation=f"security_date_truth_{content[:24]}"; manifest=semantic|{"content_hash":content,"generation_id":generation}
        _write_json(staging/"truth_manifest.json",manifest); target=root/"generations"/generation; target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists(): shutil.rmtree(staging)
        else: os.replace(staging,target)
        _atomic_json(root/"current.json",{"schema_version":POINTER_SCHEMA,"generation_id":generation,"content_hash":content,"manifest":f"generations/{generation}/truth_manifest.json"})
        return manifest|{"manifest_path":str(target/"truth_manifest.json"),"root":str(target)}
    except Exception: shutil.rmtree(staging,ignore_errors=True); raise


def _resolve(path):
    if path.is_file(): return path
    pointer=path/"current.json"
    if pointer.is_file(): return path/json.loads(pointer.read_text())["manifest"]
    return path/"truth_manifest.json"

def _read_jsonl(path):
    with path.open(encoding="utf-8") as handle: return [json.loads(line) for line in handle if line.strip()]
def _write_jsonl(path, rows):
    with path.open("w",encoding="utf-8") as handle:
        for row in rows: handle.write(json.dumps(row,sort_keys=True,separators=(",",":"),default=str)+"\n")
def _write_json(path,payload): path.write_text(json.dumps(payload,indent=2,sort_keys=True,default=str)+"\n",encoding="utf-8")
def _atomic_json(path,payload):
    temp=path.with_name(f".{path.name}.{os.getpid()}.tmp"); _write_json(temp,payload); os.replace(temp,path)

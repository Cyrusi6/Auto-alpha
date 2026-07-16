"""Task 055-G truth publication using only sealed source reads."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from task_055_f.truth_v2 import build_truth_v2 as build_task055f_truth
from task_055_f.truth_v2 import validate_truth_v2 as validate_task055f_truth

from .access import AccessBroker, canonical_hash, sha256_file
from .contracts import MAX_DATE, TRUTH_SCHEMA


class TruthV2Error(RuntimeError):
    pass


def build_truth_v2(
    *,
    governed_root: str | Path,
    parents: Mapping[str, Any],
    output_root: str | Path,
    broker: AccessBroker,
    builder_code_hash: str,
) -> dict[str, Any]:
    intermediate = build_task055f_truth(
        governed_root=governed_root,
        inventory_manifest=Path(governed_root) / str(parents["inventory_manifest"]),
        matrix_root=Path(governed_root) / str(parents["matrix_root"]),
        suspension_coverage_ledger=Path(governed_root) / str(parents["suspension_coverage_ledger"]),
        suspension_cache_root=Path(governed_root) / str(parents["suspension_cache_root"]),
        task055e_provenance_manifest=Path(governed_root) / str(parents["task055e_provenance_manifest"]),
        task055c_truth_manifest=Path(governed_root) / str(parents["task055c_truth_manifest"]),
        output_root=Path(output_root) / "source_truth",
        reader=broker,
        builder_code_hash=builder_code_hash,
    )
    source = validate_task055f_truth(intermediate["manifest_path"])
    rows = list(source["records"])
    if len(rows) != 35844:
        raise TruthV2Error(f"truth_key_count_changed_without_lineage_explanation:{len(rows)}")
    counts = dict(sorted(Counter(row["state"] for row in rows).items()))
    if counts != source.get("state_counts"):
        raise TruthV2Error("truth_state_counts_mismatch")
    return _publish(
        Path(output_root),
        rows=rows,
        source=source,
        lineage={
            "parent_lineage_content_hash": parents["content_hash"],
            "access_plan_content_hash": broker.plan["content_hash"],
            "source_truth_content_hash": source["content_hash"],
            "matrix_content_hash": source["lineage"]["matrix_content_hash"],
            "builder_code_hash": builder_code_hash,
        },
    )


def validate_truth_v2(path: str | Path) -> dict[str, Any]:
    manifest_path = _resolve(path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != TRUTH_SCHEMA or manifest.get("status") != "published":
        raise TruthV2Error("task055g_truth_schema_or_status_invalid")
    semantic = {key: value for key, value in manifest.items() if key not in {"content_hash", "generation_id"}}
    if canonical_hash(semantic) != manifest.get("content_hash"):
        raise TruthV2Error("task055g_truth_content_hash_mismatch")
    root = manifest_path.parent
    rows_entry = manifest.get("partitions", {}).get("rows") or {}
    rows_path = root / str(rows_entry.get("path") or "")
    if not rows_path.is_file() or sha256_file(rows_path) != rows_entry.get("sha256"):
        raise TruthV2Error("task055g_truth_rows_partition_mismatch")
    rows = [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines() if line]
    keys = [(row.get("ts_code"), row.get("trade_date")) for row in rows]
    if len(rows) != manifest.get("record_count") or len(keys) != len(set(keys)):
        raise TruthV2Error("task055g_truth_key_count_invalid")
    if canonical_hash(sorted(keys)) != manifest.get("key_root"):
        raise TruthV2Error("task055g_truth_key_root_mismatch")
    counts = dict(sorted(Counter(row["state"] for row in rows).items()))
    if counts != manifest.get("state_counts"):
        raise TruthV2Error("task055g_truth_state_counts_mismatch")
    return manifest | {"manifest_path": str(manifest_path), "records": rows}


def _publish(root: Path, *, rows: list[dict[str, Any]], source: Mapping[str, Any], lineage: Mapping[str, Any]) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".task055g.truth.", dir=root))
    try:
        rows_path = staging / "truth_v2_rows.jsonl"
        rows_path.write_text(
            "".join(json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
        partition = {"path": rows_path.name, "sha256": sha256_file(rows_path), "size_bytes": rows_path.stat().st_size}
        semantic = {
            "schema_version": TRUTH_SCHEMA,
            "status": "published",
            "review_version": "task055g_complete_bar_resume_and_lifecycle_precedence_v1",
            "max_date": MAX_DATE,
            "record_count": len(rows),
            "key_root": canonical_hash(sorted((row["ts_code"], row["trade_date"]) for row in rows)),
            "state_counts": dict(sorted(Counter(row["state"] for row in rows).items())),
            "suspend_type_counts": dict(sorted(Counter(row["suspend_type"] for row in rows).items())),
            "daily_empty_response_counts": source.get("daily_empty_response_counts"),
            "suspend_empty_response_counts": source.get("suspend_empty_response_counts"),
            "valuation_domain_count": source.get("valuation_domain_count"),
            "modeled_candidate_count": source.get("modeled_candidate_count"),
            "timing_uncertified_candidate_count": source.get("timing_uncertified_candidate_count"),
            "lineage": dict(lineage),
            "partitions": {"rows": partition},
            "certification_blockers": [
                "suspension_timing_semantics_uncertified",
                "vendor_historical_revision_risk",
            ],
        }
        content_hash = canonical_hash(semantic)
        generation_id = f"truth_v2_{content_hash[:24]}"
        manifest = semantic | {"content_hash": content_hash, "generation_id": generation_id}
        (staging / "truth_v2_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        target = root / "generations" / generation_id
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(staging)
        else:
            os.replace(staging, target)
        _atomic_json(root / "current.json", {"generation_id": generation_id, "content_hash": content_hash, "manifest": f"generations/{generation_id}/truth_v2_manifest.json"})
        return manifest | {"manifest_path": str(target / "truth_v2_manifest.json")}
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _resolve(path: str | Path) -> Path:
    value = Path(path)
    if value.is_file():
        return value
    pointer = value / "current.json"
    if pointer.is_file():
        return value / str(json.loads(pointer.read_text(encoding="utf-8"))["manifest"])
    candidate = value / "truth_v2_manifest.json"
    if candidate.is_file():
        return candidate
    raise TruthV2Error("task055g_truth_manifest_missing")


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)

"""Read-only evidence audit for Task 052-A governed repairs."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from validation_campaign_store.artifacts import resolve_campaign_artifacts


@dataclass(frozen=True)
class Task052AuditInputs:
    source_campaign_root: str
    task_051_output_dir: str
    index_code: str = "000300.SH"
    observed_end_date: str = "20260630"


def audit_task_052_inputs(inputs: Task052AuditInputs) -> dict[str, Any]:
    artifacts = resolve_campaign_artifacts(inputs.source_campaign_root)
    freeze_dir = Path(artifacts.data_freeze_dir)
    freeze_manifest_path = freeze_dir / "freeze_manifest.json"
    dataset_version_path = freeze_dir / "dataset_version_manifest.json"
    freeze_manifest = _read_json(freeze_manifest_path)
    dataset_version = _read_json(dataset_version_path)
    raw_index_path = Path(str(dataset_version.get("raw_data_index_manifest_path") or ""))
    raw_index = _read_json(raw_index_path)
    indexed = {str(row.get("dataset")): row for row in raw_index.get("datasets", []) if isinstance(row, dict)}

    audited_datasets = {}
    for dataset in ("suspensions", "name_changes", "st_status_daily"):
        declared = dict(indexed.get(dataset) or {})
        actual_path = _resolve_records_path(dataset, declared, artifacts.data_dir, freeze_manifest)
        audited_datasets[dataset] = audit_jsonl_dataset(actual_path, declared)

    historical_dir = _resolve_historical_universe(Path(inputs.task_051_output_dir), inputs.index_code)
    proof_path = historical_dir / "snapshot_proof_manifest.json"
    proof = _read_json(proof_path)
    union_path = historical_dir / "historical_union_members.jsonl"
    union_codes = [str(row["ts_code"]) for row in _read_jsonl(union_path) if row.get("ts_code")]
    candidate_rows = _read_jsonl(Path(artifacts.candidate_pool_path))
    legacy_request_evidence = _audit_legacy_suspension_requests(Path(artifacts.data_dir))

    credential_status = _credential_status(Path(inputs.source_campaign_root).parents[4] if len(Path(inputs.source_campaign_root).parents) > 4 else Path.cwd())
    suspension = audited_datasets["suspensions"]
    legacy_unusable = bool(
        suspension.get("record_count")
        and suspension.get("null_counts", {}).get("trade_date", suspension.get("record_count")) == suspension.get("record_count")
        and suspension.get("null_counts", {}).get("suspend_date", suspension.get("record_count")) == suspension.get("record_count")
    )
    proven_root_causes = []
    if set(suspension.get("fields", [])) == {"ann_date", "reason_type", "resume_date", "suspend_date", "suspend_reason", "ts_code"}:
        proven_root_causes.append("legacy_suspension_contract_requested_obsolete_suspend_d_fields")
    if legacy_unusable:
        proven_root_causes.append("all_legacy_suspension_rows_lack_dated_event_fields")
    if legacy_request_evidence.get("response_limit_hit_count"):
        proven_root_causes.append("legacy_suspension_request_hit_provider_row_limit_without_governed_split")

    return {
        "status": "complete_with_legacy_blockers",
        "source_campaign_id": _campaign_id(Path(artifacts.campaign_manifest_path)),
        "observed_end_date": inputs.observed_end_date,
        "index_code": inputs.index_code,
        "raw_index": {
            "path": str(raw_index_path),
            "sha256": _sha256(raw_index_path) if raw_index_path.exists() else None,
            "declared_index_hash": raw_index.get("index_hash"),
        },
        "freeze": {
            "path": str(freeze_dir),
            "freeze_manifest_path": str(freeze_manifest_path),
            "freeze_manifest_sha256": _sha256(freeze_manifest_path),
            "dataset_version_manifest_path": str(dataset_version_path),
            "dataset_version_manifest_sha256": _sha256(dataset_version_path),
            "read_only": True,
        },
        "datasets": audited_datasets,
        "historical_universe": {
            "directory": str(historical_dir),
            "proof_path": str(proof_path),
            "proof_sha256": _sha256(proof_path),
            "historical_constituent_proof": bool(proof.get("historical_constituent_proof")),
            "union_path": str(union_path),
            "union_sha256": _sha256(union_path),
            "union_count": len(union_codes),
            "stock_axis_hash": _hash_list(union_codes),
            "usable_period": proof.get("usable_period"),
        },
        "candidate_pool": {
            "path": artifacts.candidate_pool_path,
            "sha256": _sha256(Path(artifacts.candidate_pool_path)),
            "candidate_count": len(candidate_rows),
        },
        "credential_status": credential_status,
        "legacy_suspension_status": "legacy_unusable" if legacy_unusable else "requires_review",
        "legacy_request_evidence": legacy_request_evidence,
        "proven_root_causes": proven_root_causes,
        "strong_inferences": [
            "legacy_registry_contract_and_actual_null_rows_are_consistent_with_invalid_response_field_selection"
        ],
        "unknowns": [
            "original_provider_response_envelope_unavailable"
        ],
        "old_inputs_immutable": True,
    }


def audit_jsonl_dataset(path: Path, declared: dict[str, Any]) -> dict[str, Any]:
    rows = _read_jsonl(path)
    fields = sorted({key for row in rows for key in row})
    null_counts = {field: sum(row.get(field) in {None, ""} for row in rows) for field in fields}
    actual_sha = _sha256(path) if path.exists() else None
    samples = [_redacted_sample(row) for row in rows[:3]]
    primary_key = list(declared.get("primary_key_fields") or [])
    duplicate_count = _duplicate_count(rows, primary_key)
    return {
        "actual_path": str(path),
        "exists": path.exists(),
        "actual_sha256": actual_sha,
        "record_count": len(rows),
        "fields": fields,
        "null_counts": null_counts,
        "primary_key_fields": primary_key,
        "actual_duplicate_key_count": duplicate_count,
        "declared_path": declared.get("records_path"),
        "declared_sha256": declared.get("records_sha256"),
        "declared_record_count": declared.get("record_count"),
        "raw_index_path_matches": not declared or str(path) == str(declared.get("records_path")),
        "raw_index_sha_matches": not declared or actual_sha == declared.get("records_sha256"),
        "raw_index_count_matches": not declared or len(rows) == int(declared.get("record_count", -1)),
        "redacted_samples": samples,
    }


def _resolve_records_path(dataset: str, declared: dict[str, Any], data_dir: str, freeze_manifest: dict[str, Any]) -> Path:
    for entry in freeze_manifest.get("files", []):
        relative = str(entry.get("relative_path") or "")
        if relative.endswith(f"/{dataset}/records.jsonl") or relative == f"data/{dataset}/records.jsonl":
            candidate = Path(str(entry.get("path") or ""))
            if candidate.exists():
                return candidate
    declared_value = str(declared.get("records_path") or "")
    declared_path = Path(declared_value) if declared_value else None
    if declared_path is not None and declared_path.is_file():
        return declared_path
    return Path(data_dir) / dataset / "records.jsonl"


def _resolve_historical_universe(task_051_output: Path, index_code: str) -> Path:
    candidates = sorted((task_051_output / "historical_universe").glob(f"historical_index_{index_code.replace('.', '_').lower()}_*"))
    if not candidates:
        raise FileNotFoundError(f"historical universe generation missing for {index_code}")
    return candidates[-1]


def _credential_status(campaign_ancestor: Path) -> dict[str, Any]:
    candidates = [Path.cwd() / ".env.local", campaign_ancestor / ".env.local"]
    configured = False
    source = None
    for path in candidates:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("TUSHARE_TOKEN=") and line.partition("=")[2].strip():
                configured = True
                source = str(path)
                break
        if configured:
            break
    return {"tushare_token_configured": configured, "credential_source": source, "token_persisted_in_artifact": False}


def _audit_legacy_suspension_requests(data_dir: Path) -> dict[str, Any]:
    audit_path = data_dir / "api_audit.jsonl"
    rows = [row for row in _read_jsonl(audit_path) if row.get("api_name") == "suspend_d"]
    positive = [row for row in rows if int(row.get("records", 0) or 0) > 0]
    cap_hits = [row for row in positive if int(row.get("records", 0) or 0) >= 5000]
    cache_dir = data_dir / ".cache" / "tushare"
    cache_envelopes = []
    if cache_dir.exists():
        for path in cache_dir.glob("*.json"):
            try:
                payload = _read_json(path)
            except (OSError, json.JSONDecodeError):
                continue
            if (payload.get("metadata") or {}).get("api_name") == "suspend_d":
                cache_envelopes.append(
                    {
                        "path": str(path),
                        "sha256": _sha256(path),
                        "record_count": len(payload.get("records") or []),
                        "metadata_fields": sorted((payload.get("metadata") or {}).keys()),
                    }
                )
    return {
        "api_audit_path": str(audit_path),
        "api_audit_sha256": _sha256(audit_path) if audit_path.exists() else None,
        "request_count": len(rows),
        "positive_response_count": len(positive),
        "positive_response_records": sum(int(row.get("records", 0) or 0) for row in positive),
        "response_limit_hit_count": len(cap_hits),
        "response_limit_hits": [
            {
                "start_date": row.get("start_date"),
                "end_date": row.get("end_date"),
                "records": row.get("records"),
                "status": row.get("status"),
            }
            for row in cap_hits
        ],
        "cache_envelopes": cache_envelopes,
        "request_params_recoverable": bool(cache_envelopes),
        "response_fields_recoverable": bool(cache_envelopes),
    }


def _redacted_sample(row: dict[str, Any]) -> dict[str, Any]:
    redact = {"name", "change_reason", "suspend_reason"}
    return {key: "<redacted>" if key in redact and value not in {None, ""} else value for key, value in row.items()}


def _duplicate_count(rows: list[dict[str, Any]], fields: list[str]) -> int:
    if not fields:
        return 0
    counts = Counter(tuple(row.get(field) for field in fields) for row in rows)
    return sum(count - 1 for count in counts.values() if count > 1)


def _campaign_id(path: Path) -> str:
    payload = _read_json(path)
    return str(payload.get("campaign_id") or path.parent.name)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_list(values: list[str]) -> str:
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()

"""Governed, bounded Tushare repair and source-generation publication."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from data_pipeline.ashare.cache import TushareResponseCache, tushare_cache_source_hash
from data_pipeline.ashare.config import AShareDataConfig
from data_pipeline.ashare.dataset_registry import DATASET_DEFINITIONS
from data_pipeline.ashare.providers.tushare_client import TUSHARE_PROVIDER_API_VERSION, TushareHttpClient
from data_pipeline.ashare.rate_limit import RequestRateLimitConfig, SimpleRateLimiter
from data_pipeline.ashare.request_normalization import normalize_tushare_request, stable_json_hash, tushare_code_semantic_hash


DATASET_LIMITS = {"suspensions": 5000, "st_status_daily": 1000, "name_changes": 10000}
CONSERVATIVE_SUSPENSION_POLICY = "conservative_event_day_open_exclusion_v1"
SOURCE_GENERATION_SCHEMA = "task_053_governed_source_generation.v1"


@dataclass(frozen=True)
class GovernedBackfillConfig:
    union_path: Path
    securities_path: Path
    output_root: Path
    usable_start_date: str = "20160101"
    observed_end_date: str = "20260630"
    suspension_warmup_start_date: str = "20150101"
    requests_per_minute: float = 150.0
    datasets: tuple[str, ...] = ("suspensions", "st_status_daily", "name_changes")


def run_governed_backfill(config: GovernedBackfillConfig) -> dict[str, Any]:
    raise RuntimeError("superseded_by_task055j")
    codes = sorted({str(row["ts_code"]) for row in _read_jsonl(config.union_path) if row.get("ts_code")})
    if not codes:
        raise RuntimeError("historical union is empty")
    list_dates = {str(row.get("ts_code")): str(row.get("list_date") or "") for row in _read_jsonl(config.securities_path)}
    run_root = config.output_root
    run_root.mkdir(parents=True, exist_ok=True)
    cache = TushareResponseCache(run_root / "request_cache", enabled=True)
    limiter = SimpleRateLimiter(RequestRateLimitConfig(requests_per_minute=config.requests_per_minute))
    client = TushareHttpClient(AShareDataConfig.from_env(), rate_limiter=limiter)
    dataset_reports = {
        dataset: _run_dataset(dataset, codes, list_dates, config, client, cache)
        for dataset in config.datasets
    }
    complete = all(report["covered_stock_count"] == len(codes) and report["failure_count"] == 0 for report in dataset_reports.values())
    manifest = {
        "artifact_type": "task_052_backfill_report",
        "schema_version": "2.0",
        "producer": "task_052_a.backfill",
        "status": "complete" if complete else "blocked",
        "union_count": len(codes),
        "union_sha256": _sha256(config.union_path),
        "observed_end_date": config.observed_end_date,
        "requests_per_minute": config.requests_per_minute,
        "code_semantic_hash": tushare_code_semantic_hash(),
        "source_code_hash": _source_generation_code_hash(),
        "cache_source_code_hash": tushare_cache_source_hash(),
        "suspension_policy": CONSERVATIVE_SUSPENSION_POLICY,
        "datasets": dataset_reports,
        "rate_limit": limiter.summary().to_dict(),
        "token_persisted": False,
        "old_sources_mutated": False,
    }
    _atomic_json(run_root / "task_052_backfill_report.json", manifest)
    return manifest


def _run_dataset(
    dataset: str,
    codes: list[str],
    list_dates: dict[str, str],
    config: GovernedBackfillConfig,
    client: TushareHttpClient,
    cache: TushareResponseCache,
) -> dict[str, Any]:
    definition = DATASET_DEFINITIONS[dataset]
    staging = config.output_root / "staging" / dataset
    staging.mkdir(parents=True, exist_ok=True)
    ledger_path = staging / "coverage_ledger.jsonl"
    contract = _dataset_contract(dataset)
    endpoint_schema_proof = cache.build_endpoint_schema_proof(definition.api_name, definition.fields)
    completed, resume_misses = _load_valid_completed(
        ledger_path,
        staging / "stocks",
        dataset=dataset,
        codes=codes,
        list_dates=list_dates,
        config=config,
        client=client,
        cache=cache,
        contract=contract,
        endpoint_schema_proof=endpoint_schema_proof,
    )
    records_by_pk: dict[tuple[str, ...], dict[str, Any]] = {}
    failures: list[dict[str, Any]] = []
    successful_entries = dict(completed)
    for code in codes:
        entry = completed.get(code)
        if entry is not None:
            rows = _read_jsonl(staging / "stocks" / f"{code}.jsonl")
            _merge_rows(records_by_pk, rows, definition.primary_key)
            continue
        ordinal = codes.index(code) + 1
        start_date, end_date = _request_range(dataset, code, list_dates, config)
        request_spec = _request_spec(dataset, code, start_date, end_date, client, contract)
        entry = {
            "ts_code": code,
            "ordinal": ordinal,
            "dataset": dataset,
            "api_name": definition.api_name,
            "endpoint": client.api_url,
            "provider_api_version": TUSHARE_PROVIDER_API_VERSION,
            "requested_start_date": start_date,
            "requested_end_date": end_date,
            "request_spec_hash": stable_json_hash(request_spec),
            "contract_hash": contract["contract_hash"],
            "code_semantic_hash": tushare_code_semantic_hash(),
            "source_code_hash": _source_generation_code_hash(),
            "cache_source_code_hash": tushare_cache_source_hash(),
            "slices": [],
            "status": "failed",
        }
        try:
            rows = _fetch_with_backoff(dataset, code, start_date, end_date, client, cache, entry["slices"], endpoint_schema_proof)
            normalized = [_validate_row(dataset, row, code, start_date, end_date) for row in rows]
            normalized, duplicate_count = _deduplicate_rows(normalized, definition.primary_key)
            stock_path = staging / "stocks" / f"{code}.jsonl"
            _atomic_jsonl(stock_path, normalized)
            entry.update(
                status="success",
                returned_count=len(normalized),
                deduplicated_count=duplicate_count,
                stock_file=f"stocks/{code}.jsonl",
                stock_file_sha256=_sha256(stock_path),
                negative_attestation=not bool(normalized),
                slice_evidence_hash=stable_json_hash(entry["slices"]),
            )
            _merge_rows(records_by_pk, normalized, definition.primary_key)
            successful_entries[code] = entry
        except Exception as exc:
            entry.update(error_type=type(exc).__name__, error=str(exc)[:1000])
            failures.append(entry)
        _append_jsonl(ledger_path, entry)

    complete = len(successful_entries) == len(codes) and not failures
    rows = sorted(records_by_pk.values(), key=lambda row: tuple(str(row.get(key) or "") for key in definition.primary_key))
    canonical_ledger = [successful_entries[code] for code in codes if code in successful_entries]
    negative_attestations = [
        {
            "ts_code": entry["ts_code"],
            "request_spec_hash": entry["request_spec_hash"],
            "slice": slice_evidence,
        }
        for entry in canonical_ledger
        for slice_evidence in entry["slices"]
        if slice_evidence.get("negative_attestation") is True
    ]
    content_payload = {
        "schema_version": SOURCE_GENERATION_SCHEMA,
        "dataset": dataset,
        "contract": contract,
        "request_policy": _request_policy(dataset, config),
        "source_code_hash": _source_generation_code_hash(),
        "cache_source_code_hash": tushare_cache_source_hash(),
        "records": rows,
        "coverage_ledger": canonical_ledger,
        "negative_attestations": negative_attestations,
    }
    content_hash = stable_json_hash(content_payload)
    generation_id = content_hash[:24]
    target = config.output_root / "generations" / dataset / generation_id
    _publish_generation(
        target,
        rows=rows,
        canonical_ledger=canonical_ledger,
        negative_attestations=negative_attestations,
        manifest={
            "artifact_type": "task_053_governed_source_generation",
            "schema_version": SOURCE_GENERATION_SCHEMA,
            "dataset": dataset,
            "api_name": definition.api_name,
            "status": "complete" if complete else "incomplete",
            "contract": contract,
            "request_policy": _request_policy(dataset, config),
            "generation_id": generation_id,
            "content_hash": content_hash,
            "record_count": len(rows),
            "covered_stock_count": len(successful_entries),
            "required_stock_count": len(codes),
            "negative_attestation_count": len(negative_attestations),
            "source_code_hash": _source_generation_code_hash(),
            "cache_source_code_hash": tushare_cache_source_hash(),
            "endpoint_schema_proof": endpoint_schema_proof,
            "suspension_policy": CONSERVATIVE_SUSPENSION_POLICY if dataset == "suspensions" else None,
            "old_sources_mutated": False,
        },
    )
    pointer_updated = False
    if complete:
        pointer = {
            "dataset": dataset,
            "generation_id": generation_id,
            "generation_path": str(target),
            "content_hash": content_hash,
            "manifest_sha256": _sha256(target / "coverage_manifest.json"),
        }
        _atomic_json(config.output_root / f"current_{dataset}.json", pointer)
        pointer_updated = True
    return {
        "dataset": dataset,
        "generation_id": generation_id,
        "generation_path": str(target),
        "content_hash": content_hash,
        "record_count": len(rows),
        "covered_stock_count": len(successful_entries),
        "required_stock_count": len(codes),
        "failure_count": len(failures),
        "failures": failures[:20],
        "resume_miss_count": len(resume_misses),
        "resume_misses": resume_misses[:20],
        "negative_attestation_count": len(negative_attestations),
        "pointer_updated": pointer_updated,
    }


def _fetch_with_backoff(
    dataset: str,
    code: str,
    start_date: str | None,
    end_date: str | None,
    client: TushareHttpClient,
    cache: TushareResponseCache,
    slices: list[dict[str, Any]],
    endpoint_schema_proof: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    last_error: Exception | None = None
    for attempt in range(1, 6):
        try:
            return _fetch_recursive(dataset, code, start_date, end_date, client, cache, slices, endpoint_schema_proof)
        except Exception as exc:
            last_error = exc
            message = str(exc)
            if "HTTP Error 429" not in message and "HTTP Error 307" not in message:
                raise
            delay = min(60.0, float(5 * (2 ** (attempt - 1))))
            slices.append({"retry_attempt": attempt, "retry_reason": message[:300], "backoff_seconds": delay})
            time.sleep(delay)
    raise RuntimeError(f"provider transport retries exhausted for {dataset}/{code}: {last_error}") from last_error


def _fetch_recursive(
    dataset: str,
    code: str,
    start_date: str | None,
    end_date: str | None,
    client: TushareHttpClient,
    cache: TushareResponseCache,
    slices: list[dict[str, Any]],
    endpoint_schema_proof: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    definition = DATASET_DEFINITIONS[dataset]
    params: dict[str, Any] = {"ts_code": code}
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    cached = cache.read(
        definition.api_name,
        params,
        definition.fields,
        endpoint_schema_proof=endpoint_schema_proof,
        allow_legacy_source_semantics=True,
    )
    if cached and cached.hit:
        rows = cached.records
        source = "cache"
        envelope = cached.envelope or {}
        negative = cached.negative_attestation is not None
        cache_path = cached.path
    else:
        response = client.post_with_metadata(definition.api_name, params=params, fields=definition.fields)
        rows = response.records
        cache_path = cache.write(
            definition.api_name,
            params,
            definition.fields,
            rows,
            response_code=response.response_code,
            response_message=response.response_message,
            response_fields=response.response_fields,
            item_count=response.item_count,
            response_fields_observed=True,
            endpoint=response.endpoint,
            provider_api_version=response.provider_api_version,
        )
        envelope = json.loads(cache_path.read_text(encoding="utf-8"))
        source = "provider"
        negative = not bool(rows)
    slices.append(
        {
            "normalized_request": normalize_tushare_request(definition.api_name, params, definition.fields),
            "request_fingerprint": envelope.get("request_fingerprint"),
            "start_date": start_date,
            "end_date": end_date,
            "returned_count": len(rows),
            "source": source,
            "negative_attestation": negative,
            "response_fields": (envelope.get("response") or {}).get("fields"),
            "response_fields_observed": bool((envelope.get("response") or {}).get("fields_observed")),
            "response_hash": stable_json_hash(rows),
            "cache_key": cache_path.name,
            "cache_sha256": _sha256(cache_path),
            "cache_schema_version": envelope.get("schema_version"),
            "cache_code_semantic_hash": envelope.get("code_semantic_hash"),
            "cache_source_code_hash": envelope.get("source_code_hash"),
            "endpoint": ((envelope.get("provider") or {}).get("endpoint") or client.api_url),
            "provider_api_version": ((envelope.get("provider") or {}).get("api_version") or TUSHARE_PROVIDER_API_VERSION),
            "endpoint_schema_proof_hash": endpoint_schema_proof.get("proof_hash") if negative and endpoint_schema_proof else None,
        }
    )
    limit = DATASET_LIMITS[dataset]
    if len(rows) < limit:
        return rows
    if not start_date or not end_date or start_date >= end_date:
        raise RuntimeError(f"{dataset} response reached limit without splittable date range for {code}")
    left_end, right_start = _bisect_dates(start_date, end_date)
    return _fetch_recursive(dataset, code, start_date, left_end, client, cache, slices, endpoint_schema_proof) + _fetch_recursive(
        dataset, code, right_start, end_date, client, cache, slices, endpoint_schema_proof
    )


def _validate_row(
    dataset: str,
    row: dict[str, Any],
    expected_code: str,
    requested_start_date: str | None,
    requested_end_date: str | None,
) -> dict[str, Any]:
    definition = DATASET_DEFINITIONS[dataset]
    missing = [field for field in definition.fields if field not in row]
    if missing:
        raise RuntimeError(f"{dataset} response missing fields: {','.join(missing)}")
    normalized = {field: row.get(field) for field in definition.fields}
    if str(normalized.get("ts_code") or "") != expected_code:
        raise RuntimeError(f"{dataset} returned unexpected ts_code")
    if dataset in {"suspensions", "st_status_daily"}:
        trade_date = _valid_date(normalized.get("trade_date"), f"{dataset}.trade_date")
        if requested_start_date and trade_date < requested_start_date:
            raise RuntimeError(f"{dataset} returned date before requested range")
        if requested_end_date and trade_date > requested_end_date:
            raise RuntimeError(f"{dataset} returned date after requested range")
        normalized["trade_date"] = trade_date
    if dataset == "suspensions":
        suspend_type = str(normalized.get("suspend_type") or "").strip().upper()
        if suspend_type not in {"S", "R"}:
            raise RuntimeError("suspensions returned invalid suspend_type")
        raw_timing = normalized.get("suspend_timing")
        normalized["suspend_type"] = suspend_type
        normalized["suspend_timing"] = None if raw_timing is None else str(raw_timing).strip()
        normalized["timing_parse_status"] = "raw_null" if raw_timing is None else ("blank" if not str(raw_timing).strip() else "explicit")
        normalized["canonical_interval"] = _canonical_interval(normalized["suspend_timing"])
        normalized["suspension_event_present"] = True
        normalized["conservative_open_excluded"] = True
    elif dataset == "st_status_daily":
        for field in ("type", "type_name"):
            value = str(normalized.get(field) or "").strip()
            if not value:
                raise RuntimeError(f"st_status_daily returned empty {field}")
            normalized[field] = value
    elif dataset == "name_changes":
        for field in ("start_date", "end_date", "ann_date"):
            if normalized.get(field) not in (None, ""):
                normalized[field] = _valid_date(normalized[field], f"name_changes.{field}")
    return normalized


def _canonical_interval(value: Any) -> str | None:
    if value is None or not str(value).strip():
        return None
    timing = str(value).strip().lower()
    if timing in {"全天", "全日", "全日停牌", "d", "day", "09:30-15:00", "09:30~15:00"}:
        return "full_day"
    if "开盘" in timing or timing.startswith("09:30"):
        return "open_associated"
    return "explicit_unclassified"


def _deduplicate_rows(rows: list[dict[str, Any]], primary_key: tuple[str, ...]) -> tuple[list[dict[str, Any]], int]:
    keyed: dict[tuple[str, ...], dict[str, Any]] = {}
    duplicates = 0
    for row in rows:
        key = tuple(str(row.get(field) or "") for field in primary_key)
        existing = keyed.get(key)
        if existing is None:
            keyed[key] = row
        elif existing == row:
            duplicates += 1
        else:
            raise RuntimeError(f"conflicting primary key: {key}")
    return list(keyed.values()), duplicates


def _merge_rows(target: dict[tuple[str, ...], dict[str, Any]], rows: list[dict[str, Any]], primary_key: tuple[str, ...]) -> None:
    for row in rows:
        key = tuple(str(row.get(field) or "") for field in primary_key)
        existing = target.get(key)
        if existing is not None and existing != row:
            raise RuntimeError(f"conflicting primary key: {key}")
        target[key] = row


def _load_valid_completed(
    ledger_path: Path,
    stocks_dir: Path,
    *,
    dataset: str,
    codes: list[str],
    list_dates: dict[str, str],
    config: GovernedBackfillConfig,
    client: TushareHttpClient,
    cache: TushareResponseCache,
    contract: dict[str, Any],
    endpoint_schema_proof: dict[str, Any] | None,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    entries_by_code: dict[str, list[dict[str, Any]]] = {}
    for entry in _read_jsonl(ledger_path):
        code = str(entry.get("ts_code") or "")
        if code in codes:
            entries_by_code.setdefault(code, []).append(entry)
    valid: dict[str, dict[str, Any]] = {}
    misses: list[dict[str, str]] = []
    for code in codes:
        reason = "no_success_entry"
        start_date, end_date = _request_range(dataset, code, list_dates, config)
        expected_spec = _request_spec(dataset, code, start_date, end_date, client, contract)
        for entry in reversed(entries_by_code.get(code, [])):
            try:
                _validate_resume_entry(
                    entry,
                    stocks_dir / f"{code}.jsonl",
                    expected_spec=expected_spec,
                    expected_contract=contract,
                    cache=cache,
                    endpoint_schema_proof=endpoint_schema_proof,
                )
            except RuntimeError as exc:
                reason = str(exc)
                continue
            valid[code] = entry
            break
        if code not in valid:
            misses.append({"ts_code": code, "reason": reason})
    return valid, misses


def _validate_resume_entry(
    entry: dict[str, Any],
    stock_path: Path,
    *,
    expected_spec: dict[str, Any],
    expected_contract: dict[str, Any],
    cache: TushareResponseCache,
    endpoint_schema_proof: dict[str, Any] | None,
) -> None:
    if entry.get("status") != "success":
        raise RuntimeError("ledger status is not success")
    expected = {
        "dataset": expected_spec["dataset"],
        "api_name": expected_spec["api_name"],
        "endpoint": expected_spec["endpoint"],
        "provider_api_version": expected_spec["provider_api_version"],
        "requested_start_date": expected_spec["start_date"],
        "requested_end_date": expected_spec["end_date"],
        "request_spec_hash": stable_json_hash(expected_spec),
        "contract_hash": expected_contract["contract_hash"],
        "code_semantic_hash": tushare_code_semantic_hash(),
        "source_code_hash": _source_generation_code_hash(),
        "cache_source_code_hash": tushare_cache_source_hash(),
    }
    for field, value in expected.items():
        if entry.get(field) != value:
            raise RuntimeError(f"ledger {field} mismatch")
    if not stock_path.is_file():
        raise RuntimeError("stock file missing")
    if entry.get("stock_file_sha256") != _sha256(stock_path):
        raise RuntimeError("stock file SHA mismatch")
    rows = _read_jsonl(stock_path)
    if int(entry.get("returned_count", -1)) != len(rows):
        raise RuntimeError("stock file row count mismatch")
    slices = entry.get("slices")
    if not isinstance(slices, list) or not slices or entry.get("slice_evidence_hash") != stable_json_hash(slices):
        raise RuntimeError("slice evidence missing or changed")
    for slice_evidence in slices:
        if "retry_attempt" in slice_evidence:
            continue
        normalized_request = slice_evidence.get("normalized_request")
        if not isinstance(normalized_request, dict):
            raise RuntimeError("slice normalized request missing")
        cache_path = cache.root_dir / str(slice_evidence.get("cache_key") or "")
        if not cache_path.is_file() or slice_evidence.get("cache_sha256") != _sha256(cache_path):
            raise RuntimeError("slice cache file missing or changed")
        result = cache.read(
            str(normalized_request.get("api_name") or ""),
            normalized_request.get("params"),
            normalized_request.get("fields"),
            endpoint_schema_proof=endpoint_schema_proof,
            allow_legacy_source_semantics=True,
        )
        if not result or not result.hit or result.path != cache_path:
            raise RuntimeError("slice cache fingerprint mismatch")
        if slice_evidence.get("request_fingerprint") != (result.envelope or {}).get("request_fingerprint"):
            raise RuntimeError("slice request fingerprint mismatch")
        if slice_evidence.get("endpoint") != expected_spec["endpoint"]:
            raise RuntimeError("slice endpoint mismatch")
        if slice_evidence.get("provider_api_version") != expected_spec["provider_api_version"]:
            raise RuntimeError("slice provider API version mismatch")


def _dataset_contract(dataset: str) -> dict[str, Any]:
    definition = DATASET_DEFINITIONS[dataset]
    unsigned = {
        "dataset": dataset,
        "api_name": definition.api_name,
        "fields": list(definition.fields),
        "primary_key": list(definition.primary_key),
    }
    return {**unsigned, "contract_hash": stable_json_hash(unsigned)}


def _request_range(dataset: str, code: str, list_dates: dict[str, str], config: GovernedBackfillConfig) -> tuple[str | None, str | None]:
    if dataset == "suspensions":
        return config.suspension_warmup_start_date, config.observed_end_date
    if dataset == "st_status_daily":
        return max("20160101", list_dates.get(code) or "20160101"), config.observed_end_date
    return None, None


def _request_spec(
    dataset: str,
    code: str,
    start_date: str | None,
    end_date: str | None,
    client: TushareHttpClient,
    contract: dict[str, Any],
) -> dict[str, Any]:
    definition = DATASET_DEFINITIONS[dataset]
    params: dict[str, Any] = {"ts_code": code}
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    return {
        "dataset": dataset,
        "api_name": definition.api_name,
        "endpoint": client.api_url,
        "provider_api_version": TUSHARE_PROVIDER_API_VERSION,
        "start_date": start_date,
        "end_date": end_date,
        "normalized_request": normalize_tushare_request(definition.api_name, params, definition.fields),
        "contract_hash": contract["contract_hash"],
    }


def _request_policy(dataset: str, config: GovernedBackfillConfig) -> dict[str, Any]:
    return {
        "dataset": dataset,
        "usable_start_date": config.usable_start_date,
        "observed_end_date": config.observed_end_date,
        "suspension_warmup_start_date": config.suspension_warmup_start_date if dataset == "suspensions" else None,
        "row_limit": DATASET_LIMITS[dataset],
        "recursive_date_bisection": True,
    }


def _publish_generation(
    target: Path,
    *,
    rows: list[dict[str, Any]],
    canonical_ledger: list[dict[str, Any]],
    negative_attestations: list[dict[str, Any]],
    manifest: dict[str, Any],
) -> None:
    if target.exists():
        existing = json.loads((target / "coverage_manifest.json").read_text(encoding="utf-8"))
        if existing.get("content_hash") != manifest["content_hash"]:
            raise RuntimeError("existing generation content hash mismatch")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=str(target.parent)))
    try:
        _atomic_jsonl(temporary / "records.jsonl", rows)
        _atomic_jsonl(temporary / "coverage_ledger.jsonl", canonical_ledger)
        _atomic_jsonl(temporary / "negative_attestations.jsonl", negative_attestations)
        finalized = {
            **manifest,
            "records_sha256": _sha256(temporary / "records.jsonl"),
            "coverage_ledger_sha256": _sha256(temporary / "coverage_ledger.jsonl"),
            "negative_attestations_sha256": _sha256(temporary / "negative_attestations.jsonl"),
        }
        _atomic_json(temporary / "coverage_manifest.json", finalized)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _source_generation_code_hash() -> str:
    paths = (
        Path(__file__).resolve(),
        Path(__file__).resolve().parents[1] / "data_pipeline" / "ashare" / "cache.py",
        Path(__file__).resolve().parents[1] / "data_pipeline" / "ashare" / "providers" / "tushare_client.py",
        Path(__file__).resolve().parents[1] / "data_pipeline" / "ashare" / "request_normalization.py",
        Path(__file__).resolve().parents[1] / "data_pipeline" / "ashare" / "dataset_registry.py",
    )
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(Path(__file__).resolve().parents[1]).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _valid_date(value: Any, field: str) -> str:
    text = str(value or "").strip()
    try:
        datetime.strptime(text, "%Y%m%d")
    except ValueError as exc:
        raise RuntimeError(f"{field} is not YYYYMMDD") from exc
    return text


def _bisect_dates(start: str, end: str) -> tuple[str, str]:
    left = datetime.strptime(start, "%Y%m%d").date()
    right = datetime.strptime(end, "%Y%m%d").date()
    midpoint = left + timedelta(days=(right - left).days // 2)
    return midpoint.strftime("%Y%m%d"), (midpoint + timedelta(days=1)).strftime("%Y%m%d")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise RuntimeError(f"expected object in {path}")
                rows.append(value)
    return rows


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _atomic_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(temporary, path)


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run bounded governed historical-union source generation.")
    parser.add_argument("--union-path", required=True)
    parser.add_argument("--securities-path", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--requests-per-minute", type=float, default=150.0)
    parser.add_argument("--dataset", action="append", choices=("suspensions", "st_status_daily", "name_changes"))
    args = parser.parse_args(argv)
    report = run_governed_backfill(
        GovernedBackfillConfig(
            Path(args.union_path),
            Path(args.securities_path),
            Path(args.output_root),
            requests_per_minute=args.requests_per_minute,
            datasets=tuple(args.dataset or ("suspensions", "st_status_daily", "name_changes")),
        )
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "union_count": report["union_count"],
                "datasets": {
                    key: {
                        "covered_stock_count": value["covered_stock_count"],
                        "failure_count": value["failure_count"],
                        "pointer_updated": value["pointer_updated"],
                    }
                    for key, value in report["datasets"].items()
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if report["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())

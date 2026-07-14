"""Governed, bounded Tushare repair for the Task 052-A historical union."""

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

from data_pipeline.ashare.cache import TushareResponseCache
from data_pipeline.ashare.config import AShareDataConfig
from data_pipeline.ashare.dataset_registry import DATASET_DEFINITIONS
from data_pipeline.ashare.providers.tushare_client import TushareHttpClient
from data_pipeline.ashare.rate_limit import RequestRateLimitConfig, SimpleRateLimiter
from data_pipeline.ashare.request_normalization import stable_json_hash, tushare_code_semantic_hash


DATASET_LIMITS = {"suspensions": 5000, "st_status_daily": 1000, "name_changes": 10000}


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
    codes = sorted({str(row["ts_code"]) for row in _read_jsonl(config.union_path) if row.get("ts_code")})
    if not codes:
        raise RuntimeError("historical union is empty")
    list_dates = {str(row.get("ts_code")): str(row.get("list_date") or "") for row in _read_jsonl(config.securities_path)}
    run_root = config.output_root
    run_root.mkdir(parents=True, exist_ok=True)
    cache = TushareResponseCache(run_root / "request_cache", enabled=True)
    limiter = SimpleRateLimiter(RequestRateLimitConfig(requests_per_minute=config.requests_per_minute))
    client = TushareHttpClient(AShareDataConfig.from_env(), rate_limiter=limiter)
    dataset_reports: dict[str, Any] = {}
    for dataset in config.datasets:
        dataset_reports[dataset] = _run_dataset(dataset, codes, list_dates, config, client, cache)
    complete = all(report["covered_stock_count"] == len(codes) for report in dataset_reports.values())
    manifest = {
        "artifact_type": "task_052_backfill_report",
        "schema_version": "1.0",
        "producer": "task_052_a.backfill",
        "status": "complete" if complete else "blocked",
        "union_count": len(codes),
        "union_sha256": _sha256(config.union_path),
        "observed_end_date": config.observed_end_date,
        "requests_per_minute": config.requests_per_minute,
        "code_semantic_hash": tushare_code_semantic_hash(),
        "datasets": dataset_reports,
        "rate_limit": limiter.summary().to_dict(),
        "token_persisted": False,
        "old_sources_mutated": False,
    }
    _atomic_json(run_root / "task_052_backfill_report.json", manifest)
    return manifest


def _run_dataset(dataset: str, codes: list[str], list_dates: dict[str, str], config: GovernedBackfillConfig, client: TushareHttpClient, cache: TushareResponseCache) -> dict[str, Any]:
    definition = DATASET_DEFINITIONS[dataset]
    staging = config.output_root / "staging" / dataset
    staging.mkdir(parents=True, exist_ok=True)
    ledger_path = staging / "coverage_ledger.jsonl"
    completed = _load_completed(ledger_path)
    records_by_pk: dict[tuple[str, ...], dict[str, Any]] = {}
    failures: list[dict[str, Any]] = []
    for ordinal, code in enumerate(codes, 1):
        if code in completed:
            rows = _read_jsonl(staging / "stocks" / f"{code}.jsonl")
            _merge_rows(records_by_pk, rows, definition.primary_key)
            continue
        if dataset == "suspensions":
            start_date, end_date = config.suspension_warmup_start_date, config.observed_end_date
        elif dataset == "st_status_daily":
            start_date = max("20160101", list_dates.get(code) or "20160101")
            end_date = config.observed_end_date
        else:
            start_date = end_date = None
        entry = {"ts_code": code, "ordinal": ordinal, "requested_start_date": start_date, "requested_end_date": end_date, "slices": [], "status": "failed"}
        try:
            rows = _fetch_with_backoff(dataset, code, start_date, end_date, client, cache, entry["slices"])
            normalized = [_validate_row(dataset, row, code) for row in rows]
            stock_path = staging / "stocks" / f"{code}.jsonl"
            _atomic_jsonl(stock_path, normalized)
            entry.update(status="success", returned_count=len(normalized), content_sha256=_sha256(stock_path), negative_attestation=not bool(normalized))
            _merge_rows(records_by_pk, normalized, definition.primary_key)
        except Exception as exc:
            entry.update(error_type=type(exc).__name__, error=str(exc)[:1000])
            failures.append(entry)
        _append_jsonl(ledger_path, entry)
    rows = sorted(records_by_pk.values(), key=lambda row: tuple(str(row.get(key) or "") for key in definition.primary_key))
    records_hash = stable_json_hash(rows)
    generation_id = records_hash[:24]
    target = config.output_root / "generations" / dataset / generation_id
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f".{dataset}-", dir=str(target.parent)))
        try:
            _atomic_jsonl(temporary / "records.jsonl", rows)
            proof = {
                "dataset": dataset,
                "api_name": definition.api_name,
                "contract_fields": list(definition.fields),
                "contract_primary_key": list(definition.primary_key),
                "contract_hash": stable_json_hash({"fields": definition.fields, "primary_key": definition.primary_key}),
                "generation_id": generation_id,
                "records_sha256": _sha256(temporary / "records.jsonl"),
                "record_count": len(rows),
                "covered_stock_count": len(completed | {str(row.get("ts_code")) for row in _read_jsonl(ledger_path) if row.get("status") == "success"}),
                "required_stock_count": len(codes),
                "code_semantic_hash": tushare_code_semantic_hash(),
                "old_sources_mutated": False,
            }
            _atomic_json(temporary / "coverage_manifest.json", proof)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    success_codes = {str(row.get("ts_code")) for row in _read_jsonl(ledger_path) if row.get("status") == "success"}
    pointer = {"dataset": dataset, "generation_id": generation_id, "generation_path": str(target), "content_hash": records_hash}
    _atomic_json(config.output_root / f"current_{dataset}.json", pointer)
    return {**pointer, "record_count": len(rows), "covered_stock_count": len(success_codes), "required_stock_count": len(codes), "failure_count": len(failures), "failures": failures[:20]}


def _fetch_with_backoff(dataset: str, code: str, start_date: str | None, end_date: str | None, client: TushareHttpClient, cache: TushareResponseCache, slices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    last_error: Exception | None = None
    for attempt in range(1, 6):
        try:
            return _fetch_recursive(dataset, code, start_date, end_date, client, cache, slices)
        except Exception as exc:
            last_error = exc
            message = str(exc)
            if "HTTP Error 429" not in message and "HTTP Error 307" not in message:
                raise
            delay = min(60.0, float(5 * (2 ** (attempt - 1))))
            slices.append({"retry_attempt": attempt, "retry_reason": message[:300], "backoff_seconds": delay})
            time.sleep(delay)
    raise RuntimeError(f"provider transport retries exhausted for {dataset}/{code}: {last_error}") from last_error


def _fetch_recursive(dataset: str, code: str, start_date: str | None, end_date: str | None, client: TushareHttpClient, cache: TushareResponseCache, slices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    definition = DATASET_DEFINITIONS[dataset]
    params: dict[str, Any] = {"ts_code": code}
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    cached = cache.read(definition.api_name, params, definition.fields)
    if cached and cached.hit:
        rows = cached.records
        source = "cache"
        negative = cached.negative_attestation is not None
    else:
        response = client.post_with_metadata(definition.api_name, params=params, fields=definition.fields)
        rows = response.records
        cache.write(definition.api_name, params, definition.fields, rows, response_code=response.response_code, response_message=response.response_message, response_fields=response.response_fields, item_count=response.item_count)
        source = "provider"
        negative = not bool(rows)
    slices.append({"start_date": start_date, "end_date": end_date, "returned_count": len(rows), "source": source, "negative_attestation": negative, "response_hash": stable_json_hash(rows)})
    limit = DATASET_LIMITS[dataset]
    if len(rows) < limit:
        return rows
    if not start_date or not end_date or start_date >= end_date:
        raise RuntimeError(f"{dataset} response reached limit without splittable date range for {code}")
    left_end, right_start = _bisect_dates(start_date, end_date)
    return _fetch_recursive(dataset, code, start_date, left_end, client, cache, slices) + _fetch_recursive(dataset, code, right_start, end_date, client, cache, slices)


def _validate_row(dataset: str, row: dict[str, Any], expected_code: str) -> dict[str, Any]:
    definition = DATASET_DEFINITIONS[dataset]
    missing = [field for field in definition.fields if field not in row]
    if missing:
        raise RuntimeError(f"{dataset} response missing fields: {','.join(missing)}")
    normalized = {field: row.get(field) for field in definition.fields}
    if str(normalized.get("ts_code") or "") != expected_code:
        raise RuntimeError(f"{dataset} returned unexpected ts_code")
    if dataset == "suspensions":
        if normalized.get("suspend_type") not in {"S", "R"}:
            raise RuntimeError("suspensions returned invalid suspend_type")
        normalized["suspend_timing"] = str(normalized.get("suspend_timing") or "unknown").strip() or "unknown"
    return normalized


def _merge_rows(target: dict[tuple[str, ...], dict[str, Any]], rows: list[dict[str, Any]], primary_key: tuple[str, ...]) -> None:
    for row in rows:
        key = tuple(str(row.get(field) or "") for field in primary_key)
        existing = target.get(key)
        if existing is not None and existing != row:
            raise RuntimeError(f"conflicting primary key: {key}")
        target[key] = row


def _bisect_dates(start: str, end: str) -> tuple[str, str]:
    left = datetime.strptime(start, "%Y%m%d").date()
    right = datetime.strptime(end, "%Y%m%d").date()
    midpoint = left + timedelta(days=(right - left).days // 2)
    return midpoint.strftime("%Y%m%d"), (midpoint + timedelta(days=1)).strftime("%Y%m%d")


def _load_completed(path: Path) -> set[str]:
    return {str(row.get("ts_code")) for row in _read_jsonl(path) if row.get("status") == "success"}


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
    parser = argparse.ArgumentParser(description="Run bounded Task 052-A historical-union repairs.")
    parser.add_argument("--union-path", required=True)
    parser.add_argument("--securities-path", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--requests-per-minute", type=float, default=150.0)
    parser.add_argument("--dataset", action="append", choices=("suspensions", "st_status_daily", "name_changes"))
    args = parser.parse_args(argv)
    report = run_governed_backfill(GovernedBackfillConfig(Path(args.union_path), Path(args.securities_path), Path(args.output_root), requests_per_minute=args.requests_per_minute, datasets=tuple(args.dataset or ("suspensions", "st_status_daily", "name_changes"))))
    print(json.dumps({"status": report["status"], "union_count": report["union_count"], "datasets": {key: {"covered_stock_count": value["covered_stock_count"], "failure_count": value["failure_count"]} for key, value in report["datasets"].items()}}, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())

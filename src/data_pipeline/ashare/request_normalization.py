"""Deterministic Tushare request identities for governed ingestion."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable


REQUEST_NORMALIZATION_VERSION = "tushare_request.v1"
CODE_SEMANTIC_VERSION = "task_052a_ingestion.v1"


def normalize_tushare_request(
    api_name: str,
    params: dict[str, Any] | None = None,
    fields: str | Iterable[str] | None = None,
) -> dict[str, Any]:
    """Return a token-free, stable request representation."""

    normalized_fields = _normalize_fields(fields)
    return {
        "version": REQUEST_NORMALIZATION_VERSION,
        "api_name": str(api_name).strip(),
        "params": _normalize_value(params or {}),
        "fields": normalized_fields,
    }


def tushare_request_fingerprint(
    api_name: str,
    params: dict[str, Any] | None = None,
    fields: str | Iterable[str] | None = None,
) -> str:
    return stable_json_hash(normalize_tushare_request(api_name, params=params, fields=fields))


def tushare_code_semantic_hash() -> str:
    """Invalidate cached responses when ingestion semantics change."""

    payload = {
        "version": CODE_SEMANTIC_VERSION,
        "request_normalization": REQUEST_NORMALIZATION_VERSION,
        "cache_envelope": "tushare_cache_envelope.v2",
        "strict_response_rows": True,
        "canonical_datasets": {
            "suspend_d": ["ts_code", "trade_date", "suspend_timing", "suspend_type"],
            "stock_st": ["ts_code", "name", "trade_date", "type", "type_name"],
            "namechange": ["ts_code", "name", "start_date", "end_date", "ann_date", "change_reason"],
        },
    }
    return stable_json_hash(payload)


def stable_json_hash(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_fields(fields: str | Iterable[str] | None) -> list[str]:
    if fields is None:
        return []
    values = fields.split(",") if isinstance(fields, str) else list(fields)
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        field = str(value).strip()
        if field and field not in seen:
            normalized.append(field)
            seen.add(field)
    return normalized


def _normalize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _normalize_value(value[key]) for key in sorted(value, key=lambda item: str(item))}
    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]
    if isinstance(value, set):
        return sorted((_normalize_value(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True))
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value).strip()

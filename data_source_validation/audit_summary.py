"""Summaries for API request audit logs."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .models import AuditSummary


def summarize_api_audit(path: str | Path) -> AuditSummary:
    audit_path = Path(path)
    rows = _read_jsonl(audit_path)
    total = len(rows)
    success = sum(1 for row in rows if row.get("status") == "success")
    failed = sum(1 for row in rows if row.get("status") == "error")
    cache_hits = sum(1 for row in rows if row.get("cache_hit") is True)
    durations = sorted(_to_float(row.get("duration_seconds")) for row in rows)
    durations = [value for value in durations if value is not None]
    return AuditSummary(
        path=str(audit_path),
        total_requests=total,
        success_requests=success,
        failed_requests=failed,
        cache_hit_count=cache_hits,
        cache_hit_rate=(cache_hits / total if total else 0.0),
        api_name_distribution=dict(Counter(str(row.get("api_name") or "") for row in rows)),
        dataset_distribution=dict(Counter(str(row.get("dataset") or "") for row in rows)),
        duration_p50=_percentile(durations, 0.50),
        duration_p95=_percentile(durations, 0.95),
        duration_max=max(durations) if durations else 0.0,
        errors_by_category=dict(Counter(_error_category(row.get("error")) for row in rows if row.get("error"))),
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    index = min(len(values) - 1, max(0, int(round((len(values) - 1) * q))))
    return float(values[index])


def _error_category(error: Any) -> str:
    text = str(error or "").lower()
    if "rate" in text or "limit" in text or "频次" in text:
        return "rate_limited"
    if "permission" in text or "权限" in text or "积分" in text:
        return "permission_denied"
    if "token" in text:
        return "token"
    if "timeout" in text or "network" in text or "url" in text:
        return "network"
    if "field" in text or "schema" in text:
        return "schema"
    return "other"

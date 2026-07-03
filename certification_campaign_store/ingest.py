"""Ingest factor certification queues."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact

from .models import FactorCertificationCampaignRecord, FactorCertificationItemRecord
from .registry import LocalFactorCertificationCampaignStore


def ingest_certification_queue(
    store_dir: str | Path,
    queue_path: str | Path,
    *,
    certification_campaign_id: str | None = None,
    max_items: int | None = None,
    rank_range: str | None = None,
    family_filter: str | None = None,
    source_filter: str | None = None,
    policy_profile: str = "sample_lenient_certification",
) -> dict[str, Any]:
    queue_path = Path(queue_path)
    rows = _read_jsonl(queue_path)
    selected = _apply_rank_range(rows, rank_range)
    selected = _apply_family_filter(selected, family_filter)
    selected = _apply_source_filter(selected, source_filter)
    if max_items and max_items > 0:
        selected = selected[:max_items]
    campaign_id = certification_campaign_id or f"factor_cert_campaign_{_hash(str(queue_path) + str(len(selected)))}"
    store = LocalFactorCertificationCampaignStore(store_dir)
    record = FactorCertificationCampaignRecord(
        certification_campaign_id=campaign_id,
        source_validation_campaign_id=_source_validation_campaign_id(selected),
        source_certification_queue_path=str(queue_path),
        certification_policy_profile=policy_profile,
        candidate_count=len(selected),
        status="registered",
        created_at=_utc_now(),
        metadata={"source_queue_count": len(rows)},
    )
    items = [_item_from_queue(row, campaign_id, idx + 1, policy_profile) for idx, row in enumerate(selected)]
    store.register_campaign(record)
    store.write_items(items)
    report = {
        "status": "success",
        "certification_campaign_id": campaign_id,
        "source_queue_path": str(queue_path),
        "source_queue_count": len(rows),
        "family_filter": family_filter or "",
        "source_filter": source_filter or "",
        "item_count": len(items),
        "duplicate_count": max(0, len(selected) - len({item.factor_id for item in items})),
    }
    report_path = store.root_dir / "factor_certification_campaign_ingest_report.json"
    write_json_artifact(report_path, report, "factor_certification_campaign_ingest_report", "certification_campaign_store")
    return {**report, "paths": store.paths() | {"factor_certification_campaign_ingest_report_path": str(report_path)}}


def _item_from_queue(row: dict[str, Any], campaign_id: str, idx: int, policy_profile: str) -> FactorCertificationItemRecord:
    metadata = row.get("metadata", {}) if isinstance(row.get("metadata"), dict) else {}
    leaderboard = metadata.get("leaderboard", {}) if isinstance(metadata.get("leaderboard"), dict) else {}
    source_result = leaderboard.get("metadata", {}).get("source_result", {}) if isinstance(leaderboard.get("metadata"), dict) else {}
    formula_hash = str(leaderboard.get("formula_hash") or source_result.get("formula_hash") or row.get("formula_hash") or "")
    validation_score = float(leaderboard.get("validation_score", row.get("validation_score", 0.0)) or 0.0)
    return FactorCertificationItemRecord(
        item_id=f"fci_{campaign_id}_{idx:04d}_{row.get('factor_id')}",
        queue_id=str(row.get("queue_id") or f"queue_{idx:04d}"),
        factor_id=str(row.get("factor_id")),
        formula_hash=formula_hash,
        validation_rank=int(row.get("priority", idx) or idx),
        validation_score=validation_score,
        certification_policy_profile=str(row.get("certification_policy_profile") or policy_profile),
        status="pending",
        factor_store_dir=str(row.get("factor_store_dir") or ""),
        metadata={"queue_item": row},
    )


def _source_validation_campaign_id(rows: list[dict[str, Any]]) -> str | None:
    for row in rows:
        metadata = row.get("metadata", {}) if isinstance(row.get("metadata"), dict) else {}
        candidate = metadata.get("candidate", {}) if isinstance(metadata.get("candidate"), dict) else {}
        value = candidate.get("source_campaign_id")
        if value:
            return str(value)
    return None


def _apply_rank_range(rows: list[dict[str, Any]], rank_range: str | None) -> list[dict[str, Any]]:
    if not rank_range:
        return rows
    start, _, end = rank_range.partition(":")
    lo = int(start or 1)
    hi = int(end or len(rows))
    return [row for row in rows if lo <= int(row.get("priority", 0) or 0) <= hi]


def _apply_family_filter(rows: list[dict[str, Any]], family_filter: str | None) -> list[dict[str, Any]]:
    families = _split_filter(family_filter)
    if not families:
        return rows
    return [row for row in rows if families & _row_families(row)]


def _apply_source_filter(rows: list[dict[str, Any]], source_filter: str | None) -> list[dict[str, Any]]:
    sources = _split_filter(source_filter)
    if not sources:
        return rows
    return [row for row in rows if _row_source(row) in sources]


def _row_families(row: dict[str, Any]) -> set[str]:
    metadata = row.get("metadata", {}) if isinstance(row.get("metadata"), dict) else {}
    candidate = metadata.get("candidate", {}) if isinstance(metadata.get("candidate"), dict) else {}
    leaderboard = metadata.get("leaderboard", {}) if isinstance(metadata.get("leaderboard"), dict) else {}
    values = candidate.get("family_tags") or leaderboard.get("family_tags") or row.get("family_tags") or []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return set()
    return {str(value) for value in values if value}


def _row_source(row: dict[str, Any]) -> str:
    metadata = row.get("metadata", {}) if isinstance(row.get("metadata"), dict) else {}
    candidate = metadata.get("candidate", {}) if isinstance(metadata.get("candidate"), dict) else {}
    leaderboard = metadata.get("leaderboard", {}) if isinstance(metadata.get("leaderboard"), dict) else {}
    return str(
        candidate.get("source")
        or candidate.get("source_campaign_id")
        or leaderboard.get("source")
        or row.get("source")
        or row.get("source_campaign_id")
        or ""
    )


def _split_filter(value: str | None) -> set[str]:
    if not value:
        return set()
    return {part.strip() for part in value.split(",") if part.strip()}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

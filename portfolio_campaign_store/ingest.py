"""Ingest certified factor pools for portfolio campaigns."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact

from .models import PortfolioCandidateItemRecord, PortfolioCertificationCampaignRecord
from .registry import LocalPortfolioCampaignStore


def ingest_certified_factor_pool(
    store_dir: str | Path,
    certified_factor_pool_path: str | Path,
    *,
    portfolio_campaign_id: str | None = None,
    max_items: int | None = None,
    rank_range: str | None = None,
    family_filter: str | None = None,
    source_filter: str | None = None,
    portfolio_policy_profile: str = "sample_lenient_portfolio",
    scenario_profile: str = "sample",
) -> dict[str, Any]:
    path = Path(certified_factor_pool_path)
    rows = [row for row in _read_jsonl(path) if row.get("selected_for_portfolio_lab", True)]
    rows = _apply_rank_range(rows, rank_range)
    rows = _apply_family_filter(rows, family_filter)
    rows = _apply_source_filter(rows, source_filter)
    if max_items and max_items > 0:
        rows = rows[:max_items]
    campaign_id = portfolio_campaign_id or f"portfolio_campaign_{_hash(str(path) + str(len(rows)))}"
    store = LocalPortfolioCampaignStore(store_dir)
    campaign = PortfolioCertificationCampaignRecord(
        portfolio_campaign_id=campaign_id,
        source_factor_certification_campaign_id=_source_campaign_id(rows),
        certified_factor_pool_path=str(path),
        portfolio_policy_profile=portfolio_policy_profile,
        scenario_profile=scenario_profile,
        factor_count=len(rows),
        status="registered",
        created_at=_utc_now(),
        metadata={"source_factor_count": len(rows)},
    )
    items = [_item_from_pool(row, campaign_id, idx + 1) for idx, row in enumerate(rows)]
    store.register_campaign(campaign)
    store.write_items(items)
    report = {
        "status": "success",
        "portfolio_campaign_id": campaign_id,
        "factor_count": len(rows),
        "item_count": len(items),
        "family_filter": family_filter or "",
        "source_filter": source_filter or "",
    }
    report_path = store.root_dir / "portfolio_campaign_ingest_report.json"
    write_json_artifact(report_path, report, "portfolio_campaign_ingest_report", "portfolio_campaign_store")
    return {**report, "paths": store.paths() | {"portfolio_campaign_ingest_report_path": str(report_path)}}


def _item_from_pool(row: dict[str, Any], campaign_id: str, idx: int) -> PortfolioCandidateItemRecord:
    return PortfolioCandidateItemRecord(
        item_id=f"pci_{campaign_id}_{idx:04d}_{row.get('factor_id')}",
        factor_id=str(row.get("factor_id")),
        formula_hash=str(row.get("formula_hash") or ""),
        certified_factor_pool_rank=int(row.get("priority", idx) or idx),
        factor_store_dir=str(row.get("factor_store_dir") or ""),
        status="pending",
        metadata={"certified_factor_pool_record": row},
    )


def _source_campaign_id(rows: list[dict[str, Any]]) -> str | None:
    for row in rows:
        metadata = row.get("metadata", {}) if isinstance(row.get("metadata"), dict) else {}
        item = metadata.get("campaign_item", {}) if isinstance(metadata.get("campaign_item"), dict) else {}
        value = item.get("item_id")
        if value:
            return str(value).split("_000", 1)[0]
    return None


def _apply_rank_range(rows: list[dict[str, Any]], rank_range: str | None) -> list[dict[str, Any]]:
    if not rank_range:
        return rows
    start, _, end = rank_range.partition(":")
    lo = int(start or 1)
    hi = int(end or len(rows))
    return [row for row in rows if lo <= int(row.get("priority", row.get("certified_factor_pool_rank", 0)) or 0) <= hi]


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
    item = metadata.get("campaign_item", {}) if isinstance(metadata.get("campaign_item"), dict) else {}
    queue_item = item.get("metadata", {}).get("queue_item", {}) if isinstance(item.get("metadata"), dict) else {}
    values = row.get("family_tags") or item.get("family_tags") or queue_item.get("family_tags") or []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return set()
    return {str(value) for value in values if value}


def _row_source(row: dict[str, Any]) -> str:
    metadata = row.get("metadata", {}) if isinstance(row.get("metadata"), dict) else {}
    item = metadata.get("campaign_item", {}) if isinstance(metadata.get("campaign_item"), dict) else {}
    queue_item = item.get("metadata", {}).get("queue_item", {}) if isinstance(item.get("metadata"), dict) else {}
    return str(
        row.get("source")
        or row.get("source_campaign_id")
        or item.get("source")
        or queue_item.get("source")
        or queue_item.get("source_campaign_id")
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

"""Candidate pool ingest for validation campaigns."""

from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact

from .models import ValidationCampaignRecord, ValidationCandidateRecord
from .registry import LocalValidationCampaignStore


def ingest_candidate_pool(
    store_dir: str | Path,
    candidate_pool_path: str | Path,
    *,
    validation_campaign_id: str | None = None,
    source_alpha_experiment_id: str | None = None,
    max_candidates: int | None = None,
    rank_range: str | None = None,
    family_filter: str | None = None,
    source_filter: str | None = None,
    shard_count: int = 1,
    split_method: str = "simple_walk_forward",
    validation_policy_profile: str = "sample",
    data_freeze_id: str | None = None,
    data_freeze_hash: str | None = None,
    feature_set_name: str | None = None,
    matrix_cache_path: str | None = None,
    stratified: bool = True,
    seed: int = 42,
) -> dict[str, Any]:
    store = LocalValidationCampaignStore(store_dir)
    rows = _read_jsonl(candidate_pool_path)
    filtered = _filter_rows(rows, max_candidates=max_candidates, rank_range=rank_range, family_filter=family_filter, source_filter=source_filter)
    if stratified:
        filtered = _stratified_rows(filtered, max_candidates=max_candidates, seed=seed)
    candidates, duplicates = _dedupe_candidates(filtered)
    campaign_id = validation_campaign_id or _campaign_id(candidate_pool_path, len(candidates), seed)
    record = ValidationCampaignRecord(
        validation_campaign_id=campaign_id,
        source_alpha_experiment_id=source_alpha_experiment_id or _first_source_campaign(candidates),
        source_candidate_pool_path=str(candidate_pool_path),
        data_freeze_id=data_freeze_id,
        data_freeze_hash=data_freeze_hash,
        feature_set_name=feature_set_name or _first_feature_version(candidates),
        matrix_cache_path=matrix_cache_path,
        validation_policy_profile=validation_policy_profile,
        split_method=split_method,
        candidate_count=len(candidates),
        shard_count=max(1, int(shard_count or 1)),
        status="registered",
        created_at=_utc_now(),
        metadata={"duplicates": duplicates, "source_rows": len(rows)},
    )
    store.register_campaign(record)
    candidate_records = [
        ValidationCandidateRecord(
            validation_candidate_id=f"{campaign_id}_cand_{idx:05d}",
            factor_id=str(row.get("factor_id")),
            formula_hash=str(row.get("formula_hash") or ""),
            formula_names=list(row.get("formula_names") or []),
            alpha_rank=int(row.get("rank", idx + 1) or idx + 1),
            alpha_score=float(row.get("final_score", row.get("alpha_score", 0.0)) or 0.0),
            family_tags=_family_tags(row),
            source_campaign_id=str(row.get("source_campaign") or ""),
            feature_version=str(row.get("feature_version") or ""),
            factor_store_dir=str(row.get("factor_store_dir") or ""),
            factor_values_path=str(row.get("factor_values_path") or ""),
            status="pending",
            metadata={"source_candidate": row},
        )
        for idx, row in enumerate(candidates)
    ]
    store.write_candidates(candidate_records)
    report = {
        "status": "success",
        "validation_campaign_id": campaign_id,
        "source_candidate_pool_path": str(candidate_pool_path),
        "source_candidate_count": len(rows),
        "candidate_count": len(candidate_records),
        "duplicate_count": len(duplicates),
        "shard_count": record.shard_count,
        "duplicates": duplicates,
    }
    report_path = write_json_artifact(Path(store_dir) / "validation_candidate_dedup_report.json", report, "validation_candidate_dedup_report", "validation_campaign_store")
    return report | {"paths": store.paths() | {"validation_candidate_dedup_report_path": str(report_path)}}


def _read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        return []
    return [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]


def _filter_rows(
    rows: list[dict[str, Any]],
    *,
    max_candidates: int | None,
    rank_range: str | None,
    family_filter: str | None,
    source_filter: str | None,
) -> list[dict[str, Any]]:
    selected = sorted(rows, key=lambda row: int(row.get("rank", 10**9) or 10**9))
    if rank_range:
        start, end = _rank_range(rank_range)
        selected = [row for row in selected if start <= int(row.get("rank", 10**9) or 10**9) <= end]
    if family_filter:
        allowed = {item.strip() for item in family_filter.split(",") if item.strip()}
        selected = [row for row in selected if _family(row) in allowed]
    if source_filter:
        allowed = {item.strip() for item in source_filter.split(",") if item.strip()}
        selected = [row for row in selected if str(row.get("source_campaign", "")) in allowed]
    if max_candidates and max_candidates > 0:
        selected = selected[:max_candidates]
    return selected


def _stratified_rows(rows: list[dict[str, Any]], *, max_candidates: int | None, seed: int) -> list[dict[str, Any]]:
    if not max_candidates or len(rows) <= max_candidates:
        return rows
    selected: list[dict[str, Any]] = []
    selected.extend(rows[: max(1, max_candidates // 2)])
    rng = random.Random(seed)
    by_family: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_family.setdefault(_family(row), []).append(row)
    for family_rows in by_family.values():
        if len(selected) >= max_candidates:
            break
        if family_rows[0] not in selected:
            selected.append(family_rows[0])
    remaining = [row for row in rows if row not in selected]
    rng.shuffle(remaining)
    selected.extend(remaining[: max(0, max_candidates - len(selected))])
    return sorted(selected[:max_candidates], key=lambda row: int(row.get("rank", 10**9) or 10**9))


def _dedupe_candidates(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    seen_key: set[tuple[str, str, str]] = set()
    seen_factor: set[str] = set()
    selected: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    for row in rows:
        key = (str(row.get("formula_hash") or ""), str(row.get("feature_version") or ""), str(row.get("operator_version") or ""))
        factor_id = str(row.get("factor_id") or "")
        if key in seen_key or factor_id in seen_factor:
            duplicates.append({"factor_id": factor_id, "formula_hash": key[0], "reason": "duplicate_candidate"})
            continue
        seen_key.add(key)
        seen_factor.add(factor_id)
        selected.append(row)
    return selected, duplicates


def _rank_range(value: str) -> tuple[int, int]:
    if "-" in value:
        left, right = value.split("-", 1)
        return int(left), int(right)
    rank = int(value)
    return rank, rank


def _family(row: dict[str, Any]) -> str:
    tags = _family_tags(row)
    return str(row.get("family") or (tags[0] if tags else "general"))


def _family_tags(row: dict[str, Any]) -> list[str]:
    tags = row.get("family_tags") or (row.get("metadata") or {}).get("alpha_family_tags") or []
    if isinstance(tags, list) and tags:
        return [str(item) for item in tags]
    family = row.get("family")
    return [str(family)] if family else ["general"]


def _first_source_campaign(rows: list[dict[str, Any]]) -> str | None:
    return str(rows[0].get("source_campaign") or "") if rows else None


def _first_feature_version(rows: list[dict[str, Any]]) -> str | None:
    return str(rows[0].get("feature_version") or "") if rows else None


def _campaign_id(path: str | Path, count: int, seed: int) -> str:
    digest = hashlib.sha256(f"{path}|{count}|{seed}".encode("utf-8")).hexdigest()[:12]
    return f"validation_campaign_{digest}"


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

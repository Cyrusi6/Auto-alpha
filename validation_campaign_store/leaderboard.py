"""Validation leaderboard scoring."""

from __future__ import annotations

from .models import ValidationCandidateResult, ValidationLeaderboardRecord
from .registry import LocalValidationCampaignStore


def build_validation_leaderboard(store_dir: str, *, top_k: int = 100, family_cap: int = 20, source_cap: int = 50) -> list[ValidationLeaderboardRecord]:
    store = LocalValidationCampaignStore(store_dir)
    rows = [ValidationCandidateResult(**_result_defaults(row)) for row in store.load_results()]
    ordered = sorted(rows, key=lambda row: (row.blocker_count == 0, row.validation_score, row.factor_id), reverse=True)
    selected: list[ValidationCandidateResult] = []
    families: dict[str, int] = {}
    sources: dict[str, int] = {}
    for row in ordered:
        metadata = row.metadata or {}
        candidate = metadata.get("candidate", {}) if isinstance(metadata.get("candidate"), dict) else {}
        family = _family(candidate)
        source = str(candidate.get("source_campaign_id") or "")
        if families.get(family, 0) >= family_cap or sources.get(source, 0) >= source_cap:
            continue
        families[family] = families.get(family, 0) + 1
        sources[source] = sources.get(source, 0) + 1
        selected.append(row)
        if len(selected) >= top_k:
            break
    leaderboard = [
        ValidationLeaderboardRecord(
            rank=idx + 1,
            validation_candidate_id=row.validation_candidate_id,
            factor_id=row.factor_id,
            formula_hash=row.formula_hash,
            validation_score=row.validation_score,
            score_components=dict((row.metadata or {}).get("score_components", {})),
            certification_ready=row.blocker_count == 0 and row.validation_status in {"passed", "warning"},
            reason="certification ready" if row.blocker_count == 0 else "validation blockers exist",
            metadata=row.metadata,
        )
        for idx, row in enumerate(selected)
    ]
    store.write_leaderboard(leaderboard)
    return leaderboard


def _result_defaults(row: dict) -> dict:
    payload = dict(row)
    payload.setdefault("metadata", {})
    payload.setdefault("selected_for_certification", False)
    return payload


def _family(candidate: dict) -> str:
    tags = candidate.get("family_tags") or []
    if isinstance(tags, list) and tags:
        return str(tags[0])
    return "general"

"""Novelty scoring for Alpha Factory candidates."""

from __future__ import annotations


def score_novelty(candidates, existing_factors) -> dict[str, float]:
    existing_hashes = {record.formula_hash for record in existing_factors}
    existing_names = [set(record.formula or []) for record in existing_factors]
    existing_families = {
        str(family)
        for record in existing_factors
        for family in ((record.metadata or {}).get("alpha_family_tags") or [])
    }
    candidate_family_counts: dict[str, int] = {}
    for candidate in candidates:
        for family in candidate.family_tags or ["general"]:
            candidate_family_counts[str(family)] = candidate_family_counts.get(str(family), 0) + 1
    max_family_count = max(candidate_family_counts.values(), default=1)
    scores: dict[str, float] = {}
    for candidate in candidates:
        base = 1.0 if candidate.formula_hash not in existing_hashes else 0.0
        candidate_names = set(candidate.formula_names)
        max_overlap = 0.0
        for names in existing_names:
            union = candidate_names | names
            if union:
                max_overlap = max(max_overlap, len(candidate_names & names) / len(union))
        families = [str(value) for value in (candidate.family_tags or ["general"])]
        cohort_novelty = max(
            (1.0 - (candidate_family_counts.get(family, 1) - 1) / max(max_family_count, 1))
            for family in families
        )
        historical_novelty = 1.0 if any(family not in existing_families for family in families) else 0.0
        score = 0.35 * base + 0.25 * (1.0 - max_overlap) + 0.20 * cohort_novelty + 0.20 * historical_novelty
        scores[candidate.alpha_candidate_id] = float(max(0.0, min(1.0, score)))
    return scores

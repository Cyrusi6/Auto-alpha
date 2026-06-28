"""Novelty scoring for Alpha Factory candidates."""

from __future__ import annotations


def score_novelty(candidates, existing_factors) -> dict[str, float]:
    existing_hashes = {record.formula_hash for record in existing_factors}
    existing_names = [set(record.formula or []) for record in existing_factors]
    scores: dict[str, float] = {}
    for candidate in candidates:
        base = 1.0 if candidate.formula_hash not in existing_hashes else 0.0
        candidate_names = set(candidate.formula_names)
        max_overlap = 0.0
        for names in existing_names:
            union = candidate_names | names
            if union:
                max_overlap = max(max_overlap, len(candidate_names & names) / len(union))
        scores[candidate.alpha_candidate_id] = float(max(0.0, min(1.0, base - 0.5 * max_overlap + 0.25)))
    return scores

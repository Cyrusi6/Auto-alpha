"""Multi-objective Alpha Factory scoring."""

from __future__ import annotations

from dataclasses import replace


def score_candidates(candidates, proxy_rows, full_eval_rows, novelty_scores) -> tuple[list, list[dict]]:
    proxy_by_id = {row.get("alpha_candidate_id"): row for row in proxy_rows}
    full_by_hash = {}
    for row in full_eval_rows:
        request = row.get("request", {}) if isinstance(row.get("request"), dict) else {}
        formula_hash = request.get("formula_hash")
        if formula_hash:
            full_by_hash[formula_hash] = row
    updated = []
    scored_rows = []
    for candidate in candidates:
        proxy = proxy_by_id.get(candidate.alpha_candidate_id, {})
        full = full_by_hash.get(candidate.formula_hash, {})
        full_score = float(full.get("score", 0.0) or 0.0)
        proxy_score = float(proxy.get("proxy_score", candidate.proxy_score) or 0.0)
        novelty = float(novelty_scores.get(candidate.alpha_candidate_id, 0.5))
        complexity_penalty = 0.01 * float(candidate.complexity)
        lookback_penalty = 0.002 * float(candidate.lookback)
        final = full_score + 0.5 * proxy_score + 0.2 * novelty - complexity_penalty - lookback_penalty
        status = candidate.status
        reject_reason = candidate.reject_reason
        if status not in {"rejected"} and proxy.get("status") == "proxy_passed":
            status = "scored"
        row = candidate.to_dict() | {
            "proxy_score": proxy_score,
            "full_eval_score": full_score,
            "novelty_score": novelty,
            "final_score": float(final),
            "score_components": {
                "full_eval_score": full_score,
                "proxy_score": proxy_score,
                "novelty_score": novelty,
                "complexity_penalty": complexity_penalty,
                "lookback_penalty": lookback_penalty,
            },
        }
        scored_rows.append(row)
        updated.append(
            replace(
                candidate,
                proxy_score=proxy_score,
                full_eval_score=full_score,
                novelty_score=novelty,
                final_score=float(final),
                status=status,
                reject_reason=reject_reason,
                metadata={**candidate.metadata, "score_components": row["score_components"]},
            )
        )
    return updated, scored_rows

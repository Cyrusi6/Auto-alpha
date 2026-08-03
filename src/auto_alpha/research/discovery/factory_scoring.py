"""Dimensionless multi-objective Alpha Factory scoring; consolidated from auto_alpha.research.discovery.factory."""

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
        final = float(0.8 * full_score + 0.2 * proxy_score) if full else float(proxy_score)
        status = candidate.status
        reject_reason = candidate.reject_reason
        full_status = str(full.get("status") or "")
        if status != "rejected" and proxy.get("status") == "proxy_passed":
            if full_status == "validation_candidate":
                status = "validation_candidate"
                reject_reason = None
            elif full_status == "data_blocked":
                status = "data_blocked"
                reject_reason = ";".join(full.get("gate_reasons") or full.get("data_blockers") or ["full_research_data_blocked"])
            elif full_status:
                status = "research_rejected"
                reject_reason = ";".join(full.get("gate_reasons") or ["full_eval_oos_gate_not_passed"])
            else:
                status = "research_evaluated"
                reject_reason = "positive_oos_evidence_missing"
        row = candidate.to_dict() | {
            "proxy_score": proxy_score,
            "full_eval_score": full_score,
            "novelty_score": novelty,
            "final_score": float(final),
            "score_components": {
                "full_eval_score": full_score,
                "proxy_score": proxy_score,
                "proxy_normalized_objectives": proxy.get("normalized_objectives") or {},
                "full_normalized_objectives": full.get("normalized_objectives") or {},
                "novelty_score": novelty,
                "aggregation": "0.8*full_standardized+0.2*proxy_standardized" if full else "proxy_standardized_only",
                "score_method": "dimensionless_cohort_multi_objective_v1",
            },
            "status": status,
            "reject_reason": reject_reason,
            "validation_status": full_status or "not_evaluated",
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
                validation_status=full_status or "not_evaluated",
                reject_reason=reject_reason,
                metadata={
                    **candidate.metadata,
                    "score_components": row["score_components"],
                    "gate_decision": full.get("gate_decision") if isinstance(full, dict) else None,
                },
            )
        )
    return updated, scored_rows

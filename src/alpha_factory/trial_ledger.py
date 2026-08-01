"""Immutable campaign trial ledger and selection-bias accounting."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact


def write_trial_ledger(
    *,
    candidates,
    static_rows: list[dict[str, Any]],
    proxy_rows: list[dict[str, Any]],
    full_rows: list[dict[str, Any]],
    scored_rows: list[dict[str, Any]],
    shortlist,
    campaign_id: str,
    policy_id: str,
    policy_hash: str,
    output_dir: str | Path,
) -> tuple[dict[str, str], dict[str, Any]]:
    static_by_id = {str(row.get("alpha_candidate_id") or ""): row for row in static_rows}
    proxy_by_id = {str(row.get("alpha_candidate_id") or ""): row for row in proxy_rows}
    full_by_hash = {str(row.get("formula_hash") or (row.get("request") or {}).get("formula_hash") or ""): row for row in full_rows}
    scored_by_id = {str(row.get("alpha_candidate_id") or ""): row for row in scored_rows}
    shortlist_ids = {item.alpha_candidate_id for item in shortlist}
    rows = []
    for ordinal, candidate in enumerate(candidates):
        static = static_by_id.get(candidate.alpha_candidate_id, {})
        proxy = proxy_by_id.get(candidate.alpha_candidate_id, {})
        full = full_by_hash.get(candidate.formula_hash, {})
        scored = scored_by_id.get(candidate.alpha_candidate_id, {})
        rows.append(
            {
                "trial_ordinal": ordinal,
                "alpha_candidate_id": candidate.alpha_candidate_id,
                "formula_hash": candidate.formula_hash,
                "source": candidate.source,
                "family_tags": candidate.family_tags,
                "complexity": candidate.complexity,
                "lookback": candidate.lookback,
                "static_status": static.get("status", "not_evaluated"),
                "static_errors": static.get("errors") or [],
                "proxy_status": proxy.get("status", "not_evaluated"),
                "proxy_score": proxy.get("proxy_score"),
                "proxy_blockers": proxy.get("proxy_blockers") or [],
                "proxy_target_sampled": bool(proxy.get("sampled_dates")),
                "full_research_selected": bool(full),
                "full_research_status": full.get("status", "not_selected"),
                "full_research_score": full.get("score"),
                "raw_p_value": full.get("raw_p_value"),
                "bh_q_value": full.get("bh_q_value"),
                "selection_adjusted_p_value": full.get("selection_adjusted_p_value"),
                "final_status": scored.get("status", candidate.status),
                "final_score": scored.get("final_score", candidate.final_score),
                "shortlisted": candidate.alpha_candidate_id in shortlist_ids,
                "target_or_outcome_read": bool(proxy.get("sampled_dates")) or bool(full),
                "selection_data_reused": True,
                "untouched_holdout": False,
                "evidence_level": "retrospective_research_only",
            }
        )
    root_payload = {
        "campaign_id": campaign_id,
        "policy_hash": policy_hash,
        "rows": rows,
    }
    trial_root = hashlib.sha256(
        json.dumps(root_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    summary = _selection_bias_summary(rows) | {
        "campaign_id": campaign_id,
        "policy_id": policy_id,
        "policy_hash": policy_hash,
        "trial_root": trial_root,
        "status": "complete",
        "selection_data_reused": True,
        "untouched_holdout": False,
        "certification_ready": False,
    }
    target = Path(output_dir)
    ledger_path = write_jsonl_artifact(
        target / "alpha_trial_ledger.jsonl",
        rows,
        "alpha_trial_ledger",
        "alpha_factory",
    )
    report_path = write_json_artifact(
        target / "alpha_selection_bias_report.json",
        summary,
        "alpha_selection_bias_report",
        "alpha_factory",
    )
    return {
        "alpha_trial_ledger_path": str(ledger_path),
        "alpha_selection_bias_report_path": str(report_path),
    }, summary


def _selection_bias_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    counts = {
        "generated": total,
        "static_passed": sum(row["static_status"] == "passed" for row in rows),
        "proxy_passed": sum(row["proxy_status"] == "proxy_passed" for row in rows),
        "full_research_selected": sum(bool(row["full_research_selected"]) for row in rows),
        "validation_candidate": sum(row["full_research_status"] == "validation_candidate" for row in rows),
        "shortlisted": sum(bool(row["shortlisted"]) for row in rows),
    }
    stage_rates = {
        "static_pass_rate": counts["static_passed"] / max(counts["generated"], 1),
        "proxy_selection_rate": counts["proxy_passed"] / max(counts["static_passed"], 1),
        "full_selection_rate": counts["full_research_selected"] / max(counts["proxy_passed"], 1),
        "validation_candidate_rate": counts["validation_candidate"] / max(counts["full_research_selected"], 1),
        "shortlist_rate": counts["shortlisted"] / max(counts["generated"], 1),
    }
    source_counts: dict[str, int] = {}
    family_counts: dict[str, int] = {}
    for row in rows:
        source = str(row.get("source") or "unknown")
        source_counts[source] = source_counts.get(source, 0) + 1
        for family in row.get("family_tags") or ["general"]:
            family_counts[str(family)] = family_counts.get(str(family), 0) + 1
    return {
        "trial_count": total,
        "unique_formula_hash_count": len({row["formula_hash"] for row in rows}),
        "stage_counts": counts,
        "stage_rates": stage_rates,
        "source_trial_distribution": source_counts,
        "family_trial_distribution": family_counts,
        "target_exposed_trial_count": sum(bool(row["target_or_outcome_read"]) for row in rows),
        "minimum_bh_q_value": min((float(row["bh_q_value"]) for row in rows if row.get("bh_q_value") is not None), default=1.0),
    }

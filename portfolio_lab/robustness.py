"""Robustness aggregation for portfolio lab trials."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from portfolio_optimizer import PortfolioPolicy

from .models import PortfolioTrialMetrics


def build_robustness_report(metrics: list[PortfolioTrialMetrics], policies: list[PortfolioPolicy]) -> dict[str, Any]:
    by_policy: dict[str, list[PortfolioTrialMetrics]] = defaultdict(list)
    for row in metrics:
        by_policy[row.policy_id].append(row)
    rows = []
    for policy in policies:
        policy_rows = by_policy.get(policy.policy_id, [])
        successful = [row for row in policy_rows if row.status == "success"]
        scores = [row.score for row in successful]
        scenario_pass_ratio = len(successful) / len(policy_rows) if policy_rows else 0.0
        mean_score = sum(scores) / len(scores) if scores else -999.0
        worst_score = min(scores) if scores else -999.0
        avg_turnover = sum(row.avg_turnover for row in successful) / len(successful) if successful else 0.0
        avg_reject_rate = sum(row.constraint_reject_rate for row in successful) / len(successful) if successful else 0.0
        rows.append(
            {
                "policy_id": policy.policy_id,
                "policy_name": policy.policy_name,
                "portfolio_method": policy.portfolio_method,
                "trial_count": len(policy_rows),
                "successful_trials": len(successful),
                "scenario_pass_ratio": float(scenario_pass_ratio),
                "mean_score": float(mean_score),
                "worst_score": float(worst_score),
                "avg_turnover": float(avg_turnover),
                "avg_reject_rate": float(avg_reject_rate),
                "selection_score": float(mean_score + 0.25 * worst_score + scenario_pass_ratio - 0.1 * avg_turnover - 0.2 * avg_reject_rate),
            }
        )
    rows.sort(key=lambda item: (float(item["selection_score"]), float(item["mean_score"])), reverse=True)
    return {
        "policy_count": len(policies),
        "trial_count": len(metrics),
        "successful_trial_count": sum(row.status == "success" for row in metrics),
        "ranked_policies": rows,
        "selected_policy_id": rows[0]["policy_id"] if rows else None,
        "selected_score": rows[0]["selection_score"] if rows else -999.0,
    }

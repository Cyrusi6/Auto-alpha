"""Reward helpers for neural formula search."""

from __future__ import annotations

from typing import Any


INVALID_REWARD = -1.0


def formula_reward_from_research_result(result: Any, invalid_reward: float = INVALID_REWARD) -> float:
    if result is None:
        return float(invalid_reward)
    status = getattr(result, "status", None)
    score = float(getattr(result, "score", 0.0) or 0.0)
    if status == "approved":
        return float(1.0 + score)
    if status == "rejected":
        return float(0.25 + score)
    if status == "skipped_existing":
        return float(0.1 + score)
    if status == "error":
        return float(invalid_reward)
    return float(score)

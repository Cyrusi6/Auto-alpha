"""Reward helpers for neural formula search."""

from __future__ import annotations

from typing import Any


INVALID_REWARD = -1.0


def formula_reward_from_research_result(result: Any, invalid_reward: float = INVALID_REWARD) -> float:
    if result is None:
        return float(invalid_reward)
    status = getattr(result, "status", None)
    score = float(getattr(result, "score", 0.0) or 0.0)
    if status == "error":
        return float(invalid_reward)
    if status == "skipped_existing":
        return 0.0
    return float(score)

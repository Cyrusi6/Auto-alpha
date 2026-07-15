"""Pre-registered Task 055-A portfolio and execution policies."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any, Mapping, Sequence

import numpy as np


@dataclass(frozen=True)
class ScenarioPolicy:
    name: str
    top_n: int = 20
    lot_size: int = 100
    adv_participation: float = 0.10
    commission_rate: float = 0.0003
    minimum_commission: float = 5.0
    stamp_duty_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001
    slippage_bps: float = 5.0
    impact_bps: float = 5.0
    sell_cash_lag: int = 1
    buy_share_lag: int = 1

    def __post_init__(self) -> None:
        if self.top_n < 0:
            raise ValueError("top_n must be non-negative")
        if self.lot_size <= 0:
            raise ValueError("lot_size must be positive")
        if not 0.0 <= self.adv_participation <= 1.0:
            raise ValueError("adv_participation must be between zero and one")
        if self.sell_cash_lag < 0 or self.buy_share_lag < 1:
            raise ValueError("Task055A requires non-negative cash lag and T+1 buy shares")
        rates = (
            self.commission_rate,
            self.minimum_commission,
            self.stamp_duty_rate,
            self.transfer_fee_rate,
            self.slippage_bps,
            self.impact_bps,
        )
        if any(not np.isfinite(value) or value < 0 for value in rates):
            raise ValueError("cost policy values must be finite and non-negative")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


BASELINE = ScenarioPolicy(name="baseline")
LOW_COST = replace(
    BASELINE,
    name="low_cost",
    commission_rate=0.00015,
    minimum_commission=1.0,
    slippage_bps=2.5,
    impact_bps=2.5,
)
HIGH_COST = replace(
    BASELINE,
    name="high_cost",
    commission_rate=0.0005,
    minimum_commission=5.0,
    slippage_bps=10.0,
    impact_bps=10.0,
)
LOW_CAPACITY = replace(BASELINE, name="low_capacity", adv_participation=0.05)
HIGH_CAPACITY = replace(BASELINE, name="high_capacity", adv_participation=0.20)

PREREGISTERED_SCENARIOS: dict[str, ScenarioPolicy] = {
    policy.name: policy
    for policy in (BASELINE, LOW_COST, HIGH_COST, LOW_CAPACITY, HIGH_CAPACITY)
}
SCENARIO_POLICIES = PREREGISTERED_SCENARIOS
PREREGISTERED_SCENARIO_POLICIES = PREREGISTERED_SCENARIOS


def get_scenario_policy(name: str) -> ScenarioPolicy:
    aliases = {
        "base": "baseline",
        "cost_low": "low_cost",
        "cost_high": "high_cost",
        "capacity_low": "low_capacity",
        "capacity_high": "high_capacity",
    }
    canonical = aliases.get(str(name), str(name))
    try:
        return PREREGISTERED_SCENARIOS[canonical]
    except KeyError as exc:
        raise ValueError(f"unknown Task055A scenario policy: {name}") from exc


def strict_true_mask(values: Any, shape: tuple[int, ...] | None = None) -> np.ndarray:
    """Return True only for explicit, non-missing truth; unknown fails closed."""

    if values is None:
        if shape is None:
            raise ValueError("shape is required for a missing strict mask")
        return np.zeros(shape, dtype=bool)
    raw = np.asarray(values, dtype=object)
    if shape is not None and raw.shape != shape:
        raise ValueError(f"mask shape {raw.shape} does not match {shape}")
    result = np.zeros(raw.shape, dtype=bool)
    for index, value in np.ndenumerate(raw):
        if isinstance(value, (bool, np.bool_)):
            result[index] = bool(value)
        elif isinstance(value, (int, float, np.integer, np.floating)):
            numeric = float(value)
            result[index] = bool(np.isfinite(numeric) and numeric == 1.0)
    return result


def combine_strict_masks(
    masks: Mapping[str, Any] | None,
    required: Sequence[str],
    shape: tuple[int, ...],
) -> np.ndarray:
    combined = np.ones(shape, dtype=bool)
    source = masks or {}
    for name in required:
        combined &= strict_true_mask(source.get(name), shape)
    return combined


def stable_top_n_equal_weight(
    scores: Any,
    asset_ids: Sequence[str] | None = None,
    eligible: Any | None = None,
    top_n: int = 20,
) -> np.ndarray:
    """Long-only equal weights with deterministic asset-id tie breaking."""

    values = np.asarray(scores, dtype=float)
    if values.ndim != 1:
        raise ValueError("scores must be one-dimensional")
    count = values.shape[0]
    ids = [str(index) for index in range(count)] if asset_ids is None else [str(item) for item in asset_ids]
    if len(ids) != count:
        raise ValueError("asset_ids length must match scores")
    valid = np.isfinite(values)
    if eligible is not None:
        valid &= strict_true_mask(eligible, (count,))
    selected = [index for index in range(count) if valid[index]]
    selected.sort(key=lambda index: (-float(values[index]), ids[index]))
    selected = selected[: max(0, int(top_n))]
    weights = np.zeros(count, dtype=float)
    if selected:
        weights[selected] = 1.0 / len(selected)
    return weights


def top20_equal_weight(
    scores: Any,
    asset_ids: Sequence[str] | None = None,
    eligible: Any | None = None,
) -> np.ndarray:
    return stable_top_n_equal_weight(scores, asset_ids, eligible, top_n=20)


build_top20_equal_weight = top20_equal_weight

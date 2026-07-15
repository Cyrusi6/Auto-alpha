"""Pre-registered Task 055-A portfolio and execution policies."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any, Mapping, Sequence

import numpy as np


@dataclass(frozen=True)
class ScenarioPolicy:
    name: str
    initial_aum: float = 1_000_000.0
    top_n: int = 20
    max_weight: float = 0.10
    lot_size: int = 100
    adv_participation: float = 0.10
    commission_rate: float = 0.0003
    minimum_commission: float = 5.0
    stamp_duty_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001
    slippage_bps: float = 5.0
    impact_bps: float = 5.0
    modeled_cost_multiplier: float = 1.0
    zero_all_costs: bool = False
    fee_schedule_id: str = "cn_ashare_historical_fees_modeled_execution_v1"
    sell_cash_lag: int = 1
    buy_share_lag: int = 1

    def __post_init__(self) -> None:
        if self.initial_aum <= 0 or not np.isfinite(self.initial_aum):
            raise ValueError("initial_aum must be positive and finite")
        if self.top_n < 0:
            raise ValueError("top_n must be non-negative")
        if not 0.0 < self.max_weight <= 1.0:
            raise ValueError("max_weight must be in (0, 1]")
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
ZERO_COST = replace(BASELINE, name="zero_cost_accounting", zero_all_costs=True)
DOUBLE_MODELED_COST = replace(BASELINE, name="double_modeled_cost", modeled_cost_multiplier=2.0)
PARTICIPATION_5_PERCENT = replace(BASELINE, name="participation_5_percent", adv_participation=0.05)
AUM_10_MILLION = replace(BASELINE, name="aum_10_million", initial_aum=10_000_000.0)

PREREGISTERED_SCENARIOS: dict[str, ScenarioPolicy] = {
    policy.name: policy
    for policy in (BASELINE, ZERO_COST, DOUBLE_MODELED_COST, PARTICIPATION_5_PERCENT, AUM_10_MILLION)
}
SCENARIO_POLICIES = PREREGISTERED_SCENARIOS
PREREGISTERED_SCENARIO_POLICIES = PREREGISTERED_SCENARIOS


def get_scenario_policy(name: str) -> ScenarioPolicy:
    aliases = {
        "base": "baseline",
        "zero_cost": "zero_cost_accounting",
        "2x_cost": "double_modeled_cost",
        "5pct_participation": "participation_5_percent",
        "10m_aum": "aum_10_million",
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

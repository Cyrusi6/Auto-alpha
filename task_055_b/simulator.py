"""Task 055-B simulator requiring an immutable governed fee schedule."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from task_055_a.simulator import EventLedgerSimulator

from .fees import fee_components_for_fill, validate_fee_schedule


class GovernedEventLedgerSimulator(EventLedgerSimulator):
    """Event ledger whose fill costs come only from a verified fee manifest."""

    def __init__(
        self,
        policy: Any,
        *,
        fee_schedule: str | Path | Mapping[str, Any],
        initial_cash: float | None = None,
    ) -> None:
        if isinstance(fee_schedule, Mapping):
            schedule = dict(fee_schedule)
            if schedule.get("schema_version") != "task055b_fee_schedule_v1" or not schedule.get("content_hash"):
                raise ValueError("task055b_verified_fee_schedule_required")
        else:
            schedule = validate_fee_schedule(fee_schedule)
        if str(schedule.get("policy_id") or "") != str(
            getattr(policy, "fee_schedule_id", None)
            if not isinstance(policy, Mapping)
            else policy.get("fee_schedule_id")
        ):
            raise ValueError("task055b_fee_schedule_policy_mismatch")
        self.fee_schedule = schedule
        super().__init__(policy, initial_cash=initial_cash)

    def _costs(self, side: str, notional: float, trade_date: str) -> dict[str, float]:
        return fee_components_for_fill(
            {"date": trade_date, "side": side, "notional": notional},
            self.fee_schedule,
            modeled_cost_multiplier=self.policy.modeled_cost_multiplier,
            zero_all_costs=self.policy.zero_all_costs,
        )

    def run(self, market: Mapping[str, Any], scores: Any, **kwargs: Any):
        if "valuation_open" not in market or "valuation_close" not in market:
            raise ValueError("task055b_explicit_valuation_marks_required")
        return super().run(market, scores, **kwargs)

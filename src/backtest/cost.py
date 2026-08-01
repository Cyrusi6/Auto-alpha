"""A-share transaction cost model."""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class CostBreakdown:
    commission: float
    stamp_duty: float
    transfer_fee: float
    slippage: float
    market_impact: float
    total: float


@dataclass(frozen=True)
class AShareCostModel:
    commission_rate: float = 0.0003
    min_commission: float = 5.0
    stamp_duty_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001
    slippage_bps: float = 5.0
    market_impact_bps: float = 0.0

    def estimate(self, side: str, trade_value: float) -> CostBreakdown:
        value = float(trade_value)
        if value <= 0 or not math.isfinite(value):
            return CostBreakdown(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

        commission = max(value * self.commission_rate, self.min_commission)
        stamp_duty = value * self.stamp_duty_rate if side.upper() == "SELL" else 0.0
        transfer_fee = value * self.transfer_fee_rate
        slippage = value * self.slippage_bps / 10000.0
        market_impact = value * self.market_impact_bps / 10000.0
        total = commission + stamp_duty + transfer_fee + slippage + market_impact
        return CostBreakdown(
            commission=float(commission),
            stamp_duty=float(stamp_duty),
            transfer_fee=float(transfer_fee),
            slippage=float(slippage),
            market_impact=float(market_impact),
            total=float(total),
        )

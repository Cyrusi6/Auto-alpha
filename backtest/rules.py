"""A-share trading rules for local simulation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AShareTradingRules:
    lot_size: int = 100
    max_position_weight: float = 0.10
    volume_limit_ratio: float = 0.10
    allow_fractional_weight: bool = True

    def round_shares(self, shares: float) -> int:
        if shares <= 0:
            return 0
        return int(shares // self.lot_size) * self.lot_size

    @staticmethod
    def is_t_plus_one_sell_allowed(buy_date_index: int, sell_date_index: int) -> bool:
        return sell_date_index > buy_date_index

    @staticmethod
    def can_buy(price: float, is_suspended: bool = False, is_limit_up: bool = False) -> tuple[bool, str]:
        if is_suspended:
            return False, "suspended"
        if is_limit_up:
            return False, "limit_up"
        if price <= 0:
            return False, "invalid_price"
        return True, ""

    @staticmethod
    def can_sell(price: float, is_suspended: bool = False, is_limit_down: bool = False) -> tuple[bool, str]:
        if is_suspended:
            return False, "suspended"
        if is_limit_down:
            return False, "limit_down"
        if price <= 0:
            return False, "invalid_price"
        return True, ""

    def clamp_weight(self, weight: float) -> float:
        return max(0.0, min(float(weight), self.max_position_weight))

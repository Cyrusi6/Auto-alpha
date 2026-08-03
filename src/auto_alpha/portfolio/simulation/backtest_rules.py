"""A-share trading rules for local simulation; consolidated from auto_alpha.portfolio.simulation.backtest."""

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

    @staticmethod
    def is_open_at_limit(price: float, limit_price: float, *, direction: str) -> bool:
        if price <= 0 or limit_price <= 0:
            return False
        tolerance = max(abs(limit_price) * 1e-4, 1e-4)
        if direction == "up":
            return price >= limit_price - tolerance
        if direction == "down":
            return price <= limit_price + tolerance
        raise ValueError("direction must be up or down")

    def clamp_weight(self, weight: float) -> float:
        return max(0.0, min(float(weight), self.max_position_weight))

    def volume_limited_shares(self, requested_shares: int, volume: float) -> tuple[int, str]:
        max_shares = self.round_shares(max(float(volume), 0.0) * self.volume_limit_ratio)
        if max_shares <= 0:
            return 0, "volume_limit"
        if requested_shares > max_shares:
            return max_shares, "volume_limit_partial"
        return requested_shares, ""

"""Provider protocol for A-share data sources."""

from __future__ import annotations

from typing import Protocol

from ..config import AShareDataConfig
from ..schema import (
    AdjustmentFactor,
    DailyBar,
    DailyBasic,
    DailyLimit,
    FinancialFeature,
    IndexMember,
    CorporateAction,
    Security,
    TradeCalendarRecord,
)


class AShareDataProvider(Protocol):
    def fetch_securities(self, config: AShareDataConfig) -> list[Security]:
        ...

    def fetch_trade_calendar(self, config: AShareDataConfig) -> list[TradeCalendarRecord]:
        ...

    def fetch_daily_bars(self, config: AShareDataConfig) -> list[DailyBar]:
        ...

    def fetch_daily_basic(self, config: AShareDataConfig) -> list[DailyBasic]:
        ...

    def fetch_financial_features(self, config: AShareDataConfig) -> list[FinancialFeature]:
        ...

    def fetch_daily_limits(self, config: AShareDataConfig) -> list[DailyLimit]:
        ...

    def fetch_adjustment_factors(self, config: AShareDataConfig) -> list[AdjustmentFactor]:
        ...

    def fetch_index_members(self, config: AShareDataConfig) -> list[IndexMember]:
        ...

    def fetch_corporate_actions(self, config: AShareDataConfig) -> list[CorporateAction]:
        ...

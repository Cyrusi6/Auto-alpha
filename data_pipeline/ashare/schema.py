"""Dataclasses for A-share securities, market data, financials, and factors."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Security:
    ts_code: str
    symbol: str
    name: str
    exchange: str
    list_date: str
    delist_date: str | None = None
    industry: str | None = None
    board: str | None = None
    is_st: bool = False


@dataclass(frozen=True)
class TradeCalendarRecord:
    trade_date: str
    is_open: bool
    prev_trade_date: str | None = None
    next_trade_date: str | None = None


@dataclass(frozen=True)
class DailyBar:
    trade_date: str
    ts_code: str
    open: float
    high: float
    low: float
    close: float
    pre_close: float
    volume: float
    amount: float
    adj_factor: float | None = None
    limit_up: float | None = None
    limit_down: float | None = None
    is_suspended: bool = False


@dataclass(frozen=True)
class DailyBasic:
    trade_date: str
    ts_code: str
    turnover_rate: float | None = None
    volume_ratio: float | None = None
    pe_ttm: float | None = None
    pb: float | None = None
    ps_ttm: float | None = None
    total_mv: float | None = None
    circ_mv: float | None = None


@dataclass(frozen=True)
class DailyLimit:
    trade_date: str
    ts_code: str
    up_limit: float
    down_limit: float
    pre_close: float


@dataclass(frozen=True)
class AdjustmentFactor:
    trade_date: str
    ts_code: str
    adj_factor: float


@dataclass(frozen=True)
class IndexMember:
    index_code: str
    trade_date: str
    ts_code: str
    weight: float


@dataclass(frozen=True)
class FinancialFeature:
    ts_code: str
    report_period: str
    announce_date: str
    roe: float | None = None
    roa: float | None = None
    gross_margin: float | None = None
    revenue_yoy: float | None = None
    net_profit_yoy: float | None = None
    debt_to_asset: float | None = None
    operating_cashflow: float | None = None

    def is_available_on(self, trade_date: str) -> bool:
        return self.announce_date <= trade_date


@dataclass(frozen=True)
class FactorMetadata:
    factor_id: str
    formula: str
    formula_hash: str
    lookback_days: int
    created_at: str
    status: str = "candidate"
    description: str | None = None


@dataclass(frozen=True)
class FactorValue:
    trade_date: str
    ts_code: str
    factor_id: str
    value: float | None

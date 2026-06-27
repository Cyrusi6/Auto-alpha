"""Deterministic local A-share sample data provider."""

from __future__ import annotations

from ..config import AShareDataConfig
from ..schema import DailyBar, DailyBasic, FinancialFeature, Security, TradeCalendarRecord


class SampleAShareDataProvider:
    def fetch_securities(self, config: AShareDataConfig) -> list[Security]:
        return [
            Security(
                ts_code="000001.SZ",
                symbol="000001",
                name="平安银行",
                exchange="SZSE",
                list_date="19910403",
                industry="银行",
                board="主板",
            ),
            Security(
                ts_code="600000.SH",
                symbol="600000",
                name="浦发银行",
                exchange="SSE",
                list_date="19991110",
                industry="银行",
                board="主板",
            ),
            Security(
                ts_code="830000.BJ",
                symbol="830000",
                name="北证样例",
                exchange="BSE",
                list_date="20220104",
                industry="工业",
                board="北交所",
            ),
        ]

    def fetch_trade_calendar(self, config: AShareDataConfig) -> list[TradeCalendarRecord]:
        return [
            TradeCalendarRecord(
                trade_date="20240102",
                is_open=True,
                prev_trade_date="20231229",
                next_trade_date="20240103",
            ),
            TradeCalendarRecord(
                trade_date="20240103",
                is_open=True,
                prev_trade_date="20240102",
                next_trade_date="20240104",
            ),
            TradeCalendarRecord(
                trade_date="20240104",
                is_open=True,
                prev_trade_date="20240103",
                next_trade_date="20240105",
            ),
        ]

    def fetch_daily_bars(self, config: AShareDataConfig) -> list[DailyBar]:
        return [
            DailyBar(
                trade_date="20240102",
                ts_code="000001.SZ",
                open=9.40,
                high=9.58,
                low=9.32,
                close=9.50,
                pre_close=9.39,
                volume=882345.0,
                amount=837451.2,
                adj_factor=1.02,
                limit_up=10.33,
                limit_down=8.45,
            ),
            DailyBar(
                trade_date="20240103",
                ts_code="000001.SZ",
                open=9.51,
                high=9.64,
                low=9.43,
                close=9.55,
                pre_close=9.50,
                volume=756210.0,
                amount=721433.4,
                adj_factor=1.02,
                limit_up=10.45,
                limit_down=8.55,
            ),
            DailyBar(
                trade_date="20240102",
                ts_code="600000.SH",
                open=6.65,
                high=6.72,
                low=6.60,
                close=6.69,
                pre_close=6.64,
                volume=512480.0,
                amount=342743.8,
                adj_factor=1.01,
                limit_up=7.30,
                limit_down=5.98,
            ),
            DailyBar(
                trade_date="20240103",
                ts_code="600000.SH",
                open=6.68,
                high=6.75,
                low=6.62,
                close=6.70,
                pre_close=6.69,
                volume=498120.0,
                amount=333810.4,
                adj_factor=1.01,
                limit_up=7.36,
                limit_down=6.02,
            ),
            DailyBar(
                trade_date="20240102",
                ts_code="830000.BJ",
                open=12.10,
                high=12.45,
                low=11.96,
                close=12.30,
                pre_close=12.05,
                volume=103450.0,
                amount=126893.5,
                adj_factor=1.00,
                limit_up=15.66,
                limit_down=8.44,
            ),
            DailyBar(
                trade_date="20240103",
                ts_code="830000.BJ",
                open=12.28,
                high=12.52,
                low=12.01,
                close=12.18,
                pre_close=12.30,
                volume=98400.0,
                amount=120065.2,
                adj_factor=1.00,
                limit_up=15.99,
                limit_down=8.61,
            ),
        ]

    def fetch_daily_basic(self, config: AShareDataConfig) -> list[DailyBasic]:
        return [
            DailyBasic(
                trade_date="20240102",
                ts_code="000001.SZ",
                turnover_rate=0.45,
                volume_ratio=0.92,
                pe_ttm=4.80,
                pb=0.52,
                ps_ttm=1.18,
                total_mv=1843200.0,
                circ_mv=1840000.0,
            ),
            DailyBasic(
                trade_date="20240102",
                ts_code="600000.SH",
                turnover_rate=0.18,
                volume_ratio=0.88,
                pe_ttm=5.20,
                pb=0.43,
                ps_ttm=1.05,
                total_mv=1965000.0,
                circ_mv=1965000.0,
            ),
            DailyBasic(
                trade_date="20240102",
                ts_code="830000.BJ",
                turnover_rate=1.24,
                volume_ratio=1.11,
                pe_ttm=18.60,
                pb=2.15,
                ps_ttm=3.20,
                total_mv=24600.0,
                circ_mv=18300.0,
            ),
        ]

    def fetch_financial_features(self, config: AShareDataConfig) -> list[FinancialFeature]:
        return [
            FinancialFeature(
                ts_code="000001.SZ",
                report_period="20230930",
                announce_date="20231025",
                roe=0.096,
                roa=0.0075,
                gross_margin=0.0,
                revenue_yoy=-0.075,
                net_profit_yoy=0.087,
                debt_to_asset=0.918,
                operating_cashflow=12850000.0,
            ),
            FinancialFeature(
                ts_code="600000.SH",
                report_period="20230930",
                announce_date="20231031",
                roe=0.071,
                roa=0.0062,
                gross_margin=0.0,
                revenue_yoy=-0.082,
                net_profit_yoy=-0.031,
                debt_to_asset=0.925,
                operating_cashflow=9820000.0,
            ),
            FinancialFeature(
                ts_code="830000.BJ",
                report_period="20230930",
                announce_date="20231115",
                roe=0.112,
                roa=0.052,
                gross_margin=0.318,
                revenue_yoy=0.143,
                net_profit_yoy=0.128,
                debt_to_asset=0.348,
                operating_cashflow=21500.0,
            ),
        ]

"""Tushare Pro provider backed by the standard-library HTTP client."""

from __future__ import annotations

from typing import Any

from ..config import AShareDataConfig
from ..schema import DailyBar, DailyBasic, FinancialFeature, Security, TradeCalendarRecord
from ..validators import is_valid_ts_code, is_valid_yyyymmdd
from .tushare_client import TushareHttpClient


class TushareAShareDataProvider:
    def __init__(self, client: Any | None = None):
        self.client = client

    def fetch_securities(self, config: AShareDataConfig) -> list[Security]:
        rows = self._post(
            config,
            "stock_basic",
            params={"list_status": "L"},
            fields="ts_code,symbol,name,exchange,list_date,industry,market",
        )
        records: list[Security] = []
        for row in rows:
            ts_code = _text(row.get("ts_code"))
            list_date = _text(row.get("list_date"))
            if not is_valid_ts_code(ts_code) or not is_valid_yyyymmdd(list_date):
                continue
            name = _text(row.get("name"))
            records.append(
                Security(
                    ts_code=ts_code,
                    symbol=_text(row.get("symbol")),
                    name=name,
                    exchange=_text(row.get("exchange")),
                    list_date=list_date,
                    industry=_optional_text(row.get("industry")),
                    board=_optional_text(row.get("market")),
                    is_st=_is_st_name(name),
                )
            )
        return records

    def fetch_trade_calendar(self, config: AShareDataConfig) -> list[TradeCalendarRecord]:
        rows = self._post(
            config,
            "trade_cal",
            params=_date_params(config, exchange="SSE"),
            fields="cal_date,is_open,pretrade_date",
        )
        records: list[TradeCalendarRecord] = []
        for row in rows:
            trade_date = _text(row.get("cal_date"))
            if not is_valid_yyyymmdd(trade_date):
                continue
            prev_trade_date = _valid_optional_date(row.get("pretrade_date"))
            records.append(
                TradeCalendarRecord(
                    trade_date=trade_date,
                    is_open=_to_bool(row.get("is_open")),
                    prev_trade_date=prev_trade_date,
                )
            )
        return records

    def fetch_daily_bars(self, config: AShareDataConfig) -> list[DailyBar]:
        rows = self._post(
            config,
            "daily",
            params=_date_params(config),
            fields="ts_code,trade_date,open,high,low,close,pre_close,vol,amount",
        )
        records: list[DailyBar] = []
        for row in rows:
            ts_code = _text(row.get("ts_code"))
            trade_date = _text(row.get("trade_date"))
            if not is_valid_ts_code(ts_code) or not is_valid_yyyymmdd(trade_date):
                continue
            records.append(
                DailyBar(
                    trade_date=trade_date,
                    ts_code=ts_code,
                    open=_float_or_zero(row.get("open")),
                    high=_float_or_zero(row.get("high")),
                    low=_float_or_zero(row.get("low")),
                    close=_float_or_zero(row.get("close")),
                    pre_close=_float_or_zero(row.get("pre_close")),
                    volume=_float_or_zero(row.get("vol")),
                    amount=_float_or_zero(row.get("amount")),
                )
            )
        return records

    def fetch_daily_basic(self, config: AShareDataConfig) -> list[DailyBasic]:
        rows = self._post(
            config,
            "daily_basic",
            params=_date_params(config),
            fields=(
                "ts_code,trade_date,turnover_rate,volume_ratio,pe_ttm,pb,ps_ttm,"
                "total_mv,circ_mv"
            ),
        )
        records: list[DailyBasic] = []
        for row in rows:
            ts_code = _text(row.get("ts_code"))
            trade_date = _text(row.get("trade_date"))
            if not is_valid_ts_code(ts_code) or not is_valid_yyyymmdd(trade_date):
                continue
            records.append(
                DailyBasic(
                    trade_date=trade_date,
                    ts_code=ts_code,
                    turnover_rate=_optional_float(row.get("turnover_rate")),
                    volume_ratio=_optional_float(row.get("volume_ratio")),
                    pe_ttm=_optional_float(row.get("pe_ttm")),
                    pb=_optional_float(row.get("pb")),
                    ps_ttm=_optional_float(row.get("ps_ttm")),
                    total_mv=_optional_float(row.get("total_mv")),
                    circ_mv=_optional_float(row.get("circ_mv")),
                )
            )
        return records

    def fetch_financial_features(self, config: AShareDataConfig) -> list[FinancialFeature]:
        rows = self._post(
            config,
            "fina_indicator",
            params=_date_params(config),
            fields=(
                "ts_code,end_date,ann_date,roe,roa,grossprofit_margin,or_yoy,"
                "netprofit_yoy,debt_to_assets,ocfps"
            ),
        )
        records: list[FinancialFeature] = []
        for row in rows:
            ts_code = _text(row.get("ts_code"))
            report_period = _text(row.get("end_date"))
            announce_date = _text(row.get("ann_date"))
            if (
                not is_valid_ts_code(ts_code)
                or not is_valid_yyyymmdd(report_period)
                or not is_valid_yyyymmdd(announce_date)
            ):
                continue
            if config.end_date is not None and announce_date > config.end_date:
                continue
            records.append(
                FinancialFeature(
                    ts_code=ts_code,
                    report_period=report_period,
                    announce_date=announce_date,
                    roe=_optional_float(row.get("roe")),
                    roa=_optional_float(row.get("roa")),
                    gross_margin=_optional_float(row.get("grossprofit_margin")),
                    revenue_yoy=_optional_float(row.get("or_yoy")),
                    net_profit_yoy=_optional_float(row.get("netprofit_yoy")),
                    debt_to_asset=_optional_float(row.get("debt_to_assets")),
                    operating_cashflow=_optional_float(row.get("ocfps")),
                )
            )
        return records

    def _post(
        self,
        config: AShareDataConfig,
        api_name: str,
        params: dict[str, Any],
        fields: str,
    ) -> list[dict[str, Any]]:
        client = self.client if self.client is not None else TushareHttpClient(config)
        return client.post(api_name, params=params, fields=fields)


def _date_params(config: AShareDataConfig, **extra: str) -> dict[str, str]:
    params = {"start_date": config.start_date, **extra}
    if config.end_date:
        params["end_date"] = config.end_date
    return params


def _clean(value: Any) -> Any | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    return value


def _text(value: Any) -> str:
    cleaned = _clean(value)
    return "" if cleaned is None else str(cleaned)


def _optional_text(value: Any) -> str | None:
    cleaned = _clean(value)
    return None if cleaned is None else str(cleaned)


def _valid_optional_date(value: Any) -> str | None:
    text = _optional_text(value)
    if text is None or not is_valid_yyyymmdd(text):
        return None
    return text


def _optional_float(value: Any) -> float | None:
    cleaned = _clean(value)
    if cleaned is None:
        return None
    try:
        return float(cleaned)
    except (TypeError, ValueError):
        return None


def _float_or_zero(value: Any) -> float:
    parsed = _optional_float(value)
    return 0.0 if parsed is None else parsed


def _to_bool(value: Any) -> bool:
    cleaned = _clean(value)
    if isinstance(cleaned, bool):
        return cleaned
    if isinstance(cleaned, (int, float)):
        return cleaned != 0
    if isinstance(cleaned, str):
        return cleaned.lower() in {"1", "true", "y", "yes", "open"}
    return False


def _is_st_name(name: str) -> bool:
    upper = name.upper()
    return upper.startswith("ST") or upper.startswith("*ST")

"""Tushare Pro provider backed by the standard-library HTTP client."""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from ..audit import ApiRequestAuditEntry, ApiRequestAuditor, utc_now
from ..cache import TushareResponseCache
from ..config import AShareDataConfig
from ..rate_limit import SimpleRateLimiter
from ..schema import (
    AdjustmentFactor,
    CorporateAction,
    DailyBar,
    DailyBasic,
    DailyLimit,
    FinancialFeature,
    IndexMember,
    Security,
    TradeCalendarRecord,
)
from ..sync_plan import SyncJob
from ..validators import is_valid_ts_code, is_valid_yyyymmdd
from .tushare_client import TushareHttpClient


class TushareAShareDataProvider:
    def __init__(self, client: Any | None = None, rate_limiter: SimpleRateLimiter | None = None):
        self.client = client
        self.rate_limiter = rate_limiter

    def fetch_dataset_job(
        self,
        job: SyncJob,
        config: AShareDataConfig,
        cache: TushareResponseCache | None = None,
        auditor: ApiRequestAuditor | None = None,
    ) -> list[object]:
        job_config = _config_for_job(config, job)
        base_client = self.client if self.client is not None else TushareHttpClient(job_config, rate_limiter=self.rate_limiter)
        client = _CachedAuditedClient(
            client=base_client,
            job=job,
            cache=cache,
            auditor=auditor,
        )
        provider = TushareAShareDataProvider(client=client)
        fetcher = getattr(provider, f"fetch_{job.dataset}")
        return fetcher(job_config)

    def fetch_securities(self, config: AShareDataConfig) -> list[Security]:
        rows: list[dict[str, Any]] = []
        for list_status in config.security_list_statuses:
            for row in self._post(
                config,
                "stock_basic",
                params={"list_status": list_status},
                fields="ts_code,symbol,name,exchange,list_date,delist_date,industry,market,list_status,area",
            ):
                payload = dict(row)
                payload.setdefault("list_status", list_status)
                rows.append(payload)
        records: list[Security] = []
        seen: set[str] = set()
        for row in rows:
            ts_code = _text(row.get("ts_code"))
            list_date = _text(row.get("list_date"))
            if ts_code in seen or not is_valid_ts_code(ts_code) or not is_valid_yyyymmdd(list_date):
                continue
            seen.add(ts_code)
            name = _text(row.get("name"))
            records.append(
                Security(
                    ts_code=ts_code,
                    symbol=_text(row.get("symbol")),
                    name=name,
                    exchange=_text(row.get("exchange")),
                    list_date=list_date,
                    delist_date=_valid_optional_date(row.get("delist_date")),
                    industry=_optional_text(row.get("industry")),
                    board=_optional_text(row.get("market")),
                    is_st=_is_st_name(name),
                    list_status=_optional_text(row.get("list_status")) or _optional_text(row.get("list_statuses")),
                    area=_optional_text(row.get("area")),
                    raw_name=name,
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

    def fetch_daily_limits(self, config: AShareDataConfig) -> list[DailyLimit]:
        rows = self._post(
            config,
            "stk_limit",
            params=_date_params(config),
            fields="trade_date,ts_code,up_limit,down_limit,pre_close",
        )
        records: list[DailyLimit] = []
        for row in rows:
            ts_code = _text(row.get("ts_code"))
            trade_date = _text(row.get("trade_date"))
            if not is_valid_ts_code(ts_code) or not is_valid_yyyymmdd(trade_date):
                continue
            records.append(
                DailyLimit(
                    trade_date=trade_date,
                    ts_code=ts_code,
                    up_limit=_float_or_zero(row.get("up_limit")),
                    down_limit=_float_or_zero(row.get("down_limit")),
                    pre_close=_float_or_zero(row.get("pre_close")),
                )
            )
        return records

    def fetch_adjustment_factors(self, config: AShareDataConfig) -> list[AdjustmentFactor]:
        rows = self._post(
            config,
            "adj_factor",
            params=_date_params(config),
            fields="ts_code,trade_date,adj_factor",
        )
        records: list[AdjustmentFactor] = []
        for row in rows:
            ts_code = _text(row.get("ts_code"))
            trade_date = _text(row.get("trade_date"))
            if not is_valid_ts_code(ts_code) or not is_valid_yyyymmdd(trade_date):
                continue
            adj_factor = _optional_float(row.get("adj_factor"))
            if adj_factor is None or adj_factor <= 0:
                continue
            records.append(
                AdjustmentFactor(
                    trade_date=trade_date,
                    ts_code=ts_code,
                    adj_factor=adj_factor,
                )
            )
        return records

    def fetch_index_members(self, config: AShareDataConfig) -> list[IndexMember]:
        records: list[IndexMember] = []
        for index_code in config.index_codes:
            rows = self._post(
                config,
                "index_weight",
                params={**_date_params(config), "index_code": index_code},
                fields="index_code,con_code,trade_date,weight",
            )
            for row in rows:
                ts_code = _text(row.get("con_code"))
                trade_date = _text(row.get("trade_date"))
                index = _text(row.get("index_code")) or index_code
                if not is_valid_ts_code(ts_code) or not is_valid_yyyymmdd(trade_date):
                    continue
                weight = _optional_float(row.get("weight"))
                if weight is None or weight < 0:
                    continue
                records.append(
                    IndexMember(
                        index_code=index,
                        trade_date=trade_date,
                        ts_code=ts_code,
                        weight=weight,
                    )
                )
        return records

    def fetch_corporate_actions(self, config: AShareDataConfig) -> list[CorporateAction]:
        records: list[CorporateAction] = []
        fields = (
            "ts_code,end_date,ann_date,div_proc,stk_div,stk_bo_rate,stk_co_rate,"
            "cash_div,cash_div_tax,record_date,ex_date,pay_date,div_listdate,"
            "imp_ann_date,base_date,base_share"
        )
        for query_date in _date_range(config.start_date, config.end_date or config.start_date):
            rows = self._post(
                config,
                "dividend",
                params={config.corporate_action_query_date_field: query_date},
                fields=fields,
            )
            for row in rows:
                ts_code = _text(row.get("ts_code"))
                if not is_valid_ts_code(ts_code):
                    continue
                ann_date = _valid_optional_date(row.get("ann_date"))
                ex_date = _valid_optional_date(row.get("ex_date"))
                if ann_date is None and ex_date is None:
                    continue
                records.append(
                    CorporateAction(
                        ts_code=ts_code,
                        end_date=_valid_optional_date(row.get("end_date")),
                        ann_date=ann_date,
                        div_proc=_optional_text(row.get("div_proc")),
                        stk_div=_optional_float(row.get("stk_div")),
                        stk_bo_rate=_optional_float(row.get("stk_bo_rate")),
                        stk_co_rate=_optional_float(row.get("stk_co_rate")),
                        cash_div=_optional_float(row.get("cash_div")),
                        cash_div_tax=_optional_float(row.get("cash_div_tax")),
                        record_date=_valid_optional_date(row.get("record_date")),
                        ex_date=ex_date,
                        pay_date=_valid_optional_date(row.get("pay_date")),
                        div_listdate=_valid_optional_date(row.get("div_listdate")),
                        imp_ann_date=_valid_optional_date(row.get("imp_ann_date")),
                        base_date=_valid_optional_date(row.get("base_date")),
                        base_share=_optional_float(row.get("base_share")),
                        source="tushare",
                        raw_status=_optional_text(row.get("div_proc")),
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


def _date_range(start_date: str, end_date: str) -> list[str]:
    if not is_valid_yyyymmdd(start_date) or not is_valid_yyyymmdd(end_date):
        return [start_date]
    start = datetime.strptime(start_date, "%Y%m%d").date()
    end = datetime.strptime(end_date, "%Y%m%d").date()
    if start > end:
        return [start_date]
    values: list[str] = []
    current = start
    while current <= end:
        values.append(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return values


def _config_for_job(config: AShareDataConfig, job: SyncJob) -> AShareDataConfig:
    overrides: dict[str, Any] = {}
    if job.start_date is not None:
        overrides["start_date"] = job.start_date
    if job.end_date is not None:
        overrides["end_date"] = job.end_date
    if job.index_code is not None:
        overrides["index_codes"] = (job.index_code,)
    return replace(config, **overrides)


class _CachedAuditedClient:
    def __init__(
        self,
        client: Any,
        job: SyncJob,
        cache: TushareResponseCache | None,
        auditor: ApiRequestAuditor | None,
    ):
        self.client = client
        self.job = job
        self.cache = cache
        self.auditor = auditor

    def post(self, api_name: str, params: dict[str, Any] | None = None, fields: Any = None) -> list[dict[str, Any]]:
        started_at = utc_now()
        started = time.perf_counter()
        cache_hit = False
        records: list[dict[str, Any]] = []
        status = "success"
        error: str | None = None
        rate_event = None
        try:
            cached = self.cache.read(api_name, params=params, fields=fields) if self.cache is not None else None
            if cached is not None and cached.hit:
                cache_hit = True
                records = cached.records
                return records

            records = self.client.post(api_name, params=params, fields=fields)
            rate_event = getattr(self.client, "last_rate_limit_event", None)
            if self.cache is not None:
                self.cache.write(api_name, params=params, fields=fields, records=records)
            return records
        except Exception as exc:
            status = "error"
            error = str(exc)
            raise
        finally:
            if self.auditor is not None:
                finished_at = utc_now()
                self.auditor.write(
                    ApiRequestAuditEntry(
                        api_name=api_name,
                        dataset=self.job.dataset,
                        start_date=self.job.start_date,
                        end_date=self.job.end_date,
                        index_code=self.job.index_code,
                        cache_hit=cache_hit,
                        records=len(records),
                        status=status,
                        error=error,
                        started_at=started_at,
                        finished_at=finished_at,
                        duration_seconds=max(0.0, time.perf_counter() - started),
                        rate_limit_wait_seconds=float(getattr(rate_event, "waited_seconds", 0.0) or 0.0),
                        rate_limit_request_index=getattr(rate_event, "request_index", None),
                    )
                )


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

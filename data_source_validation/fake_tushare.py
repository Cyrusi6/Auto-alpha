"""Offline fake Tushare client scenarios for smoke validation."""

from __future__ import annotations

from typing import Any, Iterable

from data_pipeline.ashare.providers.tushare_client import (
    TushareApiError,
    TushareNetworkError,
    TusharePermissionError,
    TushareRateLimitError,
    TushareResponseEnvelope,
    TushareSchemaError,
)


FAKE_TUSHARE_SCENARIOS = {
    "success",
    "permission_denied",
    "rate_limited",
    "missing_fields",
    "empty_response",
    "malformed_payload",
    "network_error",
}


class FakeTushareHttpClient:
    def __init__(self, scenario: str = "success", token: str = "fake-token-redacted"):
        if scenario not in FAKE_TUSHARE_SCENARIOS:
            raise ValueError(f"unsupported fake Tushare scenario: {scenario}")
        self.scenario = scenario
        self.token = token
        self.calls: list[dict[str, Any]] = []

    def post(self, api_name: str, params: dict[str, Any] | None = None, fields: str | Iterable[str] | None = None) -> list[dict[str, Any]]:
        return self.post_with_metadata(api_name, params=params, fields=fields).records

    def post_with_metadata(
        self,
        api_name: str,
        params: dict[str, Any] | None = None,
        fields: str | Iterable[str] | None = None,
    ) -> TushareResponseEnvelope:
        requested_fields = _format_fields(fields)
        self.calls.append({"api_name": api_name, "params": dict(params or {}), "fields": requested_fields})
        if self.scenario == "permission_denied":
            raise TusharePermissionError("permission denied for requested api")
        if self.scenario == "rate_limited":
            raise TushareRateLimitError("rate limit exceeded")
        if self.scenario == "network_error":
            raise TushareNetworkError("offline fake network error")
        if self.scenario == "malformed_payload":
            raise TushareSchemaError("Tushare response data.fields/data.items must be lists")

        rows = [] if self.scenario == "empty_response" else list(_rows_for_api(api_name))
        response_fields = list(_response_fields(api_name))
        if self.scenario == "missing_fields" and response_fields:
            response_fields = response_fields[:-1]
            rows = [{key: row.get(key) for key in response_fields} for row in rows]
        return TushareResponseEnvelope(
            api_name=api_name,
            params_without_token=dict(params or {}),
            requested_fields=requested_fields,
            response_code=0,
            response_message="",
            response_fields=response_fields,
            records=rows,
            item_count=len(rows),
            duration_seconds=0.0,
        )


def _rows_for_api(api_name: str) -> list[dict[str, Any]]:
    mapping = {
        "stock_basic": [
            {
                "ts_code": "000001.SZ",
                "symbol": "000001",
                "name": "平安银行",
                "exchange": "SZSE",
                "list_date": "19910403",
                "industry": "银行",
                "market": "主板",
                "delist_date": None,
                "list_status": "L",
                "area": "深圳",
            }
        ],
        "trade_cal": [
            {"cal_date": "20240102", "is_open": 1, "pretrade_date": "20231229"},
            {"cal_date": "20240103", "is_open": 1, "pretrade_date": "20240102"},
            {"cal_date": "20240104", "is_open": 1, "pretrade_date": "20240103"},
        ],
        "daily": [
            {"ts_code": "000001.SZ", "trade_date": "20240102", "open": 9.4, "high": 9.58, "low": 9.32, "close": 9.5, "pre_close": 9.39, "vol": 123456.7, "amount": 837451.2},
            {"ts_code": "000001.SZ", "trade_date": "20240103", "open": 9.5, "high": 9.68, "low": 9.42, "close": 9.62, "pre_close": 9.5, "vol": 113456.7, "amount": 737451.2},
            {"ts_code": "000001.SZ", "trade_date": "20240104", "open": 9.6, "high": 9.78, "low": 9.52, "close": 9.72, "pre_close": 9.62, "vol": 103456.7, "amount": 637451.2},
        ],
        "daily_basic": [
            {"ts_code": "000001.SZ", "trade_date": "20240102", "turnover_rate": 0.45, "volume_ratio": 1.1, "pe_ttm": 4.8, "pb": 0.52, "ps_ttm": 1.18, "total_mv": 1843200, "circ_mv": 1840000},
            {"ts_code": "000001.SZ", "trade_date": "20240103", "turnover_rate": 0.42, "volume_ratio": 1.0, "pe_ttm": 4.9, "pb": 0.53, "ps_ttm": 1.19, "total_mv": 1845200, "circ_mv": 1841000},
            {"ts_code": "000001.SZ", "trade_date": "20240104", "turnover_rate": 0.41, "volume_ratio": 0.9, "pe_ttm": 5.0, "pb": 0.54, "ps_ttm": 1.20, "total_mv": 1846200, "circ_mv": 1842000},
        ],
        "fina_indicator": [
            {"ts_code": "000001.SZ", "end_date": "20230930", "ann_date": "20231025", "roe": 9.6, "roa": 0.75, "grossprofit_margin": 42.0, "or_yoy": -7.5, "netprofit_yoy": 8.7, "debt_to_assets": 91.8, "ocfps": 1.28}
        ],
        "stk_limit": [
            {"trade_date": "20240102", "ts_code": "000001.SZ", "up_limit": 10.33, "down_limit": 8.45, "pre_close": 9.39},
            {"trade_date": "20240103", "ts_code": "000001.SZ", "up_limit": 10.45, "down_limit": 8.55, "pre_close": 9.5},
            {"trade_date": "20240104", "ts_code": "000001.SZ", "up_limit": 10.58, "down_limit": 8.66, "pre_close": 9.62},
        ],
        "adj_factor": [
            {"ts_code": "000001.SZ", "trade_date": "20240102", "adj_factor": 1.02},
            {"ts_code": "000001.SZ", "trade_date": "20240103", "adj_factor": 1.02},
            {"ts_code": "000001.SZ", "trade_date": "20240104", "adj_factor": 1.03},
        ],
        "index_weight": [
            {"index_code": "000300.SH", "con_code": "000001.SZ", "trade_date": "20240103", "weight": 0.42}
        ],
    }
    if api_name not in mapping:
        raise TushareApiError(f"unsupported fake api_name: {api_name}")
    return mapping[api_name]


def _response_fields(api_name: str) -> list[str]:
    rows = _rows_for_api(api_name)
    return list(rows[0].keys()) if rows else []


def _format_fields(fields: str | Iterable[str] | None) -> str:
    if fields is None:
        return ""
    if isinstance(fields, str):
        return fields
    return ",".join(fields)

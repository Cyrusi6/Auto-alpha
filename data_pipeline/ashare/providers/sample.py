"""Deterministic local A-share sample data provider."""

from __future__ import annotations

from ..config import AShareDataConfig
from ..dataset_registry import DATASET_DEFINITIONS, AShareDatasetDefinition
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
                list_status="L",
                area="深圳",
                raw_name="平安银行",
            ),
            Security(
                ts_code="600000.SH",
                symbol="600000",
                name="浦发银行",
                exchange="SSE",
                list_date="19991110",
                industry="银行",
                board="主板",
                list_status="L",
                area="上海",
                raw_name="浦发银行",
            ),
            Security(
                ts_code="830000.BJ",
                symbol="830000",
                name="北证样例",
                exchange="BSE",
                list_date="20220104",
                industry="工业",
                board="北交所",
                list_status="L",
                area="北京",
                raw_name="北证样例",
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

    def fetch_daily_limits(self, config: AShareDataConfig) -> list[DailyLimit]:
        return [
            DailyLimit("20240102", "000001.SZ", up_limit=10.33, down_limit=8.45, pre_close=9.39),
            DailyLimit("20240103", "000001.SZ", up_limit=9.55, down_limit=8.55, pre_close=9.50),
            DailyLimit("20240102", "600000.SH", up_limit=7.30, down_limit=5.98, pre_close=6.64),
            DailyLimit("20240103", "600000.SH", up_limit=7.36, down_limit=6.70, pre_close=6.69),
            DailyLimit("20240102", "830000.BJ", up_limit=15.66, down_limit=8.44, pre_close=12.05),
            DailyLimit("20240103", "830000.BJ", up_limit=15.99, down_limit=8.61, pre_close=12.30),
        ]

    def fetch_adjustment_factors(self, config: AShareDataConfig) -> list[AdjustmentFactor]:
        return [
            AdjustmentFactor("20240102", "000001.SZ", adj_factor=1.02),
            AdjustmentFactor("20240103", "000001.SZ", adj_factor=1.03),
            AdjustmentFactor("20240102", "600000.SH", adj_factor=1.01),
            AdjustmentFactor("20240103", "600000.SH", adj_factor=1.01),
            AdjustmentFactor("20240102", "830000.BJ", adj_factor=1.00),
            AdjustmentFactor("20240103", "830000.BJ", adj_factor=1.00),
        ]

    def fetch_index_members(self, config: AShareDataConfig) -> list[IndexMember]:
        records: list[IndexMember] = []
        weights = {
            "000001.SZ": 0.42,
            "600000.SH": 0.38,
            "830000.BJ": 0.20,
        }
        for index_code in config.index_codes:
            for ts_code, weight in weights.items():
                records.append(
                    IndexMember(
                        index_code=index_code,
                        trade_date="20240103",
                        ts_code=ts_code,
                        weight=weight,
                    )
                )
        return records

    def fetch_corporate_actions(self, config: AShareDataConfig) -> list[CorporateAction]:
        return [
            CorporateAction(
                ts_code="000001.SZ",
                end_date="20231231",
                ann_date="20240101",
                div_proc="实施",
                cash_div=0.12,
                cash_div_tax=0.12,
                record_date="20240102",
                ex_date="20240103",
                pay_date="20240104",
                imp_ann_date="20240101",
                base_date="20231231",
                base_share=19405918198.0,
                source="sample",
                raw_status="实施",
            ),
            CorporateAction(
                ts_code="600000.SH",
                end_date="20231231",
                ann_date="20240101",
                div_proc="实施",
                stk_bo_rate=0.05,
                stk_co_rate=0.02,
                record_date="20240102",
                ex_date="20240103",
                div_listdate="20240104",
                imp_ann_date="20240101",
                base_date="20231231",
                base_share=29352080397.0,
                source="sample",
                raw_status="实施",
            ),
            CorporateAction(
                ts_code="830000.BJ",
                end_date="20231231",
                ann_date="20240101",
                div_proc="实施",
                cash_div=0.08,
                cash_div_tax=0.08,
                stk_div=0.10,
                record_date="20240102",
                ex_date="20240103",
                pay_date="20240104",
                div_listdate="20240104",
                imp_ann_date="20240101",
                base_date="20231231",
                base_share=80000000.0,
                source="sample",
                raw_status="实施",
            ),
            CorporateAction(
                ts_code="000001.SZ",
                end_date="20240630",
                ann_date="20240104",
                div_proc="预案",
                cash_div=0.20,
                record_date="20240104",
                ex_date="20240104",
                pay_date="20240104",
                base_date="20240630",
                source="sample",
                raw_status="预案",
            ),
        ]

    def fetch_generic_dataset(self, dataset: str, config: AShareDataConfig) -> list[dict[str, object]]:
        definition = DATASET_DEFINITIONS[dataset]
        if definition.index_param:
            return [
                _generic_sample_row(definition, config, index_code=index_code)
                for index_code in config.index_codes
            ]
        return [_generic_sample_row(definition, config)]


def _generic_sample_row(
    definition: AShareDatasetDefinition,
    config: AShareDataConfig,
    index_code: str | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {}
    for field in definition.fields:
        row[field] = _sample_value(field, definition, config, index_code=index_code)
    return row


def _sample_value(
    field: str,
    definition: AShareDatasetDefinition,
    config: AShareDataConfig,
    index_code: str | None = None,
) -> object:
    date = config.start_date
    if field in {"ts_code", "con_code"}:
        if definition.dataset == "index_basic":
            return "000300.SH"
        return index_code if definition.index_param == "ts_code" and index_code else "000001.SZ"
    if field in {"index_code", "l1_code"}:
        return index_code or "801010.SI"
    if field in {"l2_code", "l3_code", "industry_code"}:
        return {"l2_code": "801011.SI", "l3_code": "851011.SI"}.get(field, "801010.SI")
    if field in {"trade_date", "cal_date", "suspend_date", "ann_date", "f_ann_date", "first_ann_date", "actual_date", "modify_date", "ipo_date", "issue_date", "begin_date", "close_date", "start_date", "in_date", "float_date", "list_date"}:
        return date
    if field in {"end_date", "report_period", "pre_date", "out_date", "resume_date", "exp_date", "release_date"}:
        return "20231231"
    if field in {"name", "fullname", "holder_name", "buyer", "seller", "exalter", "publisher", "audit_agency", "audit_sign", "pledgor"}:
        return "样例"
    if field in {"market", "exchange", "exchange_id"}:
        return "SSE"
    if field in {"src"}:
        return "SW2021"
    if field in {"level"}:
        return "L1"
    if field in {"industry_name", "l1_name", "l2_name", "l3_name", "bz_item"}:
        return "银行"
    if field in {"is_new", "is_pub", "is_audit", "is_release", "update_flag"}:
        return "1"
    if field in {"report_type", "comp_type", "end_type", "curr_type", "type", "proc", "side", "reason", "change_reason", "suspend_reason", "reason_type", "holder_type", "in_de", "share_type", "category", "index_type", "weight_rule", "audit_result", "remark", "summary", "perf_summary", "desc"}:
        return "样例"
    if field in {"base_date"}:
        return "20041231"
    return 1.0

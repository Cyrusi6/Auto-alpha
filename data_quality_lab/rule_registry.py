"""Semantic data quality rule registry."""

from __future__ import annotations

from .models import DataQualityRuleDefinition, DataQualityRuleScope, DataQualitySeverity


CORE_REQUIRED_DATASETS = {
    "securities",
    "trade_calendar",
    "daily_bars",
    "daily_basic",
    "daily_limits",
    "adjustment_factors",
    "index_members",
    "corporate_actions",
    "financial_features",
}

OPTIONAL_EVENT_DATASETS = {
    "top_list",
    "top_inst",
    "block_trades",
    "holder_trades",
    "repurchases",
    "share_unlocks",
    "hk_holdings",
}

FINANCIAL_STATEMENT_DATASETS = {
    "income_statements",
    "balance_sheets",
    "cashflow_statements",
}

EXPANDED_DATASETS = {
    "index_basic",
    "index_daily_bars",
    "index_daily_basic",
    "industry_classification",
    "industry_members",
    "suspensions",
    "name_changes",
    "new_shares",
    "moneyflow",
    "margin_summary",
    "margin_detail",
    "top_list",
    "top_inst",
    "block_trades",
    "holder_number",
    "holder_trades",
    "top10_holders",
    "top10_float_holders",
    "pledge_detail",
    "pledge_stat",
    "repurchases",
    "share_unlocks",
    "hk_holdings",
    *FINANCIAL_STATEMENT_DATASETS,
    "earnings_forecasts",
    "earnings_express",
    "disclosure_calendar",
    "financial_audit",
    "main_business",
}


def build_rule_registry() -> list[DataQualityRuleDefinition]:
    rules = [
        _rule("securities.ts_code_unique", "securities", "Unique ts_code", "error", "ts_code must be non-empty and unique.", "compact_dedup"),
        _rule("securities.list_status_valid", "securities", "Valid list_status", "warning", "list_status should be one of L, D, P.", "inspect_empty_response"),
        _rule("securities.list_date_valid", "securities", "Valid list dates", "error", "list_date and delist_date must be valid and ordered.", "rerun_dataset"),
        _rule("trade_calendar.cal_date_unique", "trade_calendar", "Unique cal_date", "error", "cal_date must be unique.", "compact_dedup"),
        _rule("trade_calendar.is_open_valid", "trade_calendar", "Valid is_open", "error", "is_open must be boolean-like.", "rerun_dataset"),
        _rule("trade_calendar.coverage", "trade_calendar", "Calendar coverage", "warning", "Calendar should cover requested period.", "rerun_dataset"),
        _rule("daily_bars.primary_key_unique", "daily_bars", "Unique daily bar key", "error", "ts_code/trade_date must be unique.", "compact_dedup"),
        _rule("daily_bars.ohlc_non_negative", "daily_bars", "Non-negative prices", "error", "OHLC prices must be non-negative.", "rerun_dataset"),
        _rule("daily_bars.ohlc_order", "daily_bars", "OHLC ordering", "error", "high/low must bracket open and close.", "rerun_dataset"),
        _rule("daily_bars.volume_amount_consistent", "daily_bars", "Volume amount consistency", "warning", "volume and amount zero/non-zero should be consistent.", "inspect_empty_response"),
        _rule("daily_bars.pct_chg_consistency", "daily_bars", "pct_chg consistency", "warning", "pct_chg should roughly match close/pre_close.", "rerun_dataset"),
        _rule("daily_bars.trade_date_open", "daily_bars", "Trading calendar alignment", "error", "Daily bars must occur on open trading days.", "rerun_dataset"),
        _rule("daily_bars.security_lifecycle", "daily_bars", "Security lifecycle alignment", "error", "Bars should not appear before listing or after delisting.", "rerun_dataset"),
        _rule("daily_basic.primary_key_unique", "daily_basic", "Unique daily basic key", "error", "ts_code/trade_date must be unique.", "compact_dedup"),
        _rule("daily_basic.non_negative", "daily_basic", "Non-negative daily basic fields", "warning", "Turnover, volume_ratio, total_mv and circ_mv should be non-negative.", "rerun_dataset"),
        _rule("daily_basic.coverage_with_daily_bars", "daily_basic", "Daily basic coverage", "warning", "daily_basic keys should align with daily_bars.", "rerun_dataset"),
        _rule("adjustment_factors.primary_key_unique", "adjustment_factors", "Unique adjustment key", "error", "ts_code/trade_date must be unique.", "compact_dedup"),
        _rule("adjustment_factors.positive", "adjustment_factors", "Positive adjustment factor", "error", "adj_factor must be positive.", "rerun_dataset"),
        _rule("daily_limits.primary_key_unique", "daily_limits", "Unique limit key", "error", "ts_code/trade_date must be unique.", "compact_dedup"),
        _rule("daily_limits.price_order", "daily_limits", "Valid limit range", "error", "up_limit should be greater than down_limit.", "rerun_dataset"),
        _rule("daily_limits.close_within_limits", "daily_limits", "Close within limits", "warning", "daily close should not breach limit prices beyond tolerance.", "rerun_dataset"),
        _rule("index_members.primary_key_unique", "index_members", "Unique index member key", "warning", "index_code/date/ts_code should be unique.", "compact_dedup"),
        _rule("index_members.weight_non_negative", "index_members", "Non-negative index weight", "warning", "Index weights should be non-negative.", "rerun_dataset"),
        _rule("index_members.security_exists", "index_members", "Member code exists", "warning", "Index members should exist in securities.", "rerun_dataset"),
        _rule("corporate_actions.date_fields", "corporate_actions", "Valid corporate-action dates", "warning", "Corporate-action date fields should be valid.", "require_pit_review"),
        _rule("corporate_actions.non_negative", "corporate_actions", "Non-negative action values", "warning", "Dividend/share distribution values should be non-negative.", "rerun_dataset"),
        _rule("financial_features.pit_ann_date", "financial_features", "PIT announcement date", "warning", "ann_date should exist and end_date should not exceed ann_date.", "require_pit_review"),
        _rule("statements.pit_ann_date", "financial_statements", "Statement PIT announcement date", "error", "Financial statements require ann_date for PIT safety.", "require_pit_review"),
        _rule("events.valid_event_date", "event_datasets", "Valid event dates", "warning", "Event rows should have a valid event/announcement date.", "require_pit_review"),
        _rule("cross.daily_basic_key_mismatch", "cross_dataset", "Daily key mismatch", "warning", "daily_bars and daily_basic key coverage should align.", "rerun_dataset", DataQualityRuleScope.cross_dataset),
        _rule("cross.daily_limit_violation", "cross_dataset", "Limit cross-check", "warning", "daily_bars close should be within daily_limits.", "rerun_dataset", DataQualityRuleScope.cross_dataset),
        _rule("cross.unknown_security", "cross_dataset", "Unknown security codes", "error", "Dataset ts_code should exist in securities.", "rerun_dataset", DataQualityRuleScope.cross_dataset),
        _rule("raw_index.stale", "raw_data_index", "Raw index freshness", "warning", "Stale raw data index should not be trusted.", "block_freeze_until_repaired"),
    ]
    return rules


def rules_by_id() -> dict[str, DataQualityRuleDefinition]:
    return {rule.rule_id: rule for rule in build_rule_registry()}


def _rule(
    rule_id: str,
    dataset: str,
    name: str,
    severity: str,
    description: str,
    suggestion_action: str,
    scope: str = DataQualityRuleScope.dataset,
) -> DataQualityRuleDefinition:
    return DataQualityRuleDefinition(
        rule_id=rule_id,
        dataset=dataset,
        name=name,
        scope=scope,
        severity=getattr(DataQualitySeverity, severity),
        description=description,
        suggestion_action=suggestion_action,
    )

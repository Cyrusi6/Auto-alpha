"""Versioned A-share feature catalog."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Iterable

from model_core.vocab import FEATURE_NAMES

from .models import FeatureDefinition, FeatureFamily, FeatureSetManifest


FEATURE_SET_V1 = "ashare_features_v1"
FEATURE_SET_V2 = "ashare_features_v2"
FEATURE_SET_V3 = "ashare_features_v3"
DEFAULT_OPERATOR_VERSION = "ashare_ops_v1"


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def get_feature_definitions(
    feature_set_name: str = FEATURE_SET_V1,
    *,
    corporate_action_aware: bool = False,
) -> list[FeatureDefinition]:
    if feature_set_name == FEATURE_SET_V1:
        return _v1_definitions()
    if feature_set_name == FEATURE_SET_V3:
        definitions = get_feature_definitions(FEATURE_SET_V2, corporate_action_aware=corporate_action_aware)
        seen = {item.feature_name for item in definitions}
        for definition in _v3_extra_definitions():
            if definition.feature_name not in seen:
                definitions.append(definition)
                seen.add(definition.feature_name)
        return definitions
    if feature_set_name != FEATURE_SET_V2:
        raise ValueError(f"unknown feature set: {feature_set_name}")
    definitions = _v1_definitions()
    seen = {item.feature_name for item in definitions}
    for definition in _v2_extra_definitions(corporate_action_aware=corporate_action_aware):
        if definition.feature_name not in seen:
            definitions.append(definition)
            seen.add(definition.feature_name)
    return definitions


def build_feature_set_manifest(
    feature_set_name: str = FEATURE_SET_V1,
    feature_set_version: str = "1.0",
    *,
    data_freeze_id: str | None = None,
    data_freeze_hash: str | None = None,
    point_in_time: bool = False,
    corporate_action_aware: bool = False,
    target_return_mode: str = "adjusted_close",
    created_at: str | None = None,
) -> FeatureSetManifest:
    definitions = get_feature_definitions(feature_set_name, corporate_action_aware=corporate_action_aware)
    timestamp = created_at or utc_now()
    payload = {
        "feature_set_name": feature_set_name,
        "feature_set_version": feature_set_version,
        "feature_version": feature_set_name,
        "operator_version": DEFAULT_OPERATOR_VERSION,
        "features": [item.to_dict() for item in definitions],
        "data_freeze_id": data_freeze_id,
        "data_freeze_hash": data_freeze_hash,
        "point_in_time": bool(point_in_time),
        "corporate_action_aware": bool(corporate_action_aware),
        "target_return_mode": target_return_mode,
    }
    content_hash = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return FeatureSetManifest(
        feature_set_name=feature_set_name,
        feature_set_version=feature_set_version,
        feature_version=feature_set_name,
        operator_version=DEFAULT_OPERATOR_VERSION,
        feature_count=len(definitions),
        feature_definitions=[item.to_dict() for item in definitions],
        data_freeze_id=data_freeze_id,
        data_freeze_hash=data_freeze_hash,
        point_in_time=bool(point_in_time),
        corporate_action_aware=bool(corporate_action_aware),
        target_return_mode=target_return_mode,
        created_at=timestamp,
        content_hash=content_hash,
    )


def manifest_from_payload(payload: dict) -> FeatureSetManifest:
    return FeatureSetManifest(
        feature_set_name=str(payload["feature_set_name"]),
        feature_set_version=str(payload.get("feature_set_version", "1.0")),
        feature_version=str(payload.get("feature_version", payload["feature_set_name"])),
        operator_version=str(payload.get("operator_version", DEFAULT_OPERATOR_VERSION)),
        feature_count=int(payload.get("feature_count", len(payload.get("feature_definitions", [])))),
        feature_definitions=list(payload.get("feature_definitions", [])),
        data_freeze_id=payload.get("data_freeze_id"),
        data_freeze_hash=payload.get("data_freeze_hash"),
        point_in_time=bool(payload.get("point_in_time", False)),
        corporate_action_aware=bool(payload.get("corporate_action_aware", False)),
        target_return_mode=str(payload.get("target_return_mode", "adjusted_close")),
        created_at=str(payload.get("created_at", utc_now())),
        content_hash=str(payload.get("content_hash", "")),
        feature_promotion_policy_hash=payload.get("feature_promotion_policy_hash"),
        feature_promotion_summary=dict(payload.get("feature_promotion_summary", {}) or {}),
    )


def _definition(
    name: str,
    family: str,
    source_fields: Iterable[str],
    *,
    feature_version: str = FEATURE_SET_V2,
    tensor_key: str | None = None,
    transform: str = "robust_zscore",
    lookback: int = 1,
    pit_safe: bool = True,
    default_enabled: bool = True,
    required_datasets: Iterable[str] | None = None,
    optional_datasets: Iterable[str] | None = None,
    date_field: str = "trade_date",
    availability_field: str | None = None,
    pit_safety: str | None = None,
    used_for_alpha: bool = True,
    used_for_filter: bool = False,
    used_for_risk: bool = False,
    description: str = "",
) -> FeatureDefinition:
    return FeatureDefinition(
        feature_name=name,
        feature_version=feature_version,
        family=family,
        source_fields=list(source_fields),
        tensor_key=tensor_key or name.lower(),
        feature_set_name=feature_version,
        required_datasets=list(required_datasets or []),
        optional_datasets=list(optional_datasets or []),
        date_field=date_field,
        availability_field=availability_field,
        pit_safety=pit_safety or ("pit_safe" if pit_safe else "weak_pit"),
        transform=transform,
        lookback=lookback,
        point_in_time_safe=pit_safe,
        used_for_alpha=used_for_alpha,
        used_for_filter=used_for_filter,
        used_for_risk=used_for_risk,
        default_enabled=default_enabled,
        description=description,
    )


def _v1_definitions() -> list[FeatureDefinition]:
    families = {
        "RET_1D": FeatureFamily.price_return,
        "RET_5D": FeatureFamily.price_return,
        "AMPLITUDE": FeatureFamily.volatility,
        "TURNOVER_RATE": FeatureFamily.liquidity,
        "VOLUME_RATIO": FeatureFamily.liquidity,
        "LOG_AMOUNT": FeatureFamily.liquidity,
        "LOG_MKT_CAP": FeatureFamily.size,
        "PB": FeatureFamily.valuation,
        "PE_TTM": FeatureFamily.valuation,
        "ROE": FeatureFamily.quality,
        "REVENUE_YOY": FeatureFamily.growth,
    }
    sources = {
        "RET_1D": ["close"],
        "RET_5D": ["close"],
        "AMPLITUDE": ["high", "low", "pre_close"],
        "TURNOVER_RATE": ["turnover_rate"],
        "VOLUME_RATIO": ["volume_ratio"],
        "LOG_AMOUNT": ["amount"],
        "LOG_MKT_CAP": ["total_mv"],
        "PB": ["pb"],
        "PE_TTM": ["pe_ttm"],
        "ROE": ["roe"],
        "REVENUE_YOY": ["revenue_yoy"],
    }
    return [
        _definition(
            name,
            families[name],
            sources[name],
            feature_version=FEATURE_SET_V1,
            tensor_key=name.lower(),
            lookback=5 if name == "RET_5D" else 1,
        )
        for name in FEATURE_NAMES
    ]


def _v2_extra_definitions(*, corporate_action_aware: bool) -> list[FeatureDefinition]:
    definitions = [
        _definition("RET_3D", FeatureFamily.price_return, ["close"], lookback=3),
        _definition("RET_10D", FeatureFamily.price_return, ["close"], lookback=10),
        _definition("RET_20D", FeatureFamily.price_return, ["close"], lookback=20),
        _definition("INTRADAY_RETURN", FeatureFamily.price_return, ["open", "close"]),
        _definition("GAP_RETURN", FeatureFamily.price_return, ["open", "pre_close"]),
        _definition("AMOUNT_Z20", FeatureFamily.liquidity, ["amount"], lookback=20),
        _definition("TURNOVER_Z20", FeatureFamily.liquidity, ["turnover_rate"], lookback=20),
        _definition("VOLATILITY_5D", FeatureFamily.volatility, ["close"], lookback=5),
        _definition("VOLATILITY_20D", FeatureFamily.volatility, ["close"], lookback=20),
        _definition("DOWNSIDE_VOL_20D", FeatureFamily.volatility, ["close"], lookback=20),
        _definition("PS_TTM", FeatureFamily.valuation, ["ps_ttm"]),
        _definition("LIMIT_UP_FLAG", FeatureFamily.limit_suspension, ["limit_up_flag"], transform="identity"),
        _definition("LIMIT_DOWN_FLAG", FeatureFamily.limit_suspension, ["limit_down_flag"], transform="identity"),
        _definition("SUSPENSION_FLAG", FeatureFamily.limit_suspension, ["is_suspended"], transform="identity"),
        _definition("INDEX_MEMBER_FLAG", FeatureFamily.index_membership, ["index_member_matrix"], transform="identity"),
        _definition("ACTIVE_MASK", FeatureFamily.risk, ["active_mask"], transform="identity"),
        _definition("LISTING_AGE_DAYS", FeatureFamily.risk, ["listing_age_days"]),
    ]
    if corporate_action_aware:
        definitions.extend(
            [
                _definition("CASH_DIVIDEND_FLAG", FeatureFamily.corporate_action, ["cash_dividend_flag"], transform="identity"),
                _definition("STOCK_DISTRIBUTION_FLAG", FeatureFamily.corporate_action, ["stock_distribution_flag"], transform="identity"),
            ]
        )
    return definitions


def _v3(
    name: str,
    family: str,
    source_fields: Iterable[str],
    *,
    required: Iterable[str],
    optional: Iterable[str] | None = None,
    date_field: str = "trade_date",
    availability_field: str | None = None,
    pit_safety: str = "pit_safe",
    lookback: int = 1,
    transform: str = "robust_zscore",
    default_enabled: bool = True,
    used_for_alpha: bool = True,
    used_for_filter: bool = False,
    used_for_risk: bool = False,
    description: str = "",
) -> FeatureDefinition:
    weak_or_unsafe = pit_safety in {"weak_pit", "unsafe_missing_availability"}
    return _definition(
        name,
        family,
        source_fields,
        feature_version=FEATURE_SET_V3,
        tensor_key=name.lower(),
        transform=transform,
        lookback=lookback,
        pit_safe=pit_safety == "pit_safe",
        default_enabled=bool(default_enabled and not weak_or_unsafe),
        required_datasets=required,
        optional_datasets=optional or [],
        date_field=date_field,
        availability_field=availability_field,
        pit_safety=pit_safety,
        used_for_alpha=used_for_alpha and not weak_or_unsafe,
        used_for_filter=used_for_filter,
        used_for_risk=used_for_risk,
        description=description,
    )


def _v3_extra_definitions() -> list[FeatureDefinition]:
    return [
        _v3("INDEX_RETURN_1D", FeatureFamily.index_market, ["index_daily_bars.close"], required=["index_daily_bars"], lookback=60, transform="time_series_zscore"),
        _v3("INDEX_RETURN_5D", FeatureFamily.index_market, ["index_daily_bars.close"], required=["index_daily_bars"], lookback=60, transform="time_series_zscore"),
        _v3("INDEX_RETURN_20D", FeatureFamily.index_market, ["index_daily_bars.close"], required=["index_daily_bars"], lookback=60, transform="time_series_zscore"),
        _v3("INDEX_VOLATILITY_20D", FeatureFamily.index_market, ["index_daily_bars.close"], required=["index_daily_bars"], lookback=60, transform="time_series_zscore"),
        _v3("BENCHMARK_RELATIVE_RETURN_5D", FeatureFamily.index_market, ["close", "index_daily_bars.close"], required=["daily_bars"], optional=["index_daily_bars"], lookback=5),
        _v3("BENCHMARK_RELATIVE_RETURN_20D", FeatureFamily.index_market, ["close", "index_daily_bars.close"], required=["daily_bars"], optional=["index_daily_bars"], lookback=20),
        _v3("MARKET_REGIME_UP_DOWN_FLAG", FeatureFamily.index_market, ["index_daily_bars.close"], required=["index_daily_bars"], transform="identity"),
        _v3("INDEX_VALUATION_PE", FeatureFamily.index_market, ["index_daily_basic.pe"], required=["index_daily_basic"], lookback=60, transform="time_series_zscore"),
        _v3("INDEX_VALUATION_PB", FeatureFamily.index_market, ["index_daily_basic.pb"], required=["index_daily_basic"], lookback=60, transform="time_series_zscore"),
        _v3("INDUSTRY_MEMBER_FLAG", FeatureFamily.industry, ["industry_members"], required=["industry_members"], transform="identity", used_for_filter=True, used_for_risk=True),
        _v3("INDUSTRY_RELATIVE_RETURN_5D", FeatureFamily.industry, ["industry_members", "close"], required=["daily_bars"], optional=["industry_members"], lookback=5),
        _v3("INDUSTRY_RELATIVE_RETURN_20D", FeatureFamily.industry, ["industry_members", "close"], required=["daily_bars"], optional=["industry_members"], lookback=20),
        _v3("INDUSTRY_RELATIVE_TURNOVER", FeatureFamily.industry, ["industry_members", "turnover_rate"], required=["daily_basic"], optional=["industry_members"]),
        _v3("INDUSTRY_MOMENTUM", FeatureFamily.industry, ["industry_members", "close"], required=["daily_bars"], optional=["industry_members"], lookback=20),
        _v3("INDUSTRY_CONCENTRATION_PROXY", FeatureFamily.industry, ["industry_members"], required=["industry_members"], used_for_risk=True),
        _v3("RECENT_SUSPENSION_COUNT_20D", FeatureFamily.suspension_status, ["is_suspended"], required=["daily_bars"], lookback=20, used_for_filter=True, used_for_risk=True),
        _v3("NAME_CHANGE_ST_FLAG", FeatureFamily.suspension_status, ["name_changes"], required=["name_changes"], availability_field="ann_date", pit_safety="event_date_only", transform="identity", used_for_filter=True, used_for_risk=True),
        _v3("NEW_SHARE_FLAG", FeatureFamily.suspension_status, ["new_shares"], required=["new_shares"], availability_field="ipo_date", pit_safety="event_date_only", transform="identity", used_for_filter=True, used_for_risk=True),
        _v3("ST_HISTORY_FLAG", FeatureFamily.suspension_status, ["name_changes"], required=["name_changes"], availability_field="ann_date", pit_safety="event_date_only", transform="identity", used_for_filter=True, used_for_risk=True),
        _v3("ROA", FeatureFamily.financial_statement, ["income_statements", "balance_sheets"], required=["income_statements", "balance_sheets"], date_field="end_date", availability_field="ann_date"),
        _v3("GROSS_MARGIN", FeatureFamily.financial_statement, ["income_statements"], required=["income_statements"], date_field="end_date", availability_field="ann_date"),
        _v3("NET_MARGIN", FeatureFamily.financial_statement, ["income_statements"], required=["income_statements"], date_field="end_date", availability_field="ann_date"),
        _v3("ASSET_TURNOVER", FeatureFamily.financial_statement, ["income_statements", "balance_sheets"], required=["income_statements", "balance_sheets"], date_field="end_date", availability_field="ann_date"),
        _v3("DEBT_TO_ASSET", FeatureFamily.financial_statement, ["balance_sheets"], required=["balance_sheets"], date_field="end_date", availability_field="ann_date"),
        _v3("CURRENT_RATIO", FeatureFamily.financial_statement, ["balance_sheets"], required=["balance_sheets"], date_field="end_date", availability_field="ann_date"),
        _v3("OPERATING_CASHFLOW_TO_NET_INCOME", FeatureFamily.financial_statement, ["cashflow_statements", "income_statements"], required=["cashflow_statements", "income_statements"], date_field="end_date", availability_field="ann_date"),
        _v3("FREE_CASHFLOW_PROXY", FeatureFamily.financial_statement, ["cashflow_statements", "balance_sheets"], required=["cashflow_statements", "balance_sheets"], date_field="end_date", availability_field="ann_date"),
        _v3("REVENUE_GROWTH_YOY", FeatureFamily.financial_statement, ["income_statements"], required=["income_statements"], date_field="end_date", availability_field="ann_date"),
        _v3("NET_PROFIT_GROWTH_YOY", FeatureFamily.financial_statement, ["income_statements"], required=["income_statements"], date_field="end_date", availability_field="ann_date"),
        _v3("CASHFLOW_GROWTH_YOY", FeatureFamily.financial_statement, ["cashflow_statements"], required=["cashflow_statements"], date_field="end_date", availability_field="ann_date"),
        _v3("ACCRUALS_PROXY", FeatureFamily.financial_statement, ["income_statements", "cashflow_statements"], required=["income_statements", "cashflow_statements"], date_field="end_date", availability_field="ann_date"),
        _v3("FORECAST_UPWARD_REVISION_FLAG", FeatureFamily.earnings_event, ["earnings_forecasts"], required=["earnings_forecasts"], availability_field="ann_date", pit_safety="event_date_only", transform="identity"),
        _v3("FORECAST_PROFIT_WARNING_FLAG", FeatureFamily.earnings_event, ["earnings_forecasts"], required=["earnings_forecasts"], availability_field="ann_date", pit_safety="event_date_only", transform="identity"),
        _v3("EXPRESS_SURPRISE_PROXY", FeatureFamily.earnings_event, ["earnings_express"], required=["earnings_express"], availability_field="ann_date", pit_safety="event_date_only"),
        _v3("DAYS_TO_DISCLOSURE", FeatureFamily.earnings_event, ["disclosure_calendar"], required=["disclosure_calendar"], availability_field="pre_date", pit_safety="event_date_only", used_for_risk=True),
        _v3("DAYS_SINCE_DISCLOSURE", FeatureFamily.earnings_event, ["disclosure_calendar"], required=["disclosure_calendar"], availability_field="actual_date", pit_safety="event_date_only", used_for_risk=True),
        _v3("DISCLOSURE_DELAY_FLAG", FeatureFamily.earnings_event, ["disclosure_calendar"], required=["disclosure_calendar"], availability_field="actual_date", pit_safety="event_date_only", transform="identity", used_for_risk=True),
        _v3("MONEYFLOW_NET_RATIO", FeatureFamily.moneyflow, ["moneyflow"], required=["moneyflow"]),
        _v3("MONEYFLOW_MAIN_NET_RATIO", FeatureFamily.moneyflow, ["moneyflow"], required=["moneyflow"]),
        _v3("MONEYFLOW_SMALL_ORDER_RATIO", FeatureFamily.moneyflow, ["moneyflow"], required=["moneyflow"]),
        _v3("MONEYFLOW_Z20", FeatureFamily.moneyflow, ["moneyflow"], required=["moneyflow"], lookback=20),
        _v3("MONEYFLOW_TREND_5D", FeatureFamily.moneyflow, ["moneyflow"], required=["moneyflow"], lookback=5),
        _v3("MONEYFLOW_TREND_20D", FeatureFamily.moneyflow, ["moneyflow"], required=["moneyflow"], lookback=20),
        _v3("MARGIN_BALANCE_CHANGE", FeatureFamily.margin, ["margin_detail"], required=["margin_detail"]),
        _v3("MARGIN_BUY_RATIO", FeatureFamily.margin, ["margin_detail"], required=["margin_detail"]),
        _v3("SHORT_SELL_BALANCE_CHANGE", FeatureFamily.margin, ["margin_detail"], required=["margin_detail"]),
        _v3("MARGIN_CROWDING_Z20", FeatureFamily.margin, ["margin_detail"], required=["margin_detail"], lookback=20),
        _v3("TOP_LIST_FLAG", FeatureFamily.abnormal_trading, ["top_list"], required=["top_list"], pit_safety="event_date_only", transform="identity"),
        _v3("TOP_INST_NET_BUY_RATIO", FeatureFamily.abnormal_trading, ["top_inst"], required=["top_inst"], pit_safety="event_date_only"),
        _v3("BLOCK_TRADE_DISCOUNT_PROXY", FeatureFamily.abnormal_trading, ["block_trades"], required=["block_trades"], pit_safety="event_date_only"),
        _v3("BLOCK_TRADE_VALUE_RATIO", FeatureFamily.abnormal_trading, ["block_trades"], required=["block_trades"], pit_safety="event_date_only"),
        _v3("HOLDER_NUMBER_CHANGE", FeatureFamily.holder_structure, ["holder_number"], required=["holder_number"], date_field="end_date", availability_field="ann_date", pit_safety="weak_pit"),
        _v3("HOLDER_CONCENTRATION_PROXY", FeatureFamily.holder_structure, ["holder_number"], required=["holder_number"], date_field="end_date", availability_field="ann_date", pit_safety="weak_pit"),
        _v3("TOP10_HOLDER_RATIO", FeatureFamily.holder_structure, ["top10_holders"], required=["top10_holders"], date_field="end_date", availability_field="ann_date", pit_safety="weak_pit"),
        _v3("TOP10_FLOAT_HOLDER_RATIO", FeatureFamily.holder_structure, ["top10_float_holders"], required=["top10_float_holders"], date_field="end_date", availability_field="ann_date", pit_safety="weak_pit"),
        _v3("PLEDGE_RATIO", FeatureFamily.pledge_repurchase_unlock, ["pledge_stat"], required=["pledge_stat"], date_field="end_date", availability_field="ann_date", pit_safety="weak_pit", used_for_risk=True),
        _v3("PLEDGE_RISK_FLAG", FeatureFamily.pledge_repurchase_unlock, ["pledge_stat"], required=["pledge_stat"], date_field="end_date", availability_field="ann_date", pit_safety="weak_pit", transform="identity", used_for_risk=True),
        _v3("REPURCHASE_FLAG", FeatureFamily.pledge_repurchase_unlock, ["repurchases"], required=["repurchases"], availability_field="ann_date", pit_safety="event_date_only", transform="identity"),
        _v3("REPURCHASE_AMOUNT_RATIO", FeatureFamily.pledge_repurchase_unlock, ["repurchases"], required=["repurchases"], availability_field="ann_date", pit_safety="event_date_only"),
        _v3("SHARE_UNLOCK_AMOUNT_RATIO", FeatureFamily.pledge_repurchase_unlock, ["share_unlocks"], required=["share_unlocks"], availability_field="ann_date", pit_safety="event_date_only", used_for_risk=True),
        _v3("UNLOCK_PRESSURE_FLAG", FeatureFamily.pledge_repurchase_unlock, ["share_unlocks"], required=["share_unlocks"], availability_field="ann_date", pit_safety="event_date_only", transform="identity", used_for_risk=True),
        _v3("HK_HOLDING_RATIO", FeatureFamily.northbound, ["hk_holdings"], required=["hk_holdings"]),
        _v3("HK_HOLDING_CHANGE_5D", FeatureFamily.northbound, ["hk_holdings"], required=["hk_holdings"], lookback=5),
        _v3("HK_HOLDING_CHANGE_20D", FeatureFamily.northbound, ["hk_holdings"], required=["hk_holdings"], lookback=20),
        _v3("HK_HOLDING_Z20", FeatureFamily.northbound, ["hk_holdings"], required=["hk_holdings"], lookback=20),
    ]

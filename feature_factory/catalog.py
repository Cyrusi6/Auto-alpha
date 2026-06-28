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
    description: str = "",
) -> FeatureDefinition:
    return FeatureDefinition(
        feature_name=name,
        feature_version=feature_version,
        family=family,
        source_fields=list(source_fields),
        tensor_key=tensor_key or name.lower(),
        transform=transform,
        lookback=lookback,
        point_in_time_safe=pit_safe,
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

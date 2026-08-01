"""Broker file profile registry and JSON overrides."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .models import BrokerFileProfile, BrokerFileSchemaName


INTERNAL_FIELDS = [
    "client_order_id",
    "trade_date",
    "ts_code",
    "side",
    "shares",
    "price",
    "price_type",
    "order_value",
    "parent_order_id",
    "child_order_id",
    "bucket",
    "broker_batch_id",
    "production_run_id",
]

QMT_SKELETON_NOTICE = (
    "qmt_skeleton_csv is a config-driven dry-run mapping skeleton only. "
    "It does not guarantee compatibility with any real QMT or broker counter. "
    "Real columns, encoding, side enums, price types, order attributes and file paths require manual verification."
)


def get_profile(profile_name: str = BrokerFileSchemaName.generic_broker_csv) -> BrokerFileProfile:
    if profile_name == BrokerFileSchemaName.generic_broker_jsonl:
        return _profile(profile_name, BrokerFileSchemaName.generic_broker_jsonl, {}, "generic JSONL dry-run instruction schema")
    if profile_name == BrokerFileSchemaName.qmt_skeleton_csv:
        mapping = {
            "client_order_id": "client_order_id",
            "trade_date": "trade_date",
            "ts_code": "security_code",
            "side": "side",
            "shares": "volume",
            "price": "price",
            "price_type": "price_type",
            "order_value": "order_value",
            "parent_order_id": "parent_order_id",
            "child_order_id": "child_order_id",
            "bucket": "bucket",
            "broker_batch_id": "broker_batch_id",
            "production_run_id": "production_run_id",
        }
        return _profile(profile_name, BrokerFileSchemaName.qmt_skeleton_csv, mapping, QMT_SKELETON_NOTICE)
    return _profile(
        BrokerFileSchemaName.generic_broker_csv,
        BrokerFileSchemaName.generic_broker_csv,
        {},
        "generic broker CSV dry-run instruction schema; no real broker compatibility is implied",
    )


def load_profile(profile_name: str = BrokerFileSchemaName.generic_broker_csv, profile_config: str | Path | None = None) -> BrokerFileProfile:
    profile = get_profile(profile_name)
    if profile_config is None:
        return profile
    payload = json.loads(Path(profile_config).read_text(encoding="utf-8"))
    base = profile.to_dict()
    for key, value in payload.items():
        if key in base and value is not None:
            base[key] = value
    if base.get("schema_name") == BrokerFileSchemaName.custom_csv_mapping:
        base.setdefault("notice", "custom dry-run mapping; manual field certification required")
    base["profile_id"] = _profile_id(base)
    return BrokerFileProfile(**base)


def profile_hash(profile: BrokerFileProfile) -> str:
    return _profile_id(profile.to_dict()).replace("profile_", "")


def _profile(profile_name: str, schema_name: str, field_mapping: dict[str, str], notice: str) -> BrokerFileProfile:
    mapping = {field: field_mapping.get(field, field) for field in INTERNAL_FIELDS}
    payload: dict[str, Any] = {
        "profile_id": "",
        "profile_name": profile_name,
        "schema_name": schema_name,
        "field_mapping": mapping,
        "required_columns": [mapping[field] for field in INTERNAL_FIELDS[:8]],
        "optional_columns": [mapping[field] for field in INTERNAL_FIELDS[8:]],
        "notice": notice,
        "metadata": {"no_real_submit": True},
    }
    payload["profile_id"] = _profile_id(payload)
    return BrokerFileProfile(**payload)


def _profile_id(payload: dict[str, Any]) -> str:
    stable = json.dumps({k: v for k, v in payload.items() if k != "profile_id"}, ensure_ascii=False, sort_keys=True)
    return "profile_" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:16]

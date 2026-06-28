"""Broker statement schema definitions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import BrokerStatementSchema


DATASET_FILES = {
    "orders": ["external_orders.csv", "external_orders.jsonl"],
    "trades": ["external_trades.csv", "external_trades.jsonl"],
    "fills": ["external_fills.csv", "external_fills.jsonl"],
    "positions": ["external_positions.csv", "external_positions.jsonl"],
    "cash": ["external_cash.csv", "external_cash.json", "external_cash.jsonl"],
    "settlements": ["external_settlements.csv", "external_settlements.jsonl"],
    "corporate_actions": ["external_corporate_actions.csv", "external_corporate_actions.jsonl"],
}


NORMALIZED_FILES = {
    "orders": "normalized_external_orders.jsonl",
    "trades": "normalized_external_trades.jsonl",
    "fills": "normalized_external_fills.jsonl",
    "positions": "normalized_external_positions.jsonl",
    "cash": "normalized_external_cash.jsonl",
    "settlements": "normalized_external_settlements.jsonl",
    "corporate_actions": "normalized_external_corporate_actions.jsonl",
}


GENERIC_FIELDS = {
    "account_id": "account_id",
    "broker_name": "broker_name",
    "trade_date": "trade_date",
    "as_of_date": "as_of_date",
    "external_order_id": "external_order_id",
    "external_trade_id": "external_trade_id",
    "external_fill_id": "external_fill_id",
    "external_settlement_id": "external_settlement_id",
    "external_action_id": "external_action_id",
    "broker_order_id": "broker_order_id",
    "broker_fill_id": "broker_fill_id",
    "client_order_id": "client_order_id",
    "settlement_event_id": "settlement_event_id",
    "source_id": "source_id",
    "source_type": "source_type",
    "action_id": "action_id",
    "ts_code": "ts_code",
    "side": "side",
    "price": "price",
    "shares": "shares",
    "value": "value",
    "commission": "commission",
    "stamp_duty": "stamp_duty",
    "transfer_fee": "transfer_fee",
    "slippage": "slippage",
    "market_impact": "market_impact",
    "other_fee": "other_fee",
    "total_fee": "total_fee",
    "cash_balance": "cash_balance",
    "available_cash": "available_cash",
    "withdrawable_cash": "withdrawable_cash",
    "frozen_cash": "frozen_cash",
    "unsettled_receivable": "unsettled_receivable",
    "unsettled_payable": "unsettled_payable",
    "position_shares": "position_shares",
    "available_shares": "available_shares",
    "cost_basis": "cost_basis",
    "market_value": "market_value",
    "realized_pnl": "realized_pnl",
    "unrealized_pnl": "unrealized_pnl",
    "settlement_date": "settlement_date",
    "available_date": "available_date",
    "event_type": "event_type",
    "status": "status",
    "reason": "reason",
}


def default_schema(schema_name: str = "generic_broker_statement") -> BrokerStatementSchema:
    if schema_name == "qmt_statement_skeleton":
        return BrokerStatementSchema(
            schema_name=schema_name,
            field_mapping={dataset: dict(GENERIC_FIELDS) for dataset in DATASET_FILES},
            required_files=["external_cash", "external_positions"],
            optional_files=list(DATASET_FILES),
            notice=(
                "qmt_statement_skeleton is only a configurable local field-mapping skeleton; "
                "it does not guarantee compatibility with real QMT or any broker counterparty file."
            ),
        )
    if schema_name != "generic_broker_statement":
        raise ValueError(f"unsupported broker statement schema: {schema_name}")
    return BrokerStatementSchema(
        schema_name=schema_name,
        field_mapping={dataset: dict(GENERIC_FIELDS) for dataset in DATASET_FILES},
        required_files=["external_cash", "external_positions"],
        optional_files=list(DATASET_FILES),
        notice="generic_broker_statement is an internal local schema for paper reconciliation.",
    )


def load_schema(schema_name: str = "generic_broker_statement", schema_config: str | Path | None = None) -> BrokerStatementSchema:
    schema = default_schema(schema_name)
    if schema_config is None:
        return schema
    payload = json.loads(Path(schema_config).read_text(encoding="utf-8"))
    field_mapping = {key: dict(value) for key, value in schema.field_mapping.items()}
    for dataset, mapping in dict(payload.get("field_mapping") or {}).items():
        current = field_mapping.setdefault(str(dataset), {})
        current.update({str(k): str(v) for k, v in dict(mapping).items()})
    return BrokerStatementSchema(
        schema_name=str(payload.get("schema_name") or schema.schema_name),
        field_mapping=field_mapping,
        required_files=list(payload.get("required_files") or schema.required_files),
        optional_files=list(payload.get("optional_files") or schema.optional_files),
        date_format=str(payload.get("date_format") or schema.date_format),
        amount_unit=str(payload.get("amount_unit") or schema.amount_unit),
        price_unit=str(payload.get("price_unit") or schema.price_unit),
        shares_unit=str(payload.get("shares_unit") or schema.shares_unit),
        notice=str(payload.get("notice") or schema.notice),
    )


def dataset_for_filename(filename: str) -> str | None:
    for dataset, names in DATASET_FILES.items():
        if filename in names:
            return dataset
    return None


def available_source_files(source_dir: str | Path) -> dict[str, Path]:
    root = Path(source_dir)
    result: dict[str, Path] = {}
    for dataset, names in DATASET_FILES.items():
        for name in names:
            path = root / name
            if path.exists():
                result[dataset] = path
                break
    return result


def normalized_path(output_dir: str | Path, dataset: str) -> Path:
    return Path(output_dir) / NORMALIZED_FILES[dataset]

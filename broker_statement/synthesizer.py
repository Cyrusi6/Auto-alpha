"""Synthetic broker statement generation from local internal artifacts."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from broker_adapter import LocalBrokerStore
from paper_account import LocalPaperAccount


def synthesize_statement_from_internal(
    output_dir: str | Path,
    broker_store_dir: str | Path | None = None,
    broker_batch_id: str | None = None,
    paper_account_dir: str | Path | None = None,
    settlement_dir: str | Path | None = None,
    account_id: str = "paper_ashare",
    broker_name: str = "synthetic_broker",
    trade_date: str = "",
    as_of_date: str = "",
    inject_cash_diff: float = 0.0,
    inject_position_diff: list[str] | None = None,
    drop_fill: list[str] | None = None,
    duplicate_fill: list[str] | None = None,
    inject_fee_diff: list[str] | None = None,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    account = LocalPaperAccount(paper_account_dir or output / "missing_account").load_state()
    store = LocalBrokerStore(broker_store_dir or output / "missing_broker")
    broker_fills = [fill.to_dict() for fill in store.load_fills(batch_id=broker_batch_id)]
    drop_ids = set(drop_fill or [])
    duplicate_ids = set(duplicate_fill or [])
    fee_diffs = _parse_id_amounts(inject_fee_diff or [])
    fills: list[dict[str, Any]] = []
    for fill in broker_fills:
        fill_id = str(fill.get("broker_fill_id") or "")
        if fill_id in drop_ids:
            continue
        record = _fill_record(fill, account_id, broker_name, trade_date, as_of_date)
        if fill_id in fee_diffs:
            record["other_fee"] = float(record.get("other_fee", 0.0) or 0.0) + fee_diffs[fill_id]
            record["total_fee"] = float(record.get("total_fee", 0.0) or 0.0) + fee_diffs[fill_id]
        fills.append(record)
        if fill_id in duplicate_ids:
            duplicate = dict(record)
            duplicate["external_fill_id"] = f"{record.get('external_fill_id')}_dup"
            fills.append(duplicate)
    orders = [_order_record(order.to_dict(), account_id, broker_name, trade_date, as_of_date) for order in store.load_orders(batch_id=broker_batch_id)]
    positions = [_position_record(position.to_dict(), account_id, broker_name, trade_date, as_of_date) for position in account.positions.values()]
    position_diffs = _parse_id_amounts(inject_position_diff or [])
    for position in positions:
        ts_code = str(position.get("ts_code") or "")
        if ts_code in position_diffs:
            delta = int(position_diffs[ts_code])
            position["position_shares"] = int(position.get("position_shares", 0) or 0) + delta
            position["available_shares"] = int(position.get("available_shares", 0) or 0) + delta
    cash = [
        {
            "account_id": account_id,
            "broker_name": broker_name,
            "trade_date": trade_date,
            "as_of_date": as_of_date or trade_date,
            "cash_balance": float(account.cash) + float(inject_cash_diff),
            "available_cash": float(account.available_cash if account.available_cash is not None else account.cash) + float(inject_cash_diff),
            "withdrawable_cash": float(account.withdrawable_cash if account.withdrawable_cash is not None else account.cash) + float(inject_cash_diff),
            "frozen_cash": float(account.frozen_cash),
            "unsettled_receivable": float(account.unsettled_receivable),
            "unsettled_payable": float(account.unsettled_payable),
        }
    ]
    settlements = [
        _settlement_record(event, account_id, broker_name, trade_date, as_of_date)
        for event in _read_jsonl(Path(settlement_dir or "") / "settlement_events.jsonl")
    ]
    corporate_actions = [
        _corporate_action_record(entry, account_id, broker_name, trade_date, as_of_date)
        for entry in account.corporate_action_ledger
    ]
    paths = {
        "external_orders_path": _write_jsonl(output / "external_orders.jsonl", orders),
        "external_fills_path": _write_jsonl(output / "external_fills.jsonl", fills),
        "external_positions_path": _write_jsonl(output / "external_positions.jsonl", positions),
        "external_cash_path": _write_jsonl(output / "external_cash.jsonl", cash),
        "external_settlements_path": _write_jsonl(output / "external_settlements.jsonl", settlements),
        "external_corporate_actions_path": _write_jsonl(output / "external_corporate_actions.jsonl", corporate_actions),
    }
    manifest = {
        "synthetic": True,
        "created_at": _utc_now(),
        "account_id": account_id,
        "broker_name": broker_name,
        "trade_date": trade_date,
        "as_of_date": as_of_date or trade_date,
        "broker_batch_id": broker_batch_id,
        "record_counts": {
            "orders": len(orders),
            "fills": len(fills),
            "positions": len(positions),
            "cash": len(cash),
            "settlements": len(settlements),
            "corporate_actions": len(corporate_actions),
        },
        "injections": {
            "cash_diff": float(inject_cash_diff),
            "position_diff": list(inject_position_diff or []),
            "drop_fill": list(drop_fill or []),
            "duplicate_fill": list(duplicate_fill or []),
            "fee_diff": list(inject_fee_diff or []),
        },
        "paths": {key: str(value) for key, value in paths.items()},
    }
    (output / "synthetic_statement_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def _fill_record(fill: dict[str, Any], account_id: str, broker_name: str, trade_date: str, as_of_date: str) -> dict[str, Any]:
    total_fee = float(fill.get("cost", 0.0) or 0.0)
    if not total_fee:
        total_fee = sum(float(fill.get(field, 0.0) or 0.0) for field in ["commission", "stamp_duty", "transfer_fee", "slippage", "market_impact", "other_fee"])
    return {
        "account_id": account_id,
        "broker_name": broker_name,
        "trade_date": str(fill.get("trade_date") or trade_date),
        "as_of_date": as_of_date or trade_date,
        "external_fill_id": str(fill.get("broker_fill_id") or ""),
        "broker_fill_id": str(fill.get("broker_fill_id") or ""),
        "broker_order_id": str(fill.get("broker_order_id") or ""),
        "client_order_id": str(fill.get("client_order_id") or ""),
        "ts_code": str(fill.get("ts_code") or ""),
        "side": str(fill.get("side") or ""),
        "price": float(fill.get("price", 0.0) or 0.0),
        "shares": int(fill.get("shares", 0) or 0),
        "value": float(fill.get("value", 0.0) or 0.0),
        "commission": float(fill.get("commission", 0.0) or 0.0),
        "stamp_duty": float(fill.get("stamp_duty", 0.0) or 0.0),
        "transfer_fee": float(fill.get("transfer_fee", 0.0) or 0.0),
        "slippage": float(fill.get("slippage", 0.0) or 0.0),
        "market_impact": float(fill.get("market_impact", 0.0) or 0.0),
        "other_fee": float(fill.get("other_fee", 0.0) or 0.0),
        "total_fee": float(total_fee),
        "status": str(fill.get("status") or ""),
        "reason": str(fill.get("reason") or ""),
    }


def _order_record(order: dict[str, Any], account_id: str, broker_name: str, trade_date: str, as_of_date: str) -> dict[str, Any]:
    request = order.get("request") if isinstance(order.get("request"), dict) else {}
    return {
        "account_id": account_id,
        "broker_name": broker_name,
        "trade_date": str(request.get("trade_date") or trade_date),
        "as_of_date": as_of_date or trade_date,
        "external_order_id": str(order.get("broker_order_id") or ""),
        "broker_order_id": str(order.get("broker_order_id") or ""),
        "client_order_id": str(order.get("client_order_id") or ""),
        "ts_code": str(request.get("ts_code") or ""),
        "side": str(request.get("side") or ""),
        "price": float(request.get("price", 0.0) or 0.0),
        "shares": int(order.get("requested_shares", 0) or 0),
        "value": float(order.get("requested_value", 0.0) or 0.0),
        "status": str(order.get("status") or ""),
        "reason": str(order.get("reject_reason") or order.get("cancel_reason") or ""),
    }


def _position_record(position: dict[str, Any], account_id: str, broker_name: str, trade_date: str, as_of_date: str) -> dict[str, Any]:
    return {
        "account_id": account_id,
        "broker_name": broker_name,
        "trade_date": trade_date,
        "as_of_date": as_of_date or trade_date,
        "ts_code": str(position.get("ts_code") or ""),
        "position_shares": int(position.get("shares", 0) or 0),
        "available_shares": int(position.get("available_shares", position.get("shares", 0)) or 0),
        "cost_basis": float(position.get("avg_cost", 0.0) or 0.0),
        "market_value": float(position.get("market_value", 0.0) or 0.0),
        "realized_pnl": float(position.get("realized_pnl", 0.0) or 0.0),
        "unrealized_pnl": float(position.get("unrealized_pnl", 0.0) or 0.0),
    }


def _settlement_record(event: dict[str, Any], account_id: str, broker_name: str, trade_date: str, as_of_date: str) -> dict[str, Any]:
    return {
        "account_id": account_id,
        "broker_name": broker_name,
        "trade_date": str(event.get("trade_date") or trade_date),
        "as_of_date": as_of_date or trade_date,
        "external_settlement_id": str(event.get("settlement_event_id") or ""),
        "source_id": str(event.get("source_id") or ""),
        "ts_code": str(event.get("ts_code") or ""),
        "event_type": str(event.get("event_type") or ""),
        "settlement_date": str(event.get("settle_date") or ""),
        "available_date": str(event.get("available_date") or ""),
        "cash_amount": float(event.get("cash_amount", 0.0) or 0.0),
        "shares": int(event.get("shares", 0) or 0),
        "status": str(event.get("status") or ""),
        "reason": str(event.get("reason") or ""),
    }


def _corporate_action_record(entry: Any, account_id: str, broker_name: str, trade_date: str, as_of_date: str) -> dict[str, Any]:
    payload = entry.to_dict() if hasattr(entry, "to_dict") else dict(entry)
    return {
        "account_id": account_id,
        "broker_name": broker_name,
        "trade_date": str(payload.get("apply_date") or trade_date),
        "as_of_date": as_of_date or trade_date,
        "external_action_id": str(payload.get("action_id") or ""),
        "action_id": str(payload.get("action_id") or ""),
        "ts_code": str(payload.get("ts_code") or ""),
        "event_type": str(payload.get("event_type") or ""),
        "cash_amount": float(payload.get("cash_amount", 0.0) or 0.0),
        "shares": int(payload.get("shares_after", 0) or 0) - int(payload.get("shares_before", 0) or 0),
        "status": str(payload.get("status") or ""),
        "reason": str(payload.get("reason") or ""),
    }


def _parse_id_amounts(items: list[str]) -> dict[str, float]:
    result: dict[str, float] = {}
    for item in items:
        if ":" not in str(item):
            continue
        key, value = str(item).split(":", 1)
        try:
            result[key] = float(value)
        except ValueError:
            continue
    return result


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    return str(path)


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

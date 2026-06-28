"""Load external and internal artifacts for EOD reconciliation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from broker_adapter import LocalBrokerStore
from broker_statement import read_normalized_statement
from paper_account import LocalPaperAccount


def load_reconciliation_inputs(
    statement_dir: str | Path,
    broker_store_dir: str | Path | None = None,
    broker_batch_id: str | None = None,
    paper_account_dir: str | Path | None = None,
    settlement_dir: str | Path | None = None,
) -> dict[str, Any]:
    statement_root = Path(statement_dir)
    broker_store = LocalBrokerStore(broker_store_dir or statement_root / "missing_broker")
    account = LocalPaperAccount(paper_account_dir or statement_root / "missing_account")
    settlement_root = Path(settlement_dir or paper_account_dir or statement_root)
    settlement_events = _merge_rows(
        _read_jsonl(settlement_root / "settlement_events.jsonl"),
        _read_jsonl(account.settlement_events_path),
        key_fields=("settlement_event_id", "source_id"),
    )
    account_nav = _read_jsonl(settlement_root / "account_nav.jsonl") + _read_jsonl(account.account_nav_path)
    return {
        "statement_dir": str(statement_root),
        "statement_manifest": _read_json(statement_root / "broker_statement_manifest.json"),
        "statement_validation": _read_json(statement_root / "broker_statement_validation_report.json"),
        "external": read_normalized_statement(statement_root),
        "broker_orders": [record.to_dict() for record in broker_store.load_orders(batch_id=broker_batch_id)],
        "broker_fills": [record.to_dict() for record in broker_store.load_fills(batch_id=broker_batch_id)],
        "broker_events": [record.to_dict() for record in broker_store.load_events(batch_id=broker_batch_id)],
        "broker_reconciliation": _read_json(Path(broker_store_dir or "") / "broker_reconciliation.json") if broker_store_dir else {},
        "account_state": account.load_state().to_dict(),
        "trade_ledger": _read_jsonl(account.trade_ledger_path),
        "cash_ledger": _read_jsonl(account.cash_ledger_path),
        "position_lots": _merge_rows(
            _read_jsonl(settlement_root / "position_lots.jsonl"),
            _read_jsonl(account.position_lots_path),
            key_fields=("lot_id", "source_id"),
        ),
        "settlement_events": settlement_events,
        "cash_buckets": _read_jsonl(settlement_root / "cash_buckets.jsonl") or _read_jsonl(account.cash_buckets_path),
        "position_availability": _read_jsonl(settlement_root / "position_availability.jsonl")
        or _read_jsonl(account.position_availability_path),
        "realized_pnl": _read_jsonl(settlement_root / "realized_pnl.jsonl") or _read_jsonl(account.realized_pnl_path),
        "account_nav": account_nav,
        "corporate_action_ledger": _read_jsonl(account.corporate_action_ledger_path),
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _merge_rows(*groups: list[dict[str, Any]], key_fields: tuple[str, ...]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    fallback = 0
    for rows in groups:
        for row in rows:
            key = ""
            for field in key_fields:
                value = str(row.get(field) or "")
                if value:
                    key = f"{field}:{value}"
                    break
            if not key:
                fallback += 1
                key = f"row:{fallback}"
            merged[key] = row
    return list(merged.values())

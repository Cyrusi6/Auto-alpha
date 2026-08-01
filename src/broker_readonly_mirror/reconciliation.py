"""Read-only mirror reconciliation helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_jsonl_artifact

from .models import BrokerReadonlyMirrorIssue, BrokerReadonlySnapshot
from .normalizer import to_statement_artifacts


def mirror_to_statement_artifacts(snapshot: BrokerReadonlySnapshot, output_dir: str | Path) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    normalized = {
        "cash": _cash_obj(snapshot),
        "positions": [dict(item) for item in snapshot.positions],
        "orders": [dict(item) for item in snapshot.orders],
        "fills": [dict(item) for item in snapshot.fills],
        "statements": [dict(item) for item in snapshot.statements],
    }
    paths: dict[str, str] = {}
    mapping = {
        "cash": [normalized["cash"]] if normalized["cash"] else [],
        "positions": normalized["positions"],
        "orders": normalized["orders"],
        "fills": normalized["fills"],
        "trades": normalized["fills"],
        "settlements": normalized["statements"],
    }
    for dataset, rows in mapping.items():
        path = write_jsonl_artifact(root / f"normalized_external_{dataset}.jsonl", rows, f"normalized_external_{dataset}", "broker_readonly_mirror")
        paths[f"normalized_external_{dataset}_path"] = str(path)
    return paths


def reconcile_readonly_mirror(
    snapshot: BrokerReadonlySnapshot,
    *,
    paper_account_dir: str | Path | None = None,
    broker_store_dir: str | Path | None = None,
    settlement_dir: str | Path | None = None,
) -> dict[str, Any]:
    del settlement_dir
    issues: list[BrokerReadonlyMirrorIssue] = []
    account_state = _read_json(Path(paper_account_dir or "") / "account_state.json") if paper_account_dir else {}
    if account_state:
        internal_cash = float(account_state.get("cash", 0.0) or 0.0)
        external_cash = float(snapshot.cash.get("cash_balance", 0.0) or 0.0)
        if abs(internal_cash - external_cash) > 0.01:
            issues.append(
                BrokerReadonlyMirrorIssue(
                    "warning",
                    "cash_balance_difference",
                    "readonly broker cash differs from local paper account",
                    {"external_cash": external_cash, "internal_cash": internal_cash, "difference": external_cash - internal_cash},
                )
            )
        internal_positions = dict(account_state.get("positions") or {})
        for row in snapshot.positions:
            ts_code = str(row.get("ts_code") or "")
            external_shares = int(row.get("position_shares", 0) or 0)
            internal_shares = int((internal_positions.get(ts_code) or {}).get("shares", 0) or 0)
            if external_shares != internal_shares:
                issues.append(
                    BrokerReadonlyMirrorIssue(
                        "warning",
                        "position_share_difference",
                        "readonly broker position differs from local paper account",
                        {"ts_code": ts_code, "external_shares": external_shares, "internal_shares": internal_shares},
                    )
                )
    broker_fills = _read_jsonl(Path(broker_store_dir or "") / "broker_fills.jsonl") if broker_store_dir else []
    if broker_fills:
        external_ids = {str(row.get("broker_fill_id") or row.get("external_fill_id") or "") for row in snapshot.fills}
        for row in broker_fills:
            fill_id = str(row.get("broker_fill_id") or "")
            if fill_id and fill_id not in external_ids:
                issues.append(BrokerReadonlyMirrorIssue("warning", "missing_readonly_fill", "local broker fill missing from readonly mirror", {"broker_fill_id": fill_id}))
    status = "warning" if issues else "ok"
    return {
        "snapshot_id": snapshot.snapshot_id,
        "account_id": snapshot.account_id,
        "broker_name": snapshot.broker_name,
        "trade_date": snapshot.trade_date,
        "as_of_date": snapshot.as_of_date,
        "status": status,
        "break_count": len(issues),
        "summary": {
            "readonly_cash_count": 1 if snapshot.cash else 0,
            "readonly_position_count": len(snapshot.positions),
            "readonly_order_count": len(snapshot.orders),
            "readonly_fill_count": len(snapshot.fills),
            "readonly_statement_count": len(snapshot.statements),
            "readonly_mirror_break_count": len(issues),
        },
        "issues": [issue.to_dict() for issue in issues],
        "real_submit_supported": False,
    }


def _cash_obj(snapshot: BrokerReadonlySnapshot) -> dict[str, Any]:
    return dict(snapshot.cash) if snapshot.cash else {}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        return {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


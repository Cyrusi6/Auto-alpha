"""End-of-day reconciliation engine."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

from .loader import load_reconciliation_inputs
from .matcher import corporate_action_key, fill_key, index_by, position_key, settlement_key
from .models import (
    EodReconciliationReport,
    ExternalAccountMirror,
    ReconciliationBreak,
    ReconciliationBreakType,
    ReconciliationMaterialityConfig,
    ReconciliationSeverity,
)


def run_eod_reconciliation(
    statement_dir: str | Path,
    output_dir: str | Path,
    broker_store_dir: str | Path | None = None,
    broker_batch_id: str | None = None,
    paper_account_dir: str | Path | None = None,
    settlement_dir: str | Path | None = None,
    corporate_action_dir: str | Path | None = None,
    account_id: str = "paper_ashare",
    trade_date: str = "",
    as_of_date: str = "",
    materiality: ReconciliationMaterialityConfig | None = None,
    strict: bool = False,
    create_adjustment_proposals: bool = False,
) -> tuple[EodReconciliationReport, ExternalAccountMirror, dict[str, Path]]:
    from .adjustments import create_adjustment_proposals as build_adjustment_proposals
    from .report import write_eod_reconciliation_report

    config = materiality or ReconciliationMaterialityConfig()
    if strict:
        config = ReconciliationMaterialityConfig(**{**config.to_dict(), "blocker_on_unmatched_fill": True})
    inputs = load_reconciliation_inputs(statement_dir, broker_store_dir, broker_batch_id, paper_account_dir, settlement_dir)
    external = inputs["external"]
    manifest = inputs["statement_manifest"] or {}
    validation = inputs["statement_validation"] or {}
    statement_id = str(manifest.get("statement_id") or "statement")
    account_id = str(account_id or manifest.get("account_id") or "")
    trade_date = str(trade_date or manifest.get("trade_date") or "")
    as_of_date = str(as_of_date or manifest.get("as_of_date") or trade_date)
    mirror = _build_mirror(statement_id, account_id, trade_date, as_of_date, manifest, external)
    breaks: list[ReconciliationBreak] = []
    for issue in validation.get("issues", []) if isinstance(validation.get("issues"), list) else []:
        if issue.get("severity") in {"error", "blocker"}:
            breaks.append(
                _break(
                    ReconciliationBreakType.schema_parse_error,
                    ReconciliationSeverity.error,
                    str(issue.get("message") or "statement parse issue"),
                    account_id,
                    metadata={"code": issue.get("code")},
                )
            )
    _check_cash(breaks, mirror.cash, inputs["account_state"], account_id, config, strict)
    _check_positions(breaks, mirror.positions, inputs["account_state"], account_id, config, strict)
    _check_fills(breaks, mirror.fills, inputs["broker_fills"], account_id, config, strict)
    _check_trade_ledger(breaks, inputs["broker_fills"], inputs["trade_ledger"], account_id, config, strict)
    _check_fee_tax(breaks, mirror.fills, inputs["broker_fills"], account_id, config, strict)
    _check_settlements(breaks, mirror.settlements, inputs["settlement_events"], account_id, config, strict)
    _check_corporate_actions(breaks, mirror.corporate_actions, inputs["corporate_action_ledger"], account_id, config, strict)
    _check_nav(breaks, mirror, inputs, account_id, config, strict)
    _check_staleness(breaks, as_of_date, manifest, account_id, config)
    summary = _summary(breaks, mirror, inputs, config)
    report = EodReconciliationReport(
        statement_id=statement_id,
        account_id=account_id,
        trade_date=trade_date,
        as_of_date=as_of_date,
        status=str(summary["status"]),
        summary=summary,
        breaks=breaks,
        materiality=config.to_dict(),
    )
    adjustment_batch = build_adjustment_proposals(breaks, config, account_id=account_id, trade_date=trade_date, as_of_date=as_of_date) if create_adjustment_proposals else None
    paths = write_eod_reconciliation_report(report, mirror, output_dir, adjustment_batch=adjustment_batch)
    report = EodReconciliationReport(
        statement_id=report.statement_id,
        account_id=report.account_id,
        trade_date=report.trade_date,
        as_of_date=report.as_of_date,
        status=report.status,
        summary={**report.summary, "adjustment_proposal_count": len(adjustment_batch.proposals) if adjustment_batch else 0},
        breaks=report.breaks,
        materiality=report.materiality,
        paths={key: str(value) for key, value in paths.items()},
    )
    paths = write_eod_reconciliation_report(report, mirror, output_dir, adjustment_batch=adjustment_batch)
    return report, mirror, paths


def _build_mirror(statement_id: str, account_id: str, trade_date: str, as_of_date: str, manifest: dict[str, Any], external: dict[str, list[dict[str, Any]]]) -> ExternalAccountMirror:
    cash_rows = external.get("cash", [])
    cash = cash_rows[-1] if cash_rows else {}
    return ExternalAccountMirror(
        statement_id=statement_id,
        account_id=account_id,
        broker_name=str(manifest.get("broker_name") or cash.get("broker_name") or ""),
        trade_date=trade_date,
        as_of_date=as_of_date,
        synthetic=bool((manifest.get("metadata") or {}).get("synthetic")),
        cash=dict(cash),
        positions=list(external.get("positions", [])),
        fills=list(external.get("fills", [])),
        settlements=list(external.get("settlements", [])),
        corporate_actions=list(external.get("corporate_actions", [])),
    )


def _check_cash(breaks: list[ReconciliationBreak], external_cash: dict[str, Any], account_state: dict[str, Any], account_id: str, config: ReconciliationMaterialityConfig, strict: bool) -> None:
    if not external_cash:
        if config.blocker_on_missing_cash_statement:
            breaks.append(_break(ReconciliationBreakType.cash_balance_mismatch, ReconciliationSeverity.blocker, "external cash statement is missing", account_id))
        return
    internal_cash = float(account_state.get("cash", 0.0) or 0.0)
    external = float(external_cash.get("cash_balance", 0.0) or 0.0)
    diff = external - internal_cash
    if abs(diff) > config.cash_abs_tolerance:
        breaks.append(
            _break(
                ReconciliationBreakType.cash_balance_mismatch,
                _severity(strict, True),
                "external cash balance differs from internal paper account",
                account_id,
                external_value=external,
                internal_value=internal_cash,
                difference=diff,
                material=True,
            )
        )


def _check_positions(breaks: list[ReconciliationBreak], external_positions: list[dict[str, Any]], account_state: dict[str, Any], account_id: str, config: ReconciliationMaterialityConfig, strict: bool) -> None:
    if not external_positions and config.blocker_on_missing_position_statement:
        breaks.append(_break(ReconciliationBreakType.position_share_mismatch, ReconciliationSeverity.blocker, "external position statement is missing", account_id))
        return
    internal_positions = {
        ts_code: dict(position) for ts_code, position in dict(account_state.get("positions") or {}).items()
    }
    external_index = index_by(external_positions, position_key)
    for ts_code in sorted(set(external_index) | set(internal_positions)):
        external = int(external_index.get(ts_code, {}).get("position_shares", 0) or 0)
        internal = int(internal_positions.get(ts_code, {}).get("shares", 0) or 0)
        diff = external - internal
        if abs(diff) > config.position_share_tolerance:
            breaks.append(
                _break(
                    ReconciliationBreakType.position_share_mismatch,
                    _severity(strict, True),
                    f"position shares differ for {ts_code}",
                    account_id,
                    ts_code=ts_code,
                    external_value=float(external),
                    internal_value=float(internal),
                    difference=float(diff),
                    material=True,
                )
            )


def _check_fills(breaks: list[ReconciliationBreak], external_fills: list[dict[str, Any]], broker_fills: list[dict[str, Any]], account_id: str, config: ReconciliationMaterialityConfig, strict: bool) -> None:
    external_index = index_by(external_fills, fill_key)
    internal_index = index_by(broker_fills, fill_key)
    for key in sorted(set(internal_index) - set(external_index)):
        breaks.append(_break(ReconciliationBreakType.missing_external_fill, _unmatched_severity(config, strict), "internal broker fill missing in external statement", account_id, internal_id=key))
    for key in sorted(set(external_index) - set(internal_index)):
        breaks.append(_break(ReconciliationBreakType.orphan_external_fill, _unmatched_severity(config, strict), "external fill has no matching internal broker fill", account_id, external_id=key))
    for key in sorted(set(external_index) & set(internal_index)):
        external = external_index[key]
        internal = internal_index[key]
        shares_diff = int(external.get("shares", 0) or 0) - int(internal.get("shares", 0) or 0)
        if shares_diff:
            breaks.append(_break(ReconciliationBreakType.fill_quantity_mismatch, _severity(strict, True), "fill quantity differs", account_id, external_id=key, internal_id=key, difference=float(shares_diff), material=True))
        value_diff = float(external.get("value", 0.0) or 0.0) - float(internal.get("value", 0.0) or 0.0)
        if abs(value_diff) > config.fill_value_abs_tolerance:
            breaks.append(_break(ReconciliationBreakType.fill_value_mismatch, _severity(strict, True), "fill value differs", account_id, external_id=key, internal_id=key, difference=value_diff, material=True))


def _check_trade_ledger(breaks: list[ReconciliationBreak], broker_fills: list[dict[str, Any]], trade_ledger: list[dict[str, Any]], account_id: str, config: ReconciliationMaterialityConfig, strict: bool) -> None:
    broker_index = index_by([row for row in broker_fills if row.get("status") in {"FILLED", "PARTIAL"}], fill_key)
    ledger_index = index_by([row for row in trade_ledger if row.get("status") in {"FILLED", "PARTIAL"}], fill_key)
    for key in sorted(set(broker_index) - set(ledger_index)):
        breaks.append(_break(ReconciliationBreakType.missing_internal_order, _unmatched_severity(config, strict), "broker fill is missing in paper account trade ledger", account_id, internal_id=key))


def _check_fee_tax(breaks: list[ReconciliationBreak], external_fills: list[dict[str, Any]], broker_fills: list[dict[str, Any]], account_id: str, config: ReconciliationMaterialityConfig, strict: bool) -> None:
    external_index = index_by(external_fills, fill_key)
    internal_index = index_by(broker_fills, fill_key)
    for key in sorted(set(external_index) & set(internal_index)):
        external_fee = float(external_index[key].get("total_fee", 0.0) or 0.0)
        internal = internal_index[key]
        internal_fee = float(internal.get("cost", 0.0) or 0.0)
        if abs(external_fee - internal_fee) > config.fee_abs_tolerance:
            breaks.append(_break(ReconciliationBreakType.fee_tax_mismatch, _severity(strict, True), "fee/tax differs", account_id, external_id=key, internal_id=key, difference=external_fee - internal_fee, material=True))


def _check_settlements(breaks: list[ReconciliationBreak], external_settlements: list[dict[str, Any]], internal_events: list[dict[str, Any]], account_id: str, config: ReconciliationMaterialityConfig, strict: bool) -> None:
    if not external_settlements:
        return
    external_index = index_by(external_settlements, settlement_key)
    internal_index = index_by(internal_events, settlement_key)
    missing = set(external_index) - set(internal_index)
    for key in sorted(missing):
        breaks.append(_break(ReconciliationBreakType.settlement_event_mismatch, ReconciliationSeverity.warning, "external settlement item has no matching internal settlement event", account_id, external_id=key))


def _check_corporate_actions(breaks: list[ReconciliationBreak], external_actions: list[dict[str, Any]], internal_actions: list[dict[str, Any]], account_id: str, config: ReconciliationMaterialityConfig, strict: bool) -> None:
    if not external_actions:
        return
    external_index = index_by(external_actions, corporate_action_key)
    internal_index = index_by(internal_actions, corporate_action_key)
    for key in sorted(set(external_index) - set(internal_index)):
        breaks.append(_break(ReconciliationBreakType.corporate_action_mismatch, _severity(strict, True), "external corporate action has no matching internal ledger entry", account_id, external_id=key, material=True))


def _check_nav(breaks: list[ReconciliationBreak], mirror: ExternalAccountMirror, inputs: dict[str, Any], account_id: str, config: ReconciliationMaterialityConfig, strict: bool) -> None:
    account_state = inputs["account_state"]
    internal_cash = float(account_state.get("cash", 0.0) or 0.0)
    internal_nav = _latest_nav(inputs.get("account_nav", []), internal_cash, dict(account_state.get("positions") or {}))
    external_cash = float((mirror.cash or {}).get("cash_balance", 0.0) or 0.0)
    external_equity = external_cash + sum(float(row.get("market_value", 0.0) or 0.0) for row in mirror.positions)
    diff = external_equity - internal_nav
    if abs(diff) > config.nav_abs_tolerance:
        breaks.append(_break(ReconciliationBreakType.nav_mismatch, _severity(strict, True), "external equity differs from internal NAV", account_id, external_value=external_equity, internal_value=internal_nav, difference=diff, material=True))


def _check_staleness(breaks: list[ReconciliationBreak], as_of_date: str, manifest: dict[str, Any], account_id: str, config: ReconciliationMaterialityConfig) -> None:
    manifest_date = str(manifest.get("as_of_date") or "")
    if as_of_date and manifest_date and manifest_date < as_of_date:
        breaks.append(_break(ReconciliationBreakType.stale_statement, ReconciliationSeverity.warning, "statement as_of_date is older than requested as_of_date", account_id, external_id=manifest_date))


def _summary(breaks: list[ReconciliationBreak], mirror: ExternalAccountMirror, inputs: dict[str, Any], config: ReconciliationMaterialityConfig) -> dict[str, Any]:
    account_state = inputs["account_state"]
    external_cash = float((mirror.cash or {}).get("cash_balance", 0.0) or 0.0)
    internal_cash = float(account_state.get("cash", 0.0) or 0.0)
    cash_difference = external_cash - internal_cash
    positions_internal = dict(account_state.get("positions") or {})
    position_diff = sum(
        float(item.difference)
        for item in breaks
        if item.break_type == ReconciliationBreakType.position_share_mismatch
    )
    fee_diff = sum(float(item.difference) for item in breaks if item.break_type == ReconciliationBreakType.fee_tax_mismatch)
    blocker_count = sum(1 for item in breaks if item.severity == ReconciliationSeverity.blocker)
    error_count = sum(1 for item in breaks if item.severity == ReconciliationSeverity.error)
    warning_count = sum(1 for item in breaks if item.severity == ReconciliationSeverity.warning)
    material = sum(1 for item in breaks if item.material)
    status = "blocker" if blocker_count else ("error" if error_count else ("warning" if warning_count else "ok"))
    internal_nav = _latest_nav(inputs.get("account_nav", []), internal_cash, positions_internal)
    external_equity = external_cash + sum(float(row.get("market_value", 0.0) or 0.0) for row in mirror.positions)
    return {
        "status": status,
        "break_count": len(breaks),
        "error_count": error_count,
        "warning_count": warning_count,
        "blocker_count": blocker_count,
        "unresolved_break_count": sum(1 for item in breaks if not item.resolved),
        "material_break_count": material,
        "external_cash": external_cash,
        "internal_cash": internal_cash,
        "cash_difference": cash_difference,
        "external_equity": external_equity,
        "internal_equity": internal_nav,
        "nav_difference": external_equity - internal_nav,
        "external_position_count": len(mirror.positions),
        "internal_position_count": len([p for p in positions_internal.values() if int(p.get("shares", 0) or 0) != 0]),
        "position_share_difference": position_diff,
        "unmatched_fill_count": sum(1 for item in breaks if item.break_type in {ReconciliationBreakType.missing_external_fill, ReconciliationBreakType.orphan_external_fill}),
        "unmatched_external_fill_count": sum(1 for item in breaks if item.break_type == ReconciliationBreakType.orphan_external_fill),
        "unmatched_internal_fill_count": sum(1 for item in breaks if item.break_type == ReconciliationBreakType.missing_external_fill),
        "fee_tax_difference": fee_diff,
        "stale_statement": any(item.break_type == ReconciliationBreakType.stale_statement for item in breaks),
        "synthetic_statement": mirror.synthetic,
    }


def _latest_nav(rows: list[dict[str, Any]], cash: float, positions: dict[str, dict[str, Any]]) -> float:
    if rows:
        return float(rows[-1].get("equity", 0.0) or 0.0)
    return cash + sum(float(item.get("market_value", 0.0) or 0.0) for item in positions.values())


def _break(
    break_type: str,
    severity: str,
    message: str,
    account_id: str,
    *,
    ts_code: str | None = None,
    external_id: str | None = None,
    internal_id: str | None = None,
    external_value: float | None = None,
    internal_value: float | None = None,
    difference: float = 0.0,
    material: bool = False,
    metadata: dict[str, Any] | None = None,
) -> ReconciliationBreak:
    raw = "|".join([break_type, account_id, ts_code or "", external_id or "", internal_id or "", str(difference)])
    return ReconciliationBreak(
        break_id="brk_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16],
        break_type=break_type,
        severity=severity,
        message=message,
        account_id=account_id,
        ts_code=ts_code,
        external_id=external_id,
        internal_id=internal_id,
        external_value=external_value,
        internal_value=internal_value,
        difference=float(difference),
        material=bool(material),
        metadata=metadata or {},
    )


def _severity(strict: bool, material: bool) -> str:
    return ReconciliationSeverity.blocker if strict and material else ReconciliationSeverity.error


def _unmatched_severity(config: ReconciliationMaterialityConfig, strict: bool) -> str:
    return ReconciliationSeverity.blocker if strict or config.blocker_on_unmatched_fill else ReconciliationSeverity.warning

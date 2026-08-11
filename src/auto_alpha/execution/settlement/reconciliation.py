"""End-of-day reconciliation loading, matching, adjustments, reports, and command workflow."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class ReconciliationSeverity:
    info = "info"
    warning = "warning"
    error = "error"
    blocker = "blocker"


class ReconciliationBreakType:
    missing_external_fill = "missing_external_fill"
    orphan_external_fill = "orphan_external_fill"
    fill_quantity_mismatch = "fill_quantity_mismatch"
    fill_price_mismatch = "fill_price_mismatch"
    fill_value_mismatch = "fill_value_mismatch"
    fee_tax_mismatch = "fee_tax_mismatch"
    missing_internal_order = "missing_internal_order"
    order_status_mismatch = "order_status_mismatch"
    cash_balance_mismatch = "cash_balance_mismatch"
    available_cash_mismatch = "available_cash_mismatch"
    position_share_mismatch = "position_share_mismatch"
    available_share_mismatch = "available_share_mismatch"
    lot_cost_mismatch = "lot_cost_mismatch"
    settlement_event_mismatch = "settlement_event_mismatch"
    nav_mismatch = "nav_mismatch"
    corporate_action_mismatch = "corporate_action_mismatch"
    duplicate_external_id = "duplicate_external_id"
    stale_statement = "stale_statement"
    schema_parse_error = "schema_parse_error"
    materiality_exceeded = "materiality_exceeded"


@dataclass(frozen=True)
class ReconciliationMaterialityConfig:
    cash_abs_tolerance: float = 0.01
    position_share_tolerance: int = 0
    fill_value_abs_tolerance: float = 0.01
    fee_abs_tolerance: float = 0.01
    nav_abs_tolerance: float = 0.01
    stale_statement_max_days: int = 1
    blocker_on_missing_cash_statement: bool = False
    blocker_on_missing_position_statement: bool = False
    blocker_on_unmatched_fill: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReconciliationBreak:
    break_id: str
    break_type: str
    severity: str
    message: str
    account_id: str
    ts_code: str | None = None
    external_id: str | None = None
    internal_id: str | None = None
    external_value: float | None = None
    internal_value: float | None = None
    difference: float = 0.0
    material: bool = False
    resolved: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExternalAccountMirror:
    statement_id: str
    account_id: str
    broker_name: str
    trade_date: str
    as_of_date: str
    synthetic: bool
    cash: dict[str, Any] = field(default_factory=dict)
    positions: list[dict[str, Any]] = field(default_factory=list)
    fills: list[dict[str, Any]] = field(default_factory=list)
    settlements: list[dict[str, Any]] = field(default_factory=list)
    corporate_actions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EodReconciliationReport:
    statement_id: str
    account_id: str
    trade_date: str
    as_of_date: str
    status: str
    summary: dict[str, Any]
    breaks: list[ReconciliationBreak] = field(default_factory=list)
    materiality: dict[str, Any] = field(default_factory=dict)
    paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "statement_id": self.statement_id,
            "account_id": self.account_id,
            "trade_date": self.trade_date,
            "as_of_date": self.as_of_date,
            "status": self.status,
            "summary": dict(self.summary),
            "breaks": [item.to_dict() for item in self.breaks],
            "materiality": dict(self.materiality),
            "paths": dict(self.paths),
        }


@dataclass(frozen=True)
class AdjustmentProposal:
    adjustment_id: str
    break_id: str
    account_id: str
    adjustment_type: str
    ts_code: str | None = None
    cash_amount: float = 0.0
    share_delta: int = 0
    cost_basis_delta: float = 0.0
    reason: str = ""
    severity: str = "warning"
    requires_approval: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AdjustmentProposalBatch:
    adjustment_batch_id: str
    account_id: str
    trade_date: str
    as_of_date: str
    proposals: list[AdjustmentProposal]
    status: str = "pending_approval"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "adjustment_batch_id": self.adjustment_batch_id,
            "account_id": self.account_id,
            "trade_date": self.trade_date,
            "as_of_date": self.as_of_date,
            "proposals": [item.to_dict() for item in self.proposals],
            "status": self.status,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class AdjustmentLedgerEntry:
    adjustment_id: str
    approval_id: str
    account_id: str
    trade_date: str
    adjustment_type: str
    ts_code: str | None = None
    cash_amount: float = 0.0
    share_delta: int = 0
    cost_basis_delta: float = 0.0
    reason: str = ""
    applied_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AdjustmentApplicationResult:
    approval_id: str
    account_id: str
    trade_date: str
    applied_count: int
    skipped_duplicate_count: int
    ledger_entries: list[AdjustmentLedgerEntry] = field(default_factory=list)
    paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "account_id": self.account_id,
            "trade_date": self.trade_date,
            "applied_count": int(self.applied_count),
            "skipped_duplicate_count": int(self.skipped_duplicate_count),
            "ledger_entries": [entry.to_dict() for entry in self.ledger_entries],
            "paths": dict(self.paths),
        }

import json
from pathlib import Path
from typing import Any

from auto_alpha.execution.broker.adapter import LocalBrokerStore
from auto_alpha.execution.broker.statements import read_normalized_statement
from auto_alpha.execution.trading.paper import LocalPaperAccount


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
        _reconciliation_loader_read_jsonl(settlement_root / "settlement_events.jsonl"),
        _reconciliation_loader_read_jsonl(account.settlement_events_path),
        key_fields=("settlement_event_id", "source_id"),
    )
    account_nav = _reconciliation_loader_read_jsonl(settlement_root / "account_nav.jsonl") + _reconciliation_loader_read_jsonl(account.account_nav_path)
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
        "trade_ledger": _reconciliation_loader_read_jsonl(account.trade_ledger_path),
        "cash_ledger": _reconciliation_loader_read_jsonl(account.cash_ledger_path),
        "position_lots": _merge_rows(
            _reconciliation_loader_read_jsonl(settlement_root / "position_lots.jsonl"),
            _reconciliation_loader_read_jsonl(account.position_lots_path),
            key_fields=("lot_id", "source_id"),
        ),
        "settlement_events": settlement_events,
        "cash_buckets": _reconciliation_loader_read_jsonl(settlement_root / "cash_buckets.jsonl") or _reconciliation_loader_read_jsonl(account.cash_buckets_path),
        "position_availability": _reconciliation_loader_read_jsonl(settlement_root / "position_availability.jsonl")
        or _reconciliation_loader_read_jsonl(account.position_availability_path),
        "realized_pnl": _reconciliation_loader_read_jsonl(settlement_root / "realized_pnl.jsonl") or _reconciliation_loader_read_jsonl(account.realized_pnl_path),
        "account_nav": account_nav,
        "corporate_action_ledger": _reconciliation_loader_read_jsonl(account.corporate_action_ledger_path),
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _reconciliation_loader_read_jsonl(path: Path) -> list[dict[str, Any]]:
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

from typing import Any


def fill_key(record: dict[str, Any]) -> str:
    for field in ("broker_fill_id", "external_fill_id"):
        value = str(record.get(field) or "")
        if value:
            return f"{field}:{value}"
    broker_order_id = str(record.get("broker_order_id") or "")
    if broker_order_id:
        return "|".join(
            [
                "order",
                broker_order_id,
                str(record.get("ts_code") or ""),
                str(record.get("side") or ""),
                str(record.get("shares") or ""),
                str(record.get("price") or ""),
                str(record.get("trade_date") or ""),
            ]
        )
    return "|".join(
        [
            "client",
            str(record.get("client_order_id") or ""),
            str(record.get("ts_code") or ""),
            str(record.get("side") or ""),
            str(record.get("shares") or ""),
            str(record.get("trade_date") or ""),
        ]
    )


def order_key(record: dict[str, Any]) -> str:
    for field in ("broker_order_id", "external_order_id", "client_order_id", "child_order_id"):
        value = str(record.get(field) or "")
        if value:
            return f"{field}:{value}"
    return ""


def position_key(record: dict[str, Any]) -> str:
    return str(record.get("ts_code") or "")


def settlement_key(record: dict[str, Any]) -> str:
    for field in ("source_id", "broker_fill_id", "external_settlement_id", "settlement_event_id"):
        value = str(record.get(field) or "")
        if value:
            return f"{field}:{value}"
    return "|".join([str(record.get("ts_code") or ""), str(record.get("event_type") or ""), str(record.get("settlement_date") or "")])


def corporate_action_key(record: dict[str, Any]) -> str:
    for field in ("action_id", "external_action_id"):
        value = str(record.get(field) or "")
        if value:
            return f"{field}:{value}"
    return "|".join([str(record.get("ts_code") or ""), str(record.get("trade_date") or ""), str(record.get("event_type") or "")])


def index_by(records: list[dict[str, Any]], key_fn) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        key = key_fn(record)
        if key:
            result[key] = record
    return result

from pathlib import Path
from typing import Any

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact, write_jsonl_artifact



def write_eod_reconciliation_report(
    report: EodReconciliationReport,
    mirror: ExternalAccountMirror,
    output_dir: str | Path,
    adjustment_batch: AdjustmentProposalBatch | None = None,
) -> dict[str, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    report_path = write_json_artifact(
        root / "eod_reconciliation_report.json",
        report.to_dict(),
        artifact_type="eod_reconciliation_report",
        producer="reconciliation_center",
    )
    breaks_path = write_jsonl_artifact(
        root / "reconciliation_breaks.jsonl",
        [item.to_dict() for item in report.breaks],
        artifact_type="reconciliation_breaks",
        producer="reconciliation_center",
    )
    mirror_path = write_json_artifact(
        root / "external_account_mirror.json",
        mirror.to_dict(),
        artifact_type="external_account_mirror",
        producer="reconciliation_center",
    )
    cash_path = write_jsonl_artifact(root / "external_cash_mirror.jsonl", [mirror.cash] if mirror.cash else [], "external_cash_mirror", "reconciliation_center")
    position_path = write_jsonl_artifact(root / "external_position_mirror.jsonl", mirror.positions, "external_position_mirror", "reconciliation_center")
    fill_path = write_jsonl_artifact(root / "external_fill_mirror.jsonl", mirror.fills, "external_fill_mirror", "reconciliation_center")
    settlement_path = write_jsonl_artifact(
        root / "external_settlement_mirror.jsonl",
        mirror.settlements,
        "external_settlement_mirror",
        "reconciliation_center",
    )
    proposal_paths: dict[str, Path] = {}
    if adjustment_batch is not None:
        proposal_paths = write_adjustment_proposal_batch(adjustment_batch, root)
    md_path = root / "eod_reconciliation_report.md"
    md_path.write_text(_report_markdown(report), encoding="utf-8")
    return {
        "eod_reconciliation_report_path": report_path,
        "eod_reconciliation_report_md_path": md_path,
        "reconciliation_breaks_path": breaks_path,
        "external_account_mirror_path": mirror_path,
        "external_cash_mirror_path": cash_path,
        "external_position_mirror_path": position_path,
        "external_fill_mirror_path": fill_path,
        "external_settlement_mirror_path": settlement_path,
        **proposal_paths,
    }


def write_adjustment_proposal_batch(batch: AdjustmentProposalBatch, output_dir: str | Path) -> dict[str, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    proposals_path = write_jsonl_artifact(
        root / "adjustment_proposals.jsonl",
        [item.to_dict() for item in batch.proposals],
        artifact_type="adjustment_proposals",
        producer="reconciliation_center",
    )
    batch_path = write_json_artifact(
        root / "adjustment_proposal_batch.json",
        batch.to_dict(),
        artifact_type="adjustment_proposal_batch",
        producer="reconciliation_center",
    )
    return {"adjustment_proposals_path": proposals_path, "adjustment_proposal_batch_path": batch_path}


def write_adjustment_application_result(result: AdjustmentApplicationResult, output_dir: str | Path) -> dict[str, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    result_path = write_json_artifact(
        root / "adjustment_application_result.json",
        result.to_dict(),
        artifact_type="adjustment_application_result",
        producer="reconciliation_center",
    )
    ledger_path = write_jsonl_artifact(
        root / "adjustment_ledger.jsonl",
        [entry.to_dict() for entry in result.ledger_entries],
        artifact_type="adjustment_ledger",
        producer="reconciliation_center",
    )
    md_path = root / "adjustment_application_result.md"
    md_path.write_text(
        "\n".join(
            [
                "# Adjustment Application Result",
                "",
                f"- approval_id: `{result.approval_id}`",
                f"- applied_count: `{result.applied_count}`",
                f"- skipped_duplicate_count: `{result.skipped_duplicate_count}`",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return {"adjustment_application_result_path": result_path, "adjustment_ledger_path": ledger_path, "adjustment_application_result_md_path": md_path}


def _report_markdown(report: EodReconciliationReport) -> str:
    summary = report.summary
    lines = [
        "# EOD Reconciliation Report",
        "",
        f"- statement_id: `{report.statement_id}`",
        f"- status: `{report.status}`",
        f"- break_count: `{summary.get('break_count', 0)}`",
        f"- material_break_count: `{summary.get('material_break_count', 0)}`",
        f"- cash_difference: `{summary.get('cash_difference', 0.0)}`",
        f"- position_share_difference: `{summary.get('position_share_difference', 0)}`",
        f"- nav_difference: `{summary.get('nav_difference', 0.0)}`",
        "",
        "## Breaks",
        "",
        "| type | severity | difference | message |",
        "| --- | --- | ---: | --- |",
    ]
    for item in report.breaks:
        lines.append(f"| {item.break_type} | {item.severity} | {item.difference} | {item.message} |")
    return "\n".join(lines) + "\n"

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from auto_alpha.platform.governance.approval import ApprovalBatch, ApprovalType, LocalApprovalStore
from auto_alpha.execution.trading.paper import LocalPaperAccount



def create_adjustment_proposals(
    breaks: Sequence[ReconciliationBreak | dict[str, Any]],
    materiality: ReconciliationMaterialityConfig | dict[str, Any] | None = None,
    *,
    account_id: str,
    trade_date: str,
    as_of_date: str,
    mode: str = "manual_review",
) -> AdjustmentProposalBatch:
    config = materiality.to_dict() if hasattr(materiality, "to_dict") else dict(materiality or {})
    proposals: list[AdjustmentProposal] = []
    for item in breaks:
        payload = item.to_dict() if hasattr(item, "to_dict") else dict(item)
        break_type = str(payload.get("break_type") or "")
        proposal = _proposal_for_break(payload, break_type, account_id, mode)
        if proposal is not None:
            proposals.append(proposal)
    batch_id = _stable_id("adj_batch", account_id, trade_date, as_of_date, ",".join(proposal.adjustment_id for proposal in proposals))
    return AdjustmentProposalBatch(
        adjustment_batch_id=batch_id,
        account_id=account_id,
        trade_date=trade_date,
        as_of_date=as_of_date,
        proposals=proposals,
        metadata={"mode": mode, "materiality": config},
    )


def save_adjustment_proposals(batch: AdjustmentProposalBatch, output_dir: str | Path) -> dict[str, Path]:
    return write_adjustment_proposal_batch(batch, output_dir)


def create_adjustment_approval(
    batch: AdjustmentProposalBatch,
    approval_store_dir: str | Path,
    *,
    reconciliation_report_path: str | None = None,
    adjustment_proposals_path: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ApprovalBatch:
    cash_adjustment = sum(float(item.cash_amount) for item in batch.proposals)
    position_adjustment = sum(int(item.share_delta) for item in batch.proposals)
    approval_id = _stable_id("appr_adj", batch.account_id, batch.trade_date, batch.adjustment_batch_id)
    approval = ApprovalBatch(
        approval_id=approval_id,
        created_at=_utc_now(),
        factor_id="account_reconciliation",
        factor_type="account_adjustment",
        rebalance_date=batch.trade_date,
        portfolio_method="eod_reconciliation",
        orders=[],
        approval_type=ApprovalType.account_reconciliation_adjustment,
        reconciliation_report_path=reconciliation_report_path,
        adjustment_proposals_path=adjustment_proposals_path,
        adjustment_summary={
            "adjustment_batch_id": batch.adjustment_batch_id,
            "proposal_count": len(batch.proposals),
            "cash_adjustment": float(cash_adjustment),
            "position_share_adjustment": int(position_adjustment),
        },
        eod_reconciliation_status=str((metadata or {}).get("eod_reconciliation_status") or ""),
        unresolved_break_count=int((metadata or {}).get("unresolved_break_count", 0) or 0),
        material_break_count=int((metadata or {}).get("material_break_count", 0) or 0),
        metadata={
            **dict(metadata or {}),
            "adjustment_batch": batch.to_dict(),
            "adjustment_proposals": [item.to_dict() for item in batch.proposals],
        },
    )
    LocalApprovalStore(approval_store_dir).save_batch(approval)
    return approval


def apply_approved_adjustments(
    approval_store_dir: str | Path,
    approval_id: str,
    paper_account_dir: str | Path,
    output_dir: str | Path,
    *,
    account_id: str = "paper_ashare",
    trade_date: str = "",
) -> tuple[AdjustmentApplicationResult, dict[str, Path]]:
    approval = LocalApprovalStore(approval_store_dir).load_batch(approval_id)
    if approval.status != "approved":
        raise ValueError(f"adjustment approval must be approved before applying: {approval_id} is {approval.status}")
    if approval.approval_type != ApprovalType.account_reconciliation_adjustment:
        raise ValueError(f"approval is not an account reconciliation adjustment: {approval.approval_type}")
    proposals = _load_proposals_from_approval(approval)
    state, applied, skipped = LocalPaperAccount(paper_account_dir, account_id=account_id).apply_adjustments(
        proposals,
        approval_id=approval_id,
        trade_date=trade_date or approval.rebalance_date,
    )
    entries = [
        AdjustmentLedgerEntry(
            adjustment_id=str(entry.get("adjustment_id") or ""),
            approval_id=approval_id,
            account_id=account_id,
            trade_date=str(entry.get("trade_date") or trade_date or approval.rebalance_date),
            adjustment_type=str(entry.get("adjustment_type") or ""),
            ts_code=entry.get("ts_code"),
            cash_amount=float(entry.get("cash_amount", 0.0) or 0.0),
            share_delta=int(entry.get("share_delta", 0) or 0),
            cost_basis_delta=float(entry.get("cost_basis_delta", 0.0) or 0.0),
            reason=str(entry.get("reason") or ""),
            applied_at=entry.get("applied_at"),
            metadata=dict(entry.get("metadata") or {}),
        )
        for entry in applied
    ]
    result = AdjustmentApplicationResult(
        approval_id=approval_id,
        account_id=state.account_id,
        trade_date=trade_date or approval.rebalance_date,
        applied_count=len(entries),
        skipped_duplicate_count=skipped,
        ledger_entries=entries,
    )
    paths = write_adjustment_application_result(result, output_dir)
    result = AdjustmentApplicationResult(
        approval_id=result.approval_id,
        account_id=result.account_id,
        trade_date=result.trade_date,
        applied_count=result.applied_count,
        skipped_duplicate_count=result.skipped_duplicate_count,
        ledger_entries=result.ledger_entries,
        paths={key: str(value) for key, value in paths.items()},
    )
    paths = write_adjustment_application_result(result, output_dir)
    return result, paths


def _proposal_for_break(payload: dict[str, Any], break_type: str, account_id: str, mode: str) -> AdjustmentProposal | None:
    break_id = str(payload.get("break_id") or "")
    severity = str(payload.get("severity") or "warning")
    ts_code = payload.get("ts_code")
    difference = float(payload.get("difference", 0.0) or 0.0)
    if break_type == ReconciliationBreakType.cash_balance_mismatch:
        adjustment_type = "cash_manual_adjustment"
        cash_amount = difference
        share_delta = 0
        cost_delta = 0.0
        reason = "external cash balance differs from internal account"
    elif break_type == ReconciliationBreakType.position_share_mismatch:
        adjustment_type = "position_manual_adjustment"
        cash_amount = 0.0
        share_delta = int(round(difference))
        cost_delta = 0.0
        reason = "external position shares differ from internal account"
    elif break_type == ReconciliationBreakType.lot_cost_mismatch:
        adjustment_type = "cost_basis_adjustment"
        cash_amount = 0.0
        share_delta = 0
        cost_delta = difference
        reason = "external lot cost differs from internal lots"
    elif break_type in {ReconciliationBreakType.orphan_external_fill, ReconciliationBreakType.missing_external_fill, ReconciliationBreakType.corporate_action_mismatch}:
        adjustment_type = "manual_review"
        cash_amount = 0.0
        share_delta = 0
        cost_delta = 0.0
        reason = "record mismatch requires manual review"
    else:
        return None
    adjustment_id = _stable_id("adj", account_id, break_id, adjustment_type, str(difference))
    return AdjustmentProposal(
        adjustment_id=adjustment_id,
        break_id=break_id,
        account_id=account_id,
        adjustment_type=adjustment_type,
        ts_code=str(ts_code) if ts_code else None,
        cash_amount=float(cash_amount),
        share_delta=int(share_delta),
        cost_basis_delta=float(cost_delta),
        reason=reason,
        severity=severity,
        requires_approval=True,
        metadata={"mode": mode, "break_type": break_type},
    )


def _load_proposals_from_approval(approval: ApprovalBatch) -> list[dict[str, Any]]:
    proposals = approval.metadata.get("adjustment_proposals") if isinstance(approval.metadata, dict) else None
    if isinstance(proposals, list):
        return [dict(item) for item in proposals if isinstance(item, dict)]
    path = approval.adjustment_proposals_path
    if path and Path(path).exists():
        return _reconciliation_adjustments_read_jsonl(Path(path))
    batch = approval.metadata.get("adjustment_batch") if isinstance(approval.metadata, dict) else None
    if isinstance(batch, dict) and isinstance(batch.get("proposals"), list):
        return [dict(item) for item in batch["proposals"] if isinstance(item, dict)]
    return []


def _reconciliation_adjustments_read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any



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
    from auto_alpha.execution.settlement.reconciliation import create_adjustment_proposals as build_adjustment_proposals
    from auto_alpha.execution.settlement.reconciliation import write_eod_reconciliation_report

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

import argparse
import json
from pathlib import Path

from auto_alpha.platform.governance.approval import LocalApprovalStore



def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run local EOD reconciliation against broker statement artifacts.")
    sub = parser.add_subparsers(dest="command", required=True)
    for command_name in ("eod", "propose-adjustments", "create-approval", "apply-approved", "show-breaks", "show-summary"):
        command = sub.add_parser(command_name)
        command.add_argument("--statement-dir")
        command.add_argument("--broker-store-dir")
        command.add_argument("--broker-batch-id")
        command.add_argument("--paper-account-dir")
        command.add_argument("--settlement-dir")
        command.add_argument("--corporate-action-dir")
        command.add_argument("--approval-store-dir")
        command.add_argument("--output-dir")
        command.add_argument("--account-id", default="paper_ashare")
        command.add_argument("--trade-date", default="")
        command.add_argument("--as-of-date", default="")
        command.add_argument("--materiality-config")
        command.add_argument("--strict", action="store_true")
        command.add_argument("--fail-on-break", action="store_true")
        command.add_argument("--fail-on-error", action="store_true")
        command.add_argument("--create-adjustment-proposals", action="store_true")
        command.add_argument("--create-adjustment-approval", action="store_true")
        command.add_argument("--approval-id")
        command.add_argument("--reviewer", default="")
        command.add_argument("--comment")
        command.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "eod":
            report, _mirror, paths = _run_eod(args, create_proposals=args.create_adjustment_proposals)
            payload = report.to_dict() | {"paths": {key: str(value) for key, value in paths.items()}}
            print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
            return _exit_code(report, args)
        if args.command == "propose-adjustments":
            report, _mirror, paths = _run_eod(args, create_proposals=True)
            payload = {
                "status": report.status,
                "adjustment_proposals_path": str(paths.get("adjustment_proposals_path", "")),
                "adjustment_proposal_batch_path": str(paths.get("adjustment_proposal_batch_path", "")),
                "proposal_count": int(report.summary.get("adjustment_proposal_count", 0) or 0),
                "paths": {key: str(value) for key, value in paths.items()},
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
            return _exit_code(report, args)
        if args.command == "create-approval":
            report, _mirror, paths = _run_eod(args, create_proposals=True)
            batch_path = paths.get("adjustment_proposal_batch_path")
            if not batch_path or not Path(batch_path).exists():
                batch = create_adjustment_proposals(
                    report.breaks,
                    _materiality(args),
                    account_id=args.account_id,
                    trade_date=args.trade_date or report.trade_date,
                    as_of_date=args.as_of_date or report.as_of_date,
                )
                proposal_paths = save_adjustment_proposals(batch, args.output_dir or ".")
                paths.update(proposal_paths)
            else:
                batch_payload = json.loads(Path(batch_path).read_text(encoding="utf-8"))
                batch = create_adjustment_proposals(
                    [ReconciliationBreak(**payload) for payload in report.to_dict().get("breaks", [])],
                    _materiality(args),
                    account_id=str(batch_payload.get("account_id") or args.account_id),
                    trade_date=str(batch_payload.get("trade_date") or args.trade_date or report.trade_date),
                    as_of_date=str(batch_payload.get("as_of_date") or args.as_of_date or report.as_of_date),
                )
            if not args.approval_store_dir:
                raise ValueError("--approval-store-dir is required for create-approval")
            approval = create_adjustment_approval(
                batch,
                args.approval_store_dir,
                reconciliation_report_path=str(paths.get("eod_reconciliation_report_path", "")),
                adjustment_proposals_path=str(paths.get("adjustment_proposals_path", "")),
                metadata={
                    "eod_reconciliation_status": report.status,
                    "unresolved_break_count": report.summary.get("unresolved_break_count", 0),
                    "material_break_count": report.summary.get("material_break_count", 0),
                },
            )
            payload = {
                "approval_id": approval.approval_id,
                "approval_status": approval.status,
                "approval_type": approval.approval_type,
                "proposal_count": approval.adjustment_summary.get("proposal_count", 0),
                "adjustment_summary": approval.adjustment_summary,
                "paths": {key: str(value) for key, value in paths.items()},
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
            return _exit_code(report, args)
        if args.command == "apply-approved":
            if not args.approval_store_dir or not args.approval_id or not args.paper_account_dir:
                raise ValueError("--approval-store-dir, --approval-id and --paper-account-dir are required for apply-approved")
            result, paths = apply_approved_adjustments(
                args.approval_store_dir,
                args.approval_id,
                args.paper_account_dir,
                args.output_dir or ".",
                account_id=args.account_id,
                trade_date=args.trade_date,
            )
            payload = result.to_dict() | {"paths": {key: str(value) for key, value in paths.items()}}
            print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
            return 0
        if args.command == "show-breaks":
            path = Path(args.statement_dir or args.output_dir or ".") / "reconciliation_breaks.jsonl"
            payload = {"breaks": _reconciliation_run_reconcile_read_jsonl(path), "break_count": len(_reconciliation_run_reconcile_read_jsonl(path))}
            print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
            return 0
        if args.command == "show-summary":
            path = Path(args.statement_dir or args.output_dir or ".") / "eod_reconciliation_report.json"
            payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            print(json.dumps(payload.get("summary", payload), ensure_ascii=False, indent=2 if args.pretty else None))
            return 0
    except Exception as exc:  # noqa: BLE001 - CLI should return structured errors
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2 if getattr(args, "pretty", False) else None))
        return 1
    return 2


def _run_eod(args, *, create_proposals: bool):
    if not args.statement_dir or not args.output_dir:
        raise ValueError("--statement-dir and --output-dir are required")
    return run_eod_reconciliation(
        statement_dir=args.statement_dir,
        output_dir=args.output_dir,
        broker_store_dir=args.broker_store_dir,
        broker_batch_id=args.broker_batch_id,
        paper_account_dir=args.paper_account_dir,
        settlement_dir=args.settlement_dir,
        corporate_action_dir=args.corporate_action_dir,
        account_id=args.account_id,
        trade_date=args.trade_date,
        as_of_date=args.as_of_date,
        materiality=_materiality(args),
        strict=args.strict,
        create_adjustment_proposals=create_proposals,
    )


def _materiality(args) -> ReconciliationMaterialityConfig:
    if not args.materiality_config:
        return ReconciliationMaterialityConfig()
    payload = json.loads(Path(args.materiality_config).read_text(encoding="utf-8"))
    return ReconciliationMaterialityConfig(**payload)


def _exit_code(report, args) -> int:
    summary = report.summary
    if args.fail_on_break and int(summary.get("break_count", 0) or 0):
        return 1
    if args.fail_on_error and (int(summary.get("error_count", 0) or 0) or int(summary.get("blocker_count", 0) or 0)):
        return 1
    return 0


def _reconciliation_run_reconcile_read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


if __name__ == "__main__":
    raise SystemExit(main())

__all__ = [
    "AdjustmentApplicationResult",
    "AdjustmentLedgerEntry",
    "AdjustmentProposal",
    "AdjustmentProposalBatch",
    "EodReconciliationReport",
    "ExternalAccountMirror",
    "ReconciliationBreak",
    "ReconciliationBreakType",
    "ReconciliationMaterialityConfig",
    "ReconciliationSeverity",
    "apply_approved_adjustments",
    "create_adjustment_approval",
    "create_adjustment_proposals",
    "run_eod_reconciliation",
]

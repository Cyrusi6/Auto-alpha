"""Adjustment proposal and application helpers for EOD reconciliation."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from approval import ApprovalBatch, ApprovalType, LocalApprovalStore
from paper_account import LocalPaperAccount

from .models import (
    AdjustmentApplicationResult,
    AdjustmentLedgerEntry,
    AdjustmentProposal,
    AdjustmentProposalBatch,
    ReconciliationBreak,
    ReconciliationBreakType,
    ReconciliationMaterialityConfig,
)
from .report import write_adjustment_application_result, write_adjustment_proposal_batch


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
        return _read_jsonl(Path(path))
    batch = approval.metadata.get("adjustment_batch") if isinstance(approval.metadata, dict) else None
    if isinstance(batch, dict) and isinstance(batch.get("proposals"), list):
        return [dict(item) for item in batch["proposals"] if isinstance(item, dict)]
    return []


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
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

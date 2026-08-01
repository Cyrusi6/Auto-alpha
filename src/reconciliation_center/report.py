"""Report writers for EOD reconciliation and adjustments."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import AdjustmentApplicationResult, AdjustmentProposalBatch, EodReconciliationReport, ExternalAccountMirror


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

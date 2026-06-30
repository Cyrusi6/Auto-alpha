"""Report writers for local program trading compliance packs."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import (
    ComplianceGapReport,
    ComplianceReviewPackage,
    ProgramTradingComplianceChecklist,
    ProgramTradingCompliancePack,
    ProgramTradingEvidenceRecord,
    ProgramTradingRiskControlInventory,
    ProgramTradingStrategyInventory,
    ProgramTradingSystemInventory,
    SecretScanReport,
)


def build_compliance_pack(
    *,
    system_inventory: ProgramTradingSystemInventory,
    strategy_inventory: ProgramTradingStrategyInventory,
    risk_inventory: ProgramTradingRiskControlInventory,
    evidence_records: list[ProgramTradingEvidenceRecord],
    checklist: list[ProgramTradingComplianceChecklist],
    gap_report: ComplianceGapReport,
    secret_scan_report: SecretScanReport,
) -> ProgramTradingCompliancePack:
    status = "needs_review" if gap_report.gap_count or secret_scan_report.finding_count else "complete"
    if secret_scan_report.blocker_count:
        status = "failed"
    return ProgramTradingCompliancePack(
        compliance_pack_id=f"ptcp_{_utc_now().replace(':', '').replace('-', '')}",
        created_at=_utc_now(),
        status=status,
        system_inventory=system_inventory.to_dict(),
        strategy_inventory=strategy_inventory.to_dict(),
        risk_control_inventory=risk_inventory.to_dict(),
        evidence_records=[record.to_dict() for record in evidence_records],
        checklist=[item.to_dict() for item in checklist],
        gap_report=gap_report.to_dict(),
        secret_scan_report=secret_scan_report.to_dict(),
        summary={
            "evidence_count": len(evidence_records),
            "checklist_count": len(checklist),
            "gap_count": gap_report.gap_count,
            "secret_blocker_count": secret_scan_report.blocker_count,
            "real_broker_submit_supported": False,
        },
    )


def write_compliance_artifacts(
    *,
    output_dir: str | Path,
    system_inventory: ProgramTradingSystemInventory,
    strategy_inventory: ProgramTradingStrategyInventory,
    risk_inventory: ProgramTradingRiskControlInventory,
    evidence_records: list[ProgramTradingEvidenceRecord],
    checklist: list[ProgramTradingComplianceChecklist],
    gap_report: ComplianceGapReport,
    secret_scan_report: SecretScanReport,
    review_package: ComplianceReviewPackage | None = None,
) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    pack = build_compliance_pack(
        system_inventory=system_inventory,
        strategy_inventory=strategy_inventory,
        risk_inventory=risk_inventory,
        evidence_records=evidence_records,
        checklist=checklist,
        gap_report=gap_report,
        secret_scan_report=secret_scan_report,
    )
    paths = {
        "system_inventory_path": root / "program_trading_system_inventory.json",
        "strategy_inventory_path": root / "program_trading_strategy_inventory.json",
        "risk_control_inventory_path": root / "program_trading_risk_control_inventory.json",
        "compliance_pack_path": root / "program_trading_compliance_pack.json",
        "compliance_pack_md_path": root / "program_trading_compliance_pack.md",
        "evidence_records_path": root / "program_trading_evidence_records.jsonl",
        "checklist_path": root / "program_trading_compliance_checklist.jsonl",
        "gap_report_path": root / "compliance_gap_report.json",
        "gap_report_md_path": root / "compliance_gap_report.md",
        "secret_scan_report_path": root / "secret_scan_report.json",
        "secret_scan_findings_path": root / "secret_scan_findings.jsonl",
        "secret_scan_report_md_path": root / "secret_scan_report.md",
        "review_package_path": root / "compliance_review_package.json",
        "review_package_md_path": root / "compliance_review_package.md",
    }
    write_json_artifact(paths["system_inventory_path"], system_inventory.to_dict(), "program_trading_system_inventory", "program_trading_compliance")
    write_json_artifact(paths["strategy_inventory_path"], strategy_inventory.to_dict(), "program_trading_strategy_inventory", "program_trading_compliance")
    write_json_artifact(paths["risk_control_inventory_path"], risk_inventory.to_dict(), "program_trading_risk_control_inventory", "program_trading_compliance")
    write_json_artifact(paths["compliance_pack_path"], pack.to_dict(), "program_trading_compliance_pack", "program_trading_compliance")
    write_jsonl_artifact(paths["evidence_records_path"], [record.to_dict() for record in evidence_records], "program_trading_evidence_records", "program_trading_compliance")
    write_jsonl_artifact(paths["checklist_path"], [item.to_dict() for item in checklist], "program_trading_compliance_checklist", "program_trading_compliance")
    write_json_artifact(paths["gap_report_path"], gap_report.to_dict(), "compliance_gap_report", "program_trading_compliance")
    write_json_artifact(paths["secret_scan_report_path"], secret_scan_report.to_dict(), "secret_scan_report", "program_trading_compliance")
    write_jsonl_artifact(paths["secret_scan_findings_path"], [item.to_dict() for item in secret_scan_report.findings], "secret_scan_findings", "program_trading_compliance")
    actual_review = review_package or ComplianceReviewPackage(
        review_id=f"compliance_review_{pack.compliance_pack_id}",
        created_at=_utc_now(),
        compliance_pack_path=str(paths["compliance_pack_path"]),
        status="pending",
        reviewer=None,
        comment=None,
        summary=pack.summary,
    )
    write_json_artifact(paths["review_package_path"], actual_review.to_dict(), "compliance_review_package", "program_trading_compliance")
    paths["compliance_pack_md_path"].write_text(_render_pack_markdown(pack.to_dict()), encoding="utf-8")
    paths["gap_report_md_path"].write_text(_render_gap_markdown(gap_report.to_dict()), encoding="utf-8")
    paths["secret_scan_report_md_path"].write_text(_render_secret_markdown(secret_scan_report.to_dict()), encoding="utf-8")
    paths["review_package_md_path"].write_text(_render_review_markdown(actual_review.to_dict()), encoding="utf-8")
    return {key: str(value) for key, value in paths.items()}


def _render_pack_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Program Trading Compliance Pack",
        "",
        f"- status: `{payload.get('status')}`",
        f"- evidence_count: `{payload.get('summary', {}).get('evidence_count', 0)}`",
        f"- gap_count: `{payload.get('summary', {}).get('gap_count', 0)}`",
        f"- secret_blocker_count: `{payload.get('summary', {}).get('secret_blocker_count', 0)}`",
        f"- real_broker_submit_supported: `{payload.get('real_broker_submit_supported')}`",
        "",
        "> Local evidence organization only. This is not legal advice, a regulatory filing, broker authorization, or trading permission.",
        "",
        "## Checklist",
        "",
        "| item | status | reason |",
        "| --- | --- | --- |",
    ]
    for item in payload.get("checklist", []):
        lines.append(f"| {item.get('item_id')} | {item.get('status')} | {item.get('reason')} |")
    return "\n".join(lines) + "\n"


def _render_gap_markdown(payload: dict[str, Any]) -> str:
    lines = ["# Compliance Gap Report", "", f"- status: `{payload.get('status')}`", f"- gap_count: `{payload.get('gap_count', 0)}`", "", "| item | status | reason |", "| --- | --- | --- |"]
    for gap in payload.get("gaps", []):
        lines.append(f"| {gap.get('item_id')} | {gap.get('status')} | {gap.get('reason')} |")
    return "\n".join(lines) + "\n"


def _render_secret_markdown(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Secret Scan Report",
            "",
            f"- status: `{payload.get('status')}`",
            f"- scanned_files: `{payload.get('scanned_files', 0)}`",
            f"- blocker_count: `{payload.get('blocker_count', 0)}`",
            f"- warning_count: `{payload.get('warning_count', 0)}`",
            "",
        ]
    )


def _render_review_markdown(payload: dict[str, Any]) -> str:
    return "\n".join(["# Compliance Review Package", "", "```json", json.dumps(payload, ensure_ascii=False, indent=2), "```", ""])


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

"""Program trading compliance checklist derivation."""

from __future__ import annotations

from datetime import datetime

from .models import (
    ComplianceEvidenceStatus,
    ComplianceGapReport,
    ProgramTradingComplianceChecklist,
    ProgramTradingEvidenceRecord,
    ProgramTradingRiskControlInventory,
    ProgramTradingStrategyInventory,
    ProgramTradingSystemInventory,
    SecretScanReport,
)


CHECKLIST_ITEMS = [
    ("data_freeze_validated", "Data freeze validated", "data_freeze"),
    ("active_model_approved", "Active model approved", "active_model"),
    ("active_optimizer_policy_approved", "Active optimizer policy approved", "portfolio_certification"),
    ("factor_certified", "Factor certified", "factor_certification"),
    ("portfolio_certified", "Portfolio certified", "portfolio_certification"),
    ("risk_controls_available", "Risk controls available", "risk_controls"),
    ("kill_switch_available", "Kill switch available", "kill_switch"),
    ("risk_override_approval_available", "Risk override approval available", "risk_controls"),
    ("settlement_aware_accounting_available", "Settlement-aware accounting available", "settlement"),
    ("eod_reconciliation_available", "EOD reconciliation available", "eod_reconciliation"),
    ("broker_file_mapping_certified_for_dry_run", "Broker file mapping certified for dry-run", "mapping_certification"),
    ("broker_connectivity_profile_available", "Broker connectivity profile available", "broker_connectivity_profile"),
    ("broker_connectivity_network_guard_available", "Broker connectivity network guard available", "broker_network_guard"),
    ("broker_connectivity_readonly_probe_available", "Read-only broker connectivity probe available", "broker_connectivity"),
    ("broker_credential_refs_redacted", "Broker credential references are redacted", "broker_credential_refs"),
    ("broker_readonly_mirror_available", "Read-only broker mirror available", "broker_readonly_mirror"),
    ("broker_readonly_mirror_reconciled", "Read-only broker mirror reconciliation available", "broker_readonly_mirror_reconciliation"),
    ("operator_handoff_completed", "Operator handoff completed", "handoff_checklist"),
    ("file_outbox_roundtrip_passed", "File outbox roundtrip passed", "broker_file_dry_run"),
    ("live_readiness_ready_for_file_outbox_dry_run", "Readiness for file outbox dry-run", "live_readiness"),
    ("incident_response_available", "Incident response available", "incidents"),
    ("monitoring_available", "Monitoring available", "monitoring"),
    ("dashboard_artifact_only", "Dashboard is artifact-only", "monitoring"),
    ("no_real_broker_submit_path", "No real broker submit path", "no_real_broker_submit_path"),
    ("no_credentials_in_artifacts", "No credentials in artifacts", "secret_scan"),
    ("no_network_by_default", "No network by default", "system"),
    ("release_build_passed", "Release build passed", "release_build_ci"),
    ("ci_quick_passed", "CI quick passed", "release_build_ci"),
    ("human_review_required_before_real_broker", "Human review required before any external broker", "system"),
]


def build_compliance_checklist(
    *,
    system_inventory: ProgramTradingSystemInventory,
    strategy_inventory: ProgramTradingStrategyInventory,
    risk_inventory: ProgramTradingRiskControlInventory,
    evidence_records: list[ProgramTradingEvidenceRecord],
    secret_scan_report: SecretScanReport,
) -> tuple[list[ProgramTradingComplianceChecklist], ComplianceGapReport]:
    by_id = {record.evidence_id: record for record in evidence_records}
    items: list[ProgramTradingComplianceChecklist] = []
    for item_id, title, evidence_id in CHECKLIST_ITEMS:
        status = ComplianceEvidenceStatus.complete
        reason = "available"
        evidence_ids = [evidence_id] if evidence_id in by_id else []
        if evidence_id == "secret_scan":
            if secret_scan_report.blocker_count:
                status, reason = ComplianceEvidenceStatus.failed, "secret scan has blockers"
            elif secret_scan_report.warning_count:
                status, reason = ComplianceEvidenceStatus.warning, "secret scan has warnings"
            else:
                reason = "secret scan clean"
        elif evidence_id == "system":
            if item_id == "no_network_by_default":
                status = ComplianceEvidenceStatus.complete if system_inventory.network_default_disabled else ComplianceEvidenceStatus.failed
                reason = "network defaults are local/offline" if system_inventory.network_default_disabled else "network default is not disabled"
            else:
                status = ComplianceEvidenceStatus.complete if not system_inventory.real_broker_submit_supported else ComplianceEvidenceStatus.failed
                reason = "manual review remains required before any external adapter"
        else:
            record = by_id.get(evidence_id)
            if record is None:
                status, reason = ComplianceEvidenceStatus.missing, "evidence record missing"
            elif record.status != ComplianceEvidenceStatus.complete:
                status, reason = ComplianceEvidenceStatus.warning, record.summary
        if item_id == "kill_switch_available" and not risk_inventory.kill_switch_available:
            status, reason = ComplianceEvidenceStatus.warning, "kill switch evidence missing"
        if item_id == "active_model_approved" and not strategy_inventory.active_model_version_id:
            status, reason = ComplianceEvidenceStatus.warning, "active model id missing"
        items.append(
            ProgramTradingComplianceChecklist(
                item_id=item_id,
                title=title,
                status=status,
                required=True,
                reason=reason,
                evidence_ids=evidence_ids,
            )
        )
    gaps = [
        {"item_id": item.item_id, "title": item.title, "status": item.status, "reason": item.reason}
        for item in items
        if item.status in {ComplianceEvidenceStatus.warning, ComplianceEvidenceStatus.missing, ComplianceEvidenceStatus.failed}
    ]
    failed = sum(1 for item in items if item.status == ComplianceEvidenceStatus.failed)
    missing = sum(1 for item in items if item.status == ComplianceEvidenceStatus.missing)
    warnings = sum(1 for item in items if item.status == ComplianceEvidenceStatus.warning)
    gap_report = ComplianceGapReport(
        created_at=_utc_now(),
        gap_count=len(gaps),
        missing_required_count=missing + failed,
        warning_count=warnings,
        gaps=gaps,
        status="failed" if failed else "needs_review" if gaps else "complete",
    )
    return items, gap_report


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

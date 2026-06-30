"""Evidence record builders for local compliance packs."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import ComplianceEvidenceCategory, ComplianceEvidenceStatus, ProgramTradingEvidenceRecord


EVIDENCE_SPECS: list[tuple[str, str, str, str]] = [
    ("data_freeze", ComplianceEvidenceCategory.data, "Data freeze", "dataset_version_manifest.json"),
    ("active_model", ComplianceEvidenceCategory.model, "Active model registry", "model_registry_report.json"),
    ("factor_certification", ComplianceEvidenceCategory.model, "Factor certification", "factor_certification_decision.json"),
    ("portfolio_certification", ComplianceEvidenceCategory.portfolio_policy, "Portfolio certification", "portfolio_certification_decision.json"),
    ("risk_controls", ComplianceEvidenceCategory.risk_control, "Pre-trade risk controls", "risk_control_report.json"),
    ("kill_switch", ComplianceEvidenceCategory.risk_control, "Kill switch", "kill_switch_state.json"),
    ("settlement", ComplianceEvidenceCategory.account, "Settlement accounting", "settlement_report.json"),
    ("broker_file_dry_run", ComplianceEvidenceCategory.broker_file, "Broker file dry-run", "broker_file_gateway_report.json"),
    ("mapping_certification", ComplianceEvidenceCategory.broker_file, "Mapping certification", "broker_mapping_certification_decision.json"),
    ("broker_connectivity", ComplianceEvidenceCategory.execution, "Read-only broker connectivity", "broker_connectivity_report.json"),
    ("broker_connectivity_profile", ComplianceEvidenceCategory.execution, "Broker connectivity profile", "broker_connectivity_profile.json"),
    ("broker_network_guard", ComplianceEvidenceCategory.execution, "Broker connectivity network guard", "broker_network_guard_report.json"),
    ("broker_credential_refs", ComplianceEvidenceCategory.execution, "Broker credential reference manifest", "broker_credential_ref_manifest.json"),
    ("broker_readonly_mirror", ComplianceEvidenceCategory.account, "Read-only broker account mirror", "broker_readonly_mirror_report.json"),
    ("broker_readonly_mirror_reconciliation", ComplianceEvidenceCategory.account, "Read-only broker mirror reconciliation", "readonly_mirror_reconciliation_report.json"),
    ("handoff_checklist", ComplianceEvidenceCategory.operation, "Operator handoff", "operator_handoff_report.json"),
    ("eod_reconciliation", ComplianceEvidenceCategory.account, "EOD reconciliation", "eod_reconciliation_report.json"),
    ("incidents", ComplianceEvidenceCategory.incident, "Incident report", "incident_report.json"),
    ("monitoring", ComplianceEvidenceCategory.monitoring, "Monitoring report", "monitoring_report.json"),
    ("release_build_ci", ComplianceEvidenceCategory.software, "Release gate", "release_gate_report.json"),
    ("live_readiness", ComplianceEvidenceCategory.readiness, "Readiness decision", "live_readiness_decision.json"),
]


def build_evidence_pack(
    *,
    artifact_dirs: list[str | Path] | None = None,
    explicit_paths: dict[str, str | Path | None] | None = None,
    reviewer: str | None = None,
) -> list[ProgramTradingEvidenceRecord]:
    artifact_dirs = [Path(path) for path in artifact_dirs or [] if path]
    explicit_paths = explicit_paths or {}
    records: list[ProgramTradingEvidenceRecord] = []
    created_at = _utc_now()
    for evidence_id, category, title, filename in EVIDENCE_SPECS:
        path = Path(explicit_paths.get(evidence_id)) if explicit_paths.get(evidence_id) else _find_first(artifact_dirs, filename)
        if path and path.exists():
            records.append(
                ProgramTradingEvidenceRecord(
                    evidence_id=evidence_id,
                    category=category,
                    title=title,
                    status=ComplianceEvidenceStatus.complete,
                    source_path=str(path),
                    sha256=_sha256(path),
                    size_bytes=path.stat().st_size,
                    summary=f"{title} artifact found.",
                    reviewer=reviewer,
                    created_at=created_at,
                )
            )
        else:
            records.append(
                ProgramTradingEvidenceRecord(
                    evidence_id=evidence_id,
                    category=category,
                    title=title,
                    status=ComplianceEvidenceStatus.warning,
                    source_path=str(path) if path else None,
                    summary=f"{title} artifact is missing or not provided.",
                    reviewer=reviewer,
                    created_at=created_at,
                )
            )
    records.append(
        ProgramTradingEvidenceRecord(
            evidence_id="no_real_broker_submit_path",
            category=ComplianceEvidenceCategory.execution,
            title="No real broker submit path",
            status=ComplianceEvidenceStatus.complete,
            summary="Current platform boundary is local simulation, file outbox dry-run and manual handoff only.",
            reviewer=reviewer,
            created_at=created_at,
            metadata={"real_broker_submit_supported": False},
        )
    )
    return records


def _find_first(roots: list[Path], filename: str) -> Path | None:
    for root in roots:
        if not root.exists():
            continue
        direct = root / filename
        if direct.exists():
            return direct
        matches = sorted(root.rglob(filename))
        if matches:
            return matches[0]
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

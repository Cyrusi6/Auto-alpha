"""Program-trading compliance inventory, evidence, checks, secret scan, and reporting."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class ComplianceEvidenceStatus:
    complete = "complete"
    warning = "warning"
    missing = "missing"
    not_applicable = "not_applicable"
    failed = "failed"


class ComplianceEvidenceCategory:
    account = "account"
    strategy = "strategy"
    model = "model"
    portfolio_policy = "portfolio_policy"
    risk_control = "risk_control"
    execution = "execution"
    broker_file = "broker_file"
    data = "data"
    system = "system"
    software = "software"
    operation = "operation"
    incident = "incident"
    monitoring = "monitoring"
    approval = "approval"
    readiness = "readiness"


@dataclass(frozen=True)
class ProgramTradingSystemInventory:
    inventory_id: str
    created_at: str
    software_name: str
    software_version: str
    git_commit: str
    package_version: str
    python_version: str
    platform: str
    module_inventory_path: str | None = None
    cli_inventory_path: str | None = None
    dependency_inventory_path: str | None = None
    dashboard_import_status: str = "not_checked"
    network_default_disabled: bool = True
    real_broker_submit_supported: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProgramTradingStrategyInventory:
    active_model_version_id: str | None = None
    active_optimizer_policy_model_version_id: str | None = None
    factor_id: str | None = None
    portfolio_policy_id: str | None = None
    data_freeze_id: str | None = None
    factor_certification_status: str | None = None
    portfolio_certification_status: str | None = None
    validation_status: str | None = None
    alpha_campaign_id: str | None = None
    risk_policy_hash: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProgramTradingRiskControlInventory:
    pre_trade_risk_controls_enabled: bool = False
    kill_switch_available: bool = False
    risk_override_approval_required: bool = False
    max_order_value: float | None = None
    max_participation: float | None = None
    settlement_aware: bool = False
    eod_reconciliation_enabled: bool = False
    incident_response_enabled: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProgramTradingEvidenceRecord:
    evidence_id: str
    category: str
    title: str
    status: str
    source_path: str | None = None
    sha256: str | None = None
    size_bytes: int = 0
    summary: str = ""
    owner: str = "local_platform"
    reviewer: str | None = None
    created_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProgramTradingComplianceChecklist:
    item_id: str
    title: str
    status: str
    required: bool = True
    reason: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    reviewer: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SecretScanFinding:
    path: str
    line: int
    severity: str
    code: str
    message: str
    excerpt: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SecretScanReport:
    created_at: str
    scanned_files: int
    finding_count: int
    blocker_count: int
    warning_count: int
    findings: list[SecretScanFinding]
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "findings": [finding.to_dict() for finding in self.findings],
        }


@dataclass(frozen=True)
class ComplianceGapReport:
    created_at: str
    gap_count: int
    missing_required_count: int
    warning_count: int
    gaps: list[dict[str, Any]]
    status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProgramTradingCompliancePack:
    compliance_pack_id: str
    created_at: str
    status: str
    system_inventory: dict[str, Any]
    strategy_inventory: dict[str, Any]
    risk_control_inventory: dict[str, Any]
    evidence_records: list[dict[str, Any]]
    checklist: list[dict[str, Any]]
    gap_report: dict[str, Any]
    secret_scan_report: dict[str, Any]
    summary: dict[str, Any]
    real_broker_submit_supported: bool = False
    legal_notice: str = "Local evidence organization only; not legal advice, regulatory filing, broker authorization, or trading permission."

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ComplianceReviewPackage:
    review_id: str
    created_at: str
    compliance_pack_path: str
    status: str
    reviewer: str | None
    comment: str | None
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

import importlib.metadata
import json
import platform as platform_module
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any



def build_compliance_inventories(
    *,
    module_inventory_path: str | Path | None = None,
    cli_inventory_path: str | Path | None = None,
    dependency_inventory_path: str | Path | None = None,
    model_registry_report_path: str | Path | None = None,
    factor_certification_decision_path: str | Path | None = None,
    portfolio_certification_decision_path: str | Path | None = None,
    certified_portfolio_policy_path: str | Path | None = None,
    risk_control_report_path: str | Path | None = None,
    settlement_report_path: str | Path | None = None,
    eod_reconciliation_report_path: str | Path | None = None,
    incident_report_path: str | Path | None = None,
    release_manifest_path: str | Path | None = None,
) -> tuple[ProgramTradingSystemInventory, ProgramTradingStrategyInventory, ProgramTradingRiskControlInventory]:
    created_at = _compliance_inventory_utc_now()
    release_manifest = _read_json(release_manifest_path)
    model_registry = _read_json(model_registry_report_path)
    factor_decision = _read_json(factor_certification_decision_path)
    portfolio_decision = _read_json(portfolio_certification_decision_path)
    portfolio_policy = _read_json(certified_portfolio_policy_path)
    risk_report = _read_json(risk_control_report_path)
    settlement_report = _read_json(settlement_report_path)
    eod_report = _read_json(eod_reconciliation_report_path)
    incident_report = _read_json(incident_report_path)
    system = ProgramTradingSystemInventory(
        inventory_id=f"pti_{created_at.replace(':', '').replace('-', '')}",
        created_at=created_at,
        software_name="auto-alpha",
        software_version=str(release_manifest.get("release_name") or _package_version()),
        git_commit=_git_commit(),
        package_version=_package_version(),
        python_version=sys.version.split()[0],
        platform=platform_module.platform(),
        module_inventory_path=str(module_inventory_path) if module_inventory_path else None,
        cli_inventory_path=str(cli_inventory_path) if cli_inventory_path else None,
        dependency_inventory_path=str(dependency_inventory_path) if dependency_inventory_path else None,
        dashboard_import_status=_dashboard_import_status(),
        network_default_disabled=True,
        real_broker_submit_supported=False,
        metadata={
            "release_manifest_path": str(release_manifest_path) if release_manifest_path else "",
            "real_submit_boundary": "file_outbox/manual_handoff only",
        },
    )
    strategy = ProgramTradingStrategyInventory(
        active_model_version_id=_first_nonempty(model_registry, ["active_model_version_id", "model_version_id"]),
        active_optimizer_policy_model_version_id=_first_nonempty(
            model_registry,
            ["active_optimizer_policy_model_version_id", "active_optimizer_policy_id", "optimizer_policy_model_version_id"],
        ),
        factor_id=_first_nonempty(factor_decision, ["factor_id", "target_factor_id"]),
        portfolio_policy_id=_first_nonempty(portfolio_policy, ["policy_id", "portfolio_policy_id", "model_version_id"]),
        data_freeze_id=_first_nonempty(model_registry, ["data_freeze_id", "freeze_id"]),
        factor_certification_status=str(factor_decision.get("status") or factor_decision.get("decision") or ""),
        portfolio_certification_status=str(portfolio_decision.get("status") or portfolio_decision.get("decision") or ""),
        validation_status=str(factor_decision.get("validation_status") or ""),
        alpha_campaign_id=_first_nonempty(model_registry, ["alpha_campaign_id", "campaign_id"]),
        risk_policy_hash=_first_nonempty(risk_report, ["policy_hash", "risk_policy_hash"]),
        metadata={
            "model_registry_report_path": str(model_registry_report_path) if model_registry_report_path else "",
            "certified_portfolio_policy_path": str(certified_portfolio_policy_path) if certified_portfolio_policy_path else "",
        },
    )
    risk = ProgramTradingRiskControlInventory(
        pre_trade_risk_controls_enabled=bool(risk_report),
        kill_switch_available=bool(risk_report) or _path_exists(risk_control_report_path),
        risk_override_approval_required=True,
        max_order_value=_to_float(_first_nonempty(risk_report, ["max_order_value", "max_single_order_value"])),
        max_participation=_to_float(_first_nonempty(risk_report, ["max_participation", "max_participation_rate"])),
        settlement_aware=bool(settlement_report),
        eod_reconciliation_enabled=bool(eod_report),
        incident_response_enabled=bool(incident_report),
        metadata={
            "risk_control_report_path": str(risk_control_report_path) if risk_control_report_path else "",
            "settlement_report_path": str(settlement_report_path) if settlement_report_path else "",
            "eod_reconciliation_report_path": str(eod_reconciliation_report_path) if eod_reconciliation_report_path else "",
            "incident_report_path": str(incident_report_path) if incident_report_path else "",
        },
    )
    return system, strategy, risk


def _read_json(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    target = Path(path)
    if not target.exists():
        return {}
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _path_exists(path: str | Path | None) -> bool:
    return bool(path) and Path(path).exists()


def _first_nonempty(payload: dict[str, Any], keys: list[str]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return str(value)
    summary = payload.get("summary")
    if isinstance(summary, dict):
        for key in keys:
            value = summary.get(key)
            if value not in (None, ""):
                return str(value)
    return None


def _to_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dashboard_import_status() -> str:
    try:
        __import__("auto_alpha.platform.observability.dashboard.app")
        return "import_ok"
    except Exception as exc:  # pragma: no cover - defensive
        return f"import_failed:{exc}"


def _package_version() -> str:
    try:
        return importlib.metadata.version("auto-alpha")
    except importlib.metadata.PackageNotFoundError:
        return "0.1.0"


def _git_commit() -> str:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], text=True, capture_output=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else ""
    except OSError:
        return ""


def _compliance_inventory_utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any



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
    created_at = _compliance_evidence_utc_now()
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


def _compliance_evidence_utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

from datetime import datetime



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
        created_at=_compliance_checklist_utc_now(),
        gap_count=len(gaps),
        missing_required_count=missing + failed,
        warning_count=warnings,
        gaps=gaps,
        status="failed" if failed else "needs_review" if gaps else "complete",
    )
    return items, gap_report


def _compliance_checklist_utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

import re
from datetime import datetime
from pathlib import Path



SECRET_KEY_RE = re.compile(r"(?i)(tushare_token|token|password|secret|api_key|private_key|broker_password|broker_token)\s*[:=]\s*([^\s,'\"]+)")
LONG_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_\-]{32,}\b")
SCAN_SUFFIXES = {".json", ".jsonl", ".csv", ".md", ".txt", ".py", ".yaml", ".yml", ".toml", ".env", ""}
SKIP_PARTS = {".git", ".venv", "__pycache__", ".pytest_cache", "node_modules", "dist"}
PLACEHOLDERS = {"", "changeme", "placeholder", "none", "null", "<real_token>", "<token>", "your_token", "0"}


def scan_artifacts_for_secrets(paths: list[str | Path]) -> SecretScanReport:
    findings: list[SecretScanFinding] = []
    scanned = 0
    for file_path in _iter_files(paths):
        scanned += 1
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            for match in SECRET_KEY_RE.finditer(line):
                key = match.group(1)
                value = match.group(2).strip().strip("'\"")
                severity = "info" if _is_allowed_placeholder(file_path, value) else "blocker"
                findings.append(
                    SecretScanFinding(
                        path=str(file_path),
                        line=line_no,
                        severity=severity,
                        code="explicit_secret" if severity == "blocker" else "placeholder_secret",
                        message=f"{key} assignment found",
                        excerpt=_redact(line),
                    )
                )
            if LONG_TOKEN_RE.search(line) and "sha256" not in line.lower() and "hash" not in line.lower():
                findings.append(
                    SecretScanFinding(
                        path=str(file_path),
                        line=line_no,
                        severity="warning",
                        code="long_token_like_string",
                        message="long token-like string found",
                        excerpt=_redact(line),
                    )
                )
    blockers = sum(1 for item in findings if item.severity == "blocker")
    warnings = sum(1 for item in findings if item.severity == "warning")
    return SecretScanReport(
        created_at=_compliance_secret_scan_utc_now(),
        scanned_files=scanned,
        finding_count=len(findings),
        blocker_count=blockers,
        warning_count=warnings,
        findings=findings,
        status="failed" if blockers else "warning" if warnings else "complete",
    )


def _iter_files(paths: list[str | Path]):
    for raw in paths:
        root = Path(raw)
        if not root.exists():
            continue
        if root.is_file():
            if _should_scan(root):
                yield root
            continue
        for path in root.rglob("*"):
            if path.is_file() and _should_scan(path):
                yield path


def _should_scan(path: Path) -> bool:
    if any(part in SKIP_PARTS for part in path.parts):
        return False
    return path.suffix.lower() in SCAN_SUFFIXES


def _is_allowed_placeholder(path: Path, value: str) -> bool:
    normalized = value.strip().strip("'\"").lower()
    return path.name == ".env.example" and normalized in PLACEHOLDERS


def _redact(line: str) -> str:
    return SECRET_KEY_RE.sub(lambda match: f"{match.group(1)}=<redacted>", line)[:240]


def _compliance_secret_scan_utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact, write_jsonl_artifact



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
        compliance_pack_id=f"ptcp_{_compliance_report_utc_now().replace(':', '').replace('-', '')}",
        created_at=_compliance_report_utc_now(),
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
        created_at=_compliance_report_utc_now(),
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


def _compliance_report_utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

import argparse
import json
from datetime import datetime
from pathlib import Path

from auto_alpha.platform.governance.approval import ApprovalBatch, ApprovalType, LocalApprovalStore



def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build local program trading compliance artifacts.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["inventory", "scan-secrets", "build-pack", "checklist", "create-review", "report", "smoke"]:
        cmd = sub.add_parser(name)
        _add_args(cmd)
    return parser


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--artifact-dir", action="append", default=[])
    parser.add_argument("--artifact-catalog-path", action="append", default=[])
    parser.add_argument("--model-registry-report-path")
    parser.add_argument("--factor-certification-decision-path")
    parser.add_argument("--portfolio-certification-decision-path")
    parser.add_argument("--certified-portfolio-policy-path")
    parser.add_argument("--live-readiness-decision-path")
    parser.add_argument("--broker-mapping-certification-decision-path")
    parser.add_argument("--broker-file-gateway-report-path")
    parser.add_argument("--broker-uat-report-path")
    parser.add_argument("--broker-connectivity-profile-path")
    parser.add_argument("--broker-connectivity-report-path")
    parser.add_argument("--broker-network-guard-report-path")
    parser.add_argument("--broker-credential-ref-manifest-path")
    parser.add_argument("--broker-readonly-mirror-report-path")
    parser.add_argument("--readonly-mirror-reconciliation-report-path")
    parser.add_argument("--operator-handoff-report-path")
    parser.add_argument("--risk-control-report-path")
    parser.add_argument("--settlement-report-path")
    parser.add_argument("--eod-reconciliation-report-path")
    parser.add_argument("--incident-report-path")
    parser.add_argument("--monitoring-report-path")
    parser.add_argument("--release-manifest-path")
    parser.add_argument("--module-inventory-path")
    parser.add_argument("--cli-inventory-path")
    parser.add_argument("--dependency-inventory-path")
    parser.add_argument("--approval-store-dir")
    parser.add_argument("--reviewer")
    parser.add_argument("--comment")
    parser.add_argument("--fail-on-secret", action="store_true")
    parser.add_argument("--pretty", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    payload, paths, secret_blockers = _build_outputs(args)
    if args.command == "create-review" and args.approval_store_dir:
        approval = _create_review_approval(args, paths, payload)
        payload["approval"] = approval.to_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    return 1 if args.fail_on_secret and secret_blockers else 0


def _build_outputs(args: argparse.Namespace) -> tuple[dict, dict[str, str], int]:
    artifact_dirs = [Path(path) for path in args.artifact_dir]
    if args.output_dir:
        artifact_dirs.append(Path(args.output_dir))
    explicit_paths = {
        "active_model": args.model_registry_report_path,
        "factor_certification": args.factor_certification_decision_path,
        "portfolio_certification": args.portfolio_certification_decision_path,
        "live_readiness": args.live_readiness_decision_path,
        "mapping_certification": args.broker_mapping_certification_decision_path,
        "broker_file_dry_run": args.broker_file_gateway_report_path,
        "broker_connectivity": args.broker_connectivity_report_path,
        "broker_connectivity_profile": args.broker_connectivity_profile_path,
        "broker_network_guard": args.broker_network_guard_report_path,
        "broker_credential_refs": args.broker_credential_ref_manifest_path,
        "broker_readonly_mirror": args.broker_readonly_mirror_report_path,
        "broker_readonly_mirror_reconciliation": args.readonly_mirror_reconciliation_report_path,
        "handoff_checklist": args.operator_handoff_report_path,
        "risk_controls": args.risk_control_report_path,
        "settlement": args.settlement_report_path,
        "eod_reconciliation": args.eod_reconciliation_report_path,
        "incidents": args.incident_report_path,
        "monitoring": args.monitoring_report_path,
        "release_build_ci": args.release_manifest_path,
    }
    system, strategy, risk = build_compliance_inventories(
        module_inventory_path=args.module_inventory_path,
        cli_inventory_path=args.cli_inventory_path,
        dependency_inventory_path=args.dependency_inventory_path,
        model_registry_report_path=args.model_registry_report_path,
        factor_certification_decision_path=args.factor_certification_decision_path,
        portfolio_certification_decision_path=args.portfolio_certification_decision_path,
        certified_portfolio_policy_path=args.certified_portfolio_policy_path,
        risk_control_report_path=args.risk_control_report_path,
        settlement_report_path=args.settlement_report_path,
        eod_reconciliation_report_path=args.eod_reconciliation_report_path,
        incident_report_path=args.incident_report_path,
        release_manifest_path=args.release_manifest_path,
    )
    evidence = build_evidence_pack(artifact_dirs=artifact_dirs, explicit_paths=explicit_paths, reviewer=args.reviewer)
    secret_report = scan_artifacts_for_secrets([path for path in artifact_dirs if path.exists()])
    checklist, gaps = build_compliance_checklist(
        system_inventory=system,
        strategy_inventory=strategy,
        risk_inventory=risk,
        evidence_records=evidence,
        secret_scan_report=secret_report,
    )
    review = ComplianceReviewPackage(
        review_id=f"compliance_review_{_utc_id()}",
        created_at=_compliance_run_compliance_utc_now(),
        compliance_pack_path=str(Path(args.output_dir) / "program_trading_compliance_pack.json"),
        status="pending",
        reviewer=args.reviewer,
        comment=args.comment,
        summary={"gap_count": gaps.gap_count, "secret_blocker_count": secret_report.blocker_count},
    )
    paths = write_compliance_artifacts(
        output_dir=args.output_dir,
        system_inventory=system,
        strategy_inventory=strategy,
        risk_inventory=risk,
        evidence_records=evidence,
        checklist=checklist,
        gap_report=gaps,
        secret_scan_report=secret_report,
        review_package=review,
    )
    payload = {
        "status": "failed" if secret_report.blocker_count else "needs_review" if gaps.gap_count else "complete",
        "paths": paths,
        "summary": {
            "evidence_count": len(evidence),
            "gap_count": gaps.gap_count,
            "secret_blocker_count": secret_report.blocker_count,
            "real_broker_submit_supported": False,
        },
    }
    return payload, paths, secret_report.blocker_count


def _create_review_approval(args: argparse.Namespace, paths: dict[str, str], payload: dict) -> ApprovalBatch:
    store = LocalApprovalStore(args.approval_store_dir)
    approval = ApprovalBatch(
        approval_id=f"compliance_review_{_utc_id()}",
        created_at=_compliance_run_compliance_utc_now(),
        factor_id="program_trading_compliance",
        factor_type="review",
        rebalance_date="",
        portfolio_method="not_applicable",
        orders=[],
        approval_type=ApprovalType.compliance_review,
        compliance_pack_path=paths.get("compliance_pack_path"),
        compliance_summary=payload.get("summary", {}),
        metadata={"reviewer": args.reviewer or "", "comment": args.comment or ""},
    )
    store.save_batch(approval)
    return approval


def _compliance_run_compliance_utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _utc_id() -> str:
    return _compliance_run_compliance_utc_now().replace("-", "").replace(":", "").replace("Z", "")


if __name__ == "__main__":
    raise SystemExit(main())

__all__ = [
    "ComplianceEvidenceCategory",
    "ComplianceEvidenceStatus",
    "ComplianceGapReport",
    "ComplianceReviewPackage",
    "ProgramTradingComplianceChecklist",
    "ProgramTradingCompliancePack",
    "ProgramTradingEvidenceRecord",
    "ProgramTradingRiskControlInventory",
    "ProgramTradingStrategyInventory",
    "ProgramTradingSystemInventory",
    "SecretScanFinding",
    "SecretScanReport",
    "build_compliance_checklist",
    "build_compliance_inventories",
    "build_evidence_pack",
    "scan_artifacts_for_secrets",
]

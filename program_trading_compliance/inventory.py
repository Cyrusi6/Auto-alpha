"""Build local program trading inventory from existing artifacts."""

from __future__ import annotations

import importlib.metadata
import json
import platform as platform_module
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import (
    ProgramTradingRiskControlInventory,
    ProgramTradingStrategyInventory,
    ProgramTradingSystemInventory,
)


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
    created_at = _utc_now()
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
        __import__("dashboard.app")
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


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

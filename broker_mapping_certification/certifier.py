"""Certification runner for broker file mapping profiles."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from broker_file_gateway.inbox import import_inbox_files, synthesize_inbox_files
from broker_file_gateway.packager import export_file_batch
from broker_file_gateway.profiles import load_profile
from broker_file_gateway.report import write_gateway_report
from broker_file_gateway.roundtrip import run_file_roundtrip_check

from .fixtures import sample_child_orders
from .models import (
    BrokerMappingCertificationDecision,
    BrokerMappingCertificationPackage,
    BrokerMappingCertificationStatus,
)
from .policy import load_certification_policy
from .report import write_mapping_certification_report


def certify_broker_file_mapping(
    *,
    profile_name: str = "generic_broker_csv",
    profile_config: str | Path | None = None,
    policy_name: str = "dry_run_standard",
    policy_config: str | Path | None = None,
    output_dir: str | Path,
    gateway_store_dir: str | Path | None = None,
    trade_date: str = "20240104",
) -> BrokerMappingCertificationPackage:
    output = Path(output_dir)
    gateway_store = Path(gateway_store_dir) if gateway_store_dir is not None else output / "gateway_store"
    outbox_dir = output / "outbox"
    inbox_dir = output / "inbox"
    normalized_dir = output / "normalized"
    profile = load_profile(profile_name, profile_config)
    policy = load_certification_policy(policy_name, policy_config)
    export = export_file_batch(
        store_dir=gateway_store,
        outbox_dir=outbox_dir,
        profile=profile,
        child_orders=sample_child_orders(trade_date),
        production_run_id=f"mapping_cert_{trade_date}",
        approval_id=f"mapping_cert_approval_{trade_date}",
        broker_batch_id=f"mapping_cert_batch_{trade_date}",
        trade_date=trade_date,
        account_id="paper_ashare",
        refresh=True,
    )
    synthesize_inbox_files(outbox_dir=outbox_dir, inbox_dir=inbox_dir, profile=profile, file_batch_id=export["file_batch_id"])
    import_inbox_files(store_dir=gateway_store, inbox_dir=inbox_dir, output_dir=normalized_dir, profile=profile, file_batch_id=export["file_batch_id"])
    roundtrip = run_file_roundtrip_check(
        store_dir=gateway_store,
        outbox_dir=outbox_dir,
        normalized_dir=normalized_dir,
        output_dir=output,
        file_batch_id=export["file_batch_id"],
        broker_batch_id=str((export.get("batch") or {}).get("broker_batch_id") or ""),
    )
    gateway_report = write_gateway_report(store_dir=gateway_store, output_dir=output, profile=profile, roundtrip=roundtrip["roundtrip"])
    checks = _build_checks(profile, policy, roundtrip)
    status, reasons = _decide(checks, policy)
    certification_id = f"mapping_cert_{_safe_time()}_{profile.profile_id}"
    decision = BrokerMappingCertificationDecision(
        certification_id=certification_id,
        created_at=_utc_now(),
        status=status,
        profile_id=profile.profile_id,
        schema_name=profile.schema_name,
        policy_name=policy.policy_name,
        reasons=reasons,
        checks=checks,
        qmt_skeleton_notice=profile.notice if profile.schema_name == "qmt_skeleton_csv" else None,
        metadata={
            "no_real_submit": True,
            "mode": "file_outbox_dry_run",
            "gateway_report_path": (gateway_report.get("paths") or {}).get("broker_file_gateway_report_path"),
            "roundtrip_report_path": roundtrip.get("report_path"),
        },
    )
    package = BrokerMappingCertificationPackage(
        certification_id=certification_id,
        decision=decision,
        policy=policy,
        paths={
            "gateway_report_path": str((gateway_report.get("paths") or {}).get("broker_file_gateway_report_path", "")),
            "roundtrip_report_path": str(roundtrip.get("report_path", "")),
            "outbox_dir": str(outbox_dir),
            "inbox_dir": str(inbox_dir),
        },
        summary={"roundtrip": roundtrip.get("roundtrip", {})},
    )
    paths = write_mapping_certification_report(package, output)
    return BrokerMappingCertificationPackage(package.certification_id, package.decision, package.policy, {**package.paths, **paths}, package.summary)


def load_mapping_decision(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    target = Path(path)
    if not target.exists():
        return {}
    return json.loads(target.read_text(encoding="utf-8"))


def _build_checks(profile: Any, policy: Any, roundtrip: dict[str, Any]) -> dict[str, Any]:
    report = roundtrip.get("roundtrip") if isinstance(roundtrip.get("roundtrip"), dict) else {}
    required = set(profile.required_columns or [])
    mapped = set((profile.field_mapping or {}).values())
    checks = {
        "roundtrip_error_count": int(report.get("error_count", 0) or 0),
        "missing_ack_count": int(report.get("missing_ack_count", 0) or 0),
        "orphan_fill_count": int(report.get("orphan_fill_count", 0) or 0),
        "required_columns_present": required.issubset(mapped),
        "qmt_skeleton_notice_present": bool(profile.notice) if profile.schema_name == "qmt_skeleton_csv" else True,
        "no_real_submit": True,
    }
    checks["policy_max_roundtrip_errors"] = policy.max_roundtrip_errors
    return checks


def _decide(checks: dict[str, Any], policy: Any) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if checks["roundtrip_error_count"] > policy.max_roundtrip_errors:
        reasons.append("roundtrip errors exceed policy")
    if checks["missing_ack_count"] > policy.max_missing_ack:
        reasons.append("missing acknowledgements exceed policy")
    if checks["orphan_fill_count"] > policy.max_orphan_fills:
        reasons.append("orphan fills exceed policy")
    if not checks["required_columns_present"]:
        reasons.append("required mapped columns are missing")
    if policy.require_qmt_skeleton_notice and not checks["qmt_skeleton_notice_present"]:
        reasons.append("required skeleton notice is missing")
    if not reasons:
        return BrokerMappingCertificationStatus.certified_for_dry_run, []
    if policy.allow_conditional and checks["required_columns_present"]:
        return BrokerMappingCertificationStatus.conditional, reasons
    return BrokerMappingCertificationStatus.rejected, reasons


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _safe_time() -> str:
    return _utc_now().replace("-", "").replace(":", "").replace("Z", "")

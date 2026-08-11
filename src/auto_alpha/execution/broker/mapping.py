"""Broker file-mapping certification workflow."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class BrokerMappingCertificationStatus:
    certified_for_dry_run = "certified_for_dry_run"
    conditional = "conditional"
    rejected = "rejected"
    insufficient_data = "insufficient_data"


@dataclass(frozen=True)
class BrokerMappingCertificationPolicy:
    policy_name: str
    max_roundtrip_errors: int = 0
    max_missing_ack: int = 0
    max_orphan_fills: int = 0
    require_qmt_skeleton_notice: bool = True
    allow_conditional: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerMappingCertificationDecision:
    certification_id: str
    created_at: str
    status: str
    profile_id: str
    schema_name: str
    policy_name: str
    reasons: list[str] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)
    qmt_skeleton_notice: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BrokerMappingCertificationPackage:
    certification_id: str
    decision: BrokerMappingCertificationDecision
    policy: BrokerMappingCertificationPolicy
    paths: dict[str, str] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

import json
from pathlib import Path



def load_certification_policy(policy_name: str = "dry_run_standard", policy_config: str | Path | None = None) -> BrokerMappingCertificationPolicy:
    if policy_config is not None:
        payload = json.loads(Path(policy_config).read_text(encoding="utf-8"))
        return BrokerMappingCertificationPolicy(
            policy_name=str(payload.get("policy_name") or policy_name),
            max_roundtrip_errors=int(payload.get("max_roundtrip_errors", 0) or 0),
            max_missing_ack=int(payload.get("max_missing_ack", 0) or 0),
            max_orphan_fills=int(payload.get("max_orphan_fills", 0) or 0),
            require_qmt_skeleton_notice=bool(payload.get("require_qmt_skeleton_notice", True)),
            allow_conditional=bool(payload.get("allow_conditional", True)),
            metadata=dict(payload.get("metadata") or {}),
        )
    if policy_name == "sample_lenient_mapping":
        return BrokerMappingCertificationPolicy(policy_name=policy_name, max_roundtrip_errors=1, max_missing_ack=1, allow_conditional=True)
    if policy_name == "file_outbox_strict":
        return BrokerMappingCertificationPolicy(policy_name=policy_name, max_roundtrip_errors=0, max_missing_ack=0, max_orphan_fills=0, allow_conditional=False)
    return BrokerMappingCertificationPolicy(policy_name=policy_name)

from typing import Any


def sample_child_orders(trade_date: str = "20240104") -> list[dict[str, Any]]:
    return [
        {
            "child_order_id": f"cert_{trade_date}_buy_open",
            "parent_order_id": f"cert_{trade_date}_buy",
            "trade_date": trade_date,
            "ts_code": "000001.SZ",
            "side": "BUY",
            "bucket": "open",
            "order_value": 12000.0,
            "target_weight": 0.05,
            "price": 10.0,
            "reason": "mapping_certification",
        },
        {
            "child_order_id": f"cert_{trade_date}_sell_close",
            "parent_order_id": f"cert_{trade_date}_sell",
            "trade_date": trade_date,
            "ts_code": "600000.SH",
            "side": "SELL",
            "bucket": "close",
            "order_value": 8000.0,
            "target_weight": 0.02,
            "price": 12.5,
            "reason": "mapping_certification",
        },
    ]

from pathlib import Path
from typing import Any

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact, write_jsonl_artifact



def write_mapping_certification_report(package: BrokerMappingCertificationPackage, output_dir: str | Path) -> dict[str, str]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    policy_path = target / "broker_mapping_certification_policy.json"
    scorecard_path = target / "broker_mapping_certification_scorecard.json"
    decision_path = target / "broker_mapping_certification_decision.json"
    package_path = target / "broker_mapping_certification_package.json"
    report_md_path = target / "broker_mapping_certification_report.md"
    checks_path = target / "broker_mapping_certification_checks.jsonl"
    checks = [{"check": key, "value": value} for key, value in sorted(package.decision.checks.items())]
    write_json_artifact(policy_path, package.policy.to_dict(), artifact_type="broker_mapping_certification_policy", producer="broker_mapping_certification")
    write_json_artifact(scorecard_path, {"certification_id": package.certification_id, "checks": package.decision.checks}, artifact_type="broker_mapping_certification_scorecard", producer="broker_mapping_certification")
    write_json_artifact(decision_path, package.decision.to_dict(), artifact_type="broker_mapping_certification_decision", producer="broker_mapping_certification")
    write_json_artifact(package_path, package.to_dict(), artifact_type="broker_mapping_certification_package", producer="broker_mapping_certification")
    write_jsonl_artifact(checks_path, checks, artifact_type="broker_mapping_certification_checks", producer="broker_mapping_certification")
    report_md_path.write_text(_markdown(package), encoding="utf-8")
    return {
        "policy_path": str(policy_path),
        "scorecard_path": str(scorecard_path),
        "decision_path": str(decision_path),
        "package_path": str(package_path),
        "report_md_path": str(report_md_path),
        "checks_path": str(checks_path),
    }


def _markdown(package: BrokerMappingCertificationPackage) -> str:
    decision = package.decision
    lines = [
        "# Broker Mapping Certification",
        "",
        f"- certification_id: `{decision.certification_id}`",
        f"- status: `{decision.status}`",
        f"- profile_id: `{decision.profile_id}`",
        f"- schema_name: `{decision.schema_name}`",
        f"- policy: `{decision.policy_name}`",
        "",
        "## Reasons",
    ]
    lines.extend([f"- {reason}" for reason in decision.reasons] or ["- none"])
    if decision.qmt_skeleton_notice:
        lines.extend(["", "## Skeleton Notice", decision.qmt_skeleton_notice])
    return "\n".join(lines) + "\n"

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from auto_alpha.execution.broker.file_gateway import import_inbox_files
from auto_alpha.execution.broker.file_gateway import synthesize_inbox_files
from auto_alpha.execution.broker.file_gateway import export_file_batch
from auto_alpha.execution.broker.file_gateway import load_profile
from auto_alpha.execution.broker.file_gateway import write_gateway_report
from auto_alpha.execution.broker.file_gateway import run_file_roundtrip_check



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

import argparse
import json
from pathlib import Path



def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Certify broker file mapping profiles for dry-run outbox.")
    parser.add_argument("command", nargs="?", choices=["init-policy", "run", "scorecard", "decide", "report", "smoke"])
    parser.add_argument("--profile-name", default="generic_broker_csv")
    parser.add_argument("--profile-config")
    parser.add_argument("--policy", dest="policy_name", default="dry_run_standard")
    parser.add_argument("--policy-profile", dest="policy_name")
    parser.add_argument("--policy-config", dest="policy_config")
    parser.add_argument("--policy-path", dest="policy_config")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--gateway-store-dir")
    parser.add_argument("--handoff-store-dir")
    parser.add_argument("--approval-store-dir")
    parser.add_argument("--work-dir")
    parser.add_argument("--trade-date", default="20240104")
    parser.add_argument("--run-roundtrip", action="store_true")
    parser.add_argument("--run-eod-reconciliation", action="store_true")
    parser.add_argument("--handoff-package-path")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    command = args.command or ("smoke" if args.smoke else "run")
    if command == "init-policy":
        policy = load_certification_policy(args.policy_name, args.policy_config)
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "broker_mapping_certification_policy.json"
        path.write_text(json.dumps(policy.to_dict(), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        payload = {"status": "success", "policy": policy.to_dict(), "policy_path": str(path)}
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
        return 0

    package = certify_broker_file_mapping(
        profile_name=args.profile_name,
        profile_config=args.profile_config,
        policy_name=args.policy_name,
        policy_config=args.policy_config,
        output_dir=args.output_dir,
        gateway_store_dir=args.gateway_store_dir,
        trade_date=args.trade_date,
    )
    payload = package.to_dict()
    payload["command"] = command
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    return 0 if package.decision.status in {"certified_for_dry_run", "conditional"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

__all__ = [
    "BrokerMappingCertificationDecision",
    "BrokerMappingCertificationPackage",
    "BrokerMappingCertificationPolicy",
    "BrokerMappingCertificationStatus",
    "certify_broker_file_mapping",
    "load_certification_policy",
    "write_mapping_certification_report",
]

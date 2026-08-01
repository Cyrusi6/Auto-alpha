"""CLI for local program trading compliance evidence packs."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from approval import ApprovalBatch, ApprovalType, LocalApprovalStore

from .checklist import build_compliance_checklist
from .evidence import build_evidence_pack
from .inventory import build_compliance_inventories
from .models import ComplianceReviewPackage
from .report import write_compliance_artifacts
from .secret_scan import scan_artifacts_for_secrets


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
        created_at=_utc_now(),
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
        created_at=_utc_now(),
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


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _utc_id() -> str:
    return _utc_now().replace("-", "").replace(":", "").replace("Z", "")


if __name__ == "__main__":
    raise SystemExit(main())

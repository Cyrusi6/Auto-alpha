"""CLI for local pre-live Go/No-Go gates."""

from __future__ import annotations

import argparse
import json
from datetime import datetime

from approval import ApprovalBatch, ApprovalType, LocalApprovalStore

from .decision import make_go_live_gate_decision
from .models import GoLiveReviewPackage
from .policy import build_go_live_policy, load_go_live_policy
from .report import write_go_live_artifacts
from .scorecard import build_go_live_scorecard


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build local pre-live Go/No-Go artifacts.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["init-policy", "scorecard", "decide", "run", "create-review", "report", "smoke"]:
        cmd = sub.add_parser(name)
        _add_args(cmd)
    return parser


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--policy-profile", default="sample_lenient_go_live")
    parser.add_argument("--policy-path")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--approval-store-dir")
    parser.add_argument("--program-trading-compliance-pack-path")
    parser.add_argument("--secret-scan-report-path")
    parser.add_argument("--broker-uat-report-path")
    parser.add_argument("--broker-adapter-contract-report-path")
    parser.add_argument("--broker-mapping-certification-decision-path")
    parser.add_argument("--broker-file-gateway-report-path")
    parser.add_argument("--operator-handoff-report-path")
    parser.add_argument("--live-readiness-decision-path")
    parser.add_argument("--production-replay-report-path")
    parser.add_argument("--shadow-lab-report-path")
    parser.add_argument("--factor-certification-decision-path")
    parser.add_argument("--portfolio-certification-decision-path")
    parser.add_argument("--risk-control-report-path")
    parser.add_argument("--settlement-report-path")
    parser.add_argument("--eod-reconciliation-report-path")
    parser.add_argument("--incident-report-path")
    parser.add_argument("--monitoring-report-path")
    parser.add_argument("--release-gate-report-path")
    parser.add_argument("--create-review-approval", action="store_true")
    parser.add_argument("--reviewer")
    parser.add_argument("--comment")
    parser.add_argument("--pretty", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    policy = load_go_live_policy(args.policy_path) if args.policy_path else build_go_live_policy(args.policy_profile)
    scorecard = build_go_live_scorecard(
        policy,
        program_trading_compliance_pack_path=args.program_trading_compliance_pack_path,
        secret_scan_report_path=args.secret_scan_report_path,
        broker_uat_report_path=args.broker_uat_report_path,
        broker_adapter_contract_report_path=args.broker_adapter_contract_report_path,
        broker_mapping_certification_decision_path=args.broker_mapping_certification_decision_path,
        broker_file_gateway_report_path=args.broker_file_gateway_report_path,
        operator_handoff_report_path=args.operator_handoff_report_path,
        live_readiness_decision_path=args.live_readiness_decision_path,
        production_replay_report_path=args.production_replay_report_path,
        shadow_lab_report_path=args.shadow_lab_report_path,
        factor_certification_decision_path=args.factor_certification_decision_path,
        portfolio_certification_decision_path=args.portfolio_certification_decision_path,
        risk_control_report_path=args.risk_control_report_path,
        settlement_report_path=args.settlement_report_path,
        eod_reconciliation_report_path=args.eod_reconciliation_report_path,
        incident_report_path=args.incident_report_path,
        monitoring_report_path=args.monitoring_report_path,
        release_gate_report_path=args.release_gate_report_path,
    )
    decision = make_go_live_gate_decision(scorecard)
    review = GoLiveReviewPackage(
        review_id=f"go_live_review_{_utc_id()}",
        created_at=_utc_now(),
        go_live_gate_decision_path=f"{args.output_dir}/go_live_gate_decision.json",
        go_live_status=decision.status,
        status="pending",
        reviewer=args.reviewer,
        comment=args.comment,
        summary={"required_remediation_count": len(decision.required_remediation), "score": decision.score},
    )
    paths = write_go_live_artifacts(output_dir=args.output_dir, policy=policy, scorecard=scorecard, decision=decision, review_package=review)
    payload = decision.to_dict() | {"scorecard": scorecard.to_dict(), "paths": paths}
    if args.create_review_approval and args.approval_store_dir:
        approval = _create_review_approval(args, decision, paths)
        payload["approval"] = approval.to_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    return 0 if args.command in {"init-policy", "scorecard", "report", "smoke", "run", "create-review"} else 1 if not decision.passed else 0


def _create_review_approval(args: argparse.Namespace, decision, paths: dict[str, str]) -> ApprovalBatch:
    store = LocalApprovalStore(args.approval_store_dir)
    approval = ApprovalBatch(
        approval_id=f"go_live_review_{_utc_id()}",
        created_at=_utc_now(),
        factor_id="go_live_gate",
        factor_type="review",
        rebalance_date="",
        portfolio_method="not_applicable",
        orders=[],
        approval_type=ApprovalType.go_live_review,
        go_live_gate_decision_path=paths.get("go_live_gate_decision_path"),
        go_live_status=decision.status,
        go_live_summary={
            "required_remediation_count": len(decision.required_remediation),
            "score": decision.score,
            "blocker_count": decision.metadata.get("blocker_count", 0),
        },
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

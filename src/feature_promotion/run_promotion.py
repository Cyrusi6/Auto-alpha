"""CLI for feature promotion policy, evidence and review workflows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from approval.models import ApprovalBatch, ApprovalType
from approval.store import LocalApprovalStore
from feature_factory.catalog import FEATURE_SET_V3, build_feature_set_manifest
from feature_factory.run_features import main as run_features_main

from .decision import (
    build_allow_deny_lists,
    decisions_from_approval,
    default_decisions_from_review_package,
    load_decisions,
    write_decisions,
)
from .evidence import build_feature_promotion_evidence, build_promotion_candidates
from .policy import create_default_policy, load_json, load_policy, policy_hash
from .report import write_application_artifacts, write_evidence_artifacts, write_policy_artifacts, write_review_package
from .review import make_review_package


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review and promote PIT-sensitive feature families.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["init-policy", "validate-policy", "build-evidence", "create-review", "apply-approved", "report", "smoke"]:
        cmd = sub.add_parser(name)
        _add_common(cmd)
    return parser


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--feature-set-manifest-path")
    parser.add_argument("--feature-family-readiness-path")
    parser.add_argument("--feature-pit-alignment-report-path")
    parser.add_argument("--feature-build-warnings-path")
    parser.add_argument("--feature-coverage-report-path")
    parser.add_argument("--pit-validation-report-path")
    parser.add_argument("--leakage-audit-report-path")
    parser.add_argument("--raw-landing-report-path")
    parser.add_argument("--research-data-readiness-report-path")
    parser.add_argument("--feature-promotion-policy-path")
    parser.add_argument("--feature-promotion-evidence-path")
    parser.add_argument("--feature-promotion-review-package-path")
    parser.add_argument("--feature-promotion-decisions-path")
    parser.add_argument("--approval-store-dir")
    parser.add_argument("--approval-id")
    parser.add_argument("--reviewer", default="local_feature_reviewer")
    parser.add_argument("--comment")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--data-dir")
    parser.add_argument("--pretty", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        payload = _run(args)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2 if getattr(args, "pretty", False) else None))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    return 0


def _run(args: argparse.Namespace) -> dict:
    if args.command == "smoke":
        return _run_smoke(args)
    if args.command == "validate-policy":
        policy = _load_policy_payload(args)
        return {"status": "success", "policy_hash": policy_hash(policy), "policy": policy}
    if args.command == "init-policy":
        manifest = _load_manifest(args)
        policy = create_default_policy(manifest).to_dict()
        policy["policy_hash"] = policy_hash(policy)
        paths = write_policy_artifacts(policy, args.output_dir)
        return {"status": "success", "policy_hash": policy["policy_hash"], "paths": paths, "policy": policy}
    if args.command == "build-evidence":
        manifest = _load_manifest(args)
        policy = load_policy(args.feature_promotion_policy_path, manifest) or create_default_policy(manifest)
        evidence, summary = build_feature_promotion_evidence(
            manifest=manifest,
            policy=policy,
            feature_family_readiness_path=args.feature_family_readiness_path,
            feature_pit_alignment_report_path=args.feature_pit_alignment_report_path,
            feature_build_warnings_path=args.feature_build_warnings_path,
            feature_coverage_report_path=args.feature_coverage_report_path,
            pit_validation_report_path=args.pit_validation_report_path,
            leakage_audit_report_path=args.leakage_audit_report_path,
            raw_landing_report_path=args.raw_landing_report_path,
            research_data_readiness_report_path=args.research_data_readiness_report_path,
        )
        paths = write_evidence_artifacts([item.to_dict() for item in evidence], summary, args.output_dir)
        return {"status": "success", "summary": summary, "paths": paths}
    if args.command == "create-review":
        return _create_review(args)
    if args.command == "apply-approved":
        return _apply_approved(args)
    if args.command == "report":
        package = load_json(args.feature_promotion_review_package_path)
        return {"status": "success" if package else "missing", "review_package": package}
    raise ValueError(f"unsupported command: {args.command}")


def _create_review(args: argparse.Namespace) -> dict:
    manifest = _load_manifest(args)
    policy = load_policy(args.feature_promotion_policy_path, manifest) or create_default_policy(manifest)
    evidence, summary = build_feature_promotion_evidence(
        manifest=manifest,
        policy=policy,
        feature_family_readiness_path=args.feature_family_readiness_path,
        feature_pit_alignment_report_path=args.feature_pit_alignment_report_path,
        feature_build_warnings_path=args.feature_build_warnings_path,
        feature_coverage_report_path=args.feature_coverage_report_path,
        pit_validation_report_path=args.pit_validation_report_path,
        leakage_audit_report_path=args.leakage_audit_report_path,
        raw_landing_report_path=args.raw_landing_report_path,
        research_data_readiness_report_path=args.research_data_readiness_report_path,
    )
    candidates = build_promotion_candidates(manifest, policy)
    package = make_review_package(policy, candidates, evidence, metadata={"evidence_summary": summary})
    paths = {}
    paths.update(write_evidence_artifacts([item.to_dict() for item in evidence], summary, args.output_dir))
    paths.update(write_review_package(package.to_dict(), args.output_dir))
    approval_id = None
    if args.approval_store_dir:
        approval_id = package.review_id
        store = LocalApprovalStore(args.approval_store_dir)
        batch = ApprovalBatch(
            approval_id=approval_id,
            created_at=package.created_at,
            factor_id="feature_promotion",
            factor_type="feature_set",
            rebalance_date="",
            portfolio_method="feature_promotion",
            orders=[],
            approval_type=ApprovalType.feature_promotion_review,
            status="pending",
            metadata={
                "feature_promotion_policy_path": args.feature_promotion_policy_path,
                "feature_promotion_review_package_path": paths["feature_promotion_review_package_path"],
                "feature_promotion_summary": package.summary,
                "approved_feature_count": 0,
                "blocked_feature_count": package.summary.get("blocked_feature_count", 0),
                "weak_pit_feature_count": package.summary.get("weak_pit_feature_count", 0),
            },
        )
        store.save_batch(batch)
        paths["feature_promotion_approval_path"] = str(Path(args.approval_store_dir) / "approvals" / f"{approval_id}.json")
    return {"status": "success", "review_id": package.review_id, "approval_id": approval_id, "summary": package.summary, "paths": paths}


def _apply_approved(args: argparse.Namespace) -> dict:
    policy_payload = _load_policy_payload(args)
    review_package = load_json(args.feature_promotion_review_package_path)
    if args.approval_store_dir and args.approval_id:
        decisions, context = decisions_from_approval(
            approval_store_dir=args.approval_store_dir,
            approval_id=args.approval_id,
            review_package_path=args.feature_promotion_review_package_path,
        )
        review_package = context.get("review_package") or review_package
    elif args.feature_promotion_decisions_path:
        decisions = load_decisions(args.feature_promotion_decisions_path)
    else:
        if not review_package:
            raise ValueError("review package or decisions are required")
        decisions = default_decisions_from_review_package(review_package, reviewer=args.reviewer)
    decisions_path = Path(args.output_dir) / "feature_promotion_decisions.jsonl"
    write_decisions(decisions_path, decisions)
    allowlist, denylist, report = build_allow_deny_lists(policy=policy_payload, decisions=decisions, review_package=review_package)
    paths = write_application_artifacts(
        decisions=[item.to_dict() for item in decisions],
        allowlist=allowlist,
        denylist=denylist,
        report=report,
        output_dir=args.output_dir,
    )
    return {"status": "success", "summary": report, "paths": paths}


def _run_smoke(args: argparse.Namespace) -> dict:
    output_dir = Path(args.output_dir)
    feature_dir = output_dir / "features_v3"
    data_dir = Path(args.data_dir) if args.data_dir else output_dir / "sample_data"
    if not (feature_dir / "feature_set_manifest.json").exists():
        from data_pipeline.run_pipeline import main as run_pipeline_main

        run_pipeline_main(
            [
                "--sync",
                "--provider",
                "sample",
                "--data-dir",
                str(data_dir),
                "--validate",
                "--mode",
                "overwrite",
                "--index-codes",
                "000300.SH",
            ]
        )
        run_features_main(
            [
                "build",
                "--data-dir",
                str(data_dir),
                "--output-dir",
                str(feature_dir),
                "--feature-set-name",
                FEATURE_SET_V3,
            ]
        )
    args.feature_set_manifest_path = str(feature_dir / "feature_set_manifest.json")
    args.feature_family_readiness_path = str(feature_dir / "feature_family_readiness.json")
    args.feature_pit_alignment_report_path = str(feature_dir / "feature_pit_alignment_report.json")
    args.feature_build_warnings_path = str(feature_dir / "feature_build_warnings.jsonl")
    args.feature_coverage_report_path = str(feature_dir / "feature_coverage_report.json")
    init_args = vars(args).copy()
    init_args["command"] = "init-policy"
    policy_payload = _run(argparse.Namespace(**init_args))
    args.feature_promotion_policy_path = policy_payload["paths"]["feature_promotion_policy_path"]
    review_payload = _create_review(args)
    args.feature_promotion_review_package_path = review_payload["paths"]["feature_promotion_review_package_path"]
    apply_payload = _apply_approved(args)
    return {
        "status": "success",
        "policy_hash": policy_payload["policy_hash"],
        "review_id": review_payload["review_id"],
        "evidence_count": review_payload["summary"]["evidence_count"],
        "allowlist_count": apply_payload["summary"]["allowlist_count"],
        "denylist_count": apply_payload["summary"]["denylist_count"],
        "paths": policy_payload["paths"] | review_payload["paths"] | apply_payload["paths"],
    }


def _load_manifest(args: argparse.Namespace) -> dict:
    if args.feature_set_manifest_path:
        payload = load_json(args.feature_set_manifest_path)
        if payload:
            return payload
    return build_feature_set_manifest(FEATURE_SET_V3).to_dict()


def _load_policy_payload(args: argparse.Namespace) -> dict:
    payload = load_json(args.feature_promotion_policy_path)
    if payload:
        return payload
    manifest = _load_manifest(args)
    policy = create_default_policy(manifest).to_dict()
    policy["policy_hash"] = policy_hash(policy)
    return policy


if __name__ == "__main__":
    raise SystemExit(main())

"""CLI for factor certification."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from backtest.io import select_factor_id
from factor_store import LocalFactorStore

from .decision import make_certification_decision
from .models import FactorCertificationPackage
from .policy import load_certification_policy
from .report import write_factor_certification_artifacts
from .scorecard import build_factor_certification_scorecard


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Certify local factor research artifacts.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["init-policy", "scorecard", "decide", "run", "apply-status", "report", "smoke"]:
        cmd = sub.add_parser(name)
        _add_args(cmd)
    return parser


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--factor-store-dir")
    parser.add_argument("--factor-id")
    parser.add_argument("--factor-type", choices=["single", "composite", "any"], default="any")
    parser.add_argument("--latest-approved", action="store_true")
    parser.add_argument("--latest-production-candidate", action="store_true")
    parser.add_argument("--model-registry-dir")
    parser.add_argument("--model-version-id")
    parser.add_argument("--policy-path")
    parser.add_argument("--policy-profile", default="sample_lenient_certification")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--certification-queue-path")
    parser.add_argument("--queue-id")
    parser.add_argument("--max-queue-items", type=int, default=0)
    parser.add_argument("--output-root-dir")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--validation-lab-report-path")
    parser.add_argument("--multiple-testing-report-path")
    parser.add_argument("--overfit-risk-report-path")
    parser.add_argument("--placebo-test-report-path")
    parser.add_argument("--regime-validation-report-path")
    parser.add_argument("--sensitivity-report-path")
    parser.add_argument("--stress-backtest-report-path")
    parser.add_argument("--factor-validation-summary-path")
    parser.add_argument("--alpha-factory-report-path")
    parser.add_argument("--feature-set-manifest-path")
    parser.add_argument("--data-version-manifest-path")
    parser.add_argument("--research-data-freeze-path")
    parser.add_argument("--pit-validation-report-path")
    parser.add_argument("--leakage-audit-report-path")
    parser.add_argument("--corporate-action-report-path")
    parser.add_argument("--settlement-report-path")
    parser.add_argument("--risk-control-report-path")
    parser.add_argument("--eod-reconciliation-report-path")
    parser.add_argument("--apply-status", action="store_true")
    parser.add_argument("--new-status")
    parser.add_argument("--fail-on-rejected", action="store_true")
    parser.add_argument("--pretty", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        payload = _run(args)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2 if getattr(args, "pretty", False) else None))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    if args.fail_on_rejected and payload.get("certification_status") in {"rejected", "insufficient_data"}:
        return 1
    return 0


def _run(args: argparse.Namespace) -> dict[str, Any]:
    if args.certification_queue_path:
        return _run_queue(args)
    return _run_single(args)


def _run_single(args: argparse.Namespace) -> dict[str, Any]:
    if not args.factor_store_dir:
        raise ValueError("--factor-store-dir is required")
    store = LocalFactorStore(args.factor_store_dir)
    factor_id = _select_factor(args, store)
    policy = load_certification_policy(args.policy_path, args.policy_profile)
    output_dir = Path(args.output_dir)
    if args.command == "init-policy":
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "factor_certification_policy.json"
        path.write_text(json.dumps(policy.to_dict(), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return {"policy_path": str(path), "policy": policy.to_dict()}
    artifact_paths = _artifact_paths(args)
    scorecard = build_factor_certification_scorecard(factor_id, policy, artifact_paths)
    decision = make_certification_decision(scorecard, policy)
    package = FactorCertificationPackage(
        factor_id=factor_id,
        policy=policy.to_dict(),
        scorecard=scorecard.to_dict(),
        decision=decision.to_dict(),
        source_artifacts={key: value for key, value in artifact_paths.items() if value},
    )
    paths = write_factor_certification_artifacts(output_dir, policy, scorecard, decision, package)
    if args.apply_status or args.command == "apply-status":
        new_status = args.new_status or _status_to_factor_status(decision.status)
        store.update_factor_status(
            factor_id,
            new_status,
            reason=f"certification:{decision.status}",
            promotion_decision={"certification_decision": decision.to_dict()},
        )
    return {
        "factor_id": factor_id,
        "certification_status": decision.status,
        "certification_passed": decision.passed,
        "certification_policy_profile": policy.profile_name,
        "certification_blocker_count": int(scorecard.summary.get("blocker_count", 0) or 0),
        "certification_required_remediation_count": len(decision.required_remediation),
        "scorecard_summary": scorecard.summary,
        "decision": decision.to_dict(),
        "paths": paths,
    }


def _run_queue(args: argparse.Namespace) -> dict[str, Any]:
    queue_path = Path(args.certification_queue_path)
    if not queue_path.exists():
        raise FileNotFoundError(f"certification queue not found: {queue_path}")
    output_root = Path(args.output_root_dir or args.output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    rows = _read_jsonl(queue_path)
    selected = [row for row in rows if not args.queue_id or str(row.get("queue_id")) == args.queue_id]
    if args.max_queue_items and args.max_queue_items > 0:
        selected = selected[: args.max_queue_items]
    if args.dry_run:
        path = output_root / "factor_certification_queue_dry_run.json"
        payload = {
            "status": "success",
            "dry_run": True,
            "queue_path": str(queue_path),
            "queue_item_count": len(rows),
            "selected_count": len(selected),
            "queue_items": selected,
            "paths": {"factor_certification_queue_dry_run_path": str(path)},
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return payload
    results: list[dict[str, Any]] = []
    for row in selected:
        item_args = argparse.Namespace(**vars(args))
        item_args.certification_queue_path = None
        item_args.queue_id = None
        item_args.max_queue_items = 0
        item_args.output_root_dir = None
        item_args.dry_run = False
        item_args.factor_id = str(row.get("factor_id") or args.factor_id or "")
        item_args.factor_store_dir = str(row.get("factor_store_dir") or args.factor_store_dir or "")
        item_args.output_dir = str(output_root / str(row.get("queue_id") or row.get("factor_id") or "queue_item"))
        item_args.policy_profile = str(row.get("certification_policy_profile") or args.policy_profile)
        artifacts = (row.get("metadata") or {}).get("validation_artifacts", {}) if isinstance(row.get("metadata"), dict) else {}
        for attr in [
            "validation_lab_report_path",
            "multiple_testing_report_path",
            "overfit_risk_report_path",
            "placebo_test_report_path",
            "regime_validation_report_path",
            "sensitivity_report_path",
            "stress_backtest_report_path",
            "factor_validation_summary_path",
        ]:
            if artifacts.get(attr):
                setattr(item_args, attr, artifacts.get(attr))
        result = _run_single(item_args)
        results.append({"queue_id": row.get("queue_id"), "factor_id": row.get("factor_id"), **result})
    results_path = output_root / "factor_certification_queue_results.jsonl"
    results_path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False, sort_keys=True) for item in results) + ("\n" if results else ""),
        encoding="utf-8",
    )
    return {
        "status": "success",
        "queue_path": str(queue_path),
        "queue_item_count": len(rows),
        "selected_count": len(selected),
        "completed_count": len(results),
        "paths": {"factor_certification_queue_results_path": str(results_path)},
        "results": results,
    }


def _select_factor(args: argparse.Namespace, store: LocalFactorStore) -> str:
    if args.latest_production_candidate:
        record = store.load_latest_factor(status="production_candidate", factor_type=None if args.factor_type == "any" else args.factor_type)
        if record is not None:
            return record.factor_id
    return select_factor_id(
        store,
        args.factor_id,
        latest_approved=args.latest_approved or not args.factor_id,
        factor_type=args.factor_type,
    )


def _artifact_paths(args: argparse.Namespace) -> dict[str, str | None]:
    names = [
        "validation_lab_report_path",
        "multiple_testing_report_path",
        "overfit_risk_report_path",
        "placebo_test_report_path",
        "regime_validation_report_path",
        "sensitivity_report_path",
        "stress_backtest_report_path",
        "factor_validation_summary_path",
        "alpha_factory_report_path",
        "feature_set_manifest_path",
        "data_version_manifest_path",
        "research_data_freeze_path",
        "pit_validation_report_path",
        "leakage_audit_report_path",
        "corporate_action_report_path",
        "settlement_report_path",
        "risk_control_report_path",
        "eod_reconciliation_report_path",
    ]
    return {name: getattr(args, name, None) for name in names}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _status_to_factor_status(status: str) -> str:
    if status == "certified":
        return "certified"
    if status == "conditional":
        return "conditional_candidate"
    if status == "rejected":
        return "rejected"
    return "needs_review"


if __name__ == "__main__":
    raise SystemExit(main())

"""CLI for factor lifecycle review and activation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from approval import ApprovalBatch, LocalApprovalStore
from factor_store import LocalFactorStore
from model_core.data_loader import AShareDataLoader
from model_registry import LocalModelRegistry, ModelKind, ModelLifecycleStatus, build_model_lineage_graph, write_lineage_graph, write_model_registry_report

from .decision import make_lifecycle_decision
from .health import evaluate_factor_health
from .policy import load_lifecycle_policy
from .report import write_lifecycle_report
from .review import build_model_review_package
from .models import LifecycleEvaluationResult


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate and apply local model lifecycle decisions.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["evaluate", "create-review", "propose-activation", "apply-approved", "evaluate-active", "quarantine", "pause", "rollback"]:
        cmd = sub.add_parser(name)
        cmd.add_argument("--data-dir")
        cmd.add_argument("--factor-store-dir", required=True)
        cmd.add_argument("--registry-dir", required=True)
        cmd.add_argument("--approval-store-dir")
        cmd.add_argument("--output-dir", required=True)
        cmd.add_argument("--factor-id")
        cmd.add_argument("--model-version-id")
        cmd.add_argument("--latest-production-candidate", action="store_true")
        cmd.add_argument("--latest-active", action="store_true")
        cmd.add_argument("--as-of-date", default="20240104")
        cmd.add_argument("--policy-path")
        cmd.add_argument("--artifact-dir", action="append", default=[])
        cmd.add_argument("--artifact-catalog-path", action="append", default=[])
        cmd.add_argument("--promotion-decision-path")
        cmd.add_argument("--backtest-result-path")
        cmd.add_argument("--risk-report-path")
        cmd.add_argument("--capacity-report-path")
        cmd.add_argument("--execution-quality-path")
        cmd.add_argument("--broker-reconciliation-path")
        cmd.add_argument("--monitoring-report-path")
        cmd.add_argument("--artifact-validation-report-path")
        cmd.add_argument("--data-source-smoke-report-path")
        cmd.add_argument("--release-gate-report-path")
        cmd.add_argument("--pit-validation-report-path")
        cmd.add_argument("--survivorship-report-path")
        cmd.add_argument("--leakage-audit-report-path")
        cmd.add_argument("--truncation-consistency-report-path")
        cmd.add_argument("--corporate-action-report-path")
        cmd.add_argument("--total-return-report-path")
        cmd.add_argument("--adjustment-reconciliation-path")
        cmd.add_argument("--corporate-action-validation-path")
        cmd.add_argument("--settlement-report-path")
        cmd.add_argument("--account-reconciliation-report-path")
        cmd.add_argument("--account-performance-report-path")
        cmd.add_argument("--cash-buckets-path")
        cmd.add_argument("--realized-pnl-path")
        cmd.add_argument("--validation-lab-report-path")
        cmd.add_argument("--factor-validation-summary-path")
        cmd.add_argument("--multiple-testing-report-path")
        cmd.add_argument("--overfit-risk-report-path")
        cmd.add_argument("--placebo-test-report-path")
        cmd.add_argument("--regime-validation-report-path")
        cmd.add_argument("--sensitivity-report-path")
        cmd.add_argument("--stress-backtest-report-path")
        cmd.add_argument("--factor-certification-decision-path")
        cmd.add_argument("--factor-certification-scorecard-path")
        cmd.add_argument("--portfolio-lab-report-path")
        cmd.add_argument("--portfolio-robustness-report-path")
        cmd.add_argument("--portfolio-certification-decision-path")
        cmd.add_argument("--portfolio-certification-scorecard-path")
        cmd.add_argument("--certified-portfolio-policy-path")
        cmd.add_argument("--optimizer-policy-model-version-id")
        cmd.add_argument("--create-review-package", action="store_true")
        cmd.add_argument("--propose-activation", action="store_true")
        cmd.add_argument("--require-approval", action="store_true")
        cmd.add_argument("--approval-id")
        cmd.add_argument("--actor", default="local_operator")
        cmd.add_argument("--reason")
        cmd.add_argument("--explicit-override", action="store_true")
        cmd.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        payload = _run(args)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2 if getattr(args, "pretty", False) else None))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def _run(args: argparse.Namespace) -> dict:
    registry = LocalModelRegistry(args.registry_dir)
    store = LocalFactorStore(args.factor_store_dir)
    if args.command == "apply-approved":
        return _apply_approved(args, registry, store)
    if args.command in {"pause", "quarantine"}:
        model = _select_model(args, registry, store)
        updated = registry.pause(model.model_version_id, args.reason, args.actor) if args.command == "pause" else registry.quarantine(model.model_version_id, args.reason, args.actor)
        registry.sync_factor_store_status(store, updated.model_version_id)
        write_model_registry_report(registry)
        return {"model_version": updated.to_dict()}
    if args.command == "rollback":
        model, deployment = registry.rollback(actor=args.actor, reason=args.reason, explicit_override=args.explicit_override)
        registry.sync_factor_store_status(store, model.model_version_id)
        write_model_registry_report(registry)
        return {"model_version": model.to_dict(), "deployment": deployment.to_dict()}
    model = _select_model(args, registry, store, register_if_missing=args.command in {"propose-activation", "create-review"})
    factor = _select_factor(store, model.factor_id if model else args.factor_id)
    evaluation, review_package, paths = _evaluate(args, registry, store, factor, model)
    payload = evaluation.to_dict() | {"paths": paths}
    if args.command == "propose-activation" or args.propose_activation:
        if args.require_approval:
            approval_id = _create_model_approval(args, factor, model, evaluation, paths)
            payload["approval_id"] = approval_id
            payload["approval_status"] = "pending"
        else:
            payload["approval_id"] = None
    return payload


def _evaluate(args, registry, store, factor, model):
    policy = load_lifecycle_policy(args.policy_path)
    loader = AShareDataLoader(data_dir=args.data_dir, device="cpu").load_data() if args.data_dir else None
    if loader is None:
        raise ValueError("--data-dir is required for lifecycle evaluation")
    artifact_paths = {
        "promotion_decision": args.promotion_decision_path,
        "backtest_result": args.backtest_result_path,
        "risk_report": args.risk_report_path,
        "capacity_report": args.capacity_report_path,
        "execution_quality": args.execution_quality_path,
        "broker_reconciliation": args.broker_reconciliation_path,
        "monitoring_report": args.monitoring_report_path,
        "artifact_validation_report": args.artifact_validation_report_path,
        "data_source_smoke_report": args.data_source_smoke_report_path,
        "release_gate_report": args.release_gate_report_path,
        "pit_validation_report": args.pit_validation_report_path,
        "survivorship_report": args.survivorship_report_path,
        "leakage_audit_report": args.leakage_audit_report_path,
        "truncation_consistency_report": args.truncation_consistency_report_path,
        "corporate_action_report": args.corporate_action_report_path,
        "total_return_report": args.total_return_report_path,
        "adjustment_reconciliation": args.adjustment_reconciliation_path,
        "corporate_action_validation": args.corporate_action_validation_path,
        "settlement_report": args.settlement_report_path,
        "account_reconciliation_report": args.account_reconciliation_report_path,
        "account_performance_report": args.account_performance_report_path,
        "cash_buckets": args.cash_buckets_path,
        "realized_pnl": args.realized_pnl_path,
        "validation_lab_report": args.validation_lab_report_path,
        "factor_validation_summary": args.factor_validation_summary_path,
        "multiple_testing_report": args.multiple_testing_report_path,
        "overfit_risk_report": args.overfit_risk_report_path,
        "placebo_test_report": args.placebo_test_report_path,
        "regime_validation_report": args.regime_validation_report_path,
        "sensitivity_report": args.sensitivity_report_path,
        "stress_backtest_report": args.stress_backtest_report_path,
        "factor_certification_decision": args.factor_certification_decision_path,
        "factor_certification_scorecard": args.factor_certification_scorecard_path,
        "portfolio_lab_report": args.portfolio_lab_report_path,
        "portfolio_robustness_report": args.portfolio_robustness_report_path,
        "portfolio_certification_decision": args.portfolio_certification_decision_path,
        "portfolio_certification_scorecard": args.portfolio_certification_scorecard_path,
        "certified_portfolio_policy": args.certified_portfolio_policy_path,
        "optimizer_policy_model_version_id": args.optimizer_policy_model_version_id,
    }
    metrics, checks = evaluate_factor_health(loader, store, factor.factor_id, args.as_of_date, policy, artifact_paths)
    current_status = model.lifecycle_status if model else factor.status
    decision = make_lifecycle_decision(factor.factor_id, model.model_version_id if model else None, checks, policy, current_status)
    graph = build_model_lineage_graph(registry, store, args.artifact_catalog_path, args.artifact_dir)
    lineage_path = write_lineage_graph(registry, graph)
    review_package = None
    if args.create_review_package or args.command in {"create-review", "propose-activation", "evaluate-active"}:
        review_package = build_model_review_package(
            factor,
            model,
            [check.to_dict() for check in checks],
            decision,
            promotion_decision=_read_json(args.promotion_decision_path),
            lineage_graph_path=str(lineage_path),
        )
    evaluation = LifecycleEvaluationResult(
        factor_id=factor.factor_id,
        model_version_id=model.model_version_id if model else None,
        as_of_date=args.as_of_date,
        metrics=metrics,
        checks=checks,
        decision=decision,
        policy=policy.to_dict(),
    )
    paths = write_lifecycle_report(evaluation, args.output_dir, review_package=review_package, lineage_graph_path=str(lineage_path))
    paths["model_lineage_graph_path"] = str(lineage_path)
    write_model_registry_report(registry)
    return evaluation, review_package, paths


def _apply_approved(args, registry: LocalModelRegistry, store: LocalFactorStore) -> dict:
    if not args.approval_store_dir or not args.approval_id:
        raise ValueError("apply-approved requires --approval-store-dir and --approval-id")
    approval = LocalApprovalStore(args.approval_store_dir).load_batch(args.approval_id)
    if approval.status != "approved":
        raise ValueError(f"model lifecycle approval must be approved: {approval.approval_id} is {approval.status}")
    if approval.approval_type != "model_lifecycle":
        raise ValueError(f"approval is not model_lifecycle: {approval.approval_type}")
    model_version_id = approval.model_version_id or args.model_version_id
    if not model_version_id:
        raise ValueError("approval does not contain model_version_id")
    model, deployment = registry.activate(
        model_version_id,
        approval_id=approval.approval_id,
        actor=args.actor,
        reason=args.reason or "approved model lifecycle activation",
        explicit_override=args.explicit_override,
    )
    registry.sync_factor_store_status(store, model.model_version_id)
    report_json, report_md = write_model_registry_report(registry)
    return {
        "model_version": model.to_dict(),
        "deployment": deployment.to_dict(),
        "paths": {"model_registry_report_path": str(report_json), "model_registry_report_md_path": str(report_md)},
    }


def _create_model_approval(args, factor, model, evaluation, paths: dict[str, str]) -> str:
    root = Path(args.approval_store_dir) if args.approval_store_dir else Path(args.output_dir).parent / "approvals"
    approval_id = f"model_{model.model_version_id}_{_safe_time(evaluation.as_of_date)}"
    batch = ApprovalBatch(
        approval_id=approval_id,
        created_at=_utc_now(),
        factor_id=factor.factor_id,
        factor_type=factor.factor_type or "composite",
        rebalance_date=evaluation.as_of_date,
        portfolio_method="model_lifecycle",
        orders=[],
        approval_type="model_lifecycle",
        model_version_id=model.model_version_id,
        model_lifecycle_action="activate",
        model_review_package_path=paths.get("model_review_package_path"),
        lifecycle_summary=evaluation.decision.to_dict(),
        metadata={"recommended_action": evaluation.decision.recommended_action},
    )
    LocalApprovalStore(root).save_batch(batch)
    return approval_id


def _select_model(args, registry: LocalModelRegistry, store: LocalFactorStore, register_if_missing: bool = False):
    if args.model_version_id:
        model = registry.get_model_version(args.model_version_id)
        if model is None:
            raise FileNotFoundError(f"model version not found: {args.model_version_id}")
        return model
    if args.latest_active or args.command == "evaluate-active":
        model = registry.latest_active()
        if model is None:
            raise ValueError("no active model found")
        return model
    if args.latest_production_candidate:
        model = registry.latest_by_status(ModelLifecycleStatus.production_candidate)
        if model is not None:
            return model
    factor = _select_factor(store, args.factor_id)
    if register_if_missing:
        return registry.register_factor_record(factor, model_kind=ModelKind.composite_factor)
    models = registry.find_by_factor_id(factor.factor_id)
    return models[-1] if models else None


def _select_factor(store: LocalFactorStore, factor_id: str | None):
    factors = store.load_factors()
    if factor_id:
        factor = next((record for record in factors if record.factor_id == factor_id), None)
    else:
        factor = (
            store.load_latest_factor(status="production_candidate", factor_type="composite")
            or store.load_latest_factor(status="production_candidate")
            or store.load_latest_factor(factor_type="composite")
            or store.load_latest_factor()
        )
    if factor is None:
        raise FileNotFoundError("factor not found for lifecycle evaluation")
    return factor


def _read_json(path: str | None) -> dict:
    if not path or not Path(path).exists():
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _utc_now() -> str:
    from datetime import datetime

    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _safe_time(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in value).strip("_")


if __name__ == "__main__":
    raise SystemExit(main())

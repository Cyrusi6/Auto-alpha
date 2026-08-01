"""CLI for A-share target and paper order generation."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from approval import ApprovalBatch, ApprovalOrder, LocalApprovalStore
from backtest import TargetPosition, build_long_only_targets, describe_factor, factor_values_to_matrix, select_factor_id
from capacity_model import CapacityConfig, build_capacity_report, write_capacity_report
from execution import PaperBroker, export_fills_jsonl, export_orders_csv, export_orders_jsonl
from execution_plan import (
    ExecutionPlanConfig,
    ExecutionPlanResult,
    build_execution_schedule,
    build_parent_orders_from_target_orders,
    simulate_child_orders,
    write_execution_plan_report,
)
from factor_store import LocalFactorStore
from data_lake import validate_research_input
from model_core.data_loader import AShareDataLoader
from portfolio_optimizer import (
    OptimizationConfig,
    PortfolioOptimizer,
    load_portfolio_policy,
    portfolio_policy_from_payload,
    validate_certified_portfolio_policy,
)
from risk_controls import evaluate_order_records
from risk_controls.overrides import create_override_approval
from risk_model import (
    build_barra_like_risk_model,
    benchmark_weights_from_index_members,
    build_risk_report,
    estimate_return_covariance,
    write_risk_model_report,
    write_risk_report,
)

from .config import AShareStrategyConfig
from .portfolio import StrategyTargetBook
from .risk import AShareRiskEngine


class AShareStrategyRunner:
    def __init__(
        self,
        data_dir,
        factor_store_dir,
        output_dir,
        top_n: int = 20,
        max_weight: float = 0.10,
        factor_id: str | None = None,
        rebalance_date: str | None = None,
        portfolio_value: float = 1_000_000.0,
        latest_approved: bool = False,
        factor_type: str = "any",
        portfolio_method: str = "equal_weight",
        portfolio_policy_path: str | Path | None = None,
        require_certified_portfolio_policy: bool = False,
        portfolio_certification_decision_path: str | Path | None = None,
        model_registry_dir: str | Path | None = None,
        active_optimizer_policy: bool = False,
        index_code: str = "000300.SH",
        risk_aversion: float = 1.0,
        turnover_penalty: float = 0.1,
        max_turnover: float = 1.0,
        max_industry_active_weight: float = 0.20,
        max_tracking_error: float = 1.0,
        use_factor_risk_model: bool = False,
        risk_model_lookback: int | None = None,
        risk_model_shrinkage: float = 0.1,
        max_style_exposure: float | None = None,
        max_active_style_exposure: float | None = None,
        capacity_aware: bool = False,
        execution_plan_dir: str | Path | None = None,
        max_participation: float = 0.10,
        execution_buckets: str | tuple[str, ...] = "open",
        propose_parent_orders: bool = False,
        export_child_orders: bool = False,
        propose_only: bool = False,
        require_approval: bool = False,
        approval_store_dir: str | Path | None = None,
        point_in_time: bool = False,
        feature_cutoff_mode: str = "same_day_after_close",
        min_listing_days: int = 0,
        exclude_st: bool = False,
        corporate_action_aware: bool = False,
        target_return_mode: str = "adjusted_close",
        corporate_action_dir: str | Path | None = None,
        corporate_action_cash_field: str = "cash_div",
        settlement_aware: bool = False,
        settlement_profile: str = "cn_ashare_paper_default",
        settlement_dir: str | Path | None = None,
        paper_account_dir: str | Path | None = None,
        enforce_available_cash: bool = False,
        enforce_available_shares: bool = False,
        risk_controls: bool = False,
        risk_policy_path: str | Path | None = None,
        risk_control_state_dir: str | Path | None = None,
        risk_control_output_dir: str | Path | None = None,
        risk_policy_profile: str = "cn_ashare_paper_default",
        risk_fail_on_breach: bool = False,
        risk_allow_clipping: bool = False,
        create_risk_override_approval: bool = False,
        risk_override_approval_store_dir: str | Path | None = None,
        production_run_id: str | None = None,
        run_mode: str | None = None,
        orchestrator_artifact_dir: str | Path | None = None,
        data_freeze_dir: str | Path | None = None,
        data_freeze_id: str | None = None,
        data_version_manifest_path: str | Path | None = None,
        require_data_freeze: bool = False,
        freeze_validation_report_path: str | Path | None = None,
    ):
        self.source_data_dir = Path(data_dir)
        self.data_freeze_dir = Path(data_freeze_dir) if data_freeze_dir is not None else None
        self.data_freeze_id = data_freeze_id
        self.data_version_manifest_path = Path(data_version_manifest_path) if data_version_manifest_path is not None else None
        self.require_data_freeze = bool(require_data_freeze)
        self.freeze_validation_report_path = Path(freeze_validation_report_path) if freeze_validation_report_path is not None else None
        self.freeze_validation = validate_research_input(
            data_dir=data_dir,
            data_freeze_dir=data_freeze_dir,
            require_freeze=require_data_freeze,
        )
        if self.freeze_validation.error_count > 0:
            raise RuntimeError(f"data freeze validation failed: {self.freeze_validation.status}")
        self.data_dir = self.data_freeze_dir / "data" if self.data_freeze_dir is not None else Path(data_dir)
        self.factor_store_dir = Path(factor_store_dir)
        self.output_dir = Path(output_dir)
        self.top_n = int(top_n)
        self.max_weight = float(max_weight)
        self.factor_id = factor_id
        self.rebalance_date = rebalance_date
        self.portfolio_value = float(portfolio_value)
        self.latest_approved = bool(latest_approved)
        self.factor_type = factor_type
        self.portfolio_method = portfolio_method
        self.portfolio_policy_path = Path(portfolio_policy_path) if portfolio_policy_path is not None else None
        self.require_certified_portfolio_policy = bool(require_certified_portfolio_policy)
        self.portfolio_certification_decision_path = (
            Path(portfolio_certification_decision_path) if portfolio_certification_decision_path is not None else None
        )
        self.model_registry_dir = Path(model_registry_dir) if model_registry_dir is not None else None
        self.active_optimizer_policy = bool(active_optimizer_policy)
        self.index_code = index_code
        self.risk_aversion = float(risk_aversion)
        self.turnover_penalty = float(turnover_penalty)
        self.max_turnover = float(max_turnover)
        self.max_industry_active_weight = float(max_industry_active_weight)
        self.max_tracking_error = float(max_tracking_error)
        self.use_factor_risk_model = bool(use_factor_risk_model)
        self.risk_model_lookback = risk_model_lookback
        self.risk_model_shrinkage = float(risk_model_shrinkage)
        self.max_style_exposure = max_style_exposure
        self.max_active_style_exposure = max_active_style_exposure
        self.capacity_aware = bool(capacity_aware)
        self.execution_plan_dir = Path(execution_plan_dir) if execution_plan_dir is not None else self.output_dir / "plan"
        self.max_participation = float(max_participation)
        self.execution_buckets = _parse_buckets(execution_buckets)
        self.propose_parent_orders = bool(propose_parent_orders)
        self.export_child_orders = bool(export_child_orders)
        self.propose_only = bool(propose_only)
        self.require_approval = bool(require_approval)
        self.approval_store_dir = Path(approval_store_dir) if approval_store_dir is not None else None
        self.point_in_time = bool(point_in_time)
        self.feature_cutoff_mode = feature_cutoff_mode
        self.min_listing_days = int(min_listing_days)
        self.exclude_st = bool(exclude_st)
        self.corporate_action_aware = bool(corporate_action_aware)
        self.target_return_mode = target_return_mode
        self.corporate_action_dir = Path(corporate_action_dir) if corporate_action_dir is not None else None
        self.corporate_action_cash_field = corporate_action_cash_field
        self.settlement_aware = bool(settlement_aware)
        self.settlement_profile = settlement_profile
        self.settlement_dir = Path(settlement_dir) if settlement_dir is not None else None
        self.paper_account_dir = Path(paper_account_dir) if paper_account_dir is not None else None
        self.enforce_available_cash = bool(enforce_available_cash)
        self.enforce_available_shares = bool(enforce_available_shares)
        self.risk_controls = bool(risk_controls)
        self.risk_policy_path = Path(risk_policy_path) if risk_policy_path is not None else None
        self.risk_control_state_dir = Path(risk_control_state_dir) if risk_control_state_dir is not None else self.output_dir / "risk_state"
        self.risk_control_output_dir = Path(risk_control_output_dir) if risk_control_output_dir is not None else self.output_dir / "risk_controls"
        self.risk_policy_profile = risk_policy_profile
        self.risk_fail_on_breach = bool(risk_fail_on_breach)
        self.risk_allow_clipping = bool(risk_allow_clipping)
        self.create_risk_override_approval = bool(create_risk_override_approval)
        self.risk_override_approval_store_dir = Path(risk_override_approval_store_dir) if risk_override_approval_store_dir is not None else self.approval_store_dir
        self.production_run_id = production_run_id
        self.run_mode = run_mode
        self.orchestrator_artifact_dir = Path(orchestrator_artifact_dir) if orchestrator_artifact_dir is not None else None
        self.loader: AShareDataLoader | None = None
        self.selected_factor_id: str | None = None
        self.selected_factor_meta: dict[str, object] = {}
        self.optimization_summary: dict[str, object] | None = None
        self.risk_report = None
        self.portfolio_policy = None
        self.portfolio_policy_gate: dict[str, object] = {}
        self._apply_portfolio_policy()

    def _apply_portfolio_policy(self) -> None:
        policy = None
        if self.active_optimizer_policy:
            if self.model_registry_dir is None:
                raise ValueError("model_registry_dir is required when active_optimizer_policy is enabled")
            from model_registry import LocalModelRegistry

            active = LocalModelRegistry(self.model_registry_dir).latest_active_optimizer_policy()
            if active is None:
                if self.require_certified_portfolio_policy:
                    raise ValueError("active optimizer policy is required but not found")
            else:
                source = active.source_artifacts.get("certified_portfolio_policy_path") or active.source_artifacts.get("selected_portfolio_policy_path")
                if source and Path(source).exists():
                    self.portfolio_policy_path = Path(source)
                    policy = load_portfolio_policy(source)
                else:
                    policy = portfolio_policy_from_payload(active.metadata.get("portfolio_policy", active.metadata))
        elif self.portfolio_policy_path is not None:
            policy = load_portfolio_policy(self.portfolio_policy_path)

        if policy is not None:
            self.portfolio_method = policy.portfolio_method
            self.index_code = policy.index_code
            self.top_n = policy.top_n
            self.max_weight = policy.max_weight
            self.risk_aversion = policy.risk_aversion
            self.turnover_penalty = policy.turnover_penalty
            self.max_turnover = policy.max_turnover
            self.max_industry_active_weight = policy.max_industry_active_weight
            self.max_tracking_error = policy.max_tracking_error
            self.use_factor_risk_model = policy.use_factor_risk_model
            self.risk_model_lookback = policy.risk_model_lookback
            self.risk_model_shrinkage = policy.risk_model_shrinkage
            self.max_style_exposure = policy.max_style_exposure
            self.max_active_style_exposure = policy.max_active_style_exposure

        gate = validate_certified_portfolio_policy(
            self.portfolio_policy_path,
            self.portfolio_certification_decision_path,
            require=self.require_certified_portfolio_policy,
        ).to_dict()
        if policy is not None and self.portfolio_policy_path is None and policy.certification_status in {"certified", "conditional"}:
            gate.update({"certified": True, "status": policy.certification_status, "reasons": []})
        if self.require_certified_portfolio_policy and gate.get("reasons"):
            raise ValueError(f"portfolio policy certification gate failed: {gate.get('reasons')}")
        self.portfolio_policy = policy
        self.portfolio_policy_gate = gate

    def build_target_book(self) -> StrategyTargetBook:
        if self.portfolio_method not in {"equal_weight", "risk_aware"}:
            raise ValueError("portfolio_method must be equal_weight or risk_aware")
        self.loader = AShareDataLoader(
            data_dir=self.data_dir,
            device="cpu",
            point_in_time=self.point_in_time,
            feature_cutoff_mode=self.feature_cutoff_mode,
            min_listing_days=self.min_listing_days,
            exclude_st=self.exclude_st,
            corporate_action_aware=self.corporate_action_aware,
            corporate_action_dir=self.corporate_action_dir,
            target_return_mode=self.target_return_mode,
            corporate_action_cash_field=self.corporate_action_cash_field,
        ).load_data()
        store = LocalFactorStore(self.factor_store_dir)
        self.selected_factor_id = select_factor_id(
            store,
            self.factor_id,
            latest_approved=self.latest_approved,
            factor_type=self.factor_type,
        )
        self.selected_factor_meta = describe_factor(store, self.selected_factor_id)
        records = store.load_factor_values(self.selected_factor_id)
        factor_matrix = factor_values_to_matrix(records, self.loader.ts_codes, self.loader.trade_dates)
        trade_date = self.rebalance_date or self.loader.trade_dates[-1]
        if trade_date not in self.loader.trade_dates:
            raise ValueError(f"rebalance_date is not in loaded trade dates: {trade_date}")
        date_idx = self.loader.trade_dates.index(trade_date)
        if self.portfolio_method == "risk_aware":
            benchmark = benchmark_weights_from_index_members(self.loader, self.index_code, trade_date)
            covariance = estimate_return_covariance(self.loader, as_of_index=date_idx)
            factor_risk_model = (
                build_barra_like_risk_model(self.loader, lookback=self.risk_model_lookback, shrinkage=self.risk_model_shrinkage, as_of_index=date_idx)
                if self.use_factor_risk_model else None
            )
            config = OptimizationConfig(
                risk_aversion=self.risk_aversion,
                turnover_penalty=self.turnover_penalty,
                max_weight=self.max_weight,
                max_names=self.top_n,
                max_turnover=self.max_turnover,
                max_industry_active_weight=self.max_industry_active_weight,
                max_tracking_error=self.max_tracking_error,
                use_factor_risk_model=self.use_factor_risk_model,
                risk_model_lookback=self.risk_model_lookback,
                risk_model_shrinkage=self.risk_model_shrinkage,
                max_style_exposure=self.max_style_exposure,
                max_active_style_exposure=self.max_active_style_exposure,
            )
            opt_result = PortfolioOptimizer(config).optimize(
                factor_matrix[:, date_idx],
                current_weights=benchmark * 0.0,
                benchmark_weights=benchmark,
                covariance=covariance,
                loader=self.loader,
                factor_risk_model=factor_risk_model,
                date_index=date_idx if factor_risk_model is not None else None,
            )
            self.optimization_summary = opt_result.to_dict()
            weight_vector = factor_matrix[:, date_idx].clone() * 0.0
            for idx, ts_code in enumerate(self.loader.ts_codes):
                weight_vector[idx] = float(opt_result.weights.get(ts_code, 0.0))
            self.risk_report = build_risk_report(
                weight_vector,
                benchmark,
                self.loader,
                self.index_code,
                trade_date,
                factor_id=self.selected_factor_id,
                covariance=covariance,
                turnover=opt_result.turnover,
                factor_risk_model=factor_risk_model,
            )
            targets = []
            for idx, ts_code in enumerate(self.loader.ts_codes):
                weight = float(weight_vector[idx].item())
                if weight <= 1e-10:
                    continue
                benchmark_weight = float(benchmark[idx].item())
                targets.append(
                    TargetPosition(
                        trade_date=trade_date,
                        ts_code=ts_code,
                        target_weight=weight,
                        factor_value=float(factor_matrix[idx, date_idx].item()),
                        optimized_weight=weight,
                        benchmark_weight=benchmark_weight,
                        active_weight=weight - benchmark_weight,
                    )
                )
            return StrategyTargetBook(trade_date=trade_date, targets=targets)
        targets_by_date = build_long_only_targets(
            factor_matrix[:, date_idx : date_idx + 1],
            self.loader.ts_codes,
            [trade_date],
            top_n=self.top_n,
            max_weight=self.max_weight,
        )
        return StrategyTargetBook(trade_date=trade_date, targets=targets_by_date[0])

    def generate_orders(self) -> dict[str, object]:
        target_book = self.build_target_book()
        risk = AShareRiskEngine(max_weight=self.max_weight)
        ok, errors = risk.validate_targets(target_book.targets)
        if not ok:
            raise ValueError("; ".join(errors))
        orders = risk.filter_orders(target_book.to_orders(portfolio_value=self.portfolio_value))
        risk_control_summary: dict[str, object] = {}
        risk_control_paths: dict[str, str] = {}
        if self.risk_controls:
            orders, risk_control_summary, risk_control_paths = self._apply_risk_controls(orders, target_book.trade_date)

        self.output_dir.mkdir(parents=True, exist_ok=True)
        targets_csv = export_orders_csv(target_book.targets, self.output_dir / "target_positions.csv")
        targets_jsonl = export_orders_jsonl(target_book.targets, self.output_dir / "target_positions.jsonl")
        orders_csv = export_orders_csv(orders, self.output_dir / "orders.csv")
        orders_jsonl = export_orders_jsonl(orders, self.output_dir / "orders.jsonl")
        capacity_summary: dict[str, object] = {}
        execution_quality: dict[str, object] = {}
        execution_paths: dict[str, str] = {}
        parent_orders_payload: list[dict[str, object]] = []
        child_orders_payload: list[dict[str, object]] = []
        plan_result: ExecutionPlanResult | None = None
        capacity_report_payload: dict[str, object] = {}
        risk_report_path = None
        risk_report_md_path = None
        optimization_result_path = None
        if self.portfolio_method == "risk_aware":
            if self.risk_report is not None:
                risk_json, risk_md = write_risk_report(self.risk_report, self.output_dir)
                if self.use_factor_risk_model:
                    risk_json, risk_md = write_risk_model_report(self.risk_report, self.output_dir)
                risk_report_path = str(risk_json)
                risk_report_md_path = str(risk_md)
            if self.optimization_summary is not None:
                optimization_result_path = self.output_dir / "optimization_result.json"
                optimization_result_path.write_text(
                    json.dumps(
                        {
                            "factor_id": self.selected_factor_id,
                            "factor_type": self.selected_factor_meta.get("factor_type"),
                            "component_factor_ids": self.selected_factor_meta.get("component_factor_ids", []),
                            "portfolio_method": self.portfolio_method,
                            "index_code": self.index_code,
                            **self.optimization_summary,
                        },
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    ),
                    encoding="utf-8",
                )

        settlement_precheck: dict[str, object] = {}
        if self.settlement_aware and self.paper_account_dir is not None:
            from paper_account import LocalPaperAccount
            from settlement_engine.report import write_settlement_report

            prices, _volumes, _suspended, _limit_up_flags, _limit_down_flags = _market_context(self.loader, target_book.trade_date)
            account = LocalPaperAccount(self.paper_account_dir)
            settlement_precheck = account.precheck_orders(orders, prices=prices, profile=self.settlement_profile)
            if self.settlement_dir is not None:
                settlement_precheck["settlement_report_paths"] = write_settlement_report(
                    account.load_state(),
                    self.settlement_dir,
                    target_book.trade_date,
                    profile_name=self.settlement_profile,
                )

        approval_id = None
        approval_status = None
        fills = []
        fills_path = self.output_dir / "paper_fills.jsonl"
        if self.capacity_aware:
            plan_result, capacity_report_payload, execution_paths = self._build_execution_plan(orders, target_book.trade_date)
            parent_orders_payload = [order.to_dict() for order in plan_result.schedule.parent_orders]
            child_orders_payload = [order.to_dict() for order in plan_result.schedule.child_orders]
            capacity_summary = {
                "capacity_warning_count": plan_result.quality.rejected_child_orders
                + int(plan_result.capacity_report.get("portfolio", {}).get("capacity_warning_count", 0) if plan_result.capacity_report else 0),
                "estimated_impact_cost": plan_result.quality.estimated_impact_cost,
                "unfilled_order_value": plan_result.quality.unfilled_order_value,
                "execution_fill_rate": plan_result.quality.execution_fill_rate,
            }
            execution_quality = plan_result.quality.to_dict()

        if self.require_approval or self.propose_only:
            if self.approval_store_dir is None:
                raise ValueError("approval_store_dir is required when propose_only or require_approval is enabled")
            batch = ApprovalBatch(
                approval_id=_make_approval_id(self.selected_factor_id or "factor", target_book.trade_date),
                created_at=_utc_now(),
                factor_id=str(self.selected_factor_id),
                factor_type=str(self.selected_factor_meta.get("factor_type") or "unknown"),
                rebalance_date=target_book.trade_date,
                portfolio_method=self.portfolio_method,
                orders=[
                    ApprovalOrder(
                        trade_date=order.trade_date,
                        ts_code=order.ts_code,
                        side=order.side,
                        target_weight=order.target_weight,
                        order_value=order.order_value,
                        reason=order.reason,
                    )
                    for order in orders
                ],
                risk_summary={
                    "risk_metrics": self.risk_report.metrics.to_dict() if self.risk_report is not None else {},
                    "risk_constraint_violations": self.risk_report.violations if self.risk_report is not None else [],
                    "style_exposures": self.risk_report.style_exposures if self.risk_report is not None else {},
                    "active_style_exposures": self.risk_report.active_style_exposures if self.risk_report is not None else {},
                    "risk_decomposition": self.risk_report.factor_risk_contribution if self.risk_report is not None else {},
                    "n_orders": len(orders),
                    "gross_order_value": float(sum(order.order_value for order in orders)),
                    "risk_control_summary": risk_control_summary,
                },
                parent_orders=parent_orders_payload,
                child_orders=child_orders_payload,
                capacity_summary=capacity_summary,
                risk_control_report_path=risk_control_paths.get("risk_control_report_path"),
                risk_control_breaches_path=risk_control_paths.get("risk_control_breaches_path"),
                risk_control_summary=risk_control_summary,
                metadata={
                    "targets_path": str(targets_jsonl),
                    "orders_path": str(orders_jsonl),
                    "output_dir": str(self.output_dir),
                    "component_factor_ids": self.selected_factor_meta.get("component_factor_ids", []),
                    "portfolio_policy_id": self.portfolio_policy.policy_id if self.portfolio_policy else None,
                    "portfolio_policy_path": str(self.portfolio_policy_path) if self.portfolio_policy_path else None,
                    "portfolio_policy_gate": self.portfolio_policy_gate,
                    "settlement_aware": self.settlement_aware,
                    "settlement_profile": self.settlement_profile,
                    "settlement_precheck": settlement_precheck,
                    "production_run_id": self.production_run_id,
                    "run_mode": self.run_mode,
                    "orchestrator_artifact_dir": str(self.orchestrator_artifact_dir) if self.orchestrator_artifact_dir else None,
                    **execution_paths,
                    **risk_control_paths,
                },
            )
            LocalApprovalStore(self.approval_store_dir).save_batch(batch)
            approval_id = batch.approval_id
            approval_status = batch.status
        else:
            if self.capacity_aware and plan_result is not None:
                fills = plan_result.fills
                export_fills_jsonl(fills, fills_path)
            else:
                prices, volumes, suspended, limit_up_flags, limit_down_flags = _market_context(self.loader, target_book.trade_date)
                fills = PaperBroker(self.output_dir).submit_orders(
                    orders,
                    prices,
                    target_book.trade_date,
                    volumes=volumes,
                    suspended=suspended,
                    limit_up=limit_up_flags,
                    limit_down=limit_down_flags,
                )
        rejected = sum(1 for fill in fills if fill.status == "REJECTED")
        partial = sum(1 for fill in fills if fill.status == "PARTIAL")
        completed = sum(1 for fill in fills if fill.status in {"FILLED", "PARTIAL"})

        return {
            "factor_id": self.selected_factor_id,
            "factor_type": self.selected_factor_meta.get("factor_type"),
            "component_factor_ids": self.selected_factor_meta.get("component_factor_ids", []),
            "portfolio_method": self.portfolio_method,
            "portfolio_policy_id": self.portfolio_policy.policy_id if self.portfolio_policy else None,
            "portfolio_policy_path": str(self.portfolio_policy_path) if self.portfolio_policy_path else None,
            "portfolio_policy_gate": self.portfolio_policy_gate,
            "rebalance_date": target_book.trade_date,
            "n_targets": len(target_book.targets),
            "n_orders": len(orders),
            "n_fills": len(fills),
            "n_rejected": rejected,
            "n_partial": partial,
            "fill_rate": completed / len(fills) if fills else 0.0,
            "capacity_aware": self.capacity_aware,
            "capacity_warning_count": capacity_summary.get("capacity_warning_count", 0),
            "estimated_impact_cost": capacity_summary.get("estimated_impact_cost", 0.0),
            "child_order_count": len(child_orders_payload),
            "execution_quality": execution_quality,
            "output_dir": str(self.output_dir),
            "targets_path": str(targets_jsonl),
            "targets_csv_path": str(targets_csv),
            "orders_path": str(orders_jsonl),
            "orders_csv_path": str(orders_csv),
            "fills_path": str(fills_path),
            "approval_id": approval_id,
            "approval_status": approval_status,
            "propose_only": bool(self.propose_only or self.require_approval),
            "risk_metrics": self.risk_report.metrics.to_dict() if self.risk_report is not None else {},
            "risk_constraint_violations": self.risk_report.violations if self.risk_report is not None else [],
            "style_exposures": self.risk_report.style_exposures if self.risk_report is not None else {},
            "active_style_exposures": self.risk_report.active_style_exposures if self.risk_report is not None else {},
            "risk_decomposition": self.risk_report.factor_risk_contribution if self.risk_report is not None else {},
            "risk_report_path": risk_report_path,
            "risk_report_md_path": risk_report_md_path,
            "optimization_result_path": str(optimization_result_path) if optimization_result_path else None,
            "capacity_report_path": execution_paths.get("capacity_report_path"),
            "capacity_report_md_path": execution_paths.get("capacity_report_md_path"),
            "execution_plan_path": execution_paths.get("execution_plan_path"),
            "execution_plan_md_path": execution_paths.get("execution_plan_md_path"),
            "parent_orders_path": execution_paths.get("parent_orders_path"),
            "child_orders_path": execution_paths.get("child_orders_path"),
            "child_fills_path": execution_paths.get("child_fills_path"),
            "execution_quality_path": execution_paths.get("execution_quality_path"),
            "point_in_time": self.point_in_time,
            "feature_cutoff_mode": self.feature_cutoff_mode,
            "corporate_action_aware": self.corporate_action_aware,
            "target_return_mode": self.target_return_mode,
            "corporate_action_event_count": len(getattr(self.loader, "corporate_action_events", []) or []),
            "settlement_aware": self.settlement_aware,
            "settlement_profile": self.settlement_profile,
            "settlement_precheck": settlement_precheck,
            "settlement_precheck_rejected_order_count": settlement_precheck.get("precheck_rejected_order_count", 0),
            "enforce_available_cash": self.enforce_available_cash,
            "enforce_available_shares": self.enforce_available_shares,
            "risk_controls": self.risk_controls,
            "production_run_id": self.production_run_id,
            "run_mode": self.run_mode,
            "orchestrator_artifact_dir": str(self.orchestrator_artifact_dir) if self.orchestrator_artifact_dir else None,
            "risk_control_status": risk_control_summary.get("status", "not_run"),
            "risk_control_rejected_orders": risk_control_summary.get("rejected_orders", 0),
            "risk_control_clipped_orders": risk_control_summary.get("clipped_orders", 0),
            "risk_control_warning_count": risk_control_summary.get("warning_count", 0),
            "risk_control_error_count": risk_control_summary.get("error_count", 0),
            "data_freeze_dir": str(self.data_freeze_dir) if self.data_freeze_dir else None,
            "data_freeze_id": self.data_freeze_id or self.freeze_validation.freeze_id,
            "data_freeze_hash": self.freeze_validation.content_hash,
            "freeze_validation_status": self.freeze_validation.status,
            "data_version_manifest_path": str(self.data_version_manifest_path) if self.data_version_manifest_path else None,
            "freeze_validation_report_path": str(self.freeze_validation_report_path) if self.freeze_validation_report_path else None,
            "data_hash_drift_count": self.freeze_validation.error_count,
            **risk_control_paths,
        }

    def _apply_risk_controls(self, orders, trade_date: str):
        report, split, paths = evaluate_order_records(
            orders,
            policy_path=self.risk_policy_path,
            policy_profile=self.risk_policy_profile,
            state_dir=self.risk_control_state_dir,
            output_dir=self.risk_control_output_dir,
            batch_id=f"strategy_{trade_date}_{self.selected_factor_id or 'factor'}",
            trade_date=trade_date,
            scope="order",
            allow_clipping=self.risk_allow_clipping,
        )
        if self.risk_fail_on_breach and report.rejected_orders > 0:
            raise ValueError(f"risk controls rejected {report.rejected_orders} orders")
        if self.create_risk_override_approval and report.rejected_orders > 0:
            if self.risk_override_approval_store_dir is None:
                raise ValueError("risk_override_approval_store_dir is required to create risk override approval")
            request, batch, request_path = create_override_approval(
                approval_store_dir=self.risk_override_approval_store_dir,
                state_dir=self.risk_control_state_dir,
                output_dir=self.risk_control_output_dir,
                scope="order",
                reason="risk control breach override requested",
                metadata={"trade_date": trade_date, "risk_control_report_path": str(paths["risk_control_report_path"])},
            )
            paths["risk_override_request_path"] = request_path
            paths["risk_override_approval_path"] = Path(self.risk_override_approval_store_dir) / "approvals" / f"{batch.approval_id}.json"
        accepted_payloads = split["accepted"]
        adjusted_orders = [
            type(orders[0])(
                trade_date=str(row.get("trade_date") or trade_date),
                ts_code=str(row.get("ts_code") or ""),
                side=str(row.get("side") or ""),
                target_weight=float(row.get("target_weight", 0.0) or 0.0),
                order_value=float(row.get("order_value", 0.0) or 0.0),
                reason=str(row.get("reason") or "rebalance"),
            )
            for row in accepted_payloads
        ] if orders else []
        summary = {
            "status": report.status,
            "accepted_orders": report.accepted_orders,
            "rejected_orders": report.rejected_orders,
            "clipped_orders": report.clipped_orders,
            "warning_count": report.warning_count,
            "error_count": report.error_count,
            "blocker_count": report.blocker_count,
            "policy_id": report.policy_id,
            "profile": report.profile,
        }
        return adjusted_orders, summary, {key: str(value) for key, value in paths.items()}

    def _build_execution_plan(self, orders, trade_date: str) -> tuple[ExecutionPlanResult, dict[str, object], dict[str, str]]:
        if self.loader is None:
            raise ValueError("loader is not initialized")
        config = ExecutionPlanConfig(
            buckets=self.execution_buckets,
            max_child_participation=self.max_participation,
        )
        parents = build_parent_orders_from_target_orders(orders)
        schedule, capacity = build_execution_schedule(parents, self.loader, trade_date, config)
        simulated = simulate_child_orders(schedule, self.loader)
        plan_result = ExecutionPlanResult(
            schedule=schedule,
            fills=simulated.fills,
            quality=simulated.quality,
            capacity_report=capacity.to_dict(),
        )
        paths = write_execution_plan_report(plan_result, self.execution_plan_dir)
        capacity_report = build_capacity_report(
            capacity,
            CapacityConfig(max_participation=self.max_participation),
            {"source": "strategy_manager"},
        )
        capacity_json, capacity_md = write_capacity_report(capacity_report, self.execution_plan_dir)
        payload_paths = {key: str(path) for key, path in paths.items()}
        payload_paths["capacity_report_path"] = str(capacity_json)
        payload_paths["capacity_report_md_path"] = str(capacity_md)
        return plan_result, capacity_report.to_dict(), payload_paths


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate A-share target positions and paper orders.")
    defaults = AShareStrategyConfig.from_env()
    parser.add_argument("--data-dir", default=str(defaults.data_dir))
    parser.add_argument("--factor-store-dir", default=str(defaults.factor_store_dir))
    parser.add_argument("--output-dir", default=str(defaults.output_dir))
    parser.add_argument("--factor-id")
    parser.add_argument("--latest-approved", action="store_true")
    parser.add_argument("--factor-type", choices=["single", "composite", "any"], default="any")
    parser.add_argument("--rebalance-date", default=defaults.rebalance_date)
    parser.add_argument("--top-n", type=int, default=defaults.top_n)
    parser.add_argument("--max-weight", type=float, default=defaults.max_weight)
    parser.add_argument("--portfolio-value", type=float, default=1_000_000.0)
    parser.add_argument("--portfolio-method", choices=["equal_weight", "risk_aware"], default="equal_weight")
    parser.add_argument("--portfolio-policy-path")
    parser.add_argument("--require-certified-portfolio-policy", action="store_true")
    parser.add_argument("--portfolio-certification-decision-path")
    parser.add_argument("--model-registry-dir")
    parser.add_argument("--active-optimizer-policy", action="store_true")
    parser.add_argument("--index-code", default="000300.SH")
    parser.add_argument("--risk-aversion", type=float, default=1.0)
    parser.add_argument("--turnover-penalty", type=float, default=0.1)
    parser.add_argument("--max-turnover", type=float, default=1.0)
    parser.add_argument("--max-industry-active-weight", type=float, default=0.20)
    parser.add_argument("--max-tracking-error", type=float, default=1.0)
    parser.add_argument("--use-factor-risk-model", action="store_true")
    parser.add_argument("--risk-model-lookback", type=int)
    parser.add_argument("--risk-model-shrinkage", type=float, default=0.1)
    parser.add_argument("--max-style-exposure", type=float)
    parser.add_argument("--max-active-style-exposure", type=float)
    parser.add_argument("--capacity-aware", action="store_true")
    parser.add_argument("--execution-plan-dir")
    parser.add_argument("--max-participation", type=float, default=0.10)
    parser.add_argument("--execution-buckets", default="open")
    parser.add_argument("--propose-parent-orders", action="store_true")
    parser.add_argument("--export-child-orders", action="store_true")
    parser.add_argument("--propose-only", action="store_true")
    parser.add_argument("--require-approval", action="store_true")
    parser.add_argument("--approval-store-dir")
    parser.add_argument("--point-in-time", action="store_true")
    parser.add_argument("--feature-cutoff-mode", default="same_day_after_close")
    parser.add_argument("--min-listing-days", type=int, default=0)
    parser.add_argument("--exclude-st", action="store_true")
    parser.add_argument("--corporate-action-aware", action="store_true")
    parser.add_argument("--corporate-action-dir")
    parser.add_argument(
        "--target-return-mode",
        choices=["adjusted_close", "raw_close", "corporate_action_total_return"],
        default="adjusted_close",
    )
    parser.add_argument("--corporate-action-cash-field", default="cash_div")
    parser.add_argument("--settlement-aware", action="store_true")
    parser.add_argument("--settlement-profile", default="cn_ashare_paper_default")
    parser.add_argument("--settlement-dir")
    parser.add_argument("--paper-account-dir")
    parser.add_argument("--enforce-available-cash", action="store_true")
    parser.add_argument("--enforce-available-shares", action="store_true")
    parser.add_argument("--risk-controls", action="store_true")
    parser.add_argument("--risk-policy-path")
    parser.add_argument("--risk-control-state-dir")
    parser.add_argument("--risk-control-output-dir")
    parser.add_argument("--risk-policy-profile", default="cn_ashare_paper_default")
    parser.add_argument("--risk-fail-on-breach", action="store_true")
    parser.add_argument("--risk-allow-clipping", action="store_true")
    parser.add_argument("--create-risk-override-approval", action="store_true")
    parser.add_argument("--risk-override-approval-store-dir")
    parser.add_argument("--data-freeze-dir")
    parser.add_argument("--data-freeze-id")
    parser.add_argument("--data-version-manifest-path")
    parser.add_argument("--require-data-freeze", action="store_true")
    parser.add_argument("--freeze-validation-report-path")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    summary = AShareStrategyRunner(
        data_dir=args.data_dir,
        factor_store_dir=args.factor_store_dir,
        output_dir=args.output_dir,
        top_n=args.top_n,
        max_weight=args.max_weight,
        factor_id=args.factor_id,
        rebalance_date=args.rebalance_date,
        portfolio_value=args.portfolio_value,
        latest_approved=args.latest_approved,
        factor_type=args.factor_type,
        portfolio_method=args.portfolio_method,
        portfolio_policy_path=args.portfolio_policy_path,
        require_certified_portfolio_policy=args.require_certified_portfolio_policy,
        portfolio_certification_decision_path=args.portfolio_certification_decision_path,
        model_registry_dir=args.model_registry_dir,
        active_optimizer_policy=args.active_optimizer_policy,
        index_code=args.index_code,
        risk_aversion=args.risk_aversion,
        turnover_penalty=args.turnover_penalty,
        max_turnover=args.max_turnover,
        max_industry_active_weight=args.max_industry_active_weight,
        max_tracking_error=args.max_tracking_error,
        use_factor_risk_model=args.use_factor_risk_model,
        risk_model_lookback=args.risk_model_lookback,
        risk_model_shrinkage=args.risk_model_shrinkage,
        max_style_exposure=args.max_style_exposure,
        max_active_style_exposure=args.max_active_style_exposure,
        capacity_aware=args.capacity_aware,
        execution_plan_dir=args.execution_plan_dir,
        max_participation=args.max_participation,
        execution_buckets=args.execution_buckets,
        propose_parent_orders=args.propose_parent_orders,
        export_child_orders=args.export_child_orders,
        propose_only=args.propose_only,
        require_approval=args.require_approval,
        approval_store_dir=args.approval_store_dir,
        point_in_time=args.point_in_time,
        feature_cutoff_mode=args.feature_cutoff_mode,
        min_listing_days=args.min_listing_days,
        exclude_st=args.exclude_st,
        corporate_action_aware=args.corporate_action_aware,
        corporate_action_dir=args.corporate_action_dir,
        target_return_mode=args.target_return_mode,
        corporate_action_cash_field=args.corporate_action_cash_field,
        settlement_aware=args.settlement_aware,
        settlement_profile=args.settlement_profile,
        settlement_dir=args.settlement_dir,
        paper_account_dir=args.paper_account_dir,
        enforce_available_cash=args.enforce_available_cash,
        enforce_available_shares=args.enforce_available_shares,
        risk_controls=args.risk_controls,
        risk_policy_path=args.risk_policy_path,
        risk_control_state_dir=args.risk_control_state_dir,
        risk_control_output_dir=args.risk_control_output_dir,
        risk_policy_profile=args.risk_policy_profile,
        risk_fail_on_breach=args.risk_fail_on_breach,
        risk_allow_clipping=args.risk_allow_clipping,
        create_risk_override_approval=args.create_risk_override_approval,
        risk_override_approval_store_dir=args.risk_override_approval_store_dir,
        data_freeze_dir=args.data_freeze_dir,
        data_freeze_id=args.data_freeze_id,
        data_version_manifest_path=args.data_version_manifest_path,
        require_data_freeze=args.require_data_freeze,
        freeze_validation_report_path=args.freeze_validation_report_path,
    ).generate_orders()
    print(json.dumps(summary, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def _market_context(loader: AShareDataLoader, trade_date: str):
    close = loader.raw_data_cache["close"].detach().cpu()
    date_idx = loader.trade_dates.index(trade_date)
    prices = {ts_code: float(close[idx, date_idx].item()) for idx, ts_code in enumerate(loader.ts_codes)}
    volume = loader.raw_data_cache["volume"].detach().cpu()
    is_suspended = loader.raw_data_cache["is_suspended"].detach().cpu()
    limit_up = loader.raw_data_cache["limit_up_flag"].detach().cpu()
    limit_down = loader.raw_data_cache["limit_down_flag"].detach().cpu()
    volumes = {ts_code: float(volume[idx, date_idx].item()) for idx, ts_code in enumerate(loader.ts_codes)}
    suspended = {ts_code: bool(is_suspended[idx, date_idx].item() > 0.5) for idx, ts_code in enumerate(loader.ts_codes)}
    limit_up_flags = {ts_code: bool(limit_up[idx, date_idx].item() > 0.5) for idx, ts_code in enumerate(loader.ts_codes)}
    limit_down_flags = {ts_code: bool(limit_down[idx, date_idx].item() > 0.5) for idx, ts_code in enumerate(loader.ts_codes)}
    return prices, volumes, suspended, limit_up_flags, limit_down_flags


def _make_approval_id(factor_id: str, trade_date: str) -> str:
    suffix = "".join(char for char in factor_id if char.isalnum())[-12:]
    return f"approval_{trade_date}_{suffix}_{_safe_time(_utc_now())}"


def _parse_buckets(value: str | tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(value, tuple):
        return tuple(item for item in value if item)
    buckets = tuple(item.strip() for item in str(value).split(",") if item.strip())
    return buckets or ("open", "morning", "afternoon", "close")


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _safe_time(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in value).strip("_")


if __name__ == "__main__":
    raise SystemExit(main())

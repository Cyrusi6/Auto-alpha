"""Daily production run orchestration."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from corporate_actions.models import CorporateActionEvent
from corporate_actions.normalizer import normalize_corporate_action_records
from corporate_actions.report import read_jsonl, write_corporate_action_report
from approval import ApprovalStatus, LocalApprovalStore
from broker_adapter import (
    BrokerAdapterConfig,
    FileInstructionBrokerAdapter,
    LocalBrokerStore,
    SimulatedBrokerAdapter,
    broker_fills_to_execution_fills,
    build_broker_requests_from_child_orders,
    write_broker_report,
)
from execution import ExecutionOrder, PaperBroker, export_fills_jsonl, export_orders_csv, export_orders_jsonl
from execution_plan import ChildOrder, ExecutionPlanResult, ExecutionSchedule, ParentOrder, simulate_child_orders, write_execution_plan_report
from factor_store import LocalFactorStore
from model_core.data_loader import AShareDataLoader
from model_registry import LocalModelRegistry, ModelKind, ModelLifecycleStatus, write_model_registry_report
from paper_account import LocalPaperAccount, compute_account_performance
from strategy_manager.runner import AShareStrategyRunner

from .models import ProductionRunResult
from .report import write_production_run_report


class ProductionDailyRunner:
    def __init__(
        self,
        data_dir: str | Path,
        factor_store_dir: str | Path,
        approval_store_dir: str | Path,
        paper_account_dir: str | Path,
        output_dir: str | Path,
        orders_dir: str | Path,
        factor_id: str | None = None,
        latest_production: bool = False,
        rebalance_date: str | None = None,
        portfolio_method: str = "equal_weight",
        index_code: str = "000300.SH",
        top_n: int = 20,
        max_weight: float = 0.10,
        portfolio_value: float = 1_000_000.0,
        use_factor_risk_model: bool = False,
        risk_model_lookback: int | None = None,
        risk_model_shrinkage: float = 0.1,
        max_style_exposure: float | None = None,
        max_active_style_exposure: float | None = None,
        capacity_aware: bool = False,
        execution_plan_dir: str | Path | None = None,
        max_participation: float = 0.10,
        execution_buckets: str | tuple[str, ...] = "open,morning,afternoon,close",
        broker_adapter: str = "paper",
        broker_store_dir: str | Path | None = None,
        broker_outbox_dir: str | Path | None = None,
        broker_inbox_dir: str | Path | None = None,
        broker_auto_fill: bool = True,
        broker_reconcile: bool = False,
        broker_price_type: str = "MARKET",
        use_model_registry: bool = False,
        model_registry_dir: str | Path | None = None,
        model_version_id: str | None = None,
        require_active_model: bool = False,
        allow_production_candidate_fallback: bool = False,
        model_kind: str = ModelKind.composite_factor,
        model_environment: str = "paper",
        block_paused_model: bool = True,
        block_quarantined_model: bool = True,
        point_in_time: bool = False,
        feature_cutoff_mode: str = "same_day_after_close",
        min_listing_days: int = 0,
        exclude_st: bool = False,
        corporate_action_aware: bool = False,
        apply_corporate_actions: bool = False,
        corporate_action_dir: str | Path | None = None,
        corporate_action_output_dir: str | Path | None = None,
        target_return_mode: str = "adjusted_close",
        corporate_action_application_date_mode: str = "pay_date",
        corporate_action_cash_field: str = "cash_div",
        reconcile_adjustment_factors: bool = False,
        settlement_aware: bool = False,
        settlement_dir: str | Path | None = None,
        settlement_profile: str = "cn_ashare_paper_default",
        cost_basis_method: str = "average",
        settle_before_trading: bool = False,
        settle_through_date: str | None = None,
        enforce_available_cash: bool = False,
        enforce_available_shares: bool = False,
        allow_unsettled_cash_for_buy: bool = False,
        allow_unsettled_shares_for_sell: bool = False,
        run_eod_reconciliation: bool = False,
        broker_statement_dir: str | Path | None = None,
        broker_statement_schema: str = "generic_broker_statement",
        broker_statement_schema_config: str | Path | None = None,
        eod_reconciliation_dir: str | Path | None = None,
        fail_on_reconciliation_error: bool = False,
        create_adjustment_proposals: bool = False,
        create_adjustment_approval: bool = False,
        apply_approved_adjustments: bool = False,
        adjustment_approval_id: str | None = None,
    ):
        self.data_dir = Path(data_dir)
        self.factor_store_dir = Path(factor_store_dir)
        self.approval_store_dir = Path(approval_store_dir)
        self.paper_account_dir = Path(paper_account_dir)
        self.output_dir = Path(output_dir)
        self.orders_dir = Path(orders_dir)
        self.factor_id = factor_id
        self.latest_production = bool(latest_production)
        self.rebalance_date = rebalance_date
        self.portfolio_method = portfolio_method
        self.index_code = index_code
        self.top_n = int(top_n)
        self.max_weight = float(max_weight)
        self.portfolio_value = float(portfolio_value)
        self.use_factor_risk_model = bool(use_factor_risk_model)
        self.risk_model_lookback = risk_model_lookback
        self.risk_model_shrinkage = float(risk_model_shrinkage)
        self.max_style_exposure = max_style_exposure
        self.max_active_style_exposure = max_active_style_exposure
        self.capacity_aware = bool(capacity_aware)
        self.execution_plan_dir = Path(execution_plan_dir) if execution_plan_dir is not None else self.orders_dir / "plan"
        self.max_participation = float(max_participation)
        self.execution_buckets = execution_buckets
        self.broker_adapter = broker_adapter
        self.broker_store_dir = Path(broker_store_dir) if broker_store_dir is not None else self.output_dir / "broker"
        self.broker_outbox_dir = Path(broker_outbox_dir) if broker_outbox_dir is not None else self.broker_store_dir / "outbox"
        self.broker_inbox_dir = Path(broker_inbox_dir) if broker_inbox_dir is not None else None
        self.broker_auto_fill = bool(broker_auto_fill)
        self.broker_reconcile = bool(broker_reconcile)
        self.broker_price_type = broker_price_type
        self.use_model_registry = bool(use_model_registry)
        self.model_registry_dir = Path(model_registry_dir) if model_registry_dir is not None else self.output_dir.parent / "model_registry"
        self.model_version_id = model_version_id
        self.require_active_model = bool(require_active_model)
        self.allow_production_candidate_fallback = bool(allow_production_candidate_fallback)
        self.model_kind = model_kind
        self.model_environment = model_environment
        self.block_paused_model = bool(block_paused_model)
        self.block_quarantined_model = bool(block_quarantined_model)
        self.point_in_time = bool(point_in_time)
        self.feature_cutoff_mode = feature_cutoff_mode
        self.min_listing_days = int(min_listing_days)
        self.exclude_st = bool(exclude_st)
        self.corporate_action_aware = bool(corporate_action_aware)
        self.apply_corporate_actions = bool(apply_corporate_actions)
        self.corporate_action_dir = Path(corporate_action_dir) if corporate_action_dir is not None else None
        self.corporate_action_output_dir = Path(corporate_action_output_dir) if corporate_action_output_dir is not None else self.output_dir / "corporate_actions"
        self.target_return_mode = target_return_mode
        self.corporate_action_application_date_mode = corporate_action_application_date_mode
        self.corporate_action_cash_field = corporate_action_cash_field
        self.reconcile_adjustment_factors = bool(reconcile_adjustment_factors)
        self.settlement_aware = bool(settlement_aware)
        self.settlement_dir = Path(settlement_dir) if settlement_dir is not None else self.output_dir / "settlement"
        self.settlement_profile = settlement_profile
        self.cost_basis_method = cost_basis_method
        self.settle_before_trading = bool(settle_before_trading)
        self.settle_through_date = settle_through_date
        self.enforce_available_cash = bool(enforce_available_cash)
        self.enforce_available_shares = bool(enforce_available_shares)
        self.allow_unsettled_cash_for_buy = bool(allow_unsettled_cash_for_buy)
        self.allow_unsettled_shares_for_sell = bool(allow_unsettled_shares_for_sell)
        self.run_eod_reconciliation = bool(run_eod_reconciliation)
        self.broker_statement_dir = Path(broker_statement_dir) if broker_statement_dir is not None else None
        self.broker_statement_schema = broker_statement_schema
        self.broker_statement_schema_config = broker_statement_schema_config
        self.eod_reconciliation_dir = Path(eod_reconciliation_dir) if eod_reconciliation_dir is not None else self.output_dir / "eod_reconciliation"
        self.fail_on_reconciliation_error = bool(fail_on_reconciliation_error)
        self.create_adjustment_proposals = bool(create_adjustment_proposals)
        self.create_adjustment_approval = bool(create_adjustment_approval)
        self.apply_approved_adjustments = bool(apply_approved_adjustments)
        self.adjustment_approval_id = adjustment_approval_id
        self._model_context: dict[str, Any] = {}

    def run(
        self,
        require_approval: bool = False,
        approval_id: str | None = None,
        execute_approved: bool = False,
        reconcile_only: bool = False,
    ) -> ProductionRunResult:
        created_at = _utc_now()
        run_id = f"prod_{_safe_time(created_at)}"
        try:
            if reconcile_only:
                result = self._reconcile_only(run_id, created_at, approval_id)
            elif approval_id or execute_approved:
                result = self._execute_approved(run_id, created_at, approval_id)
            else:
                result = self._propose(run_id, created_at, require_approval=require_approval)
        except Exception as exc:
            result = ProductionRunResult(
                run_id=run_id,
                created_at=created_at,
                status="failed",
                factor_id=self.factor_id,
                rebalance_date=self.rebalance_date or "",
                error=str(exc),
                summary={"error": str(exc)},
            )
        json_path, md_path = write_production_run_report(result, self.output_dir)
        payload = result.to_dict()
        payload["paths"] = dict(payload.get("paths", {})) | {
            "production_run_path": str(json_path),
            "production_run_md_path": str(md_path),
        }
        final = ProductionRunResult(**payload)
        write_production_run_report(final, self.output_dir)
        return final

    def _propose(self, run_id: str, created_at: str, require_approval: bool) -> ProductionRunResult:
        factor_id = self._select_factor_id()
        if self.settlement_aware and self.settle_before_trading and self.rebalance_date:
            LocalPaperAccount(self.paper_account_dir).settle(self.rebalance_date, profile=self.settlement_profile)
        summary = AShareStrategyRunner(
            data_dir=self.data_dir,
            factor_store_dir=self.factor_store_dir,
            output_dir=self.orders_dir,
            top_n=self.top_n,
            max_weight=self.max_weight,
            factor_id=factor_id,
            rebalance_date=self.rebalance_date,
            portfolio_value=self.portfolio_value,
            factor_type="any",
            portfolio_method=self.portfolio_method,
            index_code=self.index_code,
            use_factor_risk_model=self.use_factor_risk_model,
            risk_model_lookback=self.risk_model_lookback,
            risk_model_shrinkage=self.risk_model_shrinkage,
            max_style_exposure=self.max_style_exposure,
            max_active_style_exposure=self.max_active_style_exposure,
            capacity_aware=self.capacity_aware,
            execution_plan_dir=self.execution_plan_dir,
            max_participation=self.max_participation,
            execution_buckets=self.execution_buckets,
            propose_only=require_approval,
            require_approval=require_approval,
            approval_store_dir=self.approval_store_dir if require_approval else None,
            point_in_time=self.point_in_time,
            feature_cutoff_mode=self.feature_cutoff_mode,
            min_listing_days=self.min_listing_days,
            exclude_st=self.exclude_st,
            corporate_action_aware=self.corporate_action_aware,
            corporate_action_dir=self._corporate_action_dir(),
            target_return_mode=self.target_return_mode,
            corporate_action_cash_field=self.corporate_action_cash_field,
            settlement_aware=self.settlement_aware,
            settlement_profile=self.settlement_profile,
            settlement_dir=self.settlement_dir,
            paper_account_dir=self.paper_account_dir,
            enforce_available_cash=self.enforce_available_cash,
            enforce_available_shares=self.enforce_available_shares,
        ).generate_orders()
        if self.apply_corporate_actions:
            prices = _prices_for_date(
                self.data_dir,
                str(summary["rebalance_date"]),
                corporate_action_aware=self.corporate_action_aware,
                corporate_action_dir=self._corporate_action_dir(),
                target_return_mode=self.target_return_mode,
                cash_field=self.corporate_action_cash_field,
            )
            summary.update(self._apply_corporate_actions(str(summary["rebalance_date"]), prices))
        if not require_approval:
            fills = _read_jsonl(Path(str(summary.get("fills_path"))))
            prices = _prices_for_date(
                self.data_dir,
                str(summary["rebalance_date"]),
                corporate_action_aware=self.corporate_action_aware,
                corporate_action_dir=self._corporate_action_dir(),
                target_return_mode=self.target_return_mode,
                cash_field=self.corporate_action_cash_field,
            )
            if self.settlement_aware:
                account_state = LocalPaperAccount(self.paper_account_dir).apply_fills_settlement_aware(
                    fills,
                    data_dir=self.data_dir,
                    trade_date=str(summary["rebalance_date"]),
                    profile=self.settlement_profile,
                    prices=prices,
                    cost_basis_method=self.cost_basis_method,
                )
                if self.settle_through_date:
                    account_state = LocalPaperAccount(self.paper_account_dir).settle(self.settle_through_date, prices=prices, profile=self.settlement_profile)
            else:
                account_state = LocalPaperAccount(self.paper_account_dir).apply_fills(fills, prices, str(summary["rebalance_date"]))
            account_state = LocalPaperAccount(self.paper_account_dir).mark_to_market(prices, str(summary["rebalance_date"]))
            if self.settlement_aware:
                summary.update(self._write_settlement_artifacts(account_state, str(summary["rebalance_date"])))
            summary["account"] = {
                "cash": account_state.cash,
                "positions": len(account_state.positions),
                "performance": compute_account_performance(account_state),
            }
        self._attach_model_metadata_to_summary(summary)
        if require_approval and summary.get("approval_id"):
            self._attach_model_metadata_to_approval(str(summary["approval_id"]))
        return ProductionRunResult(
            run_id=run_id,
            created_at=created_at,
            status="pending_approval" if require_approval else "executed",
            factor_id=factor_id,
            rebalance_date=str(summary["rebalance_date"]),
            approval_id=summary.get("approval_id"),
            approval_status=summary.get("approval_status"),
            executed=not require_approval,
            paths=_paths_from_summary(summary),
            summary=summary,
        )

    def _execute_approved(self, run_id: str, created_at: str, approval_id: str | None) -> ProductionRunResult:
        if not approval_id:
            raise ValueError("approval_id is required when execute_approved is enabled")
        store = LocalApprovalStore(self.approval_store_dir)
        batch = store.load_batch(approval_id)
        if batch.status != ApprovalStatus.approved:
            raise ValueError(f"approval batch must be approved before execution: {approval_id} is {batch.status}")
        self._validate_approval_model_context(batch)
        loader = AShareDataLoader(
            data_dir=self.data_dir,
            device="cpu",
            point_in_time=self.point_in_time,
            feature_cutoff_mode=self.feature_cutoff_mode,
            min_listing_days=self.min_listing_days,
            exclude_st=self.exclude_st,
            corporate_action_aware=self.corporate_action_aware,
            corporate_action_dir=self._corporate_action_dir(),
            target_return_mode=self.target_return_mode,
            corporate_action_cash_field=self.corporate_action_cash_field,
        ).load_data()
        prices, volumes, suspended, limit_up, limit_down = _market_context(loader, batch.rebalance_date)
        orders = [
            ExecutionOrder(
                trade_date=order.trade_date,
                ts_code=order.ts_code,
                side=order.side,
                target_weight=order.target_weight,
                order_value=order.order_value,
                reason=order.reason,
            )
            for order in batch.orders
        ]
        self.orders_dir.mkdir(parents=True, exist_ok=True)
        orders_jsonl = export_orders_jsonl(orders, self.orders_dir / "orders.jsonl")
        orders_csv = export_orders_csv(orders, self.orders_dir / "orders.csv")
        execution_quality: dict[str, Any] = {}
        child_order_count = 0
        plan_paths: dict[str, str] = {}
        broker_summary: dict[str, Any] = {}
        broker_paths: dict[str, str] = {}
        if batch.child_orders and self.broker_adapter != "paper":
            parent_orders = [ParentOrder(**payload) for payload in batch.parent_orders]
            child_orders = [ChildOrder(**payload) for payload in batch.child_orders]
            fills, broker_summary, broker_paths = self._execute_with_broker_adapter(
                batch=batch,
                child_orders=child_orders,
                parent_orders=parent_orders,
                prices=prices,
                volumes=volumes,
                suspended=suspended,
                limit_up=limit_up,
                limit_down=limit_down,
                account=LocalPaperAccount(self.paper_account_dir),
            )
            child_order_count = len(child_orders)
            execution_quality = broker_summary.get("execution_quality", {})
        elif batch.child_orders:
            parent_orders = [ParentOrder(**payload) for payload in batch.parent_orders]
            child_orders = [ChildOrder(**payload) for payload in batch.child_orders]
            schedule = ExecutionSchedule(
                trade_date=batch.rebalance_date,
                parent_orders=parent_orders,
                child_orders=child_orders,
                buckets=sorted({order.bucket for order in child_orders}),
                metadata={"approved": True},
            )
            simulated = simulate_child_orders(schedule, loader)
            plan_result = ExecutionPlanResult(
                schedule=schedule,
                fills=simulated.fills,
                quality=simulated.quality,
                capacity_report=batch.capacity_summary,
            )
            paths = write_execution_plan_report(plan_result, self.execution_plan_dir)
            plan_paths = {key: str(path) for key, path in paths.items()}
            fills = simulated.fills
            child_order_count = len(child_orders)
            execution_quality = simulated.quality.to_dict()
            export_fills_jsonl(fills, self.orders_dir / "paper_fills.jsonl")
        else:
            fills = PaperBroker(self.orders_dir).submit_orders(
                orders,
                prices,
                batch.rebalance_date,
                volumes=volumes,
                suspended=suspended,
                limit_up=limit_up,
                limit_down=limit_down,
            )
        fills_path = self.orders_dir / "paper_fills.jsonl"
        export_fills_jsonl(fills, fills_path)
        account = LocalPaperAccount(self.paper_account_dir)
        if self.settlement_aware and self.settle_before_trading:
            account.settle(batch.rebalance_date, prices=prices, profile=self.settlement_profile)
        if self.broker_adapter == "file" and broker_summary.get("inbox_fills", 0) == 0:
            state = account.load_state()
        else:
            if self.settlement_aware:
                state = account.apply_child_fills(
                    fills,
                    prices,
                    batch.rebalance_date,
                    settlement_aware=True,
                    data_dir=self.data_dir,
                    profile=self.settlement_profile,
                    cost_basis_method=self.cost_basis_method,
                )
                settle_date = self.settle_through_date or batch.rebalance_date
                state = account.settle(settle_date, prices=prices, profile=self.settlement_profile)
            else:
                state = account.apply_child_fills(fills, prices, batch.rebalance_date)
            corporate_summary = {}
            if self.apply_corporate_actions:
                corporate_summary = self._apply_corporate_actions(batch.rebalance_date, prices)
            state = account.mark_to_market(prices, batch.rebalance_date)
        settlement_summary = self._write_settlement_artifacts(state, batch.rebalance_date) if self.settlement_aware else {}
        rejected = sum(1 for fill in fills if fill.status == "REJECTED")
        partial = sum(1 for fill in fills if fill.status == "PARTIAL")
        completed = sum(1 for fill in fills if fill.status in {"FILLED", "PARTIAL"})
        summary = {
            "factor_id": batch.factor_id,
            "factor_type": batch.factor_type,
            "approval_id": batch.approval_id,
            "approval_status": batch.status,
            "rebalance_date": batch.rebalance_date,
            "portfolio_method": batch.portfolio_method,
            "n_orders": len(orders),
            "n_fills": len(fills),
            "n_rejected": rejected,
            "n_partial": partial,
            "fill_rate": completed / len(fills) if fills else 0.0,
            "capacity_summary": batch.capacity_summary,
            "child_order_count": child_order_count,
            "execution_quality": execution_quality,
            "risk_summary": batch.risk_summary,
            "style_exposures": batch.risk_summary.get("style_exposures", {}),
            "active_style_exposures": batch.risk_summary.get("active_style_exposures", {}),
            "risk_decomposition": batch.risk_summary.get("risk_decomposition", {}),
            "orders_path": str(orders_jsonl),
            "orders_csv_path": str(orders_csv),
            "fills_path": str(fills_path),
            "account_state_path": str(account.state_path),
            "positions_path": str(account.positions_path),
            "account_snapshots_path": str(account.snapshots_path),
            **plan_paths,
            **broker_paths,
            "broker_adapter": self.broker_adapter,
            "broker_batch_id": batch.approval_id if self.broker_adapter != "paper" else "",
            "broker_store_dir": str(self.broker_store_dir) if self.broker_adapter != "paper" else "",
            "broker_summary": broker_summary,
            "idempotent_replay_count": int(broker_summary.get("idempotent_replay_count", 0) or 0),
            "open_broker_order_count": int(broker_summary.get("open_orders", 0) or 0),
            "rejected_broker_order_count": int(broker_summary.get("rejected_orders", 0) or 0),
            "broker_unfilled_value": float(broker_summary.get("unfilled_value", 0.0) or 0.0),
            "settlement_aware": self.settlement_aware,
            "settlement_profile": self.settlement_profile,
            "cost_basis_method": self.cost_basis_method,
            **settlement_summary,
            "point_in_time": self.point_in_time,
            "feature_cutoff_mode": self.feature_cutoff_mode,
            "account": {
                "cash": state.cash,
                "positions": len(state.positions),
                "performance": compute_account_performance(state),
            },
        }
        if self.apply_corporate_actions:
            summary.update(corporate_summary if "corporate_summary" in locals() else {})
        if self.run_eod_reconciliation or self.apply_approved_adjustments:
            eod_summary = self._run_eod_reconciliation_workflow(batch.rebalance_date, broker_batch_id=batch.approval_id)
            summary.update(eod_summary)
            if self.fail_on_reconciliation_error and summary.get("eod_reconciliation_status") in {"error", "blocker"}:
                raise ValueError("EOD reconciliation produced error or blocker breaks")
        self._attach_model_metadata_to_summary(summary, batch=batch)
        production_status = "broker_exported" if self.broker_adapter == "file" and broker_summary.get("inbox_fills", 0) == 0 else "executed"
        return ProductionRunResult(
            run_id=run_id,
            created_at=created_at,
            status=production_status,
            factor_id=batch.factor_id,
            rebalance_date=batch.rebalance_date,
            approval_id=batch.approval_id,
            approval_status=batch.status,
            executed=production_status == "executed",
            paths=_paths_from_summary(summary),
            summary=summary,
        )

    def _reconcile_only(self, run_id: str, created_at: str, approval_id: str | None) -> ProductionRunResult:
        trade_date = self.rebalance_date or self.settle_through_date or ""
        summary = self._run_eod_reconciliation_workflow(trade_date, broker_batch_id=approval_id)
        status = "reconciled"
        if self.fail_on_reconciliation_error and summary.get("eod_reconciliation_status") in {"error", "blocker"}:
            status = "failed"
        return ProductionRunResult(
            run_id=run_id,
            created_at=created_at,
            status=status,
            factor_id=self.factor_id,
            rebalance_date=trade_date,
            approval_id=approval_id,
            approval_status="",
            executed=False,
            paths=_paths_from_summary(summary),
            summary=summary,
            error="EOD reconciliation produced error or blocker breaks" if status == "failed" else "",
        )

    def _execute_with_broker_adapter(
        self,
        *,
        batch,
        child_orders: list[ChildOrder],
        parent_orders: list[ParentOrder],
        prices: dict[str, float],
        volumes: dict[str, float],
        suspended: dict[str, bool],
        limit_up: dict[str, bool],
        limit_down: dict[str, bool],
        account: LocalPaperAccount,
    ) -> tuple[list[Any], dict[str, Any], dict[str, str]]:
        requests = build_broker_requests_from_child_orders(
            child_orders,
            prices,
            batch.rebalance_date,
            batch.approval_id,
            price_type=self.broker_price_type,
        )
        if self.broker_adapter == "simulated":
            adapter = SimulatedBrokerAdapter(
                self.broker_store_dir,
                prices=prices,
                volumes=volumes,
                suspended=suspended,
                limit_up=limit_up,
                limit_down=limit_down,
                auto_fill=self.broker_auto_fill,
            )
            result = adapter.submit_orders(requests, batch_id=batch.approval_id)
        elif self.broker_adapter == "file":
            adapter = FileInstructionBrokerAdapter(
                self.broker_store_dir,
                self.broker_outbox_dir,
                self.broker_inbox_dir,
                BrokerAdapterConfig(adapter_type="file", price_type=self.broker_price_type),
            )
            result = adapter.submit_orders(requests, batch_id=batch.approval_id)
        else:
            raise ValueError(f"unsupported broker adapter: {self.broker_adapter}")
        broker_fills = result.fills
        fills = broker_fills_to_execution_fills(broker_fills)
        store = LocalBrokerStore(self.broker_store_dir)
        account_trades = account.load_state().trade_ledger if self.broker_reconcile else []
        reconciliation = adapter.reconcile(
            batch.approval_id,
            expected_child_orders=[order.to_dict() for order in child_orders],
            account_trades=account_trades,
        )
        broker_report_dir = self.output_dir / "broker"
        paths = write_broker_report(store, batch.approval_id, reconciliation, broker_report_dir)
        broker_paths = {key: str(path) for key, path in paths.items()}
        if self.broker_adapter == "file":
            manifest = self.broker_outbox_dir / "broker_instruction_manifest.json"
            summary_path = self.broker_outbox_dir / "broker_batch_summary.json"
            if manifest.exists():
                broker_paths["broker_outbox_manifest_path"] = str(manifest)
            if summary_path.exists():
                broker_paths["broker_outbox_summary_path"] = str(summary_path)
        requested = sum(float(order.order_value) for order in child_orders)
        filled = sum(float(fill.value) for fill in fills if fill.status in {"FILLED", "PARTIAL"})
        quality = {
            "parent_order_count": len(parent_orders),
            "child_order_count": len(child_orders),
            "filled_child_orders": sum(1 for fill in fills if fill.status == "FILLED"),
            "partial_child_orders": sum(1 for fill in fills if fill.status == "PARTIAL"),
            "rejected_child_orders": sum(1 for fill in fills if fill.status == "REJECTED"),
            "requested_value": float(requested),
            "filled_value": float(filled),
            "unfilled_order_value": float(max(requested - filled, 0.0)),
            "execution_fill_rate": float(filled / requested) if requested > 1e-12 else 0.0,
        }
        broker_summary = {
            **result.summary,
            "adapter": self.broker_adapter,
            "batch_id": batch.approval_id,
            "broker_orders": len(result.orders),
            "broker_fills": len(result.fills),
            "inbox_fills": len(result.fills) if self.broker_adapter == "file" else 0,
            "idempotent_replay_count": result.idempotent_replay_count,
            "duplicate_request_count": result.duplicate_request_count,
            "reconciliation": reconciliation.to_dict(),
            "execution_quality": quality,
        }
        if self.broker_adapter == "file" and not fills:
            broker_summary["status"] = "broker_exported"
        return fills, broker_summary, broker_paths

    def _select_factor_id(self) -> str:
        if self.use_model_registry:
            return self._select_factor_id_from_registry()
        if self.factor_id:
            return self.factor_id
        store = LocalFactorStore(self.factor_store_dir)
        if self.latest_production:
            record = store.load_latest_factor(status="production_candidate", factor_type="composite")
            if record is not None:
                return record.factor_id
        record = store.load_latest_factor(status="approved", factor_type="composite")
        if record is not None:
            return record.factor_id
        record = store.load_latest_factor(factor_type="composite")
        if record is not None:
            return record.factor_id
        raise ValueError("no production_candidate or approved composite factor is available")

    def _select_factor_id_from_registry(self) -> str:
        registry = LocalModelRegistry(self.model_registry_dir)
        model = None
        deployment = None
        warning_count = 0
        if self.model_version_id:
            model = registry.get_model_version(self.model_version_id)
            if model is None:
                raise FileNotFoundError(f"model version not found: {self.model_version_id}")
            deployment = registry.latest_active_deployment(model_kind=model.model_kind, environment=self.model_environment)
        else:
            deployment = registry.latest_active_deployment(model_kind=self.model_kind, environment=self.model_environment)
            model = registry.get_model_version(deployment.model_version_id) if deployment is not None else None
        if model is None:
            fallback = registry.latest_by_status(ModelLifecycleStatus.production_candidate, model_kind=self.model_kind)
            if fallback is not None and self.allow_production_candidate_fallback and not self.require_active_model:
                model = fallback
                warning_count += 1
            else:
                raise ValueError("no active model is available in model registry")
        if model.lifecycle_status != ModelLifecycleStatus.active:
            if model.lifecycle_status == ModelLifecycleStatus.production_candidate and self.allow_production_candidate_fallback and not self.require_active_model:
                warning_count += 1
            elif model.lifecycle_status == ModelLifecycleStatus.paused and self.block_paused_model:
                raise ValueError(f"model is paused and cannot be used: {model.model_version_id}")
            elif model.lifecycle_status == ModelLifecycleStatus.quarantined and self.block_quarantined_model:
                raise ValueError(f"model is quarantined and cannot be used: {model.model_version_id}")
            elif model.lifecycle_status in {ModelLifecycleStatus.retired, ModelLifecycleStatus.rejected}:
                raise ValueError(f"model status cannot be used for production: {model.lifecycle_status}")
            elif self.require_active_model:
                raise ValueError(f"active model required, got {model.lifecycle_status}")
        report_json, _report_md = write_model_registry_report(registry)
        lineage_path = self.model_registry_dir / "model_lineage_graph.json"
        self._model_context = {
            "model_registry_enabled": True,
            "model_version_id": model.model_version_id,
            "model_lifecycle_status": model.lifecycle_status,
            "model_deployment_id": deployment.deployment_id if deployment else "",
            "model_registry_dir": str(self.model_registry_dir),
            "model_registry_report_path": str(report_json),
            "model_lineage_graph_path": str(lineage_path),
            "model_registry_warning_count": warning_count,
        }
        return model.factor_id

    def _attach_model_metadata_to_summary(self, summary: dict[str, Any], batch: Any | None = None) -> None:
        if self.use_model_registry:
            if not self._model_context and batch is not None:
                self._model_context = {
                    "model_registry_enabled": True,
                    "model_version_id": batch.model_version_id,
                    "model_lifecycle_status": (batch.metadata or {}).get("model_lifecycle_status", ""),
                    "model_deployment_id": (batch.metadata or {}).get("model_deployment_id", ""),
                    "model_registry_dir": str(self.model_registry_dir),
                    "model_registry_report_path": (batch.metadata or {}).get("model_registry_report_path", ""),
                    "model_lineage_graph_path": (batch.metadata or {}).get("model_lineage_graph_path", ""),
                    "model_registry_warning_count": 0,
                }
            summary.update(self._model_context)
        else:
            summary.setdefault("model_registry_enabled", False)

    def _attach_model_metadata_to_approval(self, approval_id: str) -> None:
        if not self.use_model_registry or not self._model_context:
            return
        store = LocalApprovalStore(self.approval_store_dir)
        batch = store.load_batch(approval_id)
        metadata = dict(batch.metadata or {})
        metadata.update(self._model_context)
        updated = replace(batch, model_version_id=self._model_context.get("model_version_id"), lifecycle_summary=self._model_context, metadata=metadata)
        store.save_batch(updated)

    def _validate_approval_model_context(self, batch: Any) -> None:
        if not self.use_model_registry:
            return
        registry = LocalModelRegistry(self.model_registry_dir)
        active = registry.latest_active(model_kind=self.model_kind, environment=self.model_environment)
        batch_model_id = batch.model_version_id or (batch.metadata or {}).get("model_version_id")
        if self.require_active_model:
            if active is None:
                raise ValueError("active model required but no active deployment exists")
            if batch_model_id and batch_model_id != active.model_version_id:
                raise ValueError("approval model_version_id does not match active deployment")
        if active is not None:
            deployment = registry.latest_active_deployment(model_kind=self.model_kind, environment=self.model_environment)
            self._model_context = {
                "model_registry_enabled": True,
                "model_version_id": active.model_version_id,
                "model_lifecycle_status": active.lifecycle_status,
                "model_deployment_id": deployment.deployment_id if deployment else "",
                "model_registry_dir": str(self.model_registry_dir),
                "model_registry_warning_count": 0,
            }

    def _apply_corporate_actions(self, trade_date: str, prices: dict[str, float]) -> dict[str, Any]:
        events = _load_corporate_action_events(self.data_dir, self._corporate_action_dir(), self.corporate_action_cash_field)
        report_paths = write_corporate_action_report(
            self.data_dir,
            events,
            self.corporate_action_output_dir,
            start_date="00000000",
            end_date=trade_date,
            total_return_mode="cash_reinvested",
            reconcile_adjustment=self.reconcile_adjustment_factors,
        )
        state, applications = LocalPaperAccount(self.paper_account_dir).apply_corporate_actions(
            events,
            trade_date=trade_date,
            prices=prices,
            mode=self.corporate_action_application_date_mode,
        )
        applied = [item for item in applications if getattr(item, "status", "") == "APPLIED"]
        skipped = [item for item in applications if getattr(item, "status", "") != "APPLIED"]
        summary = json.loads(Path(report_paths["corporate_actions_report_path"]).read_text(encoding="utf-8"))
        return {
            "corporate_action_aware": self.corporate_action_aware,
            "target_return_mode": self.target_return_mode,
            "corporate_action_event_count": summary.get("event_count", 0),
            "implemented_action_count": summary.get("implemented_action_count", 0),
            "corporate_action_applications": len(applications),
            "corporate_action_applied_count": len(applied),
            "corporate_action_skipped_count": len(skipped),
            "corporate_action_cash": sum(float(getattr(item, "cash_amount", 0.0) or 0.0) for item in applied),
            "corporate_action_ledger_path": str(LocalPaperAccount(self.paper_account_dir).corporate_action_ledger_path),
            "cash_ledger_path": str(LocalPaperAccount(self.paper_account_dir).cash_ledger_path),
            "corporate_action_report_path": report_paths["corporate_actions_report_path"],
            "corporate_action_report_md_path": report_paths["corporate_actions_report_md_path"],
            "total_return_report_path": report_paths["total_return_report_path"],
            "adjustment_reconciliation_path": report_paths["adjustment_reconciliation_path"],
            "account_cash_after_corporate_actions": state.cash,
        }

    def _corporate_action_dir(self) -> Path:
        return self.corporate_action_dir or self.corporate_action_output_dir

    def _write_settlement_artifacts(self, state, as_of_date: str) -> dict[str, Any]:
        if not self.settlement_aware:
            return {}
        from settlement_engine.report import write_settlement_report

        paths = write_settlement_report(state, self.settlement_dir, as_of_date, profile_name=self.settlement_profile)
        reconciliation_path = paths.get("account_reconciliation_report_path")
        reconciliation_errors = 0
        nav_difference = 0.0
        if reconciliation_path and Path(reconciliation_path).exists():
            payload = json.loads(Path(reconciliation_path).read_text(encoding="utf-8"))
            reconciliation_errors = int(payload.get("error_count", 0) or 0)
            nav_difference = float(payload.get("nav_difference", 0.0) or 0.0)
        return {
            "settlement_dir": str(self.settlement_dir),
            "settlement_report_path": paths.get("settlement_report_path"),
            "settlement_report_md_path": paths.get("settlement_report_md_path"),
            "settlement_events_path": paths.get("settlement_events_path"),
            "cash_buckets_path": paths.get("cash_buckets_path"),
            "position_lots_path": paths.get("position_lots_path"),
            "position_availability_path": paths.get("position_availability_path"),
            "realized_pnl_path": paths.get("realized_pnl_path"),
            "account_nav_path": paths.get("account_nav_path"),
            "account_performance_report_path": paths.get("account_performance_report_path"),
            "account_reconciliation_report_path": paths.get("account_reconciliation_report_path"),
            "fee_tax_report_path": paths.get("fee_tax_report_path"),
            "settlement_reconciliation_error_count": reconciliation_errors,
            "settlement_nav_difference": nav_difference,
            "pending_settlement_event_count": sum(1 for event in state.settlement_events if event.get("status") == "pending"),
            "failed_settlement_event_count": sum(1 for event in state.settlement_events if event.get("status") == "failed"),
            "realized_pnl": sum(float(record.get("realized_pnl", 0.0) or 0.0) for record in state.realized_pnl_ledger),
        }

    def _run_eod_reconciliation_workflow(self, trade_date: str, broker_batch_id: str | None = None) -> dict[str, Any]:
        from broker_statement import import_statement
        from reconciliation_center import run_eod_reconciliation
        from reconciliation_center.adjustments import apply_approved_adjustments, create_adjustment_approval

        root = self.eod_reconciliation_dir
        root.mkdir(parents=True, exist_ok=True)
        statement_dir = self._prepare_statement_dir(root, trade_date)
        summary: dict[str, Any] = {
            "external_statement_imported": bool(statement_dir),
            "statement_synthetic": False,
        }
        if statement_dir:
            summary.update(_statement_paths(statement_dir))
        if self.run_eod_reconciliation and statement_dir:
            report, _mirror, paths = run_eod_reconciliation(
                statement_dir=statement_dir,
                output_dir=root,
                broker_store_dir=self.broker_store_dir,
                broker_batch_id=broker_batch_id,
                paper_account_dir=self.paper_account_dir,
                settlement_dir=self.settlement_dir,
                corporate_action_dir=self._corporate_action_dir(),
                account_id="paper_ashare",
                trade_date=trade_date,
                as_of_date=trade_date,
                strict=False,
                create_adjustment_proposals=self.create_adjustment_proposals or self.create_adjustment_approval,
            )
            report_summary = report.summary
            summary.update(
                {
                    "eod_reconciliation_status": report.status,
                    "statement_id": report.statement_id,
                    "reconciliation_break_count": int(report_summary.get("break_count", 0) or 0),
                    "material_break_count": int(report_summary.get("material_break_count", 0) or 0),
                    "unresolved_break_count": int(report_summary.get("unresolved_break_count", 0) or 0),
                    "adjustment_proposal_count": int(report_summary.get("adjustment_proposal_count", 0) or 0),
                    "external_cash_difference": float(report_summary.get("cash_difference", 0.0) or 0.0),
                    "external_position_difference": float(report_summary.get("position_share_difference", 0.0) or 0.0),
                    "external_nav_difference": float(report_summary.get("nav_difference", 0.0) or 0.0),
                    "statement_synthetic": bool(report_summary.get("synthetic_statement", False)),
                    **{key: str(value) for key, value in paths.items()},
                }
            )
            if self.create_adjustment_approval:
                batch_path = paths.get("adjustment_proposal_batch_path")
                if batch_path and Path(batch_path).exists():
                    batch_payload = json.loads(Path(batch_path).read_text(encoding="utf-8"))
                    from reconciliation_center.models import AdjustmentProposal, AdjustmentProposalBatch

                    batch = AdjustmentProposalBatch(
                        adjustment_batch_id=str(batch_payload.get("adjustment_batch_id") or ""),
                        account_id=str(batch_payload.get("account_id") or "paper_ashare"),
                        trade_date=str(batch_payload.get("trade_date") or trade_date),
                        as_of_date=str(batch_payload.get("as_of_date") or trade_date),
                        proposals=[AdjustmentProposal(**proposal) for proposal in batch_payload.get("proposals", [])],
                        status=str(batch_payload.get("status") or "pending_approval"),
                        metadata=dict(batch_payload.get("metadata") or {}),
                    )
                    approval = create_adjustment_approval(
                        batch,
                        self.approval_store_dir,
                        reconciliation_report_path=str(paths.get("eod_reconciliation_report_path", "")),
                        adjustment_proposals_path=str(paths.get("adjustment_proposals_path", "")),
                        metadata={
                            "eod_reconciliation_status": report.status,
                            "unresolved_break_count": report_summary.get("unresolved_break_count", 0),
                            "material_break_count": report_summary.get("material_break_count", 0),
                        },
                    )
                    summary["adjustment_approval_id"] = approval.approval_id
                    summary["adjustment_approval_status"] = approval.status
        if self.apply_approved_adjustments:
            approval_id = self.adjustment_approval_id
            if not approval_id:
                raise ValueError("adjustment_approval_id is required when applying approved adjustments")
            result, paths = apply_approved_adjustments(
                self.approval_store_dir,
                approval_id,
                self.paper_account_dir,
                root / "adjustment_apply",
                trade_date=trade_date,
            )
            summary.update(
                {
                    "adjustment_approval_id": approval_id,
                    "adjustment_application_count": result.applied_count,
                    "adjustment_skipped_duplicate_count": result.skipped_duplicate_count,
                    **{key: str(value) for key, value in paths.items()},
                }
            )
        return summary

    def _prepare_statement_dir(self, output_root: Path, trade_date: str) -> Path | None:
        from broker_statement import import_statement

        if self.broker_statement_dir is None:
            return None
        source = self.broker_statement_dir
        if (source / "broker_statement_manifest.json").exists() or any(source.glob("normalized_external_*.jsonl")):
            return source
        imported = output_root / "statement_import"
        result = import_statement(
            source_dir=source,
            output_dir=imported,
            schema_config=self.broker_statement_schema_config,
            schema_name=self.broker_statement_schema,
            account_id="paper_ashare",
            trade_date=trade_date,
            as_of_date=trade_date,
        )
        return Path(result.paths.get("broker_statement_manifest_path", imported)).parent if result.paths else imported


def _paths_from_summary(summary: dict[str, Any]) -> dict[str, str]:
    keys = [
        "targets_path",
        "targets_csv_path",
        "orders_path",
        "orders_csv_path",
        "fills_path",
        "risk_report_path",
        "risk_report_md_path",
        "optimization_result_path",
        "account_state_path",
        "positions_path",
        "account_snapshots_path",
        "capacity_report_path",
        "capacity_report_md_path",
        "execution_plan_path",
        "execution_plan_md_path",
        "parent_orders_path",
        "child_orders_path",
        "child_fills_path",
        "execution_quality_path",
        "broker_report_path",
        "broker_report_md_path",
        "broker_reconciliation_path",
        "broker_reconciliation_md_path",
        "broker_orders_path",
        "broker_events_path",
        "broker_fills_path",
        "broker_outbox_manifest_path",
        "broker_outbox_summary_path",
        "model_registry_report_path",
        "model_lineage_graph_path",
        "corporate_action_report_path",
        "corporate_action_report_md_path",
        "total_return_report_path",
        "adjustment_reconciliation_path",
        "corporate_action_ledger_path",
        "cash_ledger_path",
        "settlement_report_path",
        "settlement_report_md_path",
        "settlement_events_path",
        "cash_buckets_path",
        "position_lots_path",
        "position_availability_path",
        "realized_pnl_path",
        "account_nav_path",
        "account_performance_report_path",
        "account_reconciliation_report_path",
        "fee_tax_report_path",
        "broker_statement_manifest_path",
        "broker_statement_import_report_path",
        "broker_statement_validation_report_path",
        "broker_statement_parse_issues_path",
        "eod_reconciliation_report_path",
        "eod_reconciliation_report_md_path",
        "reconciliation_breaks_path",
        "external_account_mirror_path",
        "external_cash_mirror_path",
        "external_position_mirror_path",
        "external_fill_mirror_path",
        "external_settlement_mirror_path",
        "adjustment_proposals_path",
        "adjustment_proposal_batch_path",
        "adjustment_application_result_path",
        "adjustment_ledger_path",
    ]
    return {key: str(summary[key]) for key in keys if summary.get(key)}


def _statement_paths(statement_dir: Path) -> dict[str, str]:
    candidates = {
        "broker_statement_manifest_path": statement_dir / "broker_statement_manifest.json",
        "broker_statement_import_report_path": statement_dir / "broker_statement_import_report.json",
        "broker_statement_validation_report_path": statement_dir / "broker_statement_validation_report.json",
        "broker_statement_parse_issues_path": statement_dir / "broker_statement_parse_issues.jsonl",
    }
    return {key: str(path) for key, path in candidates.items() if path.exists()}


def _prices_for_date(
    data_dir: Path,
    trade_date: str,
    *,
    corporate_action_aware: bool = False,
    corporate_action_dir: Path | None = None,
    target_return_mode: str = "adjusted_close",
    cash_field: str = "cash_div",
) -> dict[str, float]:
    loader = AShareDataLoader(
        data_dir=data_dir,
        device="cpu",
        corporate_action_aware=corporate_action_aware,
        corporate_action_dir=corporate_action_dir,
        target_return_mode=target_return_mode,
        corporate_action_cash_field=cash_field,
    ).load_data()
    return _market_context(loader, trade_date)[0]


def _load_corporate_action_events(data_dir: Path, corporate_action_dir: Path, cash_field: str) -> list[CorporateActionEvent]:
    event_path = corporate_action_dir / "corporate_action_events.jsonl"
    if event_path.exists():
        return [CorporateActionEvent(**record) for record in read_jsonl(event_path)]
    records = read_jsonl(data_dir / "corporate_actions" / "records.jsonl")
    return normalize_corporate_action_records(records, cash_field=cash_field)


def _market_context(loader: AShareDataLoader, trade_date: str):
    close = loader.raw_data_cache["close"].detach().cpu()
    date_idx = loader.trade_dates.index(trade_date)
    prices = {ts_code: float(close[idx, date_idx].item()) for idx, ts_code in enumerate(loader.ts_codes)}
    volume = loader.raw_data_cache["volume"].detach().cpu()
    is_suspended = loader.raw_data_cache["is_suspended"].detach().cpu()
    limit_up_flag = loader.raw_data_cache["limit_up_flag"].detach().cpu()
    limit_down_flag = loader.raw_data_cache["limit_down_flag"].detach().cpu()
    volumes = {ts_code: float(volume[idx, date_idx].item()) for idx, ts_code in enumerate(loader.ts_codes)}
    suspended = {ts_code: bool(is_suspended[idx, date_idx].item() > 0.5) for idx, ts_code in enumerate(loader.ts_codes)}
    limit_up = {ts_code: bool(limit_up_flag[idx, date_idx].item() > 0.5) for idx, ts_code in enumerate(loader.ts_codes)}
    limit_down = {ts_code: bool(limit_down_flag[idx, date_idx].item() > 0.5) for idx, ts_code in enumerate(loader.ts_codes)}
    return prices, volumes, suspended, limit_up, limit_down


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _safe_time(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in value).strip("_")

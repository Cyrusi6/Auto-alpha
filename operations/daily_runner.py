"""Daily production run orchestration."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from approval import ApprovalStatus, LocalApprovalStore
from execution import ExecutionOrder, PaperBroker, export_fills_jsonl, export_orders_csv, export_orders_jsonl
from execution_plan import ChildOrder, ExecutionPlanResult, ExecutionSchedule, ParentOrder, simulate_child_orders, write_execution_plan_report
from factor_store import LocalFactorStore
from model_core.data_loader import AShareDataLoader
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

    def run(
        self,
        require_approval: bool = False,
        approval_id: str | None = None,
        execute_approved: bool = False,
    ) -> ProductionRunResult:
        created_at = _utc_now()
        run_id = f"prod_{_safe_time(created_at)}"
        try:
            if approval_id or execute_approved:
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
        ).generate_orders()
        if not require_approval:
            fills = _read_jsonl(Path(str(summary.get("fills_path"))))
            prices = _prices_for_date(self.data_dir, str(summary["rebalance_date"]))
            account_state = LocalPaperAccount(self.paper_account_dir).apply_fills(fills, prices, str(summary["rebalance_date"]))
            account_state = LocalPaperAccount(self.paper_account_dir).mark_to_market(prices, str(summary["rebalance_date"]))
            summary["account"] = {
                "cash": account_state.cash,
                "positions": len(account_state.positions),
                "performance": compute_account_performance(account_state),
            }
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
        loader = AShareDataLoader(data_dir=self.data_dir, device="cpu").load_data()
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
        if batch.child_orders:
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
        account.apply_child_fills(fills, prices, batch.rebalance_date)
        state = account.mark_to_market(prices, batch.rebalance_date)
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
            "account": {
                "cash": state.cash,
                "positions": len(state.positions),
                "performance": compute_account_performance(state),
            },
        }
        return ProductionRunResult(
            run_id=run_id,
            created_at=created_at,
            status="executed",
            factor_id=batch.factor_id,
            rebalance_date=batch.rebalance_date,
            approval_id=batch.approval_id,
            approval_status=batch.status,
            executed=True,
            paths=_paths_from_summary(summary),
            summary=summary,
        )

    def _select_factor_id(self) -> str:
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
    ]
    return {key: str(summary[key]) for key in keys if summary.get(key)}


def _prices_for_date(data_dir: Path, trade_date: str) -> dict[str, float]:
    loader = AShareDataLoader(data_dir=data_dir, device="cpu").load_data()
    return _market_context(loader, trade_date)[0]


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

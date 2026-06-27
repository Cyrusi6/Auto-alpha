"""CLI for A-share target and paper order generation."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from approval import ApprovalBatch, ApprovalOrder, LocalApprovalStore
from backtest import TargetPosition, build_long_only_targets, describe_factor, factor_values_to_matrix, select_factor_id
from execution import PaperBroker, export_orders_csv, export_orders_jsonl
from factor_store import LocalFactorStore
from model_core.data_loader import AShareDataLoader
from portfolio_optimizer import OptimizationConfig, PortfolioOptimizer
from risk_model import benchmark_weights_from_index_members, build_risk_report, estimate_return_covariance, write_risk_report

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
        index_code: str = "000300.SH",
        risk_aversion: float = 1.0,
        turnover_penalty: float = 0.1,
        max_turnover: float = 1.0,
        max_industry_active_weight: float = 0.20,
        max_tracking_error: float = 1.0,
        propose_only: bool = False,
        require_approval: bool = False,
        approval_store_dir: str | Path | None = None,
    ):
        self.data_dir = Path(data_dir)
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
        self.index_code = index_code
        self.risk_aversion = float(risk_aversion)
        self.turnover_penalty = float(turnover_penalty)
        self.max_turnover = float(max_turnover)
        self.max_industry_active_weight = float(max_industry_active_weight)
        self.max_tracking_error = float(max_tracking_error)
        self.propose_only = bool(propose_only)
        self.require_approval = bool(require_approval)
        self.approval_store_dir = Path(approval_store_dir) if approval_store_dir is not None else None
        self.loader: AShareDataLoader | None = None
        self.selected_factor_id: str | None = None
        self.selected_factor_meta: dict[str, object] = {}
        self.optimization_summary: dict[str, object] | None = None
        self.risk_report = None

    def build_target_book(self) -> StrategyTargetBook:
        if self.portfolio_method not in {"equal_weight", "risk_aware"}:
            raise ValueError("portfolio_method must be equal_weight or risk_aware")
        self.loader = AShareDataLoader(data_dir=self.data_dir, device="cpu").load_data()
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
            covariance = estimate_return_covariance(self.loader)
            config = OptimizationConfig(
                risk_aversion=self.risk_aversion,
                turnover_penalty=self.turnover_penalty,
                max_weight=self.max_weight,
                max_names=self.top_n,
                max_turnover=self.max_turnover,
                max_industry_active_weight=self.max_industry_active_weight,
                max_tracking_error=self.max_tracking_error,
            )
            opt_result = PortfolioOptimizer(config).optimize(
                factor_matrix[:, date_idx],
                current_weights=benchmark * 0.0,
                benchmark_weights=benchmark,
                covariance=covariance,
                loader=self.loader,
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

        self.output_dir.mkdir(parents=True, exist_ok=True)
        targets_csv = export_orders_csv(target_book.targets, self.output_dir / "target_positions.csv")
        targets_jsonl = export_orders_jsonl(target_book.targets, self.output_dir / "target_positions.jsonl")
        orders_csv = export_orders_csv(orders, self.output_dir / "orders.csv")
        orders_jsonl = export_orders_jsonl(orders, self.output_dir / "orders.jsonl")
        risk_report_path = None
        risk_report_md_path = None
        optimization_result_path = None
        if self.portfolio_method == "risk_aware":
            if self.risk_report is not None:
                risk_json, risk_md = write_risk_report(self.risk_report, self.output_dir)
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

        approval_id = None
        approval_status = None
        fills = []
        fills_path = self.output_dir / "paper_fills.jsonl"
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
                    "n_orders": len(orders),
                    "gross_order_value": float(sum(order.order_value for order in orders)),
                },
                metadata={
                    "targets_path": str(targets_jsonl),
                    "orders_path": str(orders_jsonl),
                    "output_dir": str(self.output_dir),
                    "component_factor_ids": self.selected_factor_meta.get("component_factor_ids", []),
                },
            )
            LocalApprovalStore(self.approval_store_dir).save_batch(batch)
            approval_id = batch.approval_id
            approval_status = batch.status
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
            "rebalance_date": target_book.trade_date,
            "n_targets": len(target_book.targets),
            "n_orders": len(orders),
            "n_fills": len(fills),
            "n_rejected": rejected,
            "n_partial": partial,
            "fill_rate": completed / len(fills) if fills else 0.0,
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
            "risk_report_path": risk_report_path,
            "risk_report_md_path": risk_report_md_path,
            "optimization_result_path": str(optimization_result_path) if optimization_result_path else None,
        }


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
    parser.add_argument("--index-code", default="000300.SH")
    parser.add_argument("--risk-aversion", type=float, default=1.0)
    parser.add_argument("--turnover-penalty", type=float, default=0.1)
    parser.add_argument("--max-turnover", type=float, default=1.0)
    parser.add_argument("--max-industry-active-weight", type=float, default=0.20)
    parser.add_argument("--max-tracking-error", type=float, default=1.0)
    parser.add_argument("--propose-only", action="store_true")
    parser.add_argument("--require-approval", action="store_true")
    parser.add_argument("--approval-store-dir")
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
        index_code=args.index_code,
        risk_aversion=args.risk_aversion,
        turnover_penalty=args.turnover_penalty,
        max_turnover=args.max_turnover,
        max_industry_active_weight=args.max_industry_active_weight,
        max_tracking_error=args.max_tracking_error,
        propose_only=args.propose_only,
        require_approval=args.require_approval,
        approval_store_dir=args.approval_store_dir,
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


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _safe_time(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in value).strip("_")


if __name__ == "__main__":
    raise SystemExit(main())

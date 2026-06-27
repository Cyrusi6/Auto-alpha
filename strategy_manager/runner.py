"""CLI for A-share target and paper order generation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from backtest import build_long_only_targets, describe_factor, factor_values_to_matrix, select_factor_id
from execution import PaperBroker, export_orders_csv, export_orders_jsonl
from factor_store import LocalFactorStore
from model_core.data_loader import AShareDataLoader

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
        self.loader: AShareDataLoader | None = None
        self.selected_factor_id: str | None = None
        self.selected_factor_meta: dict[str, object] = {}

    def build_target_book(self) -> StrategyTargetBook:
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

        close = self.loader.raw_data_cache["close"].detach().cpu()
        date_idx = self.loader.trade_dates.index(target_book.trade_date)
        prices = {
            ts_code: float(close[idx, date_idx].item())
            for idx, ts_code in enumerate(self.loader.ts_codes)
        }
        volume = self.loader.raw_data_cache["volume"].detach().cpu()
        is_suspended = self.loader.raw_data_cache["is_suspended"].detach().cpu()
        limit_up = self.loader.raw_data_cache["limit_up_flag"].detach().cpu()
        limit_down = self.loader.raw_data_cache["limit_down_flag"].detach().cpu()
        volumes = {ts_code: float(volume[idx, date_idx].item()) for idx, ts_code in enumerate(self.loader.ts_codes)}
        suspended = {ts_code: bool(is_suspended[idx, date_idx].item() > 0.5) for idx, ts_code in enumerate(self.loader.ts_codes)}
        limit_up_flags = {ts_code: bool(limit_up[idx, date_idx].item() > 0.5) for idx, ts_code in enumerate(self.loader.ts_codes)}
        limit_down_flags = {ts_code: bool(limit_down[idx, date_idx].item() > 0.5) for idx, ts_code in enumerate(self.loader.ts_codes)}
        fills = PaperBroker(self.output_dir).submit_orders(
            orders,
            prices,
            target_book.trade_date,
            volumes=volumes,
            suspended=suspended,
            limit_up=limit_up_flags,
            limit_down=limit_down_flags,
        )
        fills_path = self.output_dir / "paper_fills.jsonl"
        rejected = sum(1 for fill in fills if fill.status == "REJECTED")
        partial = sum(1 for fill in fills if fill.status == "PARTIAL")
        completed = sum(1 for fill in fills if fill.status in {"FILLED", "PARTIAL"})

        return {
            "factor_id": self.selected_factor_id,
            "factor_type": self.selected_factor_meta.get("factor_type"),
            "component_factor_ids": self.selected_factor_meta.get("component_factor_ids", []),
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
    ).generate_orders()
    print(json.dumps(summary, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

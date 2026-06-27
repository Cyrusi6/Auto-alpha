"""CLI for benchmark-aware A-share portfolio optimization."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from backtest.io import describe_factor, select_factor_id
from execution.exporter import export_orders_jsonl
from factor_store import LocalFactorStore
from model_core.data_loader import AShareDataLoader
from risk_model import (
    benchmark_weights_from_index_members,
    build_barra_like_risk_model,
    build_risk_report,
    estimate_return_covariance,
    write_risk_model_report,
    write_risk_report,
)

from .models import OptimizationConfig
from .optimizer import PortfolioOptimizer


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run benchmark-aware A-share portfolio optimization.")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--factor-store-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--factor-id")
    parser.add_argument("--latest-approved", action="store_true")
    parser.add_argument("--factor-type", choices=["single", "composite", "any"], default="any")
    parser.add_argument("--index-code", default="000300.SH")
    parser.add_argument("--as-of-date")
    parser.add_argument("--max-weight", type=float, default=0.10)
    parser.add_argument("--max-names", type=int, default=20)
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
    parser.add_argument("--max-factor-risk-contribution", type=float)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    output_dir = Path(args.output_dir)
    loader = AShareDataLoader(data_dir=args.data_dir, device="cpu").load_data()
    store = LocalFactorStore(args.factor_store_dir)
    factor_id = select_factor_id(store, args.factor_id, latest_approved=args.latest_approved, factor_type=args.factor_type)
    factor_meta = describe_factor(store, factor_id)
    factor_matrix = store.load_factor_values_matrix(factor_id, loader.ts_codes, loader.trade_dates)
    as_of_date = args.as_of_date or loader.trade_dates[-1]
    date_idx = loader.trade_dates.index(as_of_date) if as_of_date in loader.trade_dates else len(loader.trade_dates) - 1
    benchmark = benchmark_weights_from_index_members(loader, args.index_code, as_of_date)
    covariance = estimate_return_covariance(loader)
    config = OptimizationConfig(
        risk_aversion=args.risk_aversion,
        turnover_penalty=args.turnover_penalty,
        max_weight=args.max_weight,
        max_names=args.max_names,
        max_turnover=args.max_turnover,
        max_industry_active_weight=args.max_industry_active_weight,
        max_tracking_error=args.max_tracking_error,
        use_factor_risk_model=args.use_factor_risk_model,
        risk_model_lookback=args.risk_model_lookback,
        risk_model_shrinkage=args.risk_model_shrinkage,
        max_style_exposure=args.max_style_exposure,
        max_active_style_exposure=args.max_active_style_exposure,
        max_factor_risk_contribution=args.max_factor_risk_contribution,
    )
    result = PortfolioOptimizer(config).optimize(
        factor_matrix[:, date_idx],
        current_weights=benchmark * 0.0,
        benchmark_weights=benchmark,
        covariance=covariance,
        loader=loader,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    weights_records = [
        {
            "trade_date": as_of_date,
            "ts_code": ts_code,
            "optimized_weight": weight,
            "benchmark_weight": float(benchmark[loader.ts_codes.index(ts_code)].item()),
            "active_weight": weight - float(benchmark[loader.ts_codes.index(ts_code)].item()),
        }
        for ts_code, weight in result.weights.items()
    ]
    export_orders_jsonl(weights_records, output_dir / "optimized_weights.jsonl")
    (output_dir / "optimization_result.json").write_text(
        json.dumps(
            {
                **result.to_dict(),
                "factor_id": factor_id,
                "factor_type": factor_meta["factor_type"],
                "component_factor_ids": factor_meta["component_factor_ids"],
                "index_code": args.index_code,
                "as_of_date": as_of_date,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    weight_vector = _weights_to_vector(result.weights, loader.ts_codes)
    factor_risk_model = (
        build_barra_like_risk_model(loader, lookback=args.risk_model_lookback, shrinkage=args.risk_model_shrinkage)
        if args.use_factor_risk_model
        else None
    )
    risk_report = build_risk_report(
        weight_vector,
        benchmark,
        loader,
        args.index_code,
        as_of_date,
        factor_id=factor_id,
        covariance=covariance,
        turnover=result.turnover,
        factor_risk_model=factor_risk_model,
    )
    risk_json, risk_md = write_risk_report(risk_report, output_dir)
    risk_model_json = None
    risk_model_md = None
    if args.use_factor_risk_model:
        risk_model_json, risk_model_md = write_risk_model_report(risk_report, output_dir)
    summary = {
        "factor_id": factor_id,
        "factor_type": factor_meta["factor_type"],
        "component_factor_ids": factor_meta["component_factor_ids"],
        "output_dir": str(output_dir),
        "weights_path": str(output_dir / "optimized_weights.jsonl"),
        "optimization_result_path": str(output_dir / "optimization_result.json"),
        "risk_report_path": str(risk_json),
        "risk_report_md_path": str(risk_md),
        "risk_model_report_path": str(risk_model_json) if risk_model_json else None,
        "risk_model_report_md_path": str(risk_model_md) if risk_model_md else None,
        "metrics": risk_report.metrics.to_dict(),
        "violations": result.violations,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


def _weights_to_vector(weights: dict[str, float], ts_codes: list[str]):
    import torch

    return torch.tensor([float(weights.get(ts_code, 0.0)) for ts_code in ts_codes], dtype=torch.float32)


if __name__ == "__main__":
    raise SystemExit(main())

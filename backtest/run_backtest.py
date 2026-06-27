"""CLI for running local A-share portfolio simulation."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from factor_store import LocalFactorStore
from model_core.data_loader import AShareDataLoader

from .io import describe_factor, factor_values_to_matrix, select_factor_id
from .simulator import AShareBacktestSimulator


def _write_jsonl(path: Path, records: list[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(asdict(record), ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run local A-share portfolio simulation.")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--factor-store-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--factor-id")
    parser.add_argument("--latest-approved", action="store_true")
    parser.add_argument("--factor-type", choices=["single", "composite", "any"], default="any")
    parser.add_argument("--initial-cash", type=float, default=1_000_000.0)
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--max-weight", type=float, default=0.10)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    output_dir = Path(args.output_dir)
    loader = AShareDataLoader(data_dir=args.data_dir, device="cpu").load_data()
    store = LocalFactorStore(args.factor_store_dir)
    factor_id = select_factor_id(
        store,
        args.factor_id,
        latest_approved=args.latest_approved,
        factor_type=args.factor_type,
    )
    factor_meta = describe_factor(store, factor_id)
    values = store.load_factor_values(factor_id)
    factors = factor_values_to_matrix(values, loader.ts_codes, loader.trade_dates)

    result = AShareBacktestSimulator(
        initial_cash=args.initial_cash,
        top_n=args.top_n,
        max_weight=args.max_weight,
    ).simulate(factors, loader)

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "backtest_result.json").write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_jsonl(output_dir / "equity_curve.jsonl", result.snapshots)
    _write_jsonl(output_dir / "trades.jsonl", result.fills)

    summary = {
        "factor_id": factor_id,
        "factor_type": factor_meta["factor_type"],
        "component_factor_ids": factor_meta["component_factor_ids"],
        "output_dir": str(output_dir),
        "metrics": result.metrics,
        "n_snapshots": len(result.snapshots),
        "n_trades": len(result.fills),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

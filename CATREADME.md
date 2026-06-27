# Current Repository Architecture

This repository is now organized as a local A-share factor research platform. The main workflow is:

1. Prepare A-share data artifacts.
2. Build feature tensors and evaluate formula factors.
3. Register factors and experiments.
4. Run portfolio simulation.
5. Export target positions and paper orders.
6. Review artifacts in the dashboard.

## Data Layer

`data_pipeline/` owns A-share data models, configuration, providers, local JSONL storage, and sync orchestration. It supports deterministic sample data and a standard-library Tushare Pro HTTP provider.

The sample provider writes:

- `securities/records.jsonl`
- `trade_calendar/records.jsonl`
- `daily_bars/records.jsonl`
- `daily_basic/records.jsonl`
- `financial_features/records.jsonl`
- `manifest.json`

## Factor Research Layer

`model_core/` owns:

- A-share feature engineering.
- Formula vocabulary and operators.
- StackVM formula execution.
- RankIC, coverage, spread, turnover, and score evaluation.
- Dry-run and minimal training engine.

The engine can register factor outputs into the factor store.

## Factor Store And Experiments

`factor_store/` persists:

- `factors.jsonl`
- `experiments.jsonl`
- `factor_values/<factor_id>.jsonl`

`evaluation/` provides train/valid/test splitting, split-level metrics, and factor reports in JSON and Markdown.

## Portfolio Simulation

`backtest/` reads factor values, builds long-only target weights, estimates local trading costs, and writes:

- `backtest_result.json`
- `equity_curve.jsonl`
- `trades.jsonl`

## Paper Execution And Order Export

`execution/` provides local paper fills and order/fill export helpers.

`strategy_manager/` builds a target book for a rebalance date, validates weights, generates orders, and writes:

- `target_positions.csv`
- `target_positions.jsonl`
- `orders.csv`
- `orders.jsonl`
- `paper_fills.jsonl`

## Dashboard

`dashboard/` is a Streamlit artifact viewer. It reads local data, factor store, reports, backtest outputs, target positions, orders, and paper fills. Missing artifacts produce empty states instead of errors.

## Development Notes

The platform is local-first and deterministic by default. Production-grade Tushare incremental sync, richer neutralization, and broker connectivity are future work.

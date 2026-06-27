# Current Repository Architecture

This repository is now organized as a local A-share factor research platform. The main workflow is:

1. Prepare A-share data artifacts.
2. Build feature tensors and evaluate formula factors.
3. Register factors and experiments.
4. Run batch or search-style research and build composite factors.
5. Run portfolio simulation.
6. Export target positions and paper orders.
7. Review artifacts in the dashboard.

## Data Layer

`data_pipeline/` owns A-share data models, configuration, providers, local JSONL storage, sync orchestration, data quality checks, and sync state. It supports deterministic sample data and a standard-library Tushare Pro HTTP provider.

The sample provider writes:

- `securities/records.jsonl`
- `trade_calendar/records.jsonl`
- `daily_bars/records.jsonl`
- `daily_basic/records.jsonl`
- `financial_features/records.jsonl`
- `daily_limits/records.jsonl`
- `adjustment_factors/records.jsonl`
- `index_members/records.jsonl`
- `manifest.json`
- `pipeline_state.json`
- `quality_report.json` when validation is enabled

Append mode merges incoming records by dataset primary key. The quality report checks empty datasets, invalid dates, invalid stock codes, duplicate keys, daily bar price errors, financial announcement date fields, limit prices, adjustment factors, and index constituent weights.

## Universe Layer

`universe/` builds local A-share research universes from governed data artifacts or `index_members`. It filters invalid stock codes, special-treatment names, delisted securities, suspended daily bars, listing age, amount, exchange, and board, then writes:

- `universe/<universe_name>.jsonl`
- `universe/<universe_name>_summary.json`

## Factor Research Layer

`model_core/` owns:

- A-share feature engineering.
- Formula vocabulary and operators.
- StackVM formula execution.
- RankIC, coverage, spread, turnover, and score evaluation.
- Universe-aware dry-run and minimal training engine.

`factor_engine/` adds cross-sectional preprocessing, basic market-cap and industry neutralization, factor correlation checks, and admission gates. The engine can register transformed factor outputs into the factor store with gate metadata and similar-factor information.

`research/` orchestrates batch factor experiments. It loads default or JSON-defined candidate formulas, executes StackVM, applies transforms and gates, skips duplicate formula hashes, writes per-factor reports, ranks candidates, and can register a composite factor. Composite methods include equal weight, score weight, and rank average.

`formula_search/` adds local formula discovery. It uses StackVM metadata to generate legal RPN formulas, estimate arity/lookback/complexity, mutate formulas, cross over parent formulas, remove duplicate hashes, and run multi-generation search through the same batch research pipeline.

## Factor Store And Experiments

`factor_store/` persists:

- `factors.jsonl`
- `experiments.jsonl`
- `factor_values/<factor_id>.jsonl`

`evaluation/` provides train/valid/test splitting, split-level metrics, and factor reports in JSON and Markdown. Reports render metric columns dynamically and include transform, gate, status, and correlation metadata when available.

Batch research writes:

- `batch_result.json`
- `batch_results.jsonl`
- `batch_report.json`
- `batch_report.md`

Formula search writes:

- `search_result.json`
- `search_candidates.jsonl`
- `search_report.json`
- `search_report.md`

Composite factor records use `factor_type=composite` and store component factor ids in metadata.

## Portfolio Simulation

`backtest/` reads single or composite factor values, builds long-only target weights, estimates local trading costs, and writes:

- `backtest_result.json`
- `equity_curve.jsonl`
- `trades.jsonl`

Returns are based on `adjusted_close`; simulated fills use raw `close`. The simulator applies suspension, limit up/down, T+1 selling, board-lot rounding, volume participation, and cost rules, with rejected and partial fills recorded in `trades.jsonl`.

## Paper Execution And Order Export

`execution/` provides local paper fills and order/fill export helpers using the same A-share trading rule primitives as the backtest.

`strategy_manager/` builds a target book for a rebalance date, validates weights, generates orders, and writes:

- `target_positions.csv`
- `target_positions.jsonl`
- `orders.csv`
- `orders.jsonl`
- `paper_fills.jsonl`

## Dashboard

`dashboard/` is a Streamlit artifact viewer. It reads local data, factor store, factor reports, batch reports, search reports, backtest outputs, target positions, orders, and paper fills. Missing artifacts produce empty states instead of errors.

## Development Notes

The platform is local-first and deterministic by default. Production-grade Tushare incremental sync, neural-guided formula search, more stable neural training, finer matching realism, minute-level liquidity, fuller risk-model neutralization, finer industry classification, large-scale performance tuning, and broker connectivity are future work.

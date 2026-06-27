# Current Repository Architecture

This repository is now organized as a local A-share factor research platform. The main workflow is:

1. Prepare A-share data artifacts.
2. Build feature tensors and evaluate formula factors.
3. Register factors and experiments.
4. Run batch or search-style research and build composite factors.
5. Run the one-click research suite, including walk-forward and promotion.
6. Run equal-weight or benchmark-aware portfolio simulation.
7. Export target positions and paper orders.
8. Run approval-gated daily paper operations.
9. Review artifacts and monitoring in the dashboard.

## Data Layer

`data_pipeline/` owns A-share data models, configuration, providers, local JSONL storage, sync planning, response cache, request audit, compaction, snapshots, dataset statistics, data quality checks, and sync state. It supports deterministic sample data and a standard-library Tushare Pro HTTP provider.

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
- `sync_plan.json` when planned sync is used
- `api_audit.jsonl` when request audit is enabled
- `dataset_stats.json` when stats are requested
- `snapshots/<snapshot_name>/<dataset>/records.jsonl` when snapshots are requested
- `<dataset>/index.json` when a dataset index is built

Append mode merges incoming records by dataset primary key. The quality report checks empty datasets, invalid dates, invalid stock codes, duplicate keys, daily bar price errors, financial announcement date fields, limit prices, adjustment factors, and index constituent weights.

Planned sync splits large daily datasets by date windows and splits index constituents by index code plus date windows. Tushare planned jobs can use local response cache, request audit, resume from successful job ids in `pipeline_state.json`, post-sync compaction, snapshots, and dataset statistics. `run_pipeline --validate-only` checks existing data without fetching new records.

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

`neural_search/` adds a lightweight neural-guided formula search path. It uses AlphaGPT with supervised warm-start sequences from the factor store, default candidates, and seed formulas; a StackVM-aware action mask prevents underflow during sampling; policy steps convert research outcomes into rewards; checkpoints and training reports are written as local artifacts.

`research_suite/` orchestrates the complete local workflow. It can run data sync, universe construction, formula search, backtest, paper orders, walk-forward robustness, promotion, suite report writing, and artifact catalog generation in one command.

## Risk Model And Portfolio Optimization

`risk_model/` builds local A-share risk views from the loaded data artifacts:

- stock-level industry, size, volatility, and beta exposures
- portfolio and benchmark industry weights
- active exposure versus an index benchmark from `index_members`
- return covariance, portfolio volatility, and tracking error
- constraint checks for max weight, industry active weight, total active weight, tracking error, names, and HHI
- `risk_report.json` and `risk_report.md`

`portfolio_optimizer/` provides a deterministic long-only benchmark-aware optimizer. It ranks alpha scores, tilts from benchmark weights, clamps max names and max weight, shrinks turnover and tracking error, and outputs:

- `optimized_weights.jsonl`
- `optimization_result.json`
- `risk_report.json`
- `risk_report.md`

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

Neural search writes:

- `neural_search_result.json`
- `neural_training_history.jsonl`
- `neural_search_report.md`
- `checkpoints/checkpoint_<phase>_<step>.pt`

`formula_search.run_search --search-mode neural` delegates to neural search. `--search-mode hybrid` runs the neural branch and the random/mutation/crossover branch together, then records neural metadata and checkpoint paths in the search result. `research_suite.run_suite --search-mode neural|hybrid` uses the same path in the one-click workflow.

Composite factor records use `factor_type=composite` and store component factor ids in metadata.

Promotion can update a passing composite factor to `status=production_candidate`. The promotion decision is stored as JSON and also merged into factor metadata.

## Portfolio Simulation

`backtest/` reads single or composite factor values, builds long-only target weights, estimates local trading costs, and writes:

- `backtest_result.json`
- `equity_curve.jsonl`
- `trades.jsonl`

Returns are based on `adjusted_close`; simulated fills use raw `close`. The simulator applies suspension, limit up/down, T+1 selling, board-lot rounding, volume participation, and cost rules, with rejected and partial fills recorded in `trades.jsonl`.

Backtest supports `--portfolio-method equal_weight` and `--portfolio-method risk_aware`. Risk-aware mode calls the optimizer on each rebalance date, records tracking error, active share, HHI, top weight, industry active exposure, and risk constraint violations, and can write a risk report directory.

## Paper Execution And Order Export

`execution/` provides local paper fills and order/fill export helpers using the same A-share trading rule primitives as the backtest.

`strategy_manager/` builds a target book for a rebalance date, validates weights, generates orders, and writes:

- `target_positions.csv`
- `target_positions.jsonl`
- `orders.csv`
- `orders.jsonl`
- `paper_fills.jsonl`

With `--portfolio-method risk_aware`, target positions include optimized weight, benchmark weight, and active weight. The summary includes risk metrics and constraint violations.

## Production Operations

`approval/` stores proposed order batches for local human review. It writes:

- `approvals/<approval_id>.json`
- `approval_log.jsonl`

Batches move through `pending`, `approved`, `rejected`, and `expired`. Approved batches cannot be rejected later, and every decision is logged.

`paper_account/` maintains a persistent local paper ledger. It writes:

- `account_state.json`
- `positions.jsonl`
- `cash_ledger.jsonl`
- `trade_ledger.jsonl`
- `account_snapshots.jsonl`

Filled and partial fills update cash and positions. Rejected fills are recorded in the trade ledger but do not change holdings. Mark-to-market writes account snapshots and performance metrics.

`operations/` runs the daily production path:

1. Select a `production_candidate` factor, or fall back to the latest approved composite factor.
2. Generate target positions and proposed orders with `strategy_manager`.
3. If approval is required, write a pending approval batch and stop.
4. After approval, execute local paper fills, update the paper account, and write `production_run.json` plus `production_run.md`.

`monitoring/` checks local production artifacts:

- data freshness versus as-of date
- quality report errors
- production factor availability and recent factor drift
- risk report violations
- rejected and partial fill ratios
- paper account equity, cash ratio, drawdown, and exposure

It writes `monitoring_report.json`, `monitoring_report.md`, and `alerts.jsonl`.

## Dashboard

`dashboard/` is a Streamlit artifact viewer. It reads local data, sync plans, request audit, dataset statistics, snapshot summaries, factor store, factor reports, batch reports, search reports, neural search reports, neural training history, checkpoint lists, suite reports, artifact catalog, promotion decisions, risk reports, optimization results, backtest outputs, target positions, orders, paper fills, production runs, approvals, paper account state, account ledgers, monitoring reports, and alerts. Missing artifacts produce empty states instead of errors.

## Research Suite Outputs

`research_suite.run_suite` writes:

- `suite_result.json`
- `suite_report.md`
- `walk_forward_result.json`
- `promotion_decision.json`
- `artifact_catalog.json`
- `artifact_catalog.md`

The artifact catalog indexes data manifest, quality report, pipeline state, universe summary, search reports, factor store files, selected factor values, backtest outputs, risk reports, optimization results, order outputs, suite report, and promotion decision.

## Development Notes

The platform is local-first and deterministic by default. Production sync now has a local plan/cache/audit/resume/snapshot/statistics skeleton. Risk model and portfolio optimization now have a basic benchmark-aware local implementation. Neural-guided formula search now has a local AlphaGPT policy-search implementation. Daily production now has local approvals, paper account ledger, and monitoring reports. Real Tushare token and quota validation, full-market performance testing, cross-source data checks, Barra-like multi-factor risk, more robust covariance estimation, a production optimizer, stronger reinforcement learning, offline pretraining, richer walk-forward policies, richer approval policies, human review workflow, broader neural training stability validation, finer matching realism, minute-level volume modeling, finer industry classification, large-scale performance tuning, and broker connectivity are future work.

# Auto-alpha

Auto-alpha is an A-share quantitative factor research platform. It provides a local, reproducible workflow for data preparation, factor formula research, factor registration, portfolio simulation, paper order export, and artifact review.

The current implementation is local-first. It uses deterministic sample data and JSON/JSONL artifacts so the full research loop can run without external services, while the Tushare HTTP provider can be enabled with a valid token.

## Modules

- `data_pipeline/`: A-share data configuration, sample and Tushare HTTP providers, market constraint datasets, sync planning, response cache, request audit, local JSONL storage, data quality checks, sync state, and data sync CLI.
- `universe/`: Local A-share universe construction from governed data artifacts.
- `model_core/`: A-share feature engineering, formula vocabulary, DSL operators, StackVM execution, factor evaluation, and mining engine.
- `factor_engine/`: Cross-sectional preprocessing, market-cap and industry neutralization, correlation checks, and factor admission gate.
- `factor_store/`: Local factor registry, experiment registry, factor value storage, and stable factor identifiers.
- `evaluation/`: Time-series sample split, split-level metrics, and factor reports.
- `research/`: Batch candidate execution, factor ranking, composite factor construction, and batch research reports.
- `formula_search/`: Formula metadata, random generation, mutation, crossover, multi-generation search, and search reports.
- `neural_search/`: AlphaGPT warm-start training, action-mask constrained formula sampling, lightweight policy search, checkpointing, and neural search reports.
- `research_suite/`: One-click orchestration, walk-forward robustness, production-candidate promotion, and artifact catalog.
- `risk_model/`: Security exposures, Barra-like style and industry factors, factor returns, risk decomposition, attribution, covariance, risk constraints, and risk reports.
- `portfolio_optimizer/`: Deterministic long-only benchmark-aware portfolio optimizer.
- `capacity_model/`: Local capacity, participation, and impact-cost estimates from amount, volume, turnover, and volatility.
- `backtest/`: Long-only A-share portfolio simulation, market constraints, and benchmark-aware risk mode.
- `execution/`: Paper broker and order/fill export utilities.
- `execution_plan/`: Parent-order to child-order slicing, schedule simulation, and execution quality reports.
- `broker_adapter/`: Local broker contract, simulated broker state machine, file instruction outbox/inbox skeleton, broker events, fills, and reconciliation.
- `strategy_manager/`: Target position and paper order generation.
- `approval/`: Local proposed-order batch approval, rejection, expiration, and audit log.
- `paper_account/`: Persistent local paper cash, positions, trades, snapshots, and performance ledger.
- `operations/`: Daily production run orchestration from production factor to proposed orders, approval-gated execution, account update, and production report.
- `monitoring/`: Local production checks for data freshness, quality, factor drift, fill quality, account performance, and alerts.
- `matrix_store/`: Governed JSONL to numpy matrix cache builder, reader, and validator for faster local loading.
- `performance_benchmark/`: Lightweight local benchmark runner for data loading, formula execution, research batches, formula search, and portfolio simulation.
- `cross_source_checks/`: Local dataset comparison reports across data directories or snapshots.
- `dashboard/`: Streamlit dashboard for local artifacts.

## Quickstart

Run the one-click sample suite:

```bash
rm -rf /tmp/auto-alpha-demo

uv run python -m research_suite.run_suite \
  --suite-name sample_suite \
  --provider sample \
  --data-dir /tmp/auto-alpha-demo/data \
  --universe-name csi300_sample \
  --index-code 000300.SH \
  --factor-store-dir /tmp/auto-alpha-demo/store \
  --report-dir /tmp/auto-alpha-demo/reports \
  --output-dir /tmp/auto-alpha-demo/suite \
  --backtest-dir /tmp/auto-alpha-demo/backtest \
  --orders-dir /tmp/auto-alpha-demo/orders \
  --as-of-date 20240104 \
  --factor-transform winsorize_zscore \
  --search-mode hybrid \
  --search-seed 42 \
  --search-population-size 12 \
  --search-generations 2 \
  --search-max-candidates 8 \
  --neural-warmup-steps 1 \
  --neural-policy-steps 1 \
  --top-k 5 \
  --composite-method rank_average \
  --promote-latest-composite \
  --walk-forward-train-size 1 \
  --walk-forward-test-size 1 \
  --walk-forward-step-size 1 \
  --pretty
```

Open the dashboard against those artifacts:

```bash
ASHARE_DASHBOARD_DATA_DIR=/tmp/auto-alpha-demo/data \
ASHARE_DASHBOARD_FACTOR_STORE_DIR=/tmp/auto-alpha-demo/store \
ASHARE_DASHBOARD_REPORT_DIR=/tmp/auto-alpha-demo/reports \
ASHARE_DASHBOARD_BACKTEST_DIR=/tmp/auto-alpha-demo/backtest \
ASHARE_DASHBOARD_ORDERS_DIR=/tmp/auto-alpha-demo/orders \
uv run streamlit run dashboard/app.py
```

## Environment

Common variables:

- `TUSHARE_TOKEN`: required when `ASHARE_PROVIDER=tushare`.
- `TUSHARE_API_URL`: optional Tushare Pro HTTP endpoint override.
- `TUSHARE_TIMEOUT_SECONDS`: optional HTTP timeout.
- `TUSHARE_RETRY_COUNT`: optional HTTP retry count.
- `ASHARE_PROVIDER`: data provider, `sample` for local deterministic data or `tushare` for Tushare Pro HTTP sync.
- `ASHARE_DATA_DIR`: local A-share data directory.
- `ASHARE_FACTOR_STORE_DIR`: local factor store directory.
- `ASHARE_ORDER_OUTPUT_DIR`: order export directory.
- `ASHARE_EXECUTION_OUTPUT_DIR`: paper execution artifact directory.
- `ASHARE_TOP_N`: number of target names.
- `ASHARE_MAX_WEIGHT`: maximum single-name weight.
- `ASHARE_REBALANCE_DATE`: optional rebalance date.

Data sync writes `manifest.json` and `pipeline_state.json`. Passing `--validate` or `--quality-report` also writes `quality_report.json`. Passing `--mode append` merges with existing JSONL records by dataset primary key.

Production-style data sync uses a stable plan of dataset jobs, date windows, and index-code windows:

```bash
uv run python -m data_pipeline.run_pipeline \
  --plan-only \
  --provider tushare \
  --data-dir data/ashare \
  --start-date 20240101 \
  --end-date 20241231 \
  --index-codes 000300.SH,000905.SH \
  --chunk-days 30 \
  --pretty

uv run python -m data_pipeline.run_pipeline \
  --sync \
  --use-plan \
  --provider tushare \
  --data-dir data/ashare \
  --start-date 20240101 \
  --end-date 20241231 \
  --index-codes 000300.SH,000905.SH \
  --chunk-days 30 \
  --mode append \
  --cache \
  --audit \
  --resume \
  --validate \
  --fail-on-quality-error \
  --compact \
  --snapshot \
  --stats \
  --pretty
```

The same path works with `--provider sample` for offline verification. Planned sync writes `sync_plan.json`; request audit writes `api_audit.jsonl`; statistics write `dataset_stats.json`; snapshots are stored under `snapshots/<snapshot_name>/`. `--validate-only` validates existing artifacts without fetching data, and standalone `--compact`, `--snapshot`, and `--stats` actions operate on existing local datasets.

Market constraint datasets include `daily_limits`, `adjustment_factors`, and `index_members`. The research and backtest stack uses `adjusted_close` for returns and raw `close` for local order simulation. The portfolio simulator applies local A-share constraints for suspension, limit up/down, T+1 selling, board lots, volume participation, and trading costs.

Build a matrix cache after governed data and universe are available:

```bash
uv run python -m matrix_store.run_build_matrix \
  --data-dir /tmp/auto-alpha-demo/data \
  --output-dir /tmp/auto-alpha-demo/data/matrix_cache \
  --universe-name csi300_sample \
  --validate \
  --pretty
```

`AShareDataLoader(..., use_matrix_cache=True, matrix_cache_dir=...)` reads `metadata.json`, `ts_codes.json`, `trade_dates.json`, `fields.json`, and `<field>.npy` matrices before building the same feature tensor and target return outputs as the JSONL path. If the cache is missing, the loader raises a clear file error; the default loader path remains JSONL.

Run local performance and cross-source checks:

```bash
uv run python -m performance_benchmark.run_benchmark \
  --data-dir /tmp/auto-alpha-demo/data \
  --matrix-cache-dir /tmp/auto-alpha-demo/data/matrix_cache \
  --output-dir /tmp/auto-alpha-demo/benchmark \
  --pretty

uv run python -m cross_source_checks.run_compare \
  --left-data-dir /tmp/auto-alpha-demo/data \
  --right-data-dir /tmp/auto-alpha-demo/data_copy \
  --output-dir /tmp/auto-alpha-demo/cross_source \
  --datasets daily_bars,daily_basic,daily_limits \
  --pretty
```

The benchmark writes `benchmark_result.json` and `benchmark_report.md`. The comparison writes `cross_source_report.json` and `cross_source_report.md`, including record count differences, missing keys, date range differences, and numeric field deltas.

The factor engine can be constrained to a local universe with `--universe-name` or `--universe-file`. `--factor-transform` supports `raw`, `winsorize`, `zscore`, `winsorize_zscore`, `neutralize_market_cap`, `neutralize_industry`, and `neutralize_industry_size`. Passing `--enable-gate` records coverage, turnover, split metrics, correlation checks, gate status, and transform metadata in the factor store and report.

The formula DSL exposes operator arity, lookback, and complexity metadata. StackVM can explain invalid formulas, estimate formula complexity and lookback, and produce stable canonical formula names. Batch research is available through `python -m research.run_batch`. The default candidate set includes at least 20 reproducible formula factors covering returns, valuation, profitability, growth, rolling time-series operators, cross-sectional operators, and simple combined expressions.

Search-style research is available through `python -m formula_search.run_search`. It generates legal RPN formulas, mutates and crosses over formulas across generations, filters duplicate hashes, calls the same batch research gate/composite path, and writes `search_result.json`, `search_candidates.jsonl`, `search_report.json`, and `search_report.md`. Backtest and strategy CLIs can select the newest approved composite factor with `--latest-approved --factor-type composite`.

Neural-guided research is available through `python -m neural_search.run_neural_search`. It trains AlphaGPT with supervised warm-start sequences, samples formulas through a StackVM-aware action mask, updates policy parameters from research rewards, writes checkpoints, and produces `neural_search_result.json`, `neural_training_history.jsonl`, and `neural_search_report.md`:

```bash
uv run python -m neural_search.run_neural_search \
  --data-dir /tmp/auto-alpha-demo/data \
  --universe-name csi300_sample \
  --factor-store-dir /tmp/auto-alpha-demo/store \
  --report-dir /tmp/auto-alpha-demo/reports \
  --output-dir /tmp/auto-alpha-demo/neural \
  --seed 42 \
  --warmup-steps 2 \
  --policy-steps 2 \
  --batch-size 4 \
  --samples-per-step 4 \
  --max-formula-len 8 \
  --factor-transform winsorize_zscore \
  --enable-gate \
  --top-k 5 \
  --composite-method rank_average \
  --pretty
```

`formula_search.run_search` supports `--search-mode random|neural|hybrid`. Hybrid mode runs a neural branch and the existing random/mutation/crossover branch against the same factor store, then records neural metadata and checkpoint paths in `search_result.json`. `model_core.engine --train-mode neural` provides a lightweight neural training entry while preserving the existing fixed-candidate engine mode.

The suite runner is available through `python -m research_suite.run_suite`. It orchestrates data sync, universe construction, formula search, composite backtest, paper order export, walk-forward robustness evaluation, promotion decision, suite report, and `artifact_catalog.json`. When promotion passes, the selected composite factor is updated to `production_candidate` in the factor store with the promotion decision in metadata.

`research_suite.run_suite` can also build matrix cache and run benchmark artifacts:

```bash
uv run python -m research_suite.run_suite \
  --suite-name matrix_perf_suite \
  --provider sample \
  --data-dir /tmp/auto-alpha-demo/data \
  --universe-name csi300_sample \
  --index-code 000300.SH \
  --factor-store-dir /tmp/auto-alpha-demo/store \
  --report-dir /tmp/auto-alpha-demo/reports \
  --output-dir /tmp/auto-alpha-demo/suite \
  --backtest-dir /tmp/auto-alpha-demo/backtest \
  --orders-dir /tmp/auto-alpha-demo/orders \
  --build-matrix-cache \
  --matrix-cache-dir /tmp/auto-alpha-demo/data/matrix_cache \
  --use-matrix-cache \
  --benchmark \
  --benchmark-dir /tmp/auto-alpha-demo/suite_benchmark \
  --pretty
```

Risk-aware portfolio construction is available in the optimizer, backtest, strategy, and suite CLIs:

```bash
uv run python -m portfolio_optimizer.run_optimize \
  --data-dir /tmp/auto-alpha-demo/data \
  --factor-store-dir /tmp/auto-alpha-demo/store \
  --output-dir /tmp/auto-alpha-demo/optimize \
  --latest-approved \
  --factor-type composite \
  --index-code 000300.SH \
  --as-of-date 20240104 \
  --max-weight 0.10 \
  --max-names 2 \
  --risk-aversion 1.0 \
  --turnover-penalty 0.1 \
  --use-factor-risk-model \
  --max-active-style-exposure 1.0 \
  --pretty

uv run python -m backtest.run_backtest \
  --data-dir /tmp/auto-alpha-demo/data \
  --factor-store-dir /tmp/auto-alpha-demo/store \
  --output-dir /tmp/auto-alpha-demo/backtest_risk \
  --latest-approved \
  --factor-type composite \
  --portfolio-method risk_aware \
  --index-code 000300.SH \
  --top-n 2 \
  --max-weight 0.10 \
  --risk-report-dir /tmp/auto-alpha-demo/risk_reports \
  --use-factor-risk-model \
  --attribution \
  --pretty
```

Barra-like risk model v1 adds style factors (`size`, `value`, `momentum`, `volatility`, `liquidity`, `quality`, `growth`), industry one-hot exposures, cross-sectional factor return estimates, factor covariance, specific risk, portfolio/active risk decomposition, and return attribution. Enable it with `--use-factor-risk-model`; backtests can also use `--attribution` and write `risk_exposures.jsonl`, `risk_decomposition.jsonl`, `return_attribution.jsonl`, and `risk_model_report.json/md`.

`strategy_manager.runner` and `research_suite.run_suite` accept the same `--portfolio-method risk_aware`, `--index-code`, `--risk-aversion`, `--turnover-penalty`, `--max-turnover`, `--max-industry-active-weight`, `--max-tracking-error`, `--use-factor-risk-model`, `--max-style-exposure`, and `--max-active-style-exposure` controls. Risk-aware artifacts include `optimization_result.json`, `risk_report.json` or `risk_model_report.json`, and Markdown reports; target positions include optimized, benchmark, and active weights.

Capacity-aware execution planning is available for research and paper operations:

```bash
uv run python -m backtest.run_backtest \
  --data-dir /tmp/auto-alpha-demo/data \
  --factor-store-dir /tmp/auto-alpha-demo/store \
  --output-dir /tmp/auto-alpha-demo/backtest_capacity \
  --latest-approved \
  --factor-type composite \
  --portfolio-method risk_aware \
  --index-code 000300.SH \
  --top-n 2 \
  --max-weight 0.10 \
  --capacity-aware \
  --max-participation 0.10 \
  --execution-plan-dir /tmp/auto-alpha-demo/execution_plan \
  --pretty

uv run python -m strategy_manager.runner \
  --data-dir /tmp/auto-alpha-demo/data \
  --factor-store-dir /tmp/auto-alpha-demo/store \
  --output-dir /tmp/auto-alpha-demo/orders_capacity \
  --latest-approved \
  --factor-type composite \
  --portfolio-method risk_aware \
  --index-code 000300.SH \
  --top-n 2 \
  --max-weight 0.10 \
  --portfolio-value 1000000 \
  --capacity-aware \
  --execution-plan-dir /tmp/auto-alpha-demo/orders_capacity/plan \
  --pretty
```

The capacity layer writes `capacity_report.json/md`; execution planning writes `execution_plan.json/md`, `parent_orders.jsonl`, `child_orders.jsonl`, `child_fills.jsonl`, and `execution_quality.json`. It remains local paper simulation only.

`broker_adapter/` adds a local broker contract on top of approved child orders. It supports:

- `simulated`: idempotent broker order submission, local status transitions, broker fills, broker events, cancellation/replacement simulation, and reconciliation.
- `file`: generic broker instruction outbox with CSV/JSONL/manifest plus optional inbox status/fill import.

The file adapter uses an internal generic schema and optional config-driven `field_mapping`. `schema_name=qmt_skeleton` is only a configurable skeleton for manual mapping review. It does not guarantee compatibility with any real QMT installation or broker desk file format.

Dashboard-specific overrides:

- `ASHARE_DASHBOARD_DATA_DIR`
- `ASHARE_DASHBOARD_FACTOR_STORE_DIR`
- `ASHARE_DASHBOARD_REPORT_DIR`
- `ASHARE_DASHBOARD_BACKTEST_DIR`
- `ASHARE_DASHBOARD_ORDERS_DIR`
- `ASHARE_DASHBOARD_APPROVAL_STORE_DIR`
- `ASHARE_DASHBOARD_PAPER_ACCOUNT_DIR`
- `ASHARE_DASHBOARD_PRODUCTION_DIR`
- `ASHARE_DASHBOARD_MONITORING_DIR`
- `ASHARE_DASHBOARD_MATRIX_CACHE_DIR`
- `ASHARE_DASHBOARD_BENCHMARK_DIR`
- `ASHARE_DASHBOARD_CROSS_SOURCE_DIR`

## Daily Production Operations

The platform still has no real broker integration. Daily production uses local approval and a persistent paper account:

```bash
uv run python -m paper_account.run_account \
  --account-dir /tmp/auto-alpha-demo/account \
  reset \
  --initial-cash 1000000 \
  --pretty

uv run python -m operations.run_daily \
  --data-dir /tmp/auto-alpha-demo/data \
  --factor-store-dir /tmp/auto-alpha-demo/store \
  --approval-store-dir /tmp/auto-alpha-demo/approvals \
  --paper-account-dir /tmp/auto-alpha-demo/account \
  --output-dir /tmp/auto-alpha-demo/production \
  --orders-dir /tmp/auto-alpha-demo/daily_orders \
  --latest-production \
  --rebalance-date 20240104 \
  --portfolio-method risk_aware \
  --index-code 000300.SH \
  --use-factor-risk-model \
  --capacity-aware \
  --broker-adapter simulated \
  --broker-store-dir /tmp/auto-alpha-demo/broker \
  --broker-auto-fill \
  --broker-reconcile \
  --max-active-style-exposure 1.0 \
  --top-n 2 \
  --max-weight 0.10 \
  --portfolio-value 1000000 \
  --require-approval \
  --pretty

uv run python -m approval.run_approval \
  --store-dir /tmp/auto-alpha-demo/approvals \
  approve \
  --approval-id <APPROVAL_ID> \
  --reviewer local_reviewer \
  --comment approved_for_paper \
  --pretty

uv run python -m operations.run_daily \
  --data-dir /tmp/auto-alpha-demo/data \
  --factor-store-dir /tmp/auto-alpha-demo/store \
  --approval-store-dir /tmp/auto-alpha-demo/approvals \
  --paper-account-dir /tmp/auto-alpha-demo/account \
  --output-dir /tmp/auto-alpha-demo/production_execute \
  --orders-dir /tmp/auto-alpha-demo/daily_orders_execute \
  --approval-id <APPROVAL_ID> \
  --execute-approved \
  --rebalance-date 20240104 \
  --portfolio-method risk_aware \
  --index-code 000300.SH \
  --use-factor-risk-model \
  --capacity-aware \
  --max-active-style-exposure 1.0 \
  --top-n 2 \
  --max-weight 0.10 \
  --portfolio-value 1000000 \
  --pretty

uv run python -m monitoring.run_monitor \
  --data-dir /tmp/auto-alpha-demo/data \
  --factor-store-dir /tmp/auto-alpha-demo/store \
  --paper-account-dir /tmp/auto-alpha-demo/account \
  --orders-dir /tmp/auto-alpha-demo/daily_orders_execute \
  --output-dir /tmp/auto-alpha-demo/monitoring \
  --as-of-date 20240104 \
  --broker-store-dir /tmp/auto-alpha-demo/broker \
  --broker-batch-id <APPROVAL_ID> \
  --pretty
```

Daily production writes `production_run.json/md`; approvals are stored under `approvals/<approval_id>.json` plus `approval_log.jsonl`; the paper account writes `account_state.json`, `positions.jsonl`, `cash_ledger.jsonl`, `trade_ledger.jsonl`, and `account_snapshots.jsonl`; broker-enabled runs write `broker_report.json/md`, `broker_orders.jsonl`, `broker_events.jsonl`, `broker_fills.jsonl`, and `broker_reconciliation.json/md`; monitoring writes `monitoring_report.json/md` and `alerts.jsonl`.

To export generic file instructions without local fills:

```bash
uv run python -m operations.run_daily \
  --data-dir /tmp/auto-alpha-demo/data \
  --factor-store-dir /tmp/auto-alpha-demo/store \
  --approval-store-dir /tmp/auto-alpha-demo/approvals \
  --paper-account-dir /tmp/auto-alpha-demo/account \
  --output-dir /tmp/auto-alpha-demo/production_file \
  --orders-dir /tmp/auto-alpha-demo/daily_orders_file \
  --approval-id <APPROVAL_ID> \
  --execute-approved \
  --rebalance-date 20240104 \
  --capacity-aware \
  --broker-adapter file \
  --broker-store-dir /tmp/auto-alpha-demo/broker_file \
  --broker-outbox-dir /tmp/auto-alpha-demo/broker_file/outbox \
  --pretty
```

Without inbox fills, file-adapter runs only export outbox instructions and do not update the paper account.

## Current Gaps

- Tushare HTTP provider and production sync scaffolding are available; production use still requires valid token, quota/permission verification, real full-market performance runs, and more data-source comparisons.
- Barra-like risk model v1 and benchmark-aware portfolio optimization are available locally; future work should add production Barra definitions, robust full-market covariance calibration, a professional optimizer, and large-scale performance tuning.
- Local daily simulation supports A-share constraints, capacity estimates, impact-cost estimates, child-order scheduling, broker-adapter state, file instruction export, and paper execution quality reports; future work should add finer real-world matching and minute-level volume modeling.
- Local formula search and a first neural-guided policy-search path are available; future work should add stronger reinforcement learning, offline pretraining, more operators, GPU performance tuning, and broader stability validation.
- Matrix cache, local performance benchmark, and data-source comparison skeletons are available; future work should add real full-market stress runs, incremental matrix refresh, and more provider pairs.
- One-click research suites now provide local walk-forward and promotion gates; daily operations now provide local approval, paper account ledger, and monitoring artifacts. Future work should add richer approval policies and human review workflow.
- Broker adapter, file instructions, and account ledger are local only. No real broker integration, credential handling, network submission, or verified QMT/broker file compatibility is implemented.

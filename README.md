# Auto-alpha

Auto-alpha is an A-share quantitative factor research platform. It provides a local, reproducible workflow for data preparation, factor formula research, factor registration, portfolio simulation, paper order export, and artifact review.

The current implementation is local-first. It uses deterministic sample data and JSON/JSONL artifacts so the full research loop can run without external services, while the Tushare HTTP provider can be enabled with a valid token.

## Modules

- `data_pipeline/`: A-share data configuration, sample and Tushare HTTP providers, market constraint datasets, local JSONL storage, data quality checks, sync state, and data sync CLI.
- `universe/`: Local A-share universe construction from governed data artifacts.
- `model_core/`: A-share feature engineering, formula vocabulary, DSL operators, StackVM execution, factor evaluation, and mining engine.
- `factor_engine/`: Cross-sectional preprocessing, market-cap and industry neutralization, correlation checks, and factor admission gate.
- `factor_store/`: Local factor registry, experiment registry, factor value storage, and stable factor identifiers.
- `evaluation/`: Time-series sample split, split-level metrics, and factor reports.
- `research/`: Batch candidate execution, factor ranking, composite factor construction, and batch research reports.
- `formula_search/`: Formula metadata, random generation, mutation, crossover, multi-generation search, and search reports.
- `research_suite/`: One-click orchestration, walk-forward robustness, production-candidate promotion, and artifact catalog.
- `backtest/`: Long-only A-share portfolio simulation and backtest CLI.
- `execution/`: Paper broker and order/fill export utilities.
- `strategy_manager/`: Target position and paper order generation.
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
  --search-seed 42 \
  --search-population-size 12 \
  --search-generations 2 \
  --search-max-candidates 8 \
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

Market constraint datasets include `daily_limits`, `adjustment_factors`, and `index_members`. The research and backtest stack uses `adjusted_close` for returns and raw `close` for local order simulation. The portfolio simulator applies local A-share constraints for suspension, limit up/down, T+1 selling, board lots, volume participation, and trading costs.

The factor engine can be constrained to a local universe with `--universe-name` or `--universe-file`. `--factor-transform` supports `raw`, `winsorize`, `zscore`, `winsorize_zscore`, `neutralize_market_cap`, `neutralize_industry`, and `neutralize_industry_size`. Passing `--enable-gate` records coverage, turnover, split metrics, correlation checks, gate status, and transform metadata in the factor store and report.

The formula DSL exposes operator arity, lookback, and complexity metadata. StackVM can explain invalid formulas, estimate formula complexity and lookback, and produce stable canonical formula names. Batch research is available through `python -m research.run_batch`. The default candidate set includes at least 20 reproducible formula factors covering returns, valuation, profitability, growth, rolling time-series operators, cross-sectional operators, and simple combined expressions.

Search-style research is available through `python -m formula_search.run_search`. It generates legal RPN formulas, mutates and crosses over formulas across generations, filters duplicate hashes, calls the same batch research gate/composite path, and writes `search_result.json`, `search_candidates.jsonl`, `search_report.json`, and `search_report.md`. Backtest and strategy CLIs can select the newest approved composite factor with `--latest-approved --factor-type composite`.

The suite runner is available through `python -m research_suite.run_suite`. It orchestrates data sync, universe construction, formula search, composite backtest, paper order export, walk-forward robustness evaluation, promotion decision, suite report, and `artifact_catalog.json`. When promotion passes, the selected composite factor is updated to `production_candidate` in the factor store with the promotion decision in metadata.

Dashboard-specific overrides:

- `ASHARE_DASHBOARD_DATA_DIR`
- `ASHARE_DASHBOARD_FACTOR_STORE_DIR`
- `ASHARE_DASHBOARD_REPORT_DIR`
- `ASHARE_DASHBOARD_BACKTEST_DIR`
- `ASHARE_DASHBOARD_ORDERS_DIR`

## Current Gaps

- Tushare HTTP provider is available, but production use still requires valid token, quota, richer data quality rules, and a fuller incremental sync strategy.
- Industry and market-cap neutralization now have a basic cross-sectional implementation; future work should expand this into a fuller risk model and finer industry classification.
- Local daily simulation supports core A-share constraints; future work should add finer real-world matching, minute-level liquidity, and richer risk models.
- Local formula search is available; future work should add neural-guided search, more operators, larger-scale performance tuning, and richer stability diagnostics.
- One-click research suites now provide local walk-forward and promotion gates; future work should add richer approval policies and human review workflow.
- Paper order export is local only; no real broker integration is implemented.

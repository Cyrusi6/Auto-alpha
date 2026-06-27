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
- `backtest/`: Long-only A-share portfolio simulation and backtest CLI.
- `execution/`: Paper broker and order/fill export utilities.
- `strategy_manager/`: Target position and paper order generation.
- `dashboard/`: Streamlit dashboard for local artifacts.

## Quickstart

Run the full sample workflow:

```bash
rm -rf /tmp/auto-alpha-demo

uv run python -m data_pipeline.run_pipeline \
  --sync \
  --provider sample \
  --data-dir /tmp/auto-alpha-demo/data \
  --validate \
  --mode overwrite \
  --index-codes 000300.SH \
  --pretty

uv run python -m data_pipeline.run_pipeline \
  --sync \
  --provider sample \
  --data-dir /tmp/auto-alpha-demo/data \
  --validate \
  --mode append \
  --pretty

uv run python -m universe.run_universe \
  --data-dir /tmp/auto-alpha-demo/data \
  --as-of-date 20240104 \
  --universe-name csi300_sample \
  --use-index-members \
  --index-code 000300.SH \
  --min-listed-days 0 \
  --min-amount 0 \
  --pretty

uv run python -m research.run_batch \
  --data-dir /tmp/auto-alpha-demo/data \
  --universe-name csi300_sample \
  --factor-store-dir /tmp/auto-alpha-demo/store \
  --report-dir /tmp/auto-alpha-demo/reports \
  --output-dir /tmp/auto-alpha-demo/batch \
  --factor-transform winsorize_zscore \
  --enable-gate \
  --top-k 5 \
  --max-candidates 8 \
  --composite-method rank_average \
  --correlation-threshold 0.99 \
  --min-coverage 0.5 \
  --pretty

uv run python -m backtest.run_backtest \
  --data-dir /tmp/auto-alpha-demo/data \
  --factor-store-dir /tmp/auto-alpha-demo/store \
  --output-dir /tmp/auto-alpha-demo/backtest \
  --latest-approved \
  --factor-type composite \
  --top-n 2 \
  --max-weight 0.10 \
  --pretty

uv run python -m strategy_manager.runner \
  --data-dir /tmp/auto-alpha-demo/data \
  --factor-store-dir /tmp/auto-alpha-demo/store \
  --output-dir /tmp/auto-alpha-demo/orders \
  --latest-approved \
  --factor-type composite \
  --top-n 2 \
  --max-weight 0.10 \
  --portfolio-value 1000000 \
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

Batch research is available through `python -m research.run_batch`. The default candidate set includes at least 12 reproducible formula factors such as `RET_1D`, `RET_5D`, valuation, profitability, growth, and simple combined expressions. A batch run executes candidates through StackVM, applies the selected transform and gate, skips existing formula hashes, writes per-factor reports, ranks candidates, and can register a composite factor with `equal_weight`, `score_weighted`, or `rank_average`. Backtest and strategy CLIs can select the newest approved composite factor with `--latest-approved --factor-type composite`.

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
- Batch research and composite factors are local and deterministic; future work should add stronger formula search, more stable neural training, and large-scale performance tuning.
- Paper order export is local only; no real broker integration is implemented.

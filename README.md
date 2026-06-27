# Auto-alpha

Auto-alpha is an A-share quantitative factor research platform. It provides a local, reproducible workflow for data preparation, factor formula research, factor registration, portfolio simulation, paper order export, and artifact review.

The current implementation is intentionally local-first. It uses deterministic sample data and JSON/JSONL artifacts so the full research loop can run without external services.

## Modules

- `data_pipeline/`: A-share data configuration, sample provider, local JSONL storage, and data sync CLI.
- `model_core/`: A-share feature engineering, formula vocabulary, DSL operators, StackVM execution, factor evaluation, and mining engine.
- `factor_store/`: Local factor registry, experiment registry, factor value storage, and stable factor identifiers.
- `evaluation/`: Time-series sample split, split-level metrics, and factor reports.
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
  --pretty

uv run python -m model_core.engine \
  --dry-run \
  --register \
  --data-dir /tmp/auto-alpha-demo/data \
  --output-dir /tmp/auto-alpha-demo/out \
  --factor-store-dir /tmp/auto-alpha-demo/store \
  --report-dir /tmp/auto-alpha-demo/reports

uv run python -m backtest.run_backtest \
  --data-dir /tmp/auto-alpha-demo/data \
  --factor-store-dir /tmp/auto-alpha-demo/store \
  --output-dir /tmp/auto-alpha-demo/backtest \
  --top-n 2 \
  --max-weight 0.10 \
  --pretty

uv run python -m strategy_manager.runner \
  --data-dir /tmp/auto-alpha-demo/data \
  --factor-store-dir /tmp/auto-alpha-demo/store \
  --output-dir /tmp/auto-alpha-demo/orders \
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

- `TUSHARE_TOKEN`: reserved for a future provider.
- `ASHARE_PROVIDER`: data provider, currently `sample` for local sync.
- `ASHARE_DATA_DIR`: local A-share data directory.
- `ASHARE_FACTOR_STORE_DIR`: local factor store directory.
- `ASHARE_ORDER_OUTPUT_DIR`: order export directory.
- `ASHARE_EXECUTION_OUTPUT_DIR`: paper execution artifact directory.
- `ASHARE_TOP_N`: number of target names.
- `ASHARE_MAX_WEIGHT`: maximum single-name weight.
- `ASHARE_REBALANCE_DATE`: optional rebalance date.

Dashboard-specific overrides:

- `ASHARE_DASHBOARD_DATA_DIR`
- `ASHARE_DASHBOARD_FACTOR_STORE_DIR`
- `ASHARE_DASHBOARD_REPORT_DIR`
- `ASHARE_DASHBOARD_BACKTEST_DIR`
- `ASHARE_DASHBOARD_ORDERS_DIR`

## Current Gaps

- Real Tushare provider is not implemented.
- Industry and market-cap neutralization are still future work.
- Portfolio simulation is intentionally simple and needs richer A-share trading constraints.
- Paper order export is local only; no real broker integration is implemented.

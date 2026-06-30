# Auto-alpha

Auto-alpha is an A-share quantitative factor research platform. It provides a local, reproducible workflow for data preparation, factor formula research, factor registration, portfolio simulation, paper order export, and artifact review.

The current implementation is local-first. It uses deterministic sample data and JSON/JSONL artifacts so the full research loop can run without external services, while the Tushare HTTP provider can be enabled with a valid token.

## Modules

- `data_pipeline/`: A-share data configuration, sample and Tushare HTTP providers, market constraint datasets, sync planning, response cache, request audit, local JSONL storage, data quality checks, sync state, and data sync CLI.
- `data_source_validation/`: Offline and gated-online provider readiness, Tushare permission/rate/field diagnostics, incremental recovery smoke, field coverage, audit summary, and baseline comparison reports.
- `data_backfill/`: Production-style full-history backfill planning, chunked job execution, staging/quarantine, resume state, coverage reports, and readiness/quota summaries.
- `data_lake/`: Dataset fingerprints, dataset version registry, immutable research freezes, freeze validation, lineage graphs, and retention reports.
- `artifact_schema/`: Artifact type registry, schema versioning, checksum manifests, JSON/JSONL validation, and legacy-compatible artifact scanning.
- `universe/`: Local A-share universe construction from governed data artifacts.
- `feature_factory/`: Versioned A-share feature catalogs, v1/v2 feature manifests, coverage reports, and opt-in feature tensor artifacts.
- `model_core/`: A-share feature engineering, formula vocabulary, DSL operators, StackVM execution, factor evaluation, and mining engine.
- `factor_engine/`: Cross-sectional preprocessing, market-cap and industry neutralization, correlation checks, and factor admission gate.
- `factor_store/`: Local factor registry, experiment registry, factor value storage, and stable factor identifiers.
- `evaluation/`: Time-series sample split, split-level metrics, and factor reports.
- `research/`: Batch candidate execution, factor ranking, composite factor construction, and batch research reports.
- `alpha_factory/`: Campaign-level large candidate generation, template/random/mutation/crossover/corpus source budgets, static checks, proxy eval, full eval, novelty/diversity scoring, and shortlist reports.
- `validation_lab/`: Out-of-sample validation, walk-forward/purged/CSCV splits, multiple-testing diagnostics, overfit-risk estimates, placebo tests, regime robustness, sensitivity checks, and stress-validation reports.
- `factor_certification/`: Factor production certification policies, scorecards, decisions, review packages, and optional factor-store status application.
- `formula_search/`: Formula metadata, random generation, mutation, crossover, multi-generation search, and search reports.
- `neural_search/`: AlphaGPT warm-start training, action-mask constrained formula sampling, lightweight policy search, checkpointing, and neural search reports.
- `compute_cluster/`: Local CPU/GPU probe, GPU leases, job queue, subprocess runner, heartbeat, retry/resume state, and compute resource reports.
- `experiment_orchestrator/`: Research experiment graph planning, formula shard jobs, scheduler integration, shard merge, and experiment reports.
- `research_suite/`: One-click orchestration, walk-forward robustness, production-candidate promotion, and artifact catalog.
- `model_registry/`: Local model/factor version registry, lifecycle state machine, active deployments, rollback records, lineage graph, and registry reports.
- `factor_lifecycle/`: Factor health checks, lifecycle decisions, human review packages, activation approval flow, and lifecycle reports.
- `risk_model/`: Security exposures, Barra-like style and industry factors, factor returns, risk decomposition, attribution, covariance, risk constraints, and risk reports.
- `risk_controls/`: Pre-trade risk limits, kill switch state, order gate artifacts, override approval hooks, and local audit logs.
- `portfolio_optimizer/`: Deterministic long-only benchmark-aware portfolio optimizer and serializable portfolio policies.
- `portfolio_lab/`: Portfolio policy grids, scenario trials, robustness ranking, and selected policy artifacts for certified factors.
- `portfolio_certification/`: Portfolio policy scorecards, certification decisions, certified policy packages, and optimizer-policy activation requests.
- `capacity_model/`: Local capacity, participation, and impact-cost estimates from amount, volume, turnover, and volatility.
- `backtest/`: Long-only A-share portfolio simulation, market constraints, and benchmark-aware risk mode.
- `execution/`: Paper broker and order/fill export utilities.
- `execution_plan/`: Parent-order to child-order slicing, schedule simulation, and execution quality reports.
- `broker_adapter/`: Local broker contract, simulated broker state machine, file instruction outbox/inbox skeleton, broker events, fills, and reconciliation.
- `broker_statement/`: Generic broker statement import, QMT-style skeleton mapping, normalized external account mirror inputs, validation, and synthetic statement smoke generation.
- `reconciliation_center/`: End-of-day reconciliation across external statements, broker fills, paper account, settlement artifacts, corporate actions, break management, and approved manual adjustments.
- `strategy_manager/`: Target position and paper order generation.
- `approval/`: Local proposed-order batch approval, rejection, expiration, and audit log.
- `settlement_engine/`: Local paper settlement profiles, settlement events, cash/share availability, position lots, fee/tax breakdown, realized PnL, NAV, and account reconciliation.
- `paper_account/`: Persistent local paper cash, positions, trades, settlement artifacts, snapshots, and performance ledger.
- `operations/`: Daily production run orchestration from production factor to proposed orders, approval-gated execution, account update, and production report.
- `production_orchestrator/`: Trading-day production calendar, readiness gates, phase plan/state, resume packaging, fail-safe close-day reports, and orchestration CLI.
- `production_replay/`: Multi-day local replay of production plans, shadow/paper runs, approvals, close-day phases, state, and replay reports.
- `shadow_trading/`: Local shadow-only order book, simulated shadow fills, shadow snapshots, drift reports, and performance artifacts.
- `shadow_lab/`: Multi-day shadow aggregation, performance series, drift summaries, and calibration suggestions.
- `live_readiness/`: Policy-driven live readiness scorecards and decisions from replay, shadow, certification, incident, monitoring, and settlement artifacts.
- `incident_response/`: Local incident records, runbook steps, acknowledge/resolve/suppress lifecycle, detection from production artifacts, and incident reports.
- `monitoring/`: Local production checks for data freshness, quality, factor drift, fill quality, account performance, and alerts.
- `release_manager/`: Local release manifest, dependency/module/CLI inventory, package build summary, release gate report, and release notes draft.
- `ci/`: Offline local CI runner shared by developers and GitHub Actions.
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
  --run-validation-lab \
  --run-multiple-testing \
  --run-overfit-risk \
  --run-placebo \
  --run-regime-validation \
  --run-sensitivity-validation \
  --run-stress-backtest-validation \
  --run-factor-certification \
  --certification-policy-profile sample_lenient_certification \
  --require-certification \
  --run-portfolio-lab \
  --portfolio-lab-scenario-profile sample \
  --portfolio-methods risk_aware,equal_weight \
  --portfolio-risk-aversions 0.5,1.0 \
  --portfolio-turnover-penalties 0.05,0.1 \
  --portfolio-max-weight-values 0.05,0.10 \
  --portfolio-max-names-values 2,3 \
  --portfolio-top-n-values 2,3 \
  --run-portfolio-certification \
  --portfolio-certification-policy-profile sample_lenient_portfolio \
  --require-portfolio-certification \
  --register-optimizer-policy \
  --create-portfolio-policy-approval \
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
ASHARE_DASHBOARD_FEATURE_FACTORY_DIR=/tmp/auto-alpha-demo/features \
ASHARE_DASHBOARD_ALPHA_FACTORY_DIR=/tmp/auto-alpha-demo/alpha_factory \
uv run streamlit run dashboard/app.py
```

## Environment

Common variables:

- `TUSHARE_TOKEN`: required when `ASHARE_PROVIDER=tushare`.
- `TUSHARE_API_URL`: optional Tushare Pro HTTP endpoint override.
- `TUSHARE_TIMEOUT_SECONDS`: optional HTTP timeout.
- `TUSHARE_RETRY_COUNT`: optional HTTP retry count.
- `RUN_TUSHARE_ONLINE_SMOKE`: optional local guard for manually running online smoke checks; tests do not require it.
- `RUN_TUSHARE_ONLINE_BACKFILL`: optional local guard for manually running online Tushare backfills; tests do not require it.
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

For production-sized history loads, use the governed backfill layer. It builds a stable job plan, executes chunks through the same provider, storage, cache, and audit path, stages raw job output, records resumable state, and writes coverage artifacts:

```bash
uv run python -m data_backfill.run_backfill execute \
  --provider sample \
  --data-dir /tmp/auto-alpha-demo/data \
  --output-dir /tmp/auto-alpha-demo/backfill \
  --start-date 20240102 \
  --end-date 20240104 \
  --datasets securities,trade_calendar,daily_bars,daily_basic,financial_features,daily_limits,adjustment_factors,index_members,corporate_actions \
  --index-codes 000300.SH \
  --chunk-days 2 \
  --validate \
  --stats \
  --compact \
  --snapshot \
  --pretty
```

After governed sync or backfill, register a dataset version and freeze research input data. A freeze copies or hardlinks local JSONL records plus universe artifacts, writes hashes and manifests, and can be required by matrix build, research suite, backtest, and operations commands:

```bash
uv run python -m data_lake.run_lake create-version \
  --data-dir /tmp/auto-alpha-demo/data \
  --registry-dir /tmp/auto-alpha-demo/data_lake_registry \
  --output-dir /tmp/auto-alpha-demo/data_version \
  --provider sample \
  --start-date 20240102 \
  --end-date 20240104 \
  --datasets securities,trade_calendar,daily_bars,daily_basic,financial_features,daily_limits,adjustment_factors,index_members,corporate_actions \
  --backfill-run-report-path /tmp/auto-alpha-demo/backfill/backfill_run_report.json \
  --backfill-coverage-report-path /tmp/auto-alpha-demo/backfill/backfill_coverage_report.json \
  --pretty

uv run python -m data_lake.run_lake create-freeze \
  --data-dir /tmp/auto-alpha-demo/data \
  --registry-dir /tmp/auto-alpha-demo/data_lake_registry \
  --output-dir /tmp/auto-alpha-demo/freeze_report \
  --freeze-dir /tmp/auto-alpha-demo/research_freeze \
  --freeze-name sample_freeze \
  --pretty
```

`data_source_validation.run_smoke` can also write a dataset version and research freeze in one offline smoke run with `--write-data-version --create-research-freeze`. Real Tushare backfills remain gated by explicit allow-network/token parameters and reports never store raw tokens.

## Validation And Certification

Alpha Factory and formula search can generate many candidates, so production promotion should include explicit anti-overfit governance. `validation_lab/` evaluates a selected single or composite factor across deterministic out-of-sample splits and writes:

- `validation_lab_report.json/md`
- `validation_splits.jsonl`
- `factor_validation_results.jsonl`
- `factor_validation_summary.json`
- `multiple_testing_report.json`
- `overfit_risk_report.json`
- `placebo_test_report.json` and `placebo_trials.jsonl`
- `regime_validation_report.json` and `regime_results.jsonl`
- `sensitivity_report.json` and `sensitivity_results.jsonl`
- `stress_backtest_report.json` and `stress_backtest_results.jsonl`

The implementation is intentionally local and conservative. PBO, deflated IC-like scores, and multiple-testing penalties are approximate diagnostics for review, not proof of future profitability.

`factor_certification/` turns validation, PIT/leakage, data-freeze, Alpha Factory, settlement, risk-control, and lifecycle artifacts into a scorecard and decision:

```bash
uv run python -m validation_lab.run_validation run-suite \
  --data-dir /tmp/auto-alpha-demo/data \
  --factor-store-dir /tmp/auto-alpha-demo/store \
  --latest-approved \
  --output-dir /tmp/auto-alpha-demo/validation \
  --run-multiple-testing \
  --run-overfit-risk \
  --run-placebo \
  --placebo-trials 3 \
  --run-regime \
  --run-sensitivity \
  --run-stress-backtest \
  --pretty

uv run python -m factor_certification.run_certify run \
  --factor-store-dir /tmp/auto-alpha-demo/store \
  --latest-approved \
  --output-dir /tmp/auto-alpha-demo/certification \
  --policy-profile sample_lenient_certification \
  --validation-lab-report-path /tmp/auto-alpha-demo/validation/validation_lab_report.json \
  --factor-validation-summary-path /tmp/auto-alpha-demo/validation/factor_validation_summary.json \
  --multiple-testing-report-path /tmp/auto-alpha-demo/validation/multiple_testing_report.json \
  --overfit-risk-report-path /tmp/auto-alpha-demo/validation/overfit_risk_report.json \
  --placebo-test-report-path /tmp/auto-alpha-demo/validation/placebo_test_report.json \
  --regime-validation-report-path /tmp/auto-alpha-demo/validation/regime_validation_report.json \
  --sensitivity-report-path /tmp/auto-alpha-demo/validation/sensitivity_report.json \
  --stress-backtest-report-path /tmp/auto-alpha-demo/validation/stress_backtest_report.json \
  --pretty
```

Certification policy profiles are `sample_lenient_certification`, `research_standard`, and `production_strict`. Certification is a governance gate and review artifact; it does not guarantee returns. Large real-data validation should run against an immutable `data_lake` research freeze.

Portfolio certification is a separate gate after factor certification. A certified factor answers whether the signal is acceptable for promotion; a certified portfolio policy answers whether optimizer settings, cost/capacity assumptions, settlement assumptions, risk constraints, and scenario robustness are acceptable for local paper deployment.

```bash
uv run python -m portfolio_lab.run_portfolio_lab run \
  --data-dir /tmp/auto-alpha-demo/data \
  --factor-store-dir /tmp/auto-alpha-demo/store \
  --latest-approved \
  --factor-type composite \
  --output-dir /tmp/auto-alpha-demo/portfolio_lab \
  --portfolio-methods risk_aware,equal_weight \
  --risk-aversions 0.5,1.0 \
  --turnover-penalties 0.05,0.1 \
  --max-weight-values 0.05,0.10 \
  --max-names-values 2,3 \
  --top-n-values 2,3 \
  --scenario-profile sample \
  --pretty

uv run python -m portfolio_certification.run_portfolio_certify run \
  --factor-store-dir /tmp/auto-alpha-demo/store \
  --latest-approved \
  --factor-type composite \
  --model-registry-dir /tmp/auto-alpha-demo/model_registry \
  --output-dir /tmp/auto-alpha-demo/portfolio_certification \
  --portfolio-lab-report-path /tmp/auto-alpha-demo/portfolio_lab/portfolio_lab_report.json \
  --portfolio-robustness-report-path /tmp/auto-alpha-demo/portfolio_lab/portfolio_robustness_report.json \
  --selected-portfolio-policy-path /tmp/auto-alpha-demo/portfolio_lab/selected_portfolio_policy.json \
  --policy-profile sample_lenient_portfolio \
  --register-policy \
  --create-activation-approval \
  --approval-store-dir /tmp/auto-alpha-demo/portfolio_policy_approvals \
  --pretty
```

Approving the generated `portfolio_policy_activation` batch and running `portfolio_certification.run_portfolio_certify apply-approved-activation` activates an `optimizer_policy` in `model_registry/`. `backtest.run_backtest`, `strategy_manager.runner`, and `operations.run_daily` can then use `--active-optimizer-policy` and enforce `--require-certified-portfolio-policy`.

Before using a real data token in production, run the data source smoke validator. It is offline by default and can use fake Tushare scenarios without network access:

```bash
uv run python -m data_source_validation.run_smoke \
  --provider tushare \
  --fake-tushare-scenario success \
  --data-dir /tmp/auto-alpha-smoke/fake_tushare_data \
  --output-dir /tmp/auto-alpha-smoke/fake_tushare_smoke \
  --start-date 20240102 \
  --end-date 20240104 \
  --datasets securities,trade_calendar,daily_bars,daily_basic,financial_features,daily_limits,adjustment_factors,index_members \
  --index-codes 000300.SH \
  --cache \
  --audit \
  --validate \
  --stats \
  --run-incremental-recovery \
  --pretty
```

Real Tushare smoke is gated. It sends requests only when `--allow-network` is passed and `TUSHARE_TOKEN` is present. Reports store only token suffix/hash metadata and never the raw token:

```bash
RUN_TUSHARE_ONLINE_SMOKE=1 TUSHARE_TOKEN=<REAL_TOKEN> \
uv run python -m data_source_validation.run_smoke \
  --provider tushare \
  --allow-network \
  --require-token \
  --data-dir /tmp/auto-alpha-smoke/real_tushare_data \
  --output-dir /tmp/auto-alpha-smoke/real_tushare_smoke \
  --start-date 20240102 \
  --end-date 20240104 \
  --datasets securities,trade_calendar,daily_bars,daily_basic,daily_limits,adjustment_factors,index_members \
  --index-codes 000300.SH \
  --max-requests 20 \
  --cache \
  --audit \
  --validate \
  --stats \
  --pretty
```

Smoke reports include `data_source_smoke_report.json/md`, `provider_probe.json`, `field_coverage.json`, `audit_summary.json`, `incremental_recovery_report.json`, `baseline_compare_summary.json`, and `dataset_contracts.json`. Use `--baseline-data-dir --compare-baseline` to compare a smoke run against a local baseline; differences are reported structurally and fail the command only with `--fail-on-baseline-diff`.

Market constraint datasets include `daily_limits`, `adjustment_factors`, and `index_members`. The research and backtest stack uses `adjusted_close` for returns and raw `close` for local order simulation. The portfolio simulator applies local A-share constraints for suspension, limit up/down, T+1 selling, board lots, volume participation, and trading costs.

Corporate action governance is available through `corporate_actions/`. The data pipeline can sync a `corporate_actions` dataset from sample or Tushare `dividend`, normalize cash dividends, bonus shares, transfers, combined distributions, and proposal-only events, then write reports for point-in-time availability, total-return series, and adjustment-factor reconciliation. The default research target remains `adjusted_close`; enable the explicit total-return path with `--corporate-action-aware --target-return-mode corporate_action_total_return`.

```bash
uv run python -m corporate_actions.run_actions report \
  --data-dir /tmp/auto-alpha-demo/data \
  --output-dir /tmp/auto-alpha-demo/corporate_actions \
  --start-date 20240102 \
  --end-date 20240104 \
  --reconcile-adjustment \
  --pretty
```

Paper accounts can apply eligible events idempotently and export `corporate_action_ledger.jsonl`:

```bash
uv run python -m paper_account.run_account \
  --account-dir /tmp/auto-alpha-demo/account \
  apply-corporate-actions \
  --data-dir /tmp/auto-alpha-demo/data \
  --corporate-action-dir /tmp/auto-alpha-demo/corporate_actions \
  --trade-date 20240104 \
  --pretty
```

Settlement-aware paper accounting is available through `settlement_engine/` and the settlement-aware `paper_account` methods. It models local paper settlement events for trade fills and corporate actions, tracks `available_cash`, `withdrawable_cash`, `frozen_cash`, `unsettled_receivable`, `unsettled_payable`, available shares, position lots, realized/unrealized PnL, NAV, and fee/tax breakdown. Profiles are configurable local assumptions:

- `cn_ashare_paper_default`: local A-share paper assumption with T+1 share availability and conservative sell cash timing.
- `conservative_t_plus_one_cash`: more conservative cash usability/withdrawal lag.
- `immediate_legacy`: old immediate accounting behavior for compatibility tests.

These profiles are local paper accounting approximations, not broker clearing interfaces or tax advice.

```bash
uv run python -m settlement_engine.run_settlement \
  apply-fills \
  --data-dir /tmp/auto-alpha-demo/data \
  --account-dir /tmp/auto-alpha-demo/account \
  --settlement-dir /tmp/auto-alpha-demo/settlement \
  --fills-path /tmp/auto-alpha-demo/sample_fills.jsonl \
  --trade-date 20240102 \
  --profile cn_ashare_paper_default \
  --cost-basis-method fifo \
  --pretty

uv run python -m settlement_engine.run_settlement \
  settle \
  --data-dir /tmp/auto-alpha-demo/data \
  --account-dir /tmp/auto-alpha-demo/account \
  --settlement-dir /tmp/auto-alpha-demo/settlement \
  --as-of-date 20240104 \
  --pretty
```

Settlement reports write `settlement_report.json/md`, `settlement_events.jsonl`, `cash_buckets.jsonl`, `position_lots.jsonl`, `position_availability.jsonl`, `realized_pnl.jsonl`, `account_nav.jsonl`, `account_performance_report.json`, `account_reconciliation_report.json`, and `fee_tax_report.json`.

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

Suites can also register the selected factor as a governed model version and create a human review package:

```bash
uv run python -m research_suite.run_suite \
  --suite-name governed_suite \
  --provider sample \
  --data-dir /tmp/auto-alpha-demo/data \
  --universe-name csi300_sample \
  --index-code 000300.SH \
  --factor-store-dir /tmp/auto-alpha-demo/store \
  --report-dir /tmp/auto-alpha-demo/reports \
  --output-dir /tmp/auto-alpha-demo/suite \
  --backtest-dir /tmp/auto-alpha-demo/backtest \
  --orders-dir /tmp/auto-alpha-demo/orders \
  --register-model-version \
  --model-registry-dir /tmp/auto-alpha-demo/model_registry \
  --create-model-review-package \
  --model-lifecycle-output-dir /tmp/auto-alpha-demo/model_lifecycle \
  --require-model-approval \
  --model-approval-store-dir /tmp/auto-alpha-demo/approvals \
  --pretty
```

The lifecycle path writes `model_versions.jsonl`, `model_deployments.jsonl`, `lifecycle_events.jsonl`, `model_registry_report.json/md`, `model_lineage_graph.json`, `factor_lifecycle_report.json/md`, `model_review_package.json/md`, and a pending `model_lifecycle` approval batch. After approval, activation is explicit:

```bash
uv run python -m approval.run_approval \
  --store-dir /tmp/auto-alpha-demo/approvals \
  approve \
  --approval-id <MODEL_APPROVAL_ID> \
  --reviewer local_reviewer \
  --comment activate_model \
  --pretty

uv run python -m factor_lifecycle.run_lifecycle \
  apply-approved \
  --factor-store-dir /tmp/auto-alpha-demo/store \
  --registry-dir /tmp/auto-alpha-demo/model_registry \
  --approval-store-dir /tmp/auto-alpha-demo/approvals \
  --output-dir /tmp/auto-alpha-demo/model_lifecycle_apply \
  --approval-id <MODEL_APPROVAL_ID> \
  --pretty
```

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

Barra-like risk model v1 adds style factors (`size`, `value`, `momentum`, `volatility`, `trading_activity`, `quality`, `growth`), industry one-hot exposures, cross-sectional factor return estimates, factor covariance, specific risk, portfolio/active risk decomposition, and return attribution. Enable it with `--use-factor-risk-model`; backtests can also use `--attribution` and write `risk_exposures.jsonl`, `risk_decomposition.jsonl`, `return_attribution.jsonl`, and `risk_model_report.json/md`.

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

`risk_controls/` provides opt-in pre-trade gates for strategy, operations, broker adapter, and backtest smoke runs. It writes `risk_control_report.json/md`, `risk_control_breaches.jsonl`, `risk_control_decisions.jsonl`, `risk_limit_usage.jsonl`, accepted/rejected/clipped order JSONL files, `kill_switch_state.json`, and override approval records. Example:

```bash
uv run python -m risk_controls.run_controls init-policy \
  --output-dir /tmp/auto-alpha-demo/risk_policy \
  --profile cn_ashare_paper_default \
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
  --risk-controls \
  --risk-control-state-dir /tmp/auto-alpha-demo/risk_state \
  --risk-control-output-dir /tmp/auto-alpha-demo/production/risk_controls \
  --block-on-kill-switch \
  --require-approval \
  --pretty
```

The kill switch is local state only:

```bash
uv run python -m risk_controls.run_controls activate-kill-switch \
  --state-dir /tmp/auto-alpha-demo/risk_state \
  --reason manual_review \
  --pretty
```

`broker_adapter/` adds a local broker contract on top of approved child orders. It supports:

- `simulated`: idempotent broker order submission, local status transitions, broker fills, broker events, cancellation/replacement simulation, and reconciliation.
- `file`: generic broker instruction outbox with CSV/JSONL/manifest plus optional inbox status/fill import.

The file adapter uses an internal generic schema and optional config-driven `field_mapping`. `schema_name=qmt_skeleton` is only a configurable skeleton for manual mapping review. It does not guarantee compatibility with any real QMT installation or broker desk file format.

`broker_statement/` imports local end-of-day statement files into a normalized external account mirror. It supports generic CSV/JSON/JSONL inputs for orders, trades, fills, positions, cash, settlements, and corporate actions. The QMT mode is a field-mapping skeleton only; real broker files must be manually mapped and reviewed before use. Import writes `broker_statement_manifest.json`, `broker_statement_import_report.json/md`, `broker_statement_validation_report.json`, `broker_statement_parse_issues.jsonl`, and `normalized_external_*.jsonl`.

`reconciliation_center/` compares the external mirror with broker-adapter fills/events, paper-account ledgers, settlement artifacts, and corporate-action ledgers. It writes `eod_reconciliation_report.json/md`, `reconciliation_breaks.jsonl`, `external_account_mirror.json`, external mirror JSONL files, optional `adjustment_proposals.jsonl`, optional `adjustment_proposal_batch.json`, and `adjustment_application_result.json/md`. Adjustment proposals are not applied automatically; they require an `account_reconciliation_adjustment` approval and are idempotent when applied to the paper account.

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
- `ASHARE_DASHBOARD_BACKFILL_DIR`
- `ASHARE_DASHBOARD_DATA_LAKE_DIR`
- `ASHARE_DASHBOARD_PORTFOLIO_LAB_DIR`
- `ASHARE_DASHBOARD_PORTFOLIO_CERTIFICATION_DIR`
- `ASHARE_DASHBOARD_PRODUCTION_ORCHESTRATOR_DIR`
- `ASHARE_DASHBOARD_SHADOW_TRADING_DIR`
- `ASHARE_DASHBOARD_INCIDENT_DIR`
- `ASHARE_DASHBOARD_SCHEMA_VALIDATION_DIR`
- `ASHARE_DASHBOARD_RELEASE_DIR`
- `ASHARE_DASHBOARD_CI_DIR`

## Daily Production Operations

The platform still has no real broker integration. Daily production uses local approval and a persistent paper account:

Portfolio-level production gates can require a certified policy. Use `--portfolio-policy-path` for an explicit certified policy package, or `--active-optimizer-policy --model-registry-dir ...` to load the active `optimizer_policy` deployment. `--require-certified-portfolio-policy` fails closed when the policy is not certified or conditional, and `--require-active-optimizer-policy` fails closed when no active optimizer policy exists.

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
  --settlement-aware \
  --settlement-dir /tmp/auto-alpha-demo/settlement \
  --settlement-profile cn_ashare_paper_default \
  --cost-basis-method fifo \
  --settle-before-trading \
  --enforce-available-cash \
  --enforce-available-shares \
  --max-active-style-exposure 1.0 \
  --top-n 2 \
  --max-weight 0.10 \
  --portfolio-value 1000000 \
  --use-model-registry \
  --model-registry-dir /tmp/auto-alpha-demo/model_registry \
  --require-active-model \
  --active-optimizer-policy \
  --require-active-optimizer-policy \
  --require-certified-portfolio-policy \
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
  --use-model-registry \
  --model-registry-dir /tmp/auto-alpha-demo/model_registry \
  --require-active-model \
  --settlement-aware \
  --settlement-dir /tmp/auto-alpha-demo/settlement \
  --settlement-profile cn_ashare_paper_default \
  --cost-basis-method fifo \
  --settle-through-date 20240104 \
  --broker-adapter simulated \
  --broker-store-dir /tmp/auto-alpha-demo/broker \
  --broker-auto-fill \
  --broker-reconcile \
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
  --model-registry-dir /tmp/auto-alpha-demo/model_registry \
  --factor-lifecycle-report-path /tmp/auto-alpha-demo/model_lifecycle/factor_lifecycle_report.json \
  --model-lineage-graph-path /tmp/auto-alpha-demo/model_registry/model_lineage_graph.json \
  --settlement-report-path /tmp/auto-alpha-demo/settlement/settlement_report.json \
  --settlement-events-path /tmp/auto-alpha-demo/settlement/settlement_events.jsonl \
  --cash-buckets-path /tmp/auto-alpha-demo/settlement/cash_buckets.jsonl \
  --position-lots-path /tmp/auto-alpha-demo/settlement/position_lots.jsonl \
  --position-availability-path /tmp/auto-alpha-demo/settlement/position_availability.jsonl \
  --realized-pnl-path /tmp/auto-alpha-demo/settlement/realized_pnl.jsonl \
  --account-nav-path /tmp/auto-alpha-demo/settlement/account_nav.jsonl \
  --account-reconciliation-report-path /tmp/auto-alpha-demo/settlement/account_reconciliation_report.json \
  --fee-tax-report-path /tmp/auto-alpha-demo/settlement/fee_tax_report.json \
  --pretty
```

Daily production writes `production_run.json/md`; approvals are stored under `approvals/<approval_id>.json` plus `approval_log.jsonl`; the paper account writes `account_state.json`, `positions.jsonl`, `cash_ledger.jsonl`, `trade_ledger.jsonl`, settlement artifacts, and `account_snapshots.jsonl`; broker-enabled runs write `broker_report.json/md`, `broker_orders.jsonl`, `broker_events.jsonl`, `broker_fills.jsonl`, and `broker_reconciliation.json/md`; model-governed runs record model version/deployment context; monitoring writes `monitoring_report.json/md` and `alerts.jsonl`.

`production_orchestrator/` wraps the daily path with a trading-day plan, readiness gates, phase state, resume metadata, incident creation, and a production day package. `shadow_only` generates approvals and a shadow book without broker/file submission or paper-account mutation. `paper_simulated` routes an approved batch through the existing simulated broker, paper account, settlement, and reconciliation path.

```bash
uv run python -m production_orchestrator.run_production plan-day \
  --production-state-dir /tmp/auto-alpha-demo/production_state \
  --output-dir /tmp/auto-alpha-demo/production_plan \
  --run-mode shadow_only \
  --trade-date 20240104 \
  --as-of-date 20240104 \
  --data-dir /tmp/auto-alpha-demo/data \
  --factor-store-dir /tmp/auto-alpha-demo/store \
  --model-registry-dir /tmp/auto-alpha-demo/model_registry \
  --approval-store-dir /tmp/auto-alpha-demo/approvals \
  --paper-account-dir /tmp/auto-alpha-demo/account \
  --orders-dir /tmp/auto-alpha-demo/daily_orders \
  --pretty

uv run python -m production_orchestrator.run_production run-day \
  --production-state-dir /tmp/auto-alpha-demo/production_state \
  --output-dir /tmp/auto-alpha-demo/production_shadow \
  --run-mode shadow_only \
  --trade-date 20240104 \
  --as-of-date 20240104 \
  --data-dir /tmp/auto-alpha-demo/data \
  --factor-store-dir /tmp/auto-alpha-demo/store \
  --approval-store-dir /tmp/auto-alpha-demo/approvals \
  --paper-account-dir /tmp/auto-alpha-demo/account \
  --orders-dir /tmp/auto-alpha-demo/daily_orders \
  --shadow-dir /tmp/auto-alpha-demo/shadow \
  --top-n 2 \
  --max-weight 0.10 \
  --pretty

uv run python -m incident_response.run_incident \
  --incident-store-dir /tmp/auto-alpha-demo/incidents \
  detect \
  --production-run-id <PRODUCTION_RUN_ID> \
  --trade-date 20240104 \
  --production-orchestrator-report-path /tmp/auto-alpha-demo/production_shadow/production_orchestrator_report.json \
  --pretty
```

The orchestrator writes `production_run_plan.json/md`, `production_orchestrator_report.json/md`, `production_readiness_report.json`, `production_phase_runs.jsonl`, `production_gate_results.jsonl`, `production_run_events.jsonl`, `production_runbook.json`, and `production_day_package.json`. Shadow trading writes `shadow_run_report.json/md`, shadow orders/fills/positions/snapshots, drift, performance, and comparison reports. Incidents write `incident_report.json/md`, `incident_records.jsonl`, `incident_events.jsonl`, and `incident_runbook.json`.

`production_replay/` runs the daily orchestrator across multiple trade dates with local replay state. It can run shadow-only days, approval-gated paper-simulated days, or a mixed window, then writes `production_replay_report.json/md`, `production_replay_plan.json`, day/event JSONL files, a replay package, and a replay artifact catalog.

```bash
uv run python -m production_replay.run_replay run \
  --replay-name sample_shadow_replay \
  --replay-mode shadow_only \
  --replay-state-dir /tmp/auto-alpha-demo/replay_state \
  --output-dir /tmp/auto-alpha-demo/production_replay \
  --start-date 20240102 \
  --end-date 20240104 \
  --data-dir /tmp/auto-alpha-demo/data \
  --factor-store-dir /tmp/auto-alpha-demo/store \
  --approval-store-dir /tmp/auto-alpha-demo/approvals \
  --paper-account-dir /tmp/auto-alpha-demo/account \
  --orders-root-dir /tmp/auto-alpha-demo/replay_orders \
  --shadow-root-dir /tmp/auto-alpha-demo/replay_shadow \
  --top-n 2 \
  --max-weight 0.10 \
  --pretty
```

`shadow_lab/` summarizes replay shadow artifacts across days, including cumulative return, drawdown, fill/reject rates, weight drift, and calibration suggestions. `live_readiness/` turns replay, shadow lab, certification, freeze, incident, monitoring, settlement, and reconciliation artifacts into a scorecard and a readiness decision. The default `sample_lenient_readiness` profile is for local smoke only; stricter profiles require longer replay windows and governed certification artifacts.

```bash
uv run python -m shadow_lab.run_shadow_lab analyze \
  --replay-report-path /tmp/auto-alpha-demo/production_replay/production_replay_report.json \
  --output-dir /tmp/auto-alpha-demo/shadow_lab \
  --min-shadow-days 1 \
  --pretty

uv run python -m live_readiness.run_readiness run \
  --policy-profile sample_lenient_readiness \
  --production-replay-report-path /tmp/auto-alpha-demo/production_replay/production_replay_report.json \
  --shadow-lab-report-path /tmp/auto-alpha-demo/shadow_lab/shadow_lab_report.json \
  --output-dir /tmp/auto-alpha-demo/live_readiness \
  --pretty
```

## Broker File Dry-Run Gateway

`broker_file_gateway/` is a local file-outbox safety layer for manual broker-file dry-runs. It maps approved child orders into a profile-driven generic CSV/JSONL schema, writes checksums and manifests, can synthesize/import local inbox files for roundtrip checks, and writes `broker_file_gateway_report.json/md`. It never submits orders, never reads broker credentials, and does not claim compatibility with a real broker counter.

`broker_mapping_certification/` certifies a mapping profile for dry-run use only. Built-in profiles include `generic_broker_csv`, `generic_broker_jsonl`, and `qmt_skeleton_csv`; the QMT profile is explicitly a skeleton with no real compatibility guarantee.

`operator_handoff/` packages the outbox for a human operator with a checklist, evidence log, optional local approval, and `operator_handoff_report.json/md`.

```bash
uv run python -m broker_mapping_certification.run_mapping_certify \
  --output-dir /tmp/auto-alpha-demo/mapping_certification \
  --profile-name generic_broker_csv \
  --policy dry_run_standard \
  --pretty

uv run python -m broker_file_gateway.run_gateway smoke \
  --gateway-store-dir /tmp/auto-alpha-demo/broker_file_gateway \
  --output-dir /tmp/auto-alpha-demo/broker_file_gateway \
  --outbox-dir /tmp/auto-alpha-demo/broker_file_gateway/outbox \
  --inbox-dir /tmp/auto-alpha-demo/broker_file_gateway/inbox \
  --pretty

uv run python -m operator_handoff.run_handoff smoke \
  --handoff-store-dir /tmp/auto-alpha-demo/operator_handoff \
  --approval-store-dir /tmp/auto-alpha-demo/approvals \
  --output-dir /tmp/auto-alpha-demo/operator_handoff \
  --file-batch-id demo_file_batch \
  --approval-id demo_order_approval \
  --pretty
```

`operations.run_daily` and `production_orchestrator.run_production` can route an approved batch through this dry-run gateway with `--broker-adapter file --broker-file-gateway --file-outbox-dry-run --require-mapping-certification`. The resulting readiness target is `ready_for_file_outbox_dry_run`; the platform still has no `ready_for_live_trading` status.

End-of-day statement reconciliation can run after execution or as a standalone reconcile-only step. A local smoke statement can be synthesized from internal broker and paper-account artifacts:

```bash
uv run python -m broker_statement.run_statement synthesize-from-internal \
  --broker-store-dir /tmp/auto-alpha-demo/broker \
  --broker-batch-id <APPROVAL_ID> \
  --paper-account-dir /tmp/auto-alpha-demo/account \
  --settlement-dir /tmp/auto-alpha-demo/settlement \
  --output-dir /tmp/auto-alpha-demo/external_statement \
  --account-id paper_ashare \
  --broker-name local_simulated \
  --trade-date 20240104 \
  --as-of-date 20240104 \
  --pretty

uv run python -m broker_statement.run_statement import \
  --source-dir /tmp/auto-alpha-demo/external_statement \
  --output-dir /tmp/auto-alpha-demo/statement_import \
  --schema-name generic_broker_statement \
  --account-id paper_ashare \
  --broker-name local_simulated \
  --trade-date 20240104 \
  --as-of-date 20240104 \
  --pretty

uv run python -m reconciliation_center.run_reconcile eod \
  --statement-dir /tmp/auto-alpha-demo/statement_import \
  --broker-store-dir /tmp/auto-alpha-demo/broker \
  --broker-batch-id <APPROVAL_ID> \
  --paper-account-dir /tmp/auto-alpha-demo/account \
  --settlement-dir /tmp/auto-alpha-demo/settlement \
  --output-dir /tmp/auto-alpha-demo/eod_reconciliation \
  --account-id paper_ashare \
  --trade-date 20240104 \
  --as-of-date 20240104 \
  --create-adjustment-proposals \
  --pretty
```

Operations can run only reconciliation without generating orders:

```bash
uv run python -m operations.run_daily \
  --data-dir /tmp/auto-alpha-demo/data \
  --factor-store-dir /tmp/auto-alpha-demo/store \
  --approval-store-dir /tmp/auto-alpha-demo/approvals \
  --paper-account-dir /tmp/auto-alpha-demo/account \
  --output-dir /tmp/auto-alpha-demo/production_reconcile_only \
  --orders-dir /tmp/auto-alpha-demo/daily_orders \
  --reconcile-only \
  --run-eod-reconciliation \
  --broker-statement-dir /tmp/auto-alpha-demo/statement_import \
  --eod-reconciliation-dir /tmp/auto-alpha-demo/production_reconcile_only/eod_reconciliation \
  --create-adjustment-proposals \
  --create-adjustment-approval \
  --pretty
```

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

## Artifact Schema, Release Gate, And CI

Core JSON reports now carry `artifact_type`, `schema_version`, `producer`, `created_at`, and `artifact_metadata`. JSONL business records remain unchanged; schema metadata is written through sidecars or manifests so dataclass loaders and dashboards can read old and new artifacts. Legacy artifacts without schema metadata validate in compatible mode with warnings rather than destructive rewrites.

Validate artifacts and build a schema manifest:

```bash
uv run python -m artifact_schema.run_validate \
  --artifact-dir /tmp/auto-alpha-demo/production_execute \
  --artifact-dir /tmp/auto-alpha-demo/broker \
  --output-dir /tmp/auto-alpha-demo/schema_validation \
  --write-manifest \
  --pretty
```

Run the local release gate and build a package:

```bash
uv run python -m release_manager.run_release \
  --release-name local_release \
  --output-dir /tmp/auto-alpha-demo/release \
  --artifact-dir /tmp/auto-alpha-demo/schema_validation \
  --run-build \
  --run-import-smoke \
  --run-dashboard-import \
  --run-schema-validation \
  --pretty

uv build
```

Run the shared local CI runner:

```bash
uv run python -m ci.run_local_ci --quick --output-dir .ci_artifacts --pretty
uv run python -m ci.run_local_ci --full --output-dir .ci_artifacts/full --pretty --skip-pytest
```

GitHub Actions are split by boundary:

- `.github/workflows/ci.yml`: default offline CI on push, pull request, and manual dispatch. It does not use Tushare secrets and does not pass `--allow-network`.
- `.github/workflows/release-smoke.yml`: manual offline release smoke with local CI, release gate, build, and pytest.
- `.github/workflows/tushare-online-smoke.yml`: manual gated real Tushare smoke. It uses `secrets.TUSHARE_TOKEN` only when manually dispatched and writes skipped diagnostics if the secret is absent.

The package build uses hatchling and includes only A-share platform modules. It excludes tests, assets, paper material, experiments, and standalone non-platform files from the wheel/sdist.

## Formula Corpus, Batch Evaluation, And AlphaGPT Pretraining

`feature_factory/` keeps the existing 11-feature v1 loader behavior as the default and adds an opt-in `ashare_features_v2` feature space. v2 extends the base feature set with additional return horizons, liquidity z-scores, volatility/downside-volatility, valuation, limit/suspension, index membership, and optional point-in-time/corporate-action flags. Missing optional raw fields are reported as warnings and encoded as zero matrices instead of breaking local sample runs.

```bash
uv run python -m feature_factory.run_features build \
  --data-dir /tmp/auto-alpha-demo/data \
  --output-dir /tmp/auto-alpha-demo/features \
  --feature-set-name ashare_features_v2 \
  --pretty
```

`alpha_factory/` is the campaign-level candidate funnel. It records campaign lineage, feature-set metadata, generator source budgets, random seed, compute configuration, static DSL checks, cheap proxy evaluation, optional `formula_batch_eval` full evaluation, novelty/diversity scoring, and shortlist artifacts:

```bash
uv run python -m alpha_factory.run_factory run \
  --campaign-name sample_alpha_factory \
  --data-dir /tmp/auto-alpha-demo/data \
  --factor-store-dir /tmp/auto-alpha-demo/store \
  --report-dir /tmp/auto-alpha-demo/reports \
  --output-dir /tmp/auto-alpha-demo/alpha_factory \
  --feature-set-name ashare_features_v2 \
  --build-feature-set \
  --feature-output-dir /tmp/auto-alpha-demo/features \
  --candidate-budget 40 \
  --template-budget 12 \
  --random-budget 12 \
  --mutation-budget 8 \
  --crossover-budget 4 \
  --proxy-max-candidates 30 \
  --top-k 8 \
  --use-batch-eval \
  --batch-eval-dir /tmp/auto-alpha-demo/alpha_batch_eval \
  --batch-eval-device cpu \
  --pretty
```

`formula_search.run_search` can continue from an Alpha Factory shortlist by passing `--alpha-candidates-path`, `--alpha-campaign-manifest-path`, `--use-alpha-shortlist-as-seed`, and the matching feature-set manifest. `research_suite.run_suite` can run the same stage before search with `--run-alpha-factory --use-alpha-shortlist-for-search`.

`formula_corpus/` builds a reusable local formula corpus from default candidates, seed formulas, factor store records, search outputs, neural search outputs, batch reports, and suite artifact catalogs. It validates formulas with `StackVM.validate_with_reason`, deduplicates by stable formula hash, merges source metadata, and writes:

- `formula_corpus.jsonl`
- `formula_sequences.jsonl`
- `formula_preferences.jsonl`
- `formula_corpus_stats.json`
- `formula_corpus_build_result.json`
- `formula_corpus_report.md`

```bash
uv run python -m formula_corpus.run_corpus \
  --factor-store-dir /tmp/auto-alpha-demo/store \
  --artifact-dir /tmp/auto-alpha-demo/search \
  --output-dir /tmp/auto-alpha-demo/formula_corpus \
  --pretty
```

`formula_batch_eval/` evaluates formula batches with one shared `AShareDataLoader`, optional matrix cache, optional eval cache, split metrics, transform, gate, correlation checks, and optional approved-factor registration:

```bash
uv run python -m formula_batch_eval.run_batch_eval \
  --data-dir /tmp/auto-alpha-demo/data \
  --factor-store-dir /tmp/auto-alpha-demo/store \
  --report-dir /tmp/auto-alpha-demo/reports \
  --output-dir /tmp/auto-alpha-demo/batch_eval \
  --corpus-path /tmp/auto-alpha-demo/formula_corpus/formula_corpus.jsonl \
  --use-matrix-cache \
  --matrix-cache-dir /tmp/auto-alpha-demo/data/matrix_cache \
  --use-eval-cache \
  --register-approved \
  --pretty
```

`neural_search.run_pretrain` performs offline supervised AlphaGPT pretraining from `formula_sequences.jsonl`, with optional preference fine-tuning from `formula_preferences.jsonl`. It defaults to CPU/auto device fallback and writes `alphagpt_pretrain_result.json`, `alphagpt_pretrain_history.jsonl`, `alphagpt_pretrain_report.md`, `checkpoint_manifest.json`, `distributed_training_report.json`, optional `resource_usage.json`, and `checkpoints/latest.pt`. `--distributed --world-size N` records single-node DDP metadata and falls back cleanly on CPU unless `--strict-cuda` is used.

```bash
uv run python -m neural_search.run_pretrain \
  --sequence-path /tmp/auto-alpha-demo/formula_corpus/formula_sequences.jsonl \
  --preference-path /tmp/auto-alpha-demo/formula_corpus/formula_preferences.jsonl \
  --output-dir /tmp/auto-alpha-demo/pretrain \
  --epochs 1 \
  --batch-size 8 \
  --device auto \
  --pretty
```

`formula_batch_eval.run_batch_eval` can split a formula corpus into deterministic shards and merge shard outputs:

```bash
uv run python -m formula_batch_eval.run_batch_eval \
  --data-dir /tmp/auto-alpha-demo/data \
  --factor-store-dir /tmp/auto-alpha-demo/store \
  --report-dir /tmp/auto-alpha-demo/reports \
  --output-dir /tmp/auto-alpha-demo/batch_eval_shard_0 \
  --corpus-path /tmp/auto-alpha-demo/formula_corpus/formula_corpus.jsonl \
  --shard-id 0 \
  --shard-count 4 \
  --write-shard-manifest \
  --resource-report-path /tmp/auto-alpha-demo/batch_eval_shard_0/resource_usage.json \
  --pretty
```

`compute_cluster/` provides a local research compute plane. It does not require GPU in CI; CUDA jobs acquire file-based GPU leases when available and otherwise report skipped/fallback states.

```bash
uv run python -m compute_cluster.run_compute probe \
  --state-dir /tmp/auto-alpha-demo/compute_state \
  --output-dir /tmp/auto-alpha-demo/compute_probe \
  --pretty

uv run python -m compute_cluster.run_compute smoke \
  --state-dir /tmp/auto-alpha-demo/compute_state \
  --output-dir /tmp/auto-alpha-demo/compute_smoke \
  --pretty
```

`experiment_orchestrator/` builds a formula-shard experiment plan, submits jobs through the local scheduler, and merges shard results:

```bash
uv run python -m experiment_orchestrator.run_experiment smoke \
  --data-dir /tmp/auto-alpha-demo/data \
  --output-dir /tmp/auto-alpha-demo/experiment \
  --compute-state-dir /tmp/auto-alpha-demo/compute_state \
  --shard-count 4 \
  --device cpu \
  --pretty
```

`formula_search.run_search` and `research_suite.run_suite` can now reuse these artifacts with `--corpus-path`, `--neural-checkpoint`, `--use-batch-eval`, `--use-matrix-cache`, and `--use-eval-cache`. A suite can build the corpus, pretrain AlphaGPT, run batch evaluation, search, backtest, orders, walk-forward, and promotion in one command by adding:

```bash
--run-alpha-factory \
--alpha-feature-set-name ashare_features_v2 \
--alpha-build-feature-set \
--alpha-factory-dir /tmp/auto-alpha-demo/alpha_factory \
--alpha-feature-output-dir /tmp/auto-alpha-demo/features \
--use-alpha-shortlist-for-search \
--build-formula-corpus \
--pretrain-alphagpt \
--use-batch-eval \
--use-matrix-cache \
--use-compute-scheduler \
--formula-shards 4 \
--compute-output-dir /tmp/auto-alpha-demo/compute \
--experiment-output-dir /tmp/auto-alpha-demo/experiment
```

## Point-In-Time And Leakage Governance

`point_in_time/` defines dataset availability contracts, security lifecycle records, active security masks, survivorship-bias reports, and PIT validation reports. It treats financial features as available only after `announce_date`; daily bars, daily basic, daily limits, and adjustment factors are marked as after-close or weak PIT contracts depending on `--feature-cutoff-mode`.

Build PIT artifacts locally:

```bash
uv run python -m point_in_time.run_pit validate \
  --data-dir /tmp/auto-alpha-demo/data \
  --output-dir /tmp/auto-alpha-demo/pit \
  --start-date 20240102 \
  --end-date 20240104 \
  --as-of-date 20240104 \
  --feature-cutoff-mode next_trade_day_open \
  --pretty
```

`leakage_audit/` scans DSL formulas, factor values, truncation consistency, backtest artifacts, and survivorship status for future-data leakage. It writes `leakage_audit_report.json/md`, `leakage_issues.jsonl`, formula scan, truncation, factor-value, and backtest leakage reports.

```bash
uv run python -m leakage_audit.run_audit \
  --data-dir /tmp/auto-alpha-demo/data \
  --factor-store-dir /tmp/auto-alpha-demo/store \
  --output-dir /tmp/auto-alpha-demo/leakage \
  --as-of-date 20240104 \
  --cutoff-date 20240104 \
  --point-in-time \
  --feature-cutoff-mode next_trade_day_open \
  --run-static-scan \
  --run-truncation-test \
  --pretty
```

Strict PIT governance is opt-in. Existing loaders and backtests keep their prior default behavior unless `--point-in-time` or `--run-leakage-audit` is passed. When enabled, `AShareDataLoader` exposes `active_mask`, `listing_age_days`, and `pit_available_mask`; matrix cache can persist those fields; universe, research, formula search, research suite, backtest, operations, lifecycle review, monitoring, dashboard, schema validation, release inventory, and local CI can consume the PIT/leakage artifacts.

Current PIT boundaries:

- `financial_features` use `announce_date <= trade_date`.
- Daily close and daily basic availability depends on `feature_cutoff_mode`; `next_trade_day_open` marks same-day end-of-day data unavailable for same-day opening decisions.
- `securities` can request `L,D,P` via `--security-list-statuses L,D,P`; if only `L` is present, reports show a current-only security-master warning rather than failing by default.
- `adjustment_factors` and `index_members` are included in contracts but require human review for production-grade as-of semantics.

## Pre-Live Compliance, Broker UAT, And Go/No-Go Review

`program_trading_compliance/` builds a local program-trading evidence pack from existing artifacts. It records system, strategy, risk-control, data, model, execution, operation, incident, monitoring, release, and approval evidence by path/hash/summary rather than copying sensitive files. It also runs a low-cost secret scan and writes compliance review artifacts. This is local evidence organization only; it is not legal advice, regulatory filing, broker authorization, or trading permission.

```bash
uv run python -m program_trading_compliance.run_compliance build-pack \
  --output-dir /tmp/auto-alpha-demo/compliance \
  --artifact-dir /tmp/auto-alpha-demo \
  --pretty
```

`broker_uat_lab/` runs deterministic offline BrokerAdapter contract UAT. The mock adapter covers idempotent submit, full/partial/rejected fills, cancel/replace, duplicate fills, out-of-order events, replay, rate-limit handling, kill-switch blocking, file-outbox roundtrip placeholders, settlement checks, and EOD checks without network calls or broker credentials.

```bash
uv run python -m broker_uat_lab.run_uat run \
  --output-dir /tmp/auto-alpha-demo/broker_uat \
  --broker-store-dir /tmp/auto-alpha-demo/broker_uat_store \
  --profile sample \
  --adapter mock \
  --pretty
```

`go_live_gate/` combines compliance, secret-scan, BrokerAdapter UAT, dry-run file gateway, mapping certification, handoff, readiness, replay, risk, settlement, incident, monitoring, and release artifacts into a pre-live scorecard. Its decision can only represent local readiness stages: `not_ready`, `insufficient_data`, `ready_for_broker_uat`, `ready_for_file_outbox_dry_run`, or `ready_for_manual_pilot_review`. It does not change production mode, enable a broker route, unlock a kill switch, or submit anything externally.

```bash
uv run python -m go_live_gate.run_go_live run \
  --policy-profile sample_lenient_go_live \
  --output-dir /tmp/auto-alpha-demo/go_live_gate \
  --program-trading-compliance-pack-path /tmp/auto-alpha-demo/compliance/program_trading_compliance_pack.json \
  --secret-scan-report-path /tmp/auto-alpha-demo/compliance/secret_scan_report.json \
  --broker-uat-report-path /tmp/auto-alpha-demo/broker_uat/broker_uat_report.json \
  --broker-adapter-contract-report-path /tmp/auto-alpha-demo/broker_uat/broker_adapter_contract_report.json \
  --pretty
```

`approval/` supports `compliance_review`, `broker_uat_review`, and `go_live_review` approval batches with empty order lists. These approvals are local review records only and never trigger execution.

## Current Gaps

- Tushare HTTP provider, production sync scaffolding, governed backfill plans, offline fake smoke, gated online smoke/backfill, permission/rate diagnostics, audit summary, incremental recovery checks, baseline comparison, dataset versioning, and research freezes are available; production use still requires real token/quota operation, real full-market performance runs, incremental matrix refresh, and more provider pairs.
- Barra-like risk model v1 and benchmark-aware portfolio optimization are available locally; future work should add production Barra definitions, robust full-market covariance calibration, a professional optimizer, and large-scale performance tuning.
- Local daily simulation supports A-share constraints, pre-trade risk controls, local kill switch, override approvals, capacity estimates, impact-cost estimates, child-order scheduling, broker-adapter state, file instruction export, settlement-aware paper accounting, lot cost, realized PnL, NAV reconciliation, generic statement import, external account mirroring, EOD break management, and execution quality reports; future work should add finer real-world matching, minute-level volume modeling, verified real broker statement mappings, richer limit policies, and real broker connectivity.
- Local formula search, batch formula evaluation, formula corpus construction, offline AlphaGPT supervised pretraining, a first neural-guided policy-search path, and a local CPU/GPU compute scheduler are available; future work should add stronger reinforcement learning, larger offline corpora, more operators, true full-market 4-GPU stress runs, richer DDP training, and broader stability validation.
- Feature Factory v2 and Alpha Factory campaign funnels are available locally; future work should expand the feature catalog against full-market data, calibrate proxy scores with longer histories, and run large GPU-backed campaigns outside default CI.
- Matrix cache, local performance benchmark, and data-source comparison skeletons are available; future work should add real full-market stress runs, incremental matrix refresh, and more provider pairs.
- One-click research suites now provide local walk-forward, promotion gates, model registry records, lifecycle review packages, active deployment state, and rollback artifacts; daily operations can require an active governed model. Future work should add richer lifecycle policies and external review workflow integrations.
- Portfolio Lab and Portfolio Certification provide local policy-grid robustness checks, certified portfolio policy packages, optimizer-policy registration, and activation approval gates. Sample certification is only a smoke path; real certification should be tied to a governed data freeze and longer production review windows.
- Broker adapter, dry-run file outbox gateway, mapping certification, operator handoff packages, local compliance evidence packs, BrokerAdapter UAT, Go/No-Go scorecards, broker statement import, settlement profiles, EOD reconciliation, account ledger, production-day orchestration, multi-day replay, shadow lab, live readiness, shadow-only simulation, and incident response are local only. No real broker integration, credential handling, network submission, verified QMT/broker file compatibility, regulatory filing automation, legal opinion, or tax reporting interface is implemented.

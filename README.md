# Auto-alpha

Auto-alpha is an A-share quantitative factor research platform. It provides a local, reproducible workflow for data preparation, factor formula research, factor registration, portfolio simulation, paper order export, and artifact review.

The current implementation is local-first. It uses deterministic sample data and JSON/JSONL artifacts so the full research loop can run without external services, while the Tushare HTTP provider can be enabled with a valid token.

Task 052-A adds governed historical-union repairs for `suspend_d`, `stock_st`, and per-security `namechange`, content-addressed immutable freeze and strict engineering PIT matrix builders, a shared research cutoff firewall, and conditional four-GPU retrospective replay evidence. This path is fail-closed and remains `retrospective_pit_proxy`; untouched holdout, certification, portfolio, paper, and live readiness stay false.

## Modules

- `data_pipeline/`: A-share data configuration, sample and Tushare HTTP providers, market constraint datasets, sync planning, response cache, request audit, local JSONL storage, data quality checks, sync state, and data sync CLI.
- `data_source_validation/`: Offline and gated-online provider readiness, Tushare permission/rate/field diagnostics, incremental recovery smoke, field coverage, audit summary, and baseline comparison reports.
- `data_backfill/`: Production-style full-history backfill planning, chunked job execution, staging/quarantine, resume state, coverage reports, and readiness/quota summaries.
- `backfill_observer/`: Read-only running-backfill observer, progress/ETA reports, repair commands, and postprocess plans.
- `backfill_repair/`: Explicit repair batch planner/runner for failed, quarantined, missing, empty, rate-limited, or timed-out backfill jobs; defaults to dry-run and blocks real data paths unless explicitly allowed.
- `raw_data_landing/`: Read-only raw JSONL landing QA, coverage matrix, duplicate-key checks, and freeze readiness gate.
- `raw_data_index/`: Streaming sidecar index for raw `records.jsonl` files; writes dataset summaries, hashes, date/security coverage, partition manifests, freshness validation, and active-download safety reports without changing the JSONL storage format.
- `data_quality_lab/`: Semantic full-market data QA rules, cross-dataset consistency checks, severity scorecards, freeze/matrix/alpha gates, and non-mutating repair suggestions.
- `research_data_readiness/`: Research-readiness gate that combines raw landing QA, running backfill progress, PIT safety, matrix freshness, and feature-family readiness.
- `post_download_orchestrator/`: Plan-first post-download workflow generator and local state machine for observer, landing QA, repair review, compact/validate/stats, data lake freeze, PIT/leakage, matrix refresh, schema validation, freeze candidate packages, and research smoke.
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
- `alpha_experiment_store/`: Local Alpha campaign warehouse, shard factor-store consolidation, cross-campaign dedupe reports, leaderboard, and validation candidate pool exports.
- `validation_campaign_store/`: Batch validation campaign warehouse for Alpha candidate pools, shard plans, validation result consolidation, validation leaderboards, and factor certification queue export.
- `validation_lab/`: Out-of-sample validation, walk-forward/purged/CSCV splits, multiple-testing diagnostics, overfit-risk estimates, placebo tests, regime robustness, sensitivity checks, and stress-validation reports.
- `factor_certification/`: Factor production certification policies, scorecards, decisions, review packages, and optional factor-store status application.
- `certification_campaign_store/`: Batch factor certification campaign warehouse for `factor_certification_queue.jsonl`, certification result consolidation, certified factor pools, and certified factor leaderboards.
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
- `portfolio_campaign_store/`: Batch portfolio campaign warehouse for certified factor pools, portfolio lab/certification item state, production candidate bundles, and optimizer policy activation queues.
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

`real_data_ops/` wraps readiness, governed backfill, data lake registration, research freeze creation, SLA checks, size reporting, and optional matrix refresh into one production-data command. Real Tushare runs remain gated by `--allow-network`, `RUN_TUSHARE_ONLINE_BACKFILL=1`, and a token; sample and fake Tushare profiles are fully offline.

```bash
uv run python -m real_data_ops.run_real_data run \
  --profile-name fake_tushare_small \
  --provider tushare \
  --fake-tushare-scenario success \
  --data-dir /tmp/auto-alpha-demo/fake_tushare_data \
  --output-dir /tmp/auto-alpha-demo/real_data_ops \
  --datasets securities,trade_calendar,daily_bars,daily_basic,financial_features,daily_limits,adjustment_factors,index_members,corporate_actions \
  --index-codes 000300.SH \
  --cache \
  --audit \
  --validate \
  --stats \
  --refresh-matrix \
  --pretty
```

The full-data profiles `tushare_online_smoke`, `tushare_full_ashare_2010_2026`, and `tushare_full_ashare_incremental` include request budgets, dataset-specific chunking, a 150 requests/minute default limiter, token redaction metadata, runbook/resume hints, SLA summaries, and storage-size reports. Keep real token values in `.env.local` or another ignored local file; reports only record redacted token metadata.

When a real backfill is already running, observe it from a separate shell with read-only tools. These commands do not stop, restart, resume, or mutate the downloader.

```bash
uv run python -m backfill_observer.run_observer observe \
  --run-dir /path/to/ashare_lake/runs/full_20100101_20260630 \
  --data-dir /path/to/ashare_lake/data \
  --staging-dir /path/to/ashare_lake/staging/full_20100101_20260630 \
  --logs-dir /path/to/ashare_lake/runs/queued_042c_logs \
  --output-dir /path/to/ashare_lake/reports/backfill_observer_latest \
  --profile-name full_research_data \
  --start-date 20100101 \
  --end-date 20260630 \
  --rate-limit-per-minute 150 \
  --expected-trade-days 4002 \
  --expected-security-count 5858 \
  --env-file-name .env.local \
  --pretty

uv run python -m raw_data_landing.run_landing report \
  --data-dir /path/to/ashare_lake/data \
  --run-dir /path/to/ashare_lake/runs/full_20100101_20260630 \
  --output-dir /path/to/ashare_lake/reports/raw_landing_latest \
  --profile-name full_research_data \
  --expected-start-date 20100101 \
  --expected-end-date 20260630 \
  --expected-trade-days 4002 \
  --expected-security-count 5858 \
  --pretty
```

`backfill_observer` writes progress, ETA, repair plan, postprocess plan, and issue artifacts. Repair commands are review-only until an operator chooses to run them. `raw_data_landing` streams landed `records.jsonl` files, estimates duplicate primary keys, summarizes date/security coverage, and writes a freeze-readiness decision that can block compact/freeze/matrix/Alpha Factory preparation when core data is incomplete.

`raw_data_index/` builds a separate sidecar index for stable or frozen raw JSONL directories. It does not rewrite `records.jsonl`; it writes `raw_data_index_manifest.json`, `raw_dataset_indexes.jsonl`, `raw_partitions.jsonl`, validation reports, and issue JSONL. For active download directories, run only the plan command unless an operator explicitly allows active-run indexing:

```bash
uv run python -m raw_data_index.run_index plan \
  --data-dir /path/to/ashare_lake/data \
  --run-dir /path/to/ashare_lake/runs/full_20100101_20260630 \
  --output-dir /path/to/ashare_lake/reports/raw_index_plan_latest \
  --profile-name full_research_data \
  --start-date 20100101 \
  --end-date 20260630 \
  --read-only \
  --plan-only \
  --pretty
```

After the download, repair, and freeze readiness gates are stable, build and validate the sidecar index against a frozen or otherwise stable data directory:

```bash
uv run python -m raw_data_index.run_index build \
  --data-dir /path/to/freeze_or_stable_data \
  --output-dir /path/to/reports/raw_index_latest \
  --profile-name full_research_data \
  --partition-granularity monthly \
  --read-only \
  --pretty
```

`data_quality_lab/` adds semantic QA on top of structural landing and raw-index checks. It validates daily price semantics, trading-calendar alignment, duplicate keys, security lifecycle, daily/basic/limit coverage, adjustment factors, financial PIT availability fields, index/industry membership, event/holder/risk datasets, and cross-dataset mismatches. It writes severity-ranked issues and repair suggestions only; it never mutates data:

```bash
uv run python -m data_quality_lab.run_quality_lab smoke \
  --output-dir /tmp/auto-alpha-demo/data_quality_lab \
  --pretty

uv run python -m data_quality_lab.run_quality_lab run \
  --data-dir /path/to/freeze_or_stable_data \
  --raw-data-index-manifest-path /path/to/reports/raw_index_latest/raw_data_index_manifest.json \
  --output-dir /path/to/reports/data_quality_latest \
  --profile-name full_research_data \
  --start-date 20100101 \
  --end-date 20260630 \
  --use-raw-data-index \
  --pretty
```

While a real download is still active, run only the plan command. It writes a plan artifact and does not scan the data directory:

```bash
uv run python -m data_quality_lab.run_quality_lab plan \
  --data-dir /home/lijunsi/data/auto-alpha/ashare_lake/data \
  --raw-data-index-manifest-path /home/lijunsi/data/auto-alpha/ashare_lake/reports/raw_index_plan_latest/raw_data_index_manifest.json \
  --output-dir /home/lijunsi/data/auto-alpha/ashare_lake/reports/data_quality_plan_latest \
  --profile-name full_research_data \
  --start-date 20100101 \
  --end-date 20260630 \
  --pretty
```

The main artifacts are `data_quality_lab_report.json/md`, `data_quality_scorecard.json`, `data_quality_issues.jsonl`, `dataset_quality_summary.jsonl`, `cross_dataset_quality_report.json`, `data_quality_repair_suggestions.jsonl`, and `data_quality_freeze_gate.json`. `research_data_readiness` can consume the freeze gate: core semantic blockers stop freeze, matrix, and core Alpha Factory; optional expanded blockers can still allow core alpha while blocking expanded v3 alpha.

You can also assess research readiness while a download is still running. This is read-only and writes only report artifacts:

```bash
uv run python -m research_data_readiness.run_readiness assess \
  --data-dir /path/to/ashare_lake/data \
  --run-dir /path/to/ashare_lake/runs/full_20100101_20260630 \
  --observer-report-path /path/to/ashare_lake/reports/backfill_observer_latest/backfill_observer_report.json \
  --dataset-progress-path /path/to/ashare_lake/reports/backfill_observer_latest/backfill_dataset_progress.jsonl \
  --raw-landing-report-path /path/to/ashare_lake/reports/raw_landing_latest/raw_data_landing_report.json \
  --freeze-readiness-path /path/to/ashare_lake/reports/raw_landing_latest/raw_freeze_readiness_decision.json \
  --output-dir /path/to/ashare_lake/reports/research_readiness_latest \
  --profile-name full_research_data \
  --expected-start-date 20100101 \
  --expected-end-date 20260630 \
  --expected-trade-days 4002 \
  --expected-security-count 5858 \
  --pretty
```

`research_data_readiness` writes dataset readiness rows, a feature-readiness catalog, a gate decision, and remediation commands. It distinguishes active downloads, completed downloads that still need repair, raw data ready for freeze, freeze readiness, matrix readiness, Alpha Factory readiness, and validation readiness. Weak-PIT or unsafe availability datasets are visible in the report and are not automatically promoted into Alpha Factory features.

When the download is complete and a repair plan has been reviewed, use `backfill_repair` first. Dry-run is the default; execute/resume writes its own state and refuses `/home/lijunsi/data` mutation unless an operator explicitly passes the real-data-path gate:

```bash
uv run python -m backfill_repair.run_repair dry-run \
  --data-dir /path/to/ashare_lake/data \
  --run-dir /path/to/ashare_lake/runs/full_20100101_20260630 \
  --output-dir /path/to/ashare_lake/reports/repair_latest \
  --repair-plan-path /path/to/ashare_lake/reports/backfill_observer_latest/backfill_repair_plan.json \
  --pretty
```

After repair is complete, generate the post-download plan first. The planner does not call Tushare and defaults to plan-only behavior:

```bash
uv run python -m post_download_orchestrator.run_post_download plan \
  --data-dir /path/to/ashare_lake/data \
  --run-dir /path/to/ashare_lake/runs/full_20100101_20260630 \
  --staging-dir /path/to/ashare_lake/staging/full_20100101_20260630 \
  --output-dir /path/to/ashare_lake/reports/post_download_latest \
  --registry-dir /path/to/ashare_lake/registry \
  --freeze-dir /path/to/ashare_lake/freeze \
  --matrix-cache-dir /path/to/ashare_lake/matrix_cache \
  --readiness-report-path /path/to/ashare_lake/reports/research_readiness_latest/research_data_readiness_report.json \
  --profile-name full_research_data \
  --start-date 20100101 \
  --end-date 20260630 \
  --plan-only \
  --pretty
```

Do not run post-download `--execute` until the active backfill is finished, repair reports are clean, semantic data-quality blockers are cleared, and readiness blockers are cleared. `--allow-incomplete` is diagnostic-only; it does not create freeze or matrix artifacts. The generated plan now includes raw-data-index plan/build/validate and semantic data-quality plan/run/scorecard/gate steps; plan steps stay diagnostic while build/QA/freeze steps remain blocked until readiness is green. A successful post-download run writes step state, step runs, events, a freeze candidate package, a final package, and an artifact catalog before any downstream Alpha Factory work is considered.

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

`validation_campaign_store/` turns `alpha_validation_candidate_pool.jsonl` into a resumable campaign: ingest and dedupe candidates, create shard input files, call `validation_lab` per shard, consolidate shard reports, rank `validation_leaderboard.jsonl`, and write `factor_certification_queue.jsonl` for the small set of candidates worth certification.

```bash
uv run python -m validation_campaign_store.run_validation_store run \
  --validation-campaign-store-dir <campaign>/validation_campaign_store \
  --source-candidate-pool-path <campaign>/validation_pool/alpha_validation_candidate_pool.jsonl \
  --data-dir <freeze>/data \
  --factor-store-dir <campaign>/consolidated_factor_store \
  --output-dir <campaign>/validation_campaign_store \
  --shard-count 8 \
  --max-candidates 200 \
  --run-placebo \
  --run-regime \
  --run-sensitivity \
  --run-stress-backtest \
  --top-k-certification-queue 20 \
  --pretty
```

`certification_campaign_store/` takes that queue into batch factor certification. It records every queue item, supports dry-run planning and resume, calls `factor_certification` for execution, consolidates decisions, and writes `certified_factor_pool.jsonl` plus `certified_factor_leaderboard.jsonl`.

```bash
uv run python -m certification_campaign_store.run_certification_campaign run \
  --certification-campaign-store-dir <campaign>/factor_certification_campaign \
  --factor-certification-queue-path <campaign>/validation_campaign_store/factor_certification_queue.jsonl \
  --output-dir <campaign>/factor_certification_campaign/items \
  --max-items 20 \
  --pretty
```

`portfolio_campaign_store/` takes `certified_factor_pool.jsonl` into portfolio policy trials and portfolio certification, then writes `production_candidate_bundle.jsonl` and `optimizer_policy_activation_queue.jsonl`. These artifacts are review inputs only. They do not activate a model or deploy trading logic; activation still requires approval, model registry, factor lifecycle, and production gates.

```bash
uv run python -m portfolio_campaign_store.run_portfolio_campaign run \
  --portfolio-campaign-store-dir <campaign>/portfolio_campaign \
  --certified-factor-pool-path <campaign>/factor_certification_campaign/certified_factor_pool.jsonl \
  --data-dir <freeze>/data \
  --factor-store-dir <campaign>/consolidated_factor_store \
  --output-dir <campaign>/portfolio_campaign/items \
  --max-items 5 \
  --max-trials 2 \
  --pretty
```

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

Real Tushare smoke is gated. It sends requests only when `--allow-network` is passed, canonical HTTPS/TLS validation succeeds, and a governed credential is injected by the operator's secret manager. Reports store only `credential_present` and `source_type`:

```bash
RUN_TUSHARE_ONLINE_SMOKE=1 \
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

The governed raw-data registry now covers the core daily set plus expanded index, industry, security-status, full financial statement, money-flow, margin, trading-event, holder, pledge, repurchase, share-unlock, and northbound holding datasets. Each expanded dataset has a Tushare API name, request fields, primary key, date/availability fields, chunk strategy, storage dedup key, field-coverage contract, dataset statistics, data-lake fingerprint support, and point-in-time contract. Datasets with uncertain publication timing are explicitly marked `weak_pit`.

`real_data_ops` provides batch acquisition profiles: `core_daily`, `index_industry_status`, `financial_statements`, `flow_margin_trading`, `holder_event_risk`, and `full_research_data`. Real raw-first runs remain gated and should use `--env-file .env.local --allow-network --require-token --rate-limit-per-minute 150 --resume --cache --audit --mode append`; add `--direct-append` for fastest raw capture, `--trade-days-only` for exchange daily datasets, and `--financial-by-ts-code` or `--ts-code-split-datasets` when a Tushare endpoint requires single-security requests.

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

`matrix_refresh/` validates whether an existing cache still matches the governed data version. `skip_if_fresh` compares the dataset-version content hash with matrix metadata and avoids rebuilding unchanged caches; `full_rebuild` recreates the cache and writes freshness and source-diff reports.

```bash
uv run python -m matrix_refresh.run_matrix_refresh refresh \
  --data-dir /tmp/auto-alpha-demo/data \
  --data-version-manifest-path /tmp/auto-alpha-demo/data_version/dataset_version_manifest.json \
  --matrix-cache-dir /tmp/auto-alpha-demo/data/matrix_cache \
  --output-dir /tmp/auto-alpha-demo/matrix_refresh \
  --refresh-mode skip_if_fresh \
  --pretty
```

Matrix cache and refresh commands can also record and compare a raw-data-index manifest. When the index is fresh, source hashing can use the sidecar index hash; when it is stale or missing, refresh reports surface a warning and fall back to the existing scan/hash path:

```bash
uv run python -m matrix_refresh.run_matrix_refresh plan \
  --data-dir /tmp/auto-alpha-demo/data \
  --matrix-cache-dir /tmp/auto-alpha-demo/data/matrix_cache \
  --output-dir /tmp/auto-alpha-demo/matrix_refresh \
  --raw-data-index-manifest-path /tmp/auto-alpha-demo/raw_index/raw_data_index_manifest.json \
  --pretty
```

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

`production_orchestrator/` wraps the daily path with a trading-day plan, readiness gates, phase state, resume metadata, incident creation, and a production day package. `shadow_only` generates approvals and a shadow book without broker/file submission or paper-account mutation. `paper_simulated` routes an approved batch through the existing simulated broker, paper account, settlement, and reconciliation path. It can optionally run `--broker-connectivity-profile mock_readonly --run-broker-readonly-mirror` as a read-only health phase; this still never calls submit/cancel/replace.

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

`production_replay/` runs the daily orchestrator across multiple trade dates with local replay state. It can run shadow-only days, approval-gated paper-simulated days, or a mixed window, then writes `production_replay_report.json/md`, `production_replay_plan.json`, day/event JSONL files, a replay package, and a replay artifact catalog. Replay summaries include read-only broker connectivity and mirror success counts when the optional health phases are enabled.

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

## Historical PIT CSI300 And Research Firewall

`universe/historical.py` builds a content-addressed historical CSI300 snapshot proof from governed `index_members` and `trade_calendar` artifacts. It validates one explicit canonical index code, complete 300-name cross-sections, weight-sum tolerances, natural-month coverage and staleness, then writes a union-of-ever-members axis plus full-replacement daily membership/weight matrices. Dates before the first complete snapshot or after the staleness limit remain unknown rather than inheriting current constituents.

`research_firewall/` provides the shared `DateFirewall` and `ResearchDataView`. The cutoff is applied before feature and target computation, and its research dates, label horizon, eligible-date hash and access audit are included in cache lineage. A configured date alone is not proof: `research_holdout_firewall_enabled=true` requires pre-compute truncation and zero out-of-bounds accesses.

Formal historical matrix, feature and validation paths are fail-closed. Missing axes, masks, validity tensors, promotion evidence or proof manifests never fall back to all-market universes or all-zero observations. Daily OHLCV and daily-basic fields remain exact-date values with `NaN`/validity for missing observations; only explicitly governed financial availability and proven suspension valuation may carry through time.

```bash
uv run python -m task_051_a.run \
  --source-campaign-root <campaign-root-from-artifact-catalog> \
  --output-dir <independent-validation-output> \
  --index-code 000300.SH \
  --pretty
```

Evidence is separated into engineering evidence, contaminated/sealed retrospective replay, and future untouched holdout. Validation states are `data_blocked`, `statistically_rejected`, `engineering_passed`, `historical_replay_passed`, and `clean_holdout_passed`. Cost, capacity, stress and drawdown remain `null`/unsupported unless an independent A-share simulator rerun produced fills and an equity curve. Retrospective evidence cannot populate certification, portfolio or deployment queues.

## Formula Corpus, Batch Evaluation, And AlphaGPT Pretraining

`feature_factory/` keeps the existing 11-feature v1 loader behavior as the default and adds opt-in `ashare_features_v2` and `ashare_features_v3` feature spaces. v2 extends the base feature set with additional return horizons, liquidity z-scores, volatility/downside-volatility, valuation, limit/suspension, index membership, and optional point-in-time/corporate-action flags. v3 adds a PIT-aware expanded-data catalog for index/industry/status, complete financial statements, earnings events, moneyflow, margin, abnormal trading, holder structure, pledge/repurchase/unlock, and northbound holding families. Missing optional raw fields are reported as warnings and encoded as zero matrices instead of breaking local sample runs. Weak-PIT or disabled features are visible in the manifest/readiness reports and are excluded from Alpha Factory sampling by default.

```bash
uv run python -m feature_factory.run_features build \
  --data-dir /tmp/auto-alpha-demo/data \
  --output-dir /tmp/auto-alpha-demo/features \
  --feature-set-name ashare_features_v2 \
  --pretty
```

The expanded v3 feature set must be explicitly requested:

```bash
uv run python -m feature_factory.run_features build \
  --data-dir /tmp/auto-alpha-demo/data \
  --output-dir /tmp/auto-alpha-demo/features_v3 \
  --device cpu \
  --feature-set-name ashare_features_v3 \
  --pretty
```

Use `--device cpu` for large full-history tensors when shared GPU memory is constrained; `--device cuda` remains opt-in and `--device auto` preserves the configured default. Manifest-only freezes resolve their governed `source_data_dir` while keeping the freeze ID/hash in the feature manifest and build result. v3 index-market returns, volatility, and valuation use a 60-day time-series z-score rather than a cross-sectional z-score, so a market-wide value is not erased when broadcast across the stock universe. v3 builds write `feature_family_readiness.json`, `feature_pit_alignment_report.json`, and `feature_build_warnings.jsonl` in addition to the usual manifest, coverage, values summary, tensor, and build-result artifacts. `feature_factory` can optionally read a raw-data-index manifest to quickly check required dataset existence and coverage before tensor work. `matrix_store` and `matrix_refresh` record the feature-set hash and recommend a full rebuild when the v3 catalog or PIT/corporate/target-return flags drift.

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
  --full-eval-max-candidates 20 \
  --top-k 8 \
  --use-batch-eval \
  --batch-eval-dir /tmp/auto-alpha-demo/alpha_batch_eval \
  --batch-eval-device cpu \
  --pretty
```

`--full-eval-max-candidates` ranks proxy-passed candidates by proxy score before the expensive full-history evaluation. `--device` controls the proxy loader independently from `--batch-eval-device`. Final multi-objective scoring uses the proxy-score percentile within the passed candidate set so proxy heuristics cannot overwhelm full-evaluation metrics. When batch evaluation is enabled, shortlist selection is limited to candidates that actually completed full evaluation. With `--register-shortlist`, full evaluation remains metric-only and the final shortlist is registered as lightweight factor metadata; full stock-date factor values are intentionally not serialized during campaign screening and can be materialized later by validation or production workflows.

To use v3 templates, pass the v3 manifest explicitly. The generator skips weak-PIT and disabled features unless `--include-weak-pit-features` is set, and family-level budgets or readiness requirements can narrow the expanded search space:

```bash
uv run python -m alpha_factory.run_factory run \
  --campaign-name sample_alpha_factory_v3 \
  --data-dir /tmp/auto-alpha-demo/data \
  --factor-store-dir /tmp/auto-alpha-demo/store_v3 \
  --output-dir /tmp/auto-alpha-demo/alpha_factory_v3 \
  --feature-set-name ashare_features_v3 \
  --feature-set-manifest-path /tmp/auto-alpha-demo/features_v3/feature_set_manifest.json \
  --require-feature-family-ready moneyflow,margin,financial_statement \
  --exclude-weak-pit-features \
  --feature-family-budget moneyflow=4,margin=4,financial_statement=4 \
  --template-budget 20 \
  --candidate-budget 40 \
  --pretty
```

For large campaigns, keep shard-local factor stores isolated and register outputs into the Alpha experiment warehouse:

```bash
uv run python -m alpha_factory.run_factory run \
  --campaign-name real_data_alpha_factory_plan_ready \
  --data-dir <freeze>/data \
  --matrix-cache-dir <freeze>/matrix_cache \
  --factor-store-dir <campaign>/factor_store \
  --output-dir <campaign>/alpha_factory \
  --use-batch-eval \
  --use-compute-scheduler \
  --shard-count 8 \
  --alpha-experiment-store-dir <campaign>/alpha_experiment_store \
  --register-experiment \
  --consolidate-shards \
  --consolidated-factor-store-dir <campaign>/consolidated_factor_store \
  --write-leaderboard \
  --validation-candidate-pool-dir <campaign>/validation_pool \
  --research-readiness-decision-path <readiness>/research_readiness_decision.json \
  --require-alpha-factory-ready
```

`alpha_experiment_store/` writes `alpha_experiment_registry.json`, `alpha_shards.jsonl`, `alpha_consolidated_factors.jsonl`, `alpha_factor_dedup_report.json`, `alpha_leaderboard.jsonl`, and `alpha_validation_candidate_pool.jsonl`. The candidate pool can be passed directly to validation:

```bash
uv run python -m validation_lab.run_validation validate-candidates \
  --data-dir <freeze>/data \
  --factor-store-dir <campaign>/consolidated_factor_store \
  --validation-candidate-pool-path <campaign>/validation_pool/alpha_validation_candidate_pool.jsonl \
  --max-candidates 20 \
  --output-dir <campaign>/validation_lab
```

For campaign-level validation, prefer the store wrapper so shard status, consolidation, leaderboard, and certification queue artifacts are all recorded:

```bash
uv run python -m validation_campaign_store.run_validation_store run \
  --validation-campaign-store-dir <campaign>/validation_campaign_store \
  --source-candidate-pool-path <campaign>/validation_pool/alpha_validation_candidate_pool.jsonl \
  --data-dir <freeze>/data \
  --factor-store-dir <campaign>/consolidated_factor_store \
  --output-dir <campaign>/validation_campaign_store \
  --shard-count 8 \
  --top-k-certification-queue 20 \
  --resume \
  --pretty
```

Large real-data validation plans can be generated without launching work. If research readiness is not validation-ready, the plan is marked blocked and `compute_jobs` is empty:

```bash
uv run python -m experiment_orchestrator.run_experiment plan \
  --workflow real_data_validation_campaign_large_plan \
  --output-dir <campaign>/validation_large_plan \
  --research-readiness-decision-path <readiness>/research_readiness_decision.json \
  --require-validation-ready \
  --validation-campaign-store-dir <campaign>/validation_campaign_store \
  --source-candidate-pool-path <campaign>/validation_pool/alpha_validation_candidate_pool.jsonl \
  --shard-count 32 \
  --candidate-budget 2000 \
  --pretty
```

The same planning boundary applies to production candidate bundle campaigns. When readiness is not portfolio-ready, this writes a blocked plan and no compute jobs:

```bash
uv run python -m experiment_orchestrator.run_experiment plan \
  --workflow real_data_portfolio_campaign_large_plan \
  --output-dir <campaign>/portfolio_large_plan \
  --research-readiness-decision-path <readiness>/research_readiness_decision.json \
  --require-portfolio-ready \
  --factor-certification-queue-path <campaign>/validation_campaign_store/factor_certification_queue.jsonl \
  --certified-factor-pool-path <campaign>/factor_certification_campaign/certified_factor_pool.jsonl \
  --max-items 50 \
  --pretty
```

To prepare a real 4GPU runbook without starting compute jobs:

```bash
uv run python -m experiment_orchestrator.run_experiment plan \
  --workflow real_data_alpha_factory_large_plan \
  --output-dir <campaign>/large_plan \
  --gpu-count 4 \
  --shard-count 32 \
  --candidate-budget 50000 \
  --research-readiness-decision-path <readiness>/research_readiness_decision.json \
  --require-alpha-factory-ready \
  --pretty
```

If readiness does not expose `can_run_core_alpha_factory` or `can_run_expanded_alpha_factory`, the plan is marked `blocked` and contains no compute jobs.

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

`broker_connectivity/` adds the safe read-only UAT connection shell. The default `mock_readonly` profile is fully offline. Any real network UAT probe must be explicitly gated by `--allow-network`, `BROKER_UAT_ALLOW_NETWORK=1`, redacted credential references, and a local `broker_connectivity_review` approval when required. It never exposes submit, cancel, replace, transfer, withdraw, or trade methods.

```bash
uv run python -m broker_connectivity.run_connectivity probe \
  --profile-name mock_readonly \
  --output-dir /tmp/auto-alpha-demo/broker_connectivity \
  --trade-date 20240104 \
  --as-of-date 20240104 \
  --pretty
```

`broker_readonly_mirror/` turns read-only account, cash, position, order, fill, and statement payloads into normalized local mirror artifacts and statement-compatible external files for reconciliation.

```bash
uv run python -m broker_readonly_mirror.run_readonly_mirror snapshot \
  --connectivity-report-path /tmp/auto-alpha-demo/broker_connectivity/broker_connectivity_report.json \
  --output-dir /tmp/auto-alpha-demo/broker_readonly_mirror \
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

`approval/` supports `compliance_review`, `broker_uat_review`, `broker_connectivity_review`, and `go_live_review` approval batches with empty order lists. These approvals are local review records only and never trigger execution.

## Feature Promotion Gate

`feature_promotion/` is the local review layer for `ashare_features_v3` weak-PIT, disabled, or unsafe expanded features. It builds a policy, per-feature evidence, a review package, local approval-compatible decisions, and allowlist/denylist artifacts. The workflow is offline by default and reads only supplied feature artifacts.

```bash
uv run python -m feature_promotion.run_promotion smoke \
  --output-dir /tmp/auto-alpha-demo/feature_promotion \
  --pretty
```

Alpha Factory, formula search, formula batch evaluation, validation, and factor certification can record or enforce the promotion policy. With `--require-feature-promotion`, only allowlisted `alpha_eligible` features enter alpha sampling; `risk_filter_only`, blocked, expired, or unreviewed weak-PIT features are rejected or warned according to the policy.

```bash
uv run python -m alpha_factory.run_factory run \
  --campaign-name v3_promotion_gate \
  --data-dir /tmp/auto-alpha-demo/data \
  --factor-store-dir /tmp/auto-alpha-demo/store \
  --output-dir /tmp/auto-alpha-demo/alpha_factory \
  --feature-set-name ashare_features_v3 \
  --feature-set-manifest-path /tmp/auto-alpha-demo/features_v3/feature_set_manifest.json \
  --feature-promotion-policy-path /tmp/auto-alpha-demo/feature_promotion/feature_promotion_policy.json \
  --feature-promotion-allowlist-path /tmp/auto-alpha-demo/feature_promotion/feature_promotion_allowlist.json \
  --feature-promotion-denylist-path /tmp/auto-alpha-demo/feature_promotion/feature_promotion_denylist.json \
  --require-feature-promotion \
  --pretty
```

Promotion does not prove alpha quality. It only creates a traceable availability, PIT, leakage, and human-review evidence chain before expanded features are allowed into candidate generation.

## Current Gaps

- Tushare HTTP provider, production sync scaffolding, governed backfill plans, read-only running-backfill observation, explicit repair batches, raw landing QA, raw sidecar indexes, semantic data-quality gates, research-readiness gates, post-download state-machine orchestration, freeze candidate packages, offline fake smoke, gated online smoke/backfill, permission/rate diagnostics, audit summary, incremental recovery checks, baseline comparison, dataset versioning, research freezes, real-data runbooks, SLA checks, storage-size reports, and incremental matrix refresh are available; production use still requires real token/quota operation, real full-market performance runs, and more provider pairs.
- Barra-like risk model v1 and benchmark-aware portfolio optimization are available locally; future work should add production Barra definitions, robust full-market covariance calibration, a professional optimizer, and large-scale performance tuning.
- Local daily simulation supports A-share constraints, pre-trade risk controls, local kill switch, override approvals, capacity estimates, impact-cost estimates, child-order scheduling, broker-adapter state, file instruction export, settlement-aware paper accounting, lot cost, realized PnL, NAV reconciliation, generic statement import, external account mirroring, EOD break management, and execution quality reports; future work should add finer real-world matching, minute-level volume modeling, verified real broker statement mappings, richer limit policies, and real broker connectivity.
- Local formula search, batch formula evaluation, formula corpus construction, offline AlphaGPT supervised pretraining, a first neural-guided policy-search path, and a local CPU/GPU compute scheduler are available; future work should add stronger reinforcement learning, larger offline corpora, more operators, true full-market 4-GPU stress runs, richer DDP training, and broader stability validation.
- Feature Factory v2/v3, feature-readiness cataloging, PIT-alignment reporting, feature promotion policy/evidence/allowlist gates, and Alpha Factory campaign funnels are available locally; future work should calibrate expanded v3 feature definitions and promotion rules on real freezes, calibrate proxy scores with longer histories, and run large GPU-backed campaigns outside default CI.
- Matrix cache, raw JSONL sidecar indexes, incremental matrix refresh, local performance benchmark, and data-source comparison skeletons are available; future work should add real full-market stress runs, finer partition-aware random access, and more provider pairs.
- One-click research suites now provide local walk-forward, promotion gates, model registry records, lifecycle review packages, active deployment state, and rollback artifacts; daily operations can require an active governed model. Future work should add richer lifecycle policies and external review workflow integrations.
- Portfolio Lab and Portfolio Certification provide local policy-grid robustness checks, certified portfolio policy packages, optimizer-policy registration, and activation approval gates. Sample certification is only a smoke path; real certification should be tied to a governed data freeze and longer production review windows.
- Broker adapter, safe read-only UAT connectivity shell, read-only account mirror, dry-run file outbox gateway, mapping certification, operator handoff packages, local compliance evidence packs, BrokerAdapter UAT, Go/No-Go scorecards, broker statement import, settlement profiles, EOD reconciliation, account ledger, production-day orchestration, multi-day replay, shadow lab, live readiness, shadow-only simulation, and incident response are local/review infrastructure only. No real order submission, cancellation, replacement, automatic live trading, verified QMT/broker compatibility, regulatory filing automation, legal opinion, or tax reporting interface is implemented.

## Real Factor Engineering Validation

- `validation_lab.materialization.FactorMaterializer` is the only formal metadata-only factor materialization path. It reads the governed v3 feature tensor and matrix cache, executes dynamic-vocabulary `StackVM`, applies the recorded transform, and writes compact `float32` NPY values plus an independent validity mask and immutable lineage manifest.
- Formal validation uses `real_long_history_engineering_robustness_v1` with configurable `756/126/126/126` rolling windows and embargo at least equal to formula lookback plus label horizon. Missing inputs, lineage drift, zero variance, low breadth, insufficient OOS history, and fixed-as-of universe evidence fail closed.
- Current retrospective campaigns always record `selection_data_reused=true`, `untouched_holdout=false`, and `evidence_level=retrospective_engineering_only`; certification and portfolio queues remain empty.
- The default backtest timing contract is `signal(t close) -> execution(t+1 open/next tradable point)`. `same_day_after_close` with zero signal lag is blocked, and covariance/risk estimates use only history available at each simulation date.
- Four-GPU validation uses four independent shard directories, exclusive CUDA leases with heartbeat, immutable input fingerprints, and fail-closed handling for unavailable GPUs, OOM, retry exhaustion, or CPU fallback.

## Conservative Suspension Engineering Replay

Task 053-A uses the versioned `conservative_event_day_open_exclusion_v1` policy. A covered `suspend_d` day with no event is known absent; any S/R event excludes that day's open from realized entry, exit and target endpoints even when provider timing is null. Null timing remains null and is not reclassified as a proven full-day suspension. Realized next-day execution state never enters the prior close ranking universe.

`task_053_a.orchestrator` validates the governed source, immutable freeze, lagged historical universe, strict matrix, joint v3 values/validity tensor, firewall proof and optional four-GPU replay evidence in dependency order. Readiness is evidence-derived and separates engineering blockers, candidate blockers, certification blockers and quality warnings. The only successful engineering terminal state is `engineering_replay_completed_certification_blocked`; untouched holdout, certification, portfolio, paper and live remain false.

The formal matrix contract is `signal close(t) -> adjusted open(t+1) entry -> adjusted open(t+2) exit`. Research signals require the t+2 endpoint to be no later than `2024-05-30`; later observations are reused diagnostics only and cannot affect ranking or replay status.

Task 053-A real engineering replay completed with deterministic A/B matrix and tensor builds, four distinct RTX 4090 shards, an uncached sibling replay comparison and immutable 4/4 resume. Candidate terminal states remain explicitly retrospective (`data_blocked`, `statistically_rejected`, or `historical_replay_passed`); these results do not constitute clean OOS or certification evidence.

### Task 054 engineering baseline

Task 054 uses a single research eligibility contract for `signal close(t) -> open(t+1) -> open(t+2)` and computes research evidence only where the `t+2` endpoint is no later than the configured cutoff. Its replay states are limited to `data_blocked`, `statistically_rejected`, and `historical_replay_passed`; none imply clean OOS or certification. Git may contain only a scrubbed evidence package, verifiable with `python -m task_054_a.verify_evidence <package.json>`; raw arrays and server paths remain outside the repository.

### Task 054-B canonical semantics and production gate

- `feature_factory.semantics` is the canonical machine-readable contract for all 95 `ashare_features_v3` features. Each feature binds raw dependencies, recursive inner operations and outer transforms, price basis, PIT availability, validity/min-period rules, implementation source hashes, longest dependency path, `max_raw_lag`, and `required_observations=max_raw_lag+1`. Formula semantics compose those feature paths with operator windows, including nested rolling, delay and delta offsets, without falling back to stored legacy lookbacks.
- `task_054_b.forensics` audits the complete frozen candidate pool rather than only the historical shortlist. It deduplicates by formula hash, requires the exact expected unique count, verifies token/name/hash/factor identity, recursively recomputes lookback, static eligibility, lookback penalty, score, rank and shortlist membership under the original frozen selection policy, and publishes a content-addressed normalized overlay without mutating historical factor records. The forensic reads frozen campaign selection artifacts only and records `target_or_outcome_read=false`; it is selection-impact evidence, not a new search or performance rerun.
- The production firewall sentinel requires `evidence_scope=real_production` and exactly 12 executions: `baseline`, `post_cutoff`, and `inside_cutoff` mutations across `raw_local`, `raw_scheduler`, `matrix_local`, and `matrix_scheduler`. It reconciles audited reads, public production-entrypoint receipts, source generations, scheduler job/run/heartbeat/device state, research-output invariance after post-cutoff mutation, cache invalidation and output change after inside-cutoff mutation, and raw/matrix plus local/scheduler equality.
- The production DAG applies stage-specific validators in the fixed order `governed_source -> strict_matrix -> v3_tensor -> production_firewall_sentinel -> identity_forensic -> four_gpu_replay`. Every stage must have complete status, schema-valid manifests, file SHA256 checks, content hashes, and exact upstream lineage. Four-GPU replay is conditional and may start only after every preceding gate verifies; it then requires the exact 20-candidate set, four scheduler states, valid CUDA replay evidence, uncached sibling equality, and immutable resume `4/4`.
- Even a fully verified engineering package has terminal status `task054b_engineering_baseline_completed_historical_selection_contaminated_certification_blocked`. `certification_ready`, `portfolio_ready`, `paper_ready`, and `live_ready` remain false and all four queues remain zero. Task 054-B must never be described as clean OOS, certification, portfolio approval, paper readiness, or live readiness.
- Repository-safe documentation/evidence must not contain real server paths, NPY payloads, or raw physical GPU UUIDs. Only scrubbed relative identities, hashes, counts, statuses, and verification summaries may be committed.

## Task 054-C production engineering baseline

- `python -m task_054_c.run --config <config.json>` is the only Task 054-C production runner. It validates the canonical engineering bundle, publishes physically bounded research projections, and executes the fixed 12-path sentinel in baseline → post-cutoff → inside-cutoff order.

## Task 055-A prospective holdout and ledger simulator

- Task 055-A seals a project-wide observation boundary before any newly available market records are opened. Dates already observed by historical campaigns remain research/development data; they cannot be renamed as an untouched holdout.
- The formal simulation input is a validated, content-addressed Simulation Bundle derived from the read-only Task 054-C canonical bundle, exact-20 normalized factor store, physical research view, independent factor validity, strict execution masks, benchmark, corporate actions, and governed unit contracts. Bare data directories, ad-hoc factor stores, readiness booleans, and compatibility fallbacks are rejected.
- The production simulator uses an event ledger for orders, fills, cash buckets, integer lots, T+1 settlement, rejections, partial fills, costs, corporate actions, positions, and open-to-open NAV. Capacity remains a `modeled_daily_bar_proxy` based only on close-time lagged evidence; it is not auction, queue, or calibrated market-impact evidence.
- Validation Lab stress output is fail-closed unless an actual simulator rerun callback supplies independently computed scenario results. It no longer fabricates total return, fill rate, drawdown, or cost by subtracting fixed penalties from prior metrics.
- Task 055-A runs every fixed Task 054-C probe independently across the preregistered scenarios. It does not generate factors, select a portfolio, certify alpha, or enable paper/live execution. Certification, portfolio, paper, and live readiness remain false and their physical queues must remain empty.
- Native validators recompute matrix/tensor partitions, axes, normalized-store identity, receipt/read-ledger chains, scheduler state, and cache invariants. Callers cannot inject empty dependencies, naked matrix/tensor paths, factor-store overrides, or readiness booleans.
- Formula lookback is expressed only as `max_raw_lag`; `required_observations=max_raw_lag+1` remains a separate field. A semantic-source change creates a new content-addressed tensor generation even when values and validity bytes remain unchanged.
- Research workers only map physical cutoff-bounded projections. Diagnostic projections and reads are separate. Server evidence is supervisor-attested and tamper-evident, not externally unforgeable.
- Historical selection remains contaminated. Certification, portfolio, paper, and live readiness remain false and all downstream queues remain zero.

### Task 055-A runtime boundary

Run the formal retrospective ledger simulator only from an authoritative Task 055-A Simulation Bundle:

```bash
python -m task_055_a.run --config /path/to/task055a_run_config.json
```

The policy seal is published before factor/execution arrays are mapped. A missing price may be carried only for valuation during a proven suspension-associated absence; unexplained gaps create immutable `data_blocked` run artifacts. A blocked factor/scenario is never converted to zero return, and certification/portfolio/paper/live queues remain outside this workflow.

## Task 055-B: Security-date evidence remediation

`task_055_b` is the fail-closed historical repair and valuation-closure layer after Task 055-A. It revalidates the prospective observation seal, inventories every relevant security-date rather than only first failures, publishes an immutable dual-geometry request plan before network access, and classifies each cell with mutually exclusive traded/non-trading/conflict/gap states.

Key production rules:

- Daily OHLCV null, non-finite, or non-positive price fields are rejected; they are never normalized to zero or treated as observed bars.
- Index membership limits selection and new buys, but does not erase an existing holding or prevent a legal sell after constituent removal.
- A suspension row alone does not authorize stale valuation. Carry requires governed official no-trade evidence or the stricter exact-date plus security-window modeled evidence contract.
- Task 055-B valuation marks are explicit immutable evidence. Artifact verification reconstructs marks and fees from raw quote evidence, corporate actions, and an immutable fee schedule; it does not fall back to raw open/close arrays.
- `factor_replay_ready`, `continuous_portfolio_valuation_ready`, and `future_research_data_ready` are separate gates. The 100-run simulator replay is not created until valuation closure has zero unresolved/conflicting cells.

Run the final native evidence gate with:

```bash
python -m task_055_b.run --config /path/to/task055b_run_config.json
```

The only success state is the explicit historical-selection-contaminated, modeled-execution, certification-blocked state. Any unresolved security-date, corporate-action break, missing fee evidence, or NAV closure failure produces `task055b_security_date_evidence_remediation_blocked`; certification, portfolio, paper, and live queues remain physically empty.

### Task 055-C evidence remediation

`python -m task_055_c.run --config <server-config.json>` rebuilds the security-date truth table, bounded request cascade, full-axis valuation marks, fee evidence gate, physical queue inspection, and conditional native simulator replay. It never accepts caller-supplied replay-success booleans. Vendor daily no-trade evidence remains modeled engineering evidence and cannot clear certification blockers.

### Task 055-D secure remediation

`python -m task_055_d.run --config /path/to/task055d_config.json` derives Task 055-C inputs from one governed root, seals L0/L1 before network use, performs token-free TLS preflight, inventories formal Tushare v3 caches, and requires both `--allow-network` and the exact request-plan hash before a governed credential may be used. Credentials are accepted only from `TUSHARE_TOKEN` or a non-symlink 0400/0600 `TUSHARE_TOKEN_FILE` outside repository and data-output roots; artifacts record only presence and source type.

The full-axis valuation v2 binds matrix partition and axis hashes and is independently regenerated from raw open/close validity plus security-date evidence. Fee Schedule v2 requires official document bytes, explicit zero rules, continuous market/side/component coverage, and fail-closed matching. Missing credential, valuation closure, fee evidence, or canonical operational-state proof yields `task055d_secure_acquisition_or_valuation_or_fee_closure_blocked`; no simulator run tree is created.

### Task 055-E offline source salvage

`python -m task_055_e.run --config <offline-config.json>` performs the credential-free, network-free first phase of Task 055-E. It derives the Task 055-C truth, Task 054-C matrix lineage, Task 055-A Simulation Bundle, governed freeze, matching raw-index declaration, Task 052+ suspension envelopes, v2/v3 caches, legacy physical cache inventory, and normalized records from one governed root.

The stage publishes a byte-addressable row provenance index, exact offline classification for the remediation key set, an immutable raw-repair delta only for fully validated source envelopes, direct prior-close reprojection for modeled-but-unmarked cells, and three separate valuation domains. The simulator gate uses only the exact-20 × five-scenario causal held-position prefix; static-axis or out-of-axis gaps remain future-research blockers rather than automatic simulator blockers. This phase never reads credentials, sends requests, opens data after `2026-06-30`, or creates simulator-success evidence.

### Task 055-F hardened truth and dynamic evidence frontier

`python -m task_055_f.run offline --config <server-config.json>` reconstructs `truth_v2` directly from indexed daily/suspend envelopes, the strict matrix, and Task 055-E provenance. `S` and `R` are separate event types; a resume row is never positive suspension evidence, same-day conflicts and intraday timing remain blocked, and even an exact positive `S` never authorizes a price without a finite prior close and the fixed 250-trading-day stale limit.

The offline run publishes an append-only actual-read ledger, compact content-addressed valuation projection, independent semantic verification, and—only after a native official Fee Schedule v2 exists—the exact-20 × five-scenario causal round-1 frontier. The frontier is explicitly the first held-position blocker set, not the total historical gap count. Unindexed cache bodies are not opened, and all reads remain bounded by `2026-06-30`.

Network execution is split across separate commands and requires both CLI authorization and the sealed plan hash:

```bash
python -m task_055_f.run canary --config <server-config.json> --allow-network --sealed-plan-hash <hash>
python -m task_055_f.run canary-verify --config <server-config.json>
python -m task_055_f.run l1-resume --config <server-config.json> --allow-network --sealed-plan-hash <hash>
```

The canary performs exactly one physical POST and stops. A chained spend ledger counts every physical attempt, L1 is exact `ts_code + trade_date`, and L2 may be generated only after L1 application and a complete truth/causal rebuild. With a closed held-mark frontier, official Fee v2, and canonical operational-state proof, the native producer executes primary 100, independently reloaded sibling 100, and immutable resume 100/100; the independent verifier recalculates every fee component, held mark, ledger, NAV, and exact 20×5 identity. Certification and deployment readiness remain false.

Modeled commission rules must explicitly exclude statutory tax/exchange components; slippage and impact must declare that they are not fee components. This prevents the statutory schedule from being embedded in commission and charged twice.

### Task 055-G pre-open evidence and fee-aware frontier seal

Task 055-G makes the offline evidence boundary observable before any governed payload is opened. An immutable Access Plan binds every allowed relative path, parent generation, expected SHA256, dataset-specific date parser, declared date range, and read mode. Production readers and the independent verifier publish separate attempted-access ledgers with `blocked_before_open`, `opened_allowed`, and `opened_policy_violation`; opening future bytes is recorded as prospective-holdout access even when validation later fails.

Fee Schedule v2 is a native staged workflow: sealed official-document plan, HTTPS acquisition, document verification, rule extraction, immutable publication, and independent reparse. Statutory rules remain tied to official document bytes and extraction assertions, while commission, slippage, and impact remain explicitly uncalibrated modeled assumptions. Fee evidence is engineering accounting input, not proof of executable capacity or certified Alpha.

The operational seal is derived from registered production writers and all canonical/legacy roots. It parses physical records instead of trusting reported counts and rejects shadow roots, unknown schemas, and symlink escapes. The fee-aware causal frontier and exact-date network plan may be published only after access, truth, Fee, operational, and independent semantic verification close their lineage.

This task does not authorize Tushare access. Its accepted terminal states are limited to `task055g_fee_aware_frontier_sealed_waiting_for_network_authorization` and `task055g_offline_engineering_baseline_blocked`. In both states `certification_ready`, `portfolio_ready`, `paper_ready`, and `live_ready` remain false, physical downstream queues remain empty, and historical-selection contamination remains an explicit certification blocker.

The production entry is `python -m task_055_g.run --governed-root <root> --output-root <sibling-run> --allow-official-fee-network`. The flag authorizes only the presealed official fee-document HTTPS plan; the Task 055-G DAG never reads Tushare credentials and the sealed remediation network state remains at zero physical attempts.

### Task 055-H offline canary authorization plane

`python -m task_055_h.run --repository-root <repo> --governed-root <root>` is a pure-offline authorization pass over the immutable Task 055-G result. It seals the ordered round-one frontier as 17 exact security-date requests, records a deterministic first canary, and requires a separate future authorization before either canary execution or resume. During Task 055-H itself, credential reads, Tushare requests, other network requests, and prospective-holdout access must all remain zero.

The Fee attestation independently binds the production Fee Schedule to its official document bytes and classifies 28 official-rate/statutory-interval records separately from 12 uncalibrated modeled records. The modeled records remain accounting assumptions; their presence cannot support certification, capacity claims, or live readiness.

The operational seal scans authoritative runtime roots as well as governed historical roots. `operational_state_unproven` means the registered roots, schemas, or physical records could not be independently proven empty and stable; it does not mean the queues are known empty. This blocker prevents canary authorization and cannot be cleared by creating a shadow empty directory.

The only Task 055-H top-level states are `canary_authorization_ready_no_network_executed` and `task055h_canary_authorization_blocked_no_network_executed`. Both are offline engineering evidence. Certification, portfolio, paper, and live readiness remain false, and no state authorizes a Tushare request by itself.

The Git-safe audit copy is `evidence/task_055_h/scrubbed_authorization_evidence.json`. It contains the complete ordered 17-key request set and hash chain, but no prices, credentials, absolute server paths, or source data. Verify it offline with `verify_scrubbed_evidence_package`; this standalone verification checks internal lineage only and does not replace server-side native artifact revalidation.

### Task 055-I single-canary execution authority

Task 055-H readiness is parent evidence only. The sole future production entrypoint is `python -m task_055_i.network_cli canary`; it accepts one sealed runtime-authority manifest, the reviewed full hash, an owner-only absolute credential file, and explicit `--allow-network`. No resume or batch command is exposed.

The canonical authority lives under the governed root rather than a task output. It binds the fixed Task 055-H seal, ordered 17-key frontier, first exact-daily request (`daily / 000413.SZ / 20160726`), immutable root identities, append-only network and transport-spend ledgers, a single-flight lock, and global `64/128/160` limits. Seal copies, root substitution, ledger deletion, and budget resets fail closed.

The Task 055-I release itself remains offline: credential reads, Tushare requests, other HTTP, real response application, and GPU work are all zero. Its isolated synthetic-response rehearsal calls the same executor and native application chain to build an immutable raw repair, governed freeze, strict matrix, v3 tensor, exact-20 materializations, research-firewall sentinel, and fee-aware exact-20 × five-scenario event-ledger replay. Synthetic evidence is permanently ineligible for the production seal.

`operational_state_unproven` remains explicit because every historical writer CLI is not yet globally constrained to one authoritative root. Monitoring and dashboard readers therefore do not claim that all downstream queues are physically proven empty. Certification, portfolio, paper, and live readiness remain false.

The Git-safe evidence is `evidence/task_055_i/task055i_scrubbed_evidence.json`. Verify its internal hash chain with `python task_055_i/verifier.py evidence/task_055_i/task055i_scrubbed_evidence.json`; full validation still requires the governed server artifacts.

### Task 055-J single-canary production closure

Task 055-J supersedes every older production Tushare entrypoint with one capability-gated gateway. The canonical authority is derived from the reviewed Task 055-H/I lineage and binds the ordered 17-key plan, fixed first daily canary, root identities, source tree, application artifact tree, append-only journals, single-flight locks, and the global `64/128/160` budget. Legacy Task 052/055-C/D/F/G/H/I network functions fail closed before credential, TLS, or transport access.

The executor persists attempt intent, transport receipt, validated v3 cache, journal completion, and execution generation in that order. Ambiguous post-without-receipt states remain permanently blocked; receipt/cache-complete crash states recover without another POST; lock replacement, ledger corruption, cache corruption, concurrent execution, root substitution, and budget reset are rejected.

The offline native rehearsal uses the real production application DAG with only the lowest HTTP response replaced. A positive daily response builds an immutable raw-repair generation, governed freeze, strict matrix, v3 values/validity tensor, exact-20 materializations, the real 12-path Research Firewall sentinel, full truth successor, and Fee-aware exact-20 × five-scenario causal replay. An empty daily response remains vendor absence, rebuilds truth/replay, and seals one unauthorized exact `suspend_d` L2 plan. Synthetic evidence is always `production_seal_eligible=false`.

The published Task 055-J state is `task055j_single_canary_production_closure_blocked_no_network_executed`. The only engineering blocker is the absence of an external immutable checkpoint proving global ledger rollback resistance; `operational_state_unproven` also remains because legacy writer roots are not globally constrained. Credential reads, Tushare POSTs, other market HTTP, GPU work, and prospective-holdout access are all zero. Certification, portfolio, optimizer, paper, and live readiness remain false.

The Git-safe evidence is `evidence/task_055_j/task055j_scrubbed_evidence.json`. Verify it in the Python 3.11 project environment with `python task_055_j/verifier.py evidence/task_055_j/task055j_scrubbed_evidence.json --repository-root .`. This checks the complete 17-key ordering, source tree, root bindings, artifact catalog, cross-lineage, budgets, and offline counters; it does not authorize a real canary.

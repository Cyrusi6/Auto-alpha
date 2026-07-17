# Current Repository Architecture

This repository is now organized as a local A-share factor research platform. The main workflow is:

`task_052_a/` audits real Task 049/050/051 lineage and runs bounded, resumable historical-union repairs without mutating prior generations. `universe/task052.py`, `data_lake/task052_freeze.py`, and `matrix_store/strict_engineering.py` provide the proof/freeze/matrix path; `research_firewall/` supplies the shared `2024-05-30` observable cutoff and t+2 endpoint contract. Four-GPU replay is conditional on all gates and never promotes the contaminated old 20 candidates to certification.

1. Prepare A-share data artifacts.
2. Build feature tensors and evaluate formula factors.
3. Register factors and experiments.
4. Run batch or search-style research and build composite factors.
5. Run the one-click research suite, including walk-forward and promotion.
6. Register promoted factors as governed model versions, build review packages, and activate approved model deployments.
7. Run equal-weight or benchmark-aware portfolio simulation.
8. Estimate capacity, build execution plans, and export target positions plus paper orders.
9. Apply opt-in pre-trade risk limits and local kill switch gates.
10. Route approved child orders through local paper, simulated broker, or file-instruction broker adapters.
11. Apply local paper settlement, cash/share availability, lot cost, PnL, and NAV reconciliation.
12. Run approval-gated daily paper operations.
13. Review artifacts and monitoring in the dashboard.

## Data Layer

`data_pipeline/` owns A-share data models, configuration, providers, local JSONL storage, sync planning, response cache, request audit, compaction, snapshots, dataset statistics, data quality checks, and sync state. It supports deterministic sample data and a standard-library Tushare Pro HTTP provider.

`data_source_validation/` is the production-readiness layer for data sources. It defines dataset contracts for the currently implemented Tushare APIs, runs offline fake Tushare scenarios, gates real Tushare probes behind `--allow-network`, redacts tokens in reports, summarizes field coverage and request audit, verifies append/resume/cache/compact/snapshot/stat recovery, and can compare a smoke run against a local baseline.

`data_backfill/` is the governed full-history loading layer. It builds stable provider/dataset/date/index jobs, enforces request quota/readiness checks, stages job outputs, supports resume from backfill state, writes coverage matrices and gap reports, and reuses the existing cache/audit/storage/quality/stat stack.

`backfill_observer/` is a read-only sidecar for active backfills. It reads state, job results, progress events, logs, and landed records, then writes progress, ETA, repair commands, postprocess commands, and issue summaries without mutating the run.

`backfill_repair/` turns reviewed observer repair plans into explicit repair batches with dry-run, execute, resume, job results, events, and repair state. It blocks real data paths and network-style repair commands by default, so real repair requires an explicit operator decision.

`raw_data_landing/` streams raw JSONL datasets to check record counts, date coverage, security coverage, parse errors, duplicate primary keys, and freeze readiness before compact/freeze/matrix work starts.

`raw_data_index/` builds a streaming sidecar index for stable raw `records.jsonl` files. It does not rewrite or migrate JSONL; it writes `raw_data_index_manifest.json`, `raw_dataset_indexes.jsonl`, `raw_partitions.jsonl`, validation reports, and issue rows so landing QA, matrix refresh, and feature builds can reuse record counts, hashes, date ranges, stock coverage, and partition summaries. Active download directories should use `plan --read-only --plan-only`; build/validate are intended for frozen or stable data.

`data_quality_lab/` is the semantic QA gate for full-market research data. It checks OHLC validity, trade-calendar alignment, duplicate keys, security lifecycle, daily/basic/limit coverage, adjustment factors, PIT availability fields for financial/event data, index/industry membership, optional event/holder/risk datasets, and cross-dataset mismatches. It writes scorecards, issue samples, repair suggestions, and `data_quality_freeze_gate.json` without mutating data. Core blockers stop freeze, matrix, and core Alpha Factory; optional expanded blockers stop only expanded v3 alpha.

`research_data_readiness/` combines raw landing QA, running backfill progress, repair/postprocess plans, point-in-time safety contracts, matrix freshness, and feature-family readiness into a single gate for freeze, matrix, Alpha Factory, and validation readiness. Weak-PIT and unsafe expanded datasets are reported explicitly and are not automatically fed into features.

`post_download_orchestrator/` creates the safe post-download sequence after a real download completes: observer refresh, landing QA, raw indexing, semantic data QA, repair review, compact/validate/stats, data lake version and freeze, PIT/leakage/corporate reports, matrix refresh, artifact schema validation, freeze candidate package, and a real-data dry smoke. It now has a local execute/resume state machine with step runs, events, final package, and artifact catalog, while still defaulting to plan-only and blocking mutation when readiness is not green.

`real_data_ops/` is the production-data run wrapper. It loads local env files without logging secrets, gates real Tushare network access, applies dataset-specific chunk plans, request budgets, and the default 150 requests/minute limiter, then writes readiness, backfill, data lake, freeze, SLA, size, runbook, and optional matrix-refresh artifacts. Sample and fake Tushare profiles are offline; real full backfill profiles require explicit network and token gates.

`data_lake/` versions governed datasets with deterministic fingerprints, registers dataset versions, creates copy/hardlink/manifest-only research freezes, validates freeze hashes, and writes lineage/retention reports. Matrix build, formula search, research suite, backtest, strategy, and operations can require a validated freeze before research or production simulation.

`corporate_actions/` normalizes cash dividends, stock bonuses, transfers, combined distributions, and proposal-only events. It writes point-in-time-aware event schedules, total-return series, adjustment-factor reconciliation reports, and paper-account corporate action ledgers. Existing research keeps the `adjusted_close` target return by default; explicit total-return runs use `--corporate-action-aware --target-return-mode corporate_action_total_return`.

The sample provider writes:

- `securities/records.jsonl`
- `trade_calendar/records.jsonl`
- `daily_bars/records.jsonl`
- `daily_basic/records.jsonl`
- `financial_features/records.jsonl`
- `daily_limits/records.jsonl`
- `adjustment_factors/records.jsonl`
- `index_members/records.jsonl`
- `corporate_actions/records.jsonl`
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

`feature_factory/` keeps `model_core` v1 defaults stable while adding versioned feature-space manifests. `ashare_features_v2` extends the original 11 features with additional return horizons, liquidity, volatility, valuation, limit/suspension, index membership, and optional PIT/corporate-action fields. `ashare_features_v3` is opt-in and adds expanded PIT-aware families for index/industry/status, complete financial statements, earnings events, moneyflow, margin, abnormal trading, holder structure, pledge/repurchase/unlock, and northbound holdings. Weak-PIT or disabled features are reported but excluded from Alpha Factory sampling by default. Feature builds write `feature_set_manifest.json`, `feature_tensor.npy`, `feature_coverage_report.json/md`, `feature_values_summary.json`, `feature_tensor_build_result.json`, and for v3 also `feature_family_readiness.json`, `feature_pit_alignment_report.json`, and `feature_build_warnings.jsonl`.

`factor_engine/` adds cross-sectional preprocessing, basic market-cap and industry neutralization, factor correlation checks, and admission gates. The engine can register transformed factor outputs into the factor store with gate metadata and similar-factor information.

`alpha_factory/` is the large-candidate campaign layer. It records campaign ids, data-freeze/feature-set lineage, generator budgets, random seed, compute config, and PIT/corporate/risk snapshots. It builds candidates from default formulas, templates, formula corpus, random generation, mutation, crossover, imported JSON, and optional neural sources; then applies static DSL checks, cheap proxy evaluation, optional `formula_batch_eval`, novelty/diversity scoring, and family caps before writing a shortlist. With `ashare_features_v3`, it can use dynamic feature-set vocabularies and expanded templates for moneyflow, margin, industry-relative, financial-quality, cashflow-quality, earnings-event, abnormal-trading, holder, pledge, and northbound families.

`alpha_experiment_store/` is the campaign warehouse for large Alpha Factory runs. It registers experiments and shards, ingests shard-local factor stores, consolidates factors by formula hash and feature/operator version, preserves source references, writes dedupe reports, ranks a leaderboard, and exports `alpha_validation_candidate_pool.jsonl` for `validation_lab`. It is designed so real 4GPU campaigns can write isolated shard stores first and merge only after shard completion.

`validation_campaign_store/` is the bridge from large Alpha candidate pools to certification. It ingests `alpha_validation_candidate_pool.jsonl`, deduplicates candidates, records campaign and shard state, runs or plans `validation_lab` shard jobs, consolidates validation artifacts, writes a validation leaderboard, and emits `factor_certification_queue.jsonl`. Real large validation should wait for research readiness; blocked readiness produces a plan/runbook with no compute jobs.

`validation_lab/` is the out-of-sample and anti-overfit governance layer. It builds deterministic walk-forward, purged/embargo, and CSCV-style splits; evaluates train/test decay, OOS score, IC stability, turnover, and robustness; summarizes multiple-testing exposure from Alpha Factory/search/batch artifacts; estimates PBO and deflated IC-like scores; and runs placebo, regime, sensitivity, and stress-backtest checks. Sample data is only a smoke path; real promotion-grade validation should be tied to a data-lake freeze.

`factor_certification/` converts validation, data-freeze, PIT/leakage, Alpha Factory, stress, settlement, risk-control, EOD reconciliation, and lifecycle artifacts into a policy scorecard and certification decision. Profiles include `sample_lenient_certification`, `research_standard`, and `production_strict`. Certification gates promotion and review; it is not a performance guarantee.

`certification_campaign_store/` is the campaign warehouse for `factor_certification_queue.jsonl`. It ingests queue items, records item state, supports dry-run/execute/resume, calls `factor_certification` for actual decisions, consolidates decisions, and writes `certified_factor_pool.jsonl` plus `certified_factor_leaderboard.jsonl`.

`portfolio_campaign_store/` starts from `certified_factor_pool.jsonl`, runs or plans portfolio lab/certification items, consolidates selected policy and certification artifacts, and writes `production_candidate_bundle.jsonl` plus `optimizer_policy_activation_queue.jsonl`. The bundle is not activation; model registry, factor lifecycle, approval, and production gates remain required.

`research/` orchestrates batch factor experiments. It loads default or JSON-defined candidate formulas, executes StackVM, applies transforms and gates, skips duplicate formula hashes, writes per-factor reports, ranks candidates, and can register a composite factor. Composite methods include equal weight, score weight, and rank average.

`formula_search/` adds local formula discovery. It uses StackVM metadata to generate legal RPN formulas, estimate arity/lookback/complexity, mutate formulas, cross over parent formulas, remove duplicate hashes, and run multi-generation search through the same batch research pipeline.

`formula_search.run_search` can seed the first generation from `alpha_factory` shortlist/candidates with `--alpha-candidates-path --use-alpha-shortlist-as-seed`, preserving feature-set and campaign metadata in downstream batch research records.

`neural_search/` adds a lightweight neural-guided formula search path. It uses AlphaGPT with supervised warm-start sequences from the factor store, default candidates, and seed formulas; a StackVM-aware action mask prevents underflow during sampling; policy steps convert research outcomes into rewards; checkpoints and training reports are written as local artifacts.

`compute_cluster/` is the local research compute plane. It probes CPU/CUDA resources with torch plus best-effort `nvidia-smi`, manages file-based GPU leases, stores compute jobs/runs/heartbeats/events as JSON/JSONL, launches subprocess jobs, supports retry/resume, and writes `compute_run_report.json/md` plus resource snapshots. No GPU is required for tests or CI.

`experiment_orchestrator/` builds compute-aware experiment plans. It shards formula corpora, emits formula batch evaluation job specs, runs them through `compute_cluster`, merges shard outputs, and writes experiment plan/graph/resource/shard/merge reports. The `real_data_alpha_factory_large_plan` workflow emits a 4GPU Alpha Factory runbook and resource plan without starting jobs; when the research-readiness gate is not alpha-ready it is marked blocked and contains no compute jobs.

`research_suite/` orchestrates the complete local workflow. It can run data sync, universe construction, formula search, backtest, paper orders, walk-forward robustness, promotion, suite report writing, and artifact catalog generation in one command.

When enabled, `research_suite.run_suite --run-alpha-factory` runs the Alpha Factory before formula search, registers feature and alpha artifacts in the suite catalog, and can continue search from the alpha shortlist with `--use-alpha-shortlist-for-search`.

When enabled, `research_suite.run_suite --run-validation-lab --run-factor-certification --require-certification` runs validation before promotion. A rejected or insufficient certification blocks production-candidate promotion; conditional certification is kept for manual review instead of automatic activation.

## Risk Model And Portfolio Optimization

`risk_model/` builds local A-share risk views from the loaded data artifacts:

- stock-level industry, size, volatility, and beta exposures
- Barra-like style factors: size, value, momentum, volatility, trading activity, quality, and growth
- industry one-hot factor exposures
- cross-sectional factor return estimates, factor covariance, and specific risk
- portfolio and benchmark industry weights
- active exposure versus an index benchmark from `index_members`
- return covariance, portfolio volatility, and tracking error
- portfolio and active risk decomposition
- return attribution and simplified active allocation/selection effects
- constraint checks for max weight, industry active weight, total active weight, tracking error, names, and HHI
- `risk_report.json/md` and, when enabled, `risk_model_report.json/md`

`portfolio_optimizer/` provides a deterministic long-only benchmark-aware optimizer and serializable portfolio policies. It ranks alpha scores, tilts from benchmark weights, clamps max names and max weight, shrinks turnover and tracking error, and outputs:

- `optimized_weights.jsonl`
- `optimization_result.json`
- `risk_report.json`
- `risk_report.md`

`portfolio_lab/` runs portfolio policy grids against scenarios such as base, higher cost, lower capacity, stricter turnover, settlement, and risk-control assumptions. It writes `portfolio_lab_report.json/md`, `portfolio_policy_grid.json`, `portfolio_scenarios.json`, trial JSONL files, `portfolio_robustness_report.json/md`, and `selected_portfolio_policy.json`.

`portfolio_certification/` turns the selected policy plus lab, validation, factor certification, data-freeze, PIT, settlement, risk-control, and reconciliation artifacts into a portfolio scorecard and decision. Passing certification writes `certified_portfolio_policy.json` and can create a `portfolio_policy_activation` approval. Applying that approval activates an `optimizer_policy` model version in `model_registry/`.

A certified factor and a certified portfolio policy are intentionally separate: the factor gate reviews signal quality; the portfolio gate reviews optimizer parameters and deployment assumptions. Sample certification is a smoke path only, not a return guarantee.

`capacity_model/` estimates stock and portfolio trading capacity from local amount, volume, turnover, and volatility matrices. It reports amount participation, volume participation, max trade value, max trade shares, estimated impact cost, capacity score, and capacity warnings.

`execution_plan/` converts target orders into parent orders, child orders, bucketed schedules, simulated child fills, and execution quality artifacts. Default buckets are `open`, `morning`, `afternoon`, and `close`.

`risk_controls/` provides opt-in local pre-trade controls. It evaluates target orders, child orders, and broker requests against JSON policy profiles, records limit usage, writes accepted/rejected/clipped order artifacts, maintains a local kill switch, and can create approval-gated override requests. It is a local paper gate only; it does not submit live orders or read broker credentials.

`broker_adapter/` defines the local broker contract. It stores broker order requests, statuses, events, fills, batch summaries, and reconciliation reports in JSON/JSONL. `SimulatedBrokerAdapter` applies local A-share trading rules to approved child orders and supports idempotent submit, cancel, replace, status, fills, and reconciliation. `FileInstructionBrokerAdapter` writes generic outbox CSV/JSONL/manifest files and can import optional inbox statuses/fills. Its `qmt_skeleton` mode is only a field-mapping skeleton and does not claim real QMT or broker file compatibility.

`broker_file_gateway/` adds a stricter dry-run file gateway on top of file instructions. It exports profile-mapped outbox CSV/JSONL files, checksum manifests, operator readme files, optional zip packages, synthesized/imported inbox smoke files, roundtrip reports, gateway state, and `broker_file_gateway_report.json/md`. It is manual-handoff only and has no real submit path. `broker_mapping_certification/` certifies mapping profiles for `certified_for_dry_run` status, including an explicit skeleton notice for QMT-style mappings. `operator_handoff/` creates a human handoff package with required checklist items, evidence, local approval, and report artifacts.

`broker_statement/` imports local generic broker statement files into normalized external orders, trades, fills, positions, cash, settlement, and corporate-action mirrors. It writes source hashes, import reports, parse issues, validation reports, and synthetic statements for local smoke tests. Its QMT mode is only a configurable skeleton and requires manual real-file verification.

`reconciliation_center/` performs end-of-day reconciliation between external statement mirrors, broker-adapter fills/events, paper-account ledgers, settlement artifacts, and corporate-action ledgers. It creates structured cash, position, fill, fee, settlement, corporate-action, NAV, stale-statement, and duplicate-id breaks, then can generate approval-gated adjustment proposals without mutating the paper account until explicitly approved.

`settlement_engine/` provides local paper clearing and account accounting. It turns trade fills and corporate action applications into deterministic settlement events, applies settlement profiles such as `cn_ashare_paper_default`, tracks available and withdrawable cash, unsettled receivable/payable, available shares, position lots, realized PnL, unrealized PnL, fee/tax breakdown, account NAV, and reconciliation reports. The profiles are local simulation assumptions, not real broker clearing rules or tax advice.

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

Backtest supports `--portfolio-method equal_weight` and `--portfolio-method risk_aware`. Risk-aware mode calls the optimizer on each rebalance date, records tracking error, active share, HHI, top weight, industry active exposure, and risk constraint violations, and can write a risk report directory. With `--use-factor-risk-model --attribution`, it also writes `risk_exposures.jsonl`, `risk_decomposition.jsonl`, `return_attribution.jsonl`, and `risk_model_report.json/md`.

With `--capacity-aware`, backtest generates parent/child execution plans before each rebalance, estimates capacity and impact cost, simulates child fills, and adds capacity/execution metrics such as amount participation, volume participation, estimated impact cost, realized execution cost, unfilled order value, execution fill rate, and capacity warning count.

## Paper Execution And Order Export

`execution/` provides local paper fills and order/fill export helpers using the same A-share trading rule primitives as the backtest.

`strategy_manager/` builds a target book for a rebalance date, validates weights, generates orders, and writes:

- `target_positions.csv`
- `target_positions.jsonl`
- `orders.csv`
- `orders.jsonl`
- `paper_fills.jsonl`

With `--portfolio-method risk_aware`, target positions include optimized weight, benchmark weight, and active weight. The summary includes risk metrics and constraint violations. With `--portfolio-policy-path`, strategy/backtest/operations override optimizer parameters from a certified policy JSON. With `--active-optimizer-policy`, they load the active optimizer policy from `model_registry/`; with `--require-certified-portfolio-policy`, uncertified policies fail closed. With `--use-factor-risk-model`, strategy and daily operations summaries include style exposure, active style exposure, and risk decomposition. With `--capacity-aware`, strategy writes `capacity_report.json/md`, `execution_plan.json/md`, `parent_orders.jsonl`, `child_orders.jsonl`, `child_fills.jsonl`, and `execution_quality.json`. With `--risk-controls`, strategy evaluates orders before approval or paper execution and writes `risk_control_report.json/md`, `risk_control_breaches.jsonl`, `risk_control_decisions.jsonl`, `risk_limit_usage.jsonl`, `accepted_orders.jsonl`, `rejected_orders.jsonl`, `clipped_orders.jsonl`, and `kill_switch_state.json`.

## Production Operations

`approval/` stores proposed order batches for local human review. It writes:

- `approvals/<approval_id>.json`
- `approval_log.jsonl`

Batches move through `pending`, `approved`, `rejected`, and `expired`. Approved batches cannot be rejected later, and every decision is logged. Approval types include order batches, model lifecycle activation, account reconciliation adjustments, and risk control overrides.

`paper_account/` maintains a persistent local paper ledger. It writes:

- `account_state.json`
- `positions.jsonl`
- `cash_ledger.jsonl`
- `trade_ledger.jsonl`
- `account_snapshots.jsonl`

Filled and partial fills update cash and positions. Rejected fills are recorded in the trade ledger but do not change holdings. Mark-to-market writes account snapshots and performance metrics.

`operations/` runs the daily production path:

1. Select an active model from `model_registry/`, or select a `production_candidate` factor when registry mode is disabled.
2. Optionally require an active `optimizer_policy` and certified portfolio policy before generating target positions.
3. Generate target positions and proposed orders with `strategy_manager`.
4. If approval is required, write a pending approval batch and stop.
5. After approval, execute local paper fills, update the paper account, and write `production_run.json` plus `production_run.md`.
6. Optionally import a broker statement, run EOD reconciliation, create adjustment proposals, create an `account_reconciliation_adjustment` approval, and apply approved manual adjustments idempotently.

Capacity-aware daily runs store parent and child order schedules inside approval batches. Approved child orders can keep the default paper simulator path, route through the simulated broker state machine, or export generic file instructions. Broker-enabled runs write `broker_report.json/md`, `broker_orders.jsonl`, `broker_events.jsonl`, `broker_fills.jsonl`, and `broker_reconciliation.json/md`. Repeated execution of the same approved child orders is idempotent at the broker order and paper-account fill layers.

Risk-control-aware daily runs can block proposal or execution while the local kill switch is active, filter proposed orders before approval, and recheck execution before broker routing. Override requests are stored as normal approval batches with `approval_type=risk_control_override`; applying an approved override is explicit and audited.

Settlement-aware daily runs can settle pending events before trading, precheck orders against available cash and available shares, apply approved broker fills into settlement events, advance settlement through a chosen date, and write `settlement_report.json/md`, `settlement_events.jsonl`, `cash_buckets.jsonl`, `position_lots.jsonl`, `position_availability.jsonl`, `realized_pnl.jsonl`, `account_nav.jsonl`, `account_performance_report.json`, `account_reconciliation_report.json`, and `fee_tax_report.json`.

EOD reconciliation writes `eod_reconciliation_report.json/md`, `reconciliation_breaks.jsonl`, `external_account_mirror.json`, external mirror JSONL files, `adjustment_proposals.jsonl`, optional `adjustment_proposal_batch.json`, and optional `adjustment_application_result.json/md`. The external account mirror never mutates internal paper state; approved adjustments are applied through `paper_account` with an idempotent `adjustment_ledger.jsonl`.

`production_orchestrator/` is the day-level runner above `operations/`. It builds the production calendar context, creates `production_run_plan.json/md`, evaluates readiness gates, records phase state, supports resume metadata, and writes `production_orchestrator_report.json/md`, `production_readiness_report.json`, phase/gate/event JSONL files, a runbook, and a `production_day_package.json`. `shadow_only` is fail-safe and does not submit broker/file instructions or mutate account state; `paper_simulated` reuses the existing approved operations path. Optional broker connectivity phases can probe a read-only profile and snapshot the read-only mirror, but they do not enable submit/cancel/replace.

`shadow_trading/` builds a separate local shadow book from target orders, parent/child order plans, or order artifacts. It writes shadow orders, fills, positions, account snapshots, drift, performance, and shadow-vs-production comparison reports without touching the paper account.

`production_replay/` runs the day-level orchestrator across a date window. It writes replay plans, day summaries, events, a package, and a replay report while preserving resume state. It can run `file_outbox_dry_run`, which routes approved orders through the dry-run gateway and records file-outbox replay counts without real submission. When optional read-only broker health phases are enabled, replay aggregation records broker connectivity success days, read-only mirror success days, and mirror break counts. `shadow_lab/` aggregates multi-day shadow artifacts into performance, drift, and calibration-suggestion reports. `live_readiness/` converts replay, shadow lab, certification, freeze, incident, monitoring, settlement, reconciliation, broker-file gateway, mapping certification, and operator handoff artifacts into a policy-driven scorecard. The file-outbox profile can produce `ready_for_file_outbox_dry_run`; there is no live-trading readiness status.

`incident_response/` stores production incidents and runbook steps in local JSON/JSONL files. It can detect incidents from orchestrator, freeze, portfolio certification, risk, EOD reconciliation, and monitoring artifacts, and supports acknowledge, resolve, and suppress transitions through `incident_response.run_incident`.

`model_registry/` stores governed model versions and active deployments:

- `model_versions.jsonl`
- `model_state.json`
- `model_deployments.jsonl`
- `lifecycle_events.jsonl`
- `model_registry_manifest.json`
- `model_registry_report.json/md`
- `model_lineage_graph.json`

`factor_lifecycle/` evaluates factor health, writes human review packages, creates `model_lifecycle` approval batches, applies approved activations, and supports pause, quarantine, retire, and rollback flows. `operations.run_daily --use-model-registry --require-active-model` blocks paused, quarantined, retired, or missing active models before order generation.

`monitoring/` checks local production artifacts:

- data freshness versus as-of date
- quality report errors
- production factor availability and recent factor drift
- production orchestrator status, readiness gates, phase failures, and close-day status
- multi-day production replay failures, shadow lab drift, calibration suggestions, and live readiness decisions
- broker file gateway roundtrip issues, mapping certification status, operator handoff checklist completion, and real-submit detection
- shadow run status, fill rate, and drift
- open, critical, and unresolved incidents plus runbook progress
- risk report violations
- active model status and lifecycle health
- pending model review approvals
- model lineage completeness and rollback availability
- style exposure drift
- active risk drift
- factor risk concentration
- return attribution anomalies
- capacity warnings
- execution fill quality
- unfilled order value
- impact cost spikes
- broker reconciliation issues
- open, rejected, and idempotent replayed broker orders
- file instruction outbox status
- broker statement import and validation status
- external statement staleness
- unresolved/material EOD reconciliation breaks
- cash, position, NAV, fill, fee, settlement, and corporate-action external differences
- pending/applied account reconciliation adjustments
- pending or failed settlement events
- cash/share availability and lot reconciliation
- realized/unrealized PnL and NAV reconciliation
- fee/tax breakdown
- rejected and partial fill ratios
- paper account equity, cash ratio, drawdown, and exposure

It writes `monitoring_report.json`, `monitoring_report.md`, and `alerts.jsonl`.

## Matrix Cache And Performance

`matrix_store/` converts governed JSONL datasets into a local numpy matrix cache:

- `matrix_cache/metadata.json`
- `matrix_cache/ts_codes.json`
- `matrix_cache/trade_dates.json`
- `matrix_cache/fields.json`
- `matrix_cache/<field>.npy`
- `matrix_cache/matrix_validation_report.json`

`AShareDataLoader` can opt into this path with `use_matrix_cache=True` and still exposes the same `ts_codes`, `trade_dates`, `raw_data_cache`, `feat_tensor`, `target_ret`, `industry_codes`, and security metadata as the JSONL loader.

`performance_benchmark/` writes `benchmark_result.json` and `benchmark_report.md` for local loader, StackVM, batch research, formula search, and portfolio simulation timings. It uses simple wall-clock timing and is intended as a repeatable local skeleton rather than full profiling.

`matrix_refresh/` compares a governed dataset-version hash with matrix cache metadata, supports `skip_if_fresh`, `validate_only`, and `full_rebuild`, and writes `matrix_refresh_plan.json`, `matrix_source_diff.json`, `matrix_freshness_report.json`, `matrix_refresh_result.json`, and issue JSONL artifacts.

`raw_data_index/` is optional but recommended before large matrix/feature work. When a fresh raw index manifest is supplied, matrix refresh can use the index hash as a source fingerprint and feature builds can use dataset index summaries for fast readiness checks. If the index is missing or stale, callers warn and fall back to the legacy scan path.

`cross_source_checks/` compares two data directories or snapshots and writes `cross_source_report.json` plus `cross_source_report.md`. It reports record count differences, missing keys, numeric field deltas, date range differences, and stock-code count differences.

Data-source smoke writes `data_source_smoke_report.json/md`, `provider_probe.json`, `field_coverage.json`, `audit_summary.json`, `incremental_recovery_report.json`, `baseline_compare_summary.json`, and `dataset_contracts.json`. Default tests use sample and fake Tushare clients only; a real Tushare token is used only when an operator explicitly passes `--allow-network --require-token`.

The raw-data registry now includes the core daily set plus expanded index, industry, security-status, full financial statement, money-flow, margin, trading-event, holder, pledge, repurchase, share-unlock, and northbound holding datasets. Each expanded dataset has a Tushare API name, fields, primary key, date/availability metadata, chunk strategy, storage dedup key, field-coverage contract, statistics/fingerprint support, and PIT contract. Weak publication-timing datasets are marked `weak_pit`.

`real_data_ops` exposes batch profiles for `core_daily`, `index_industry_status`, `financial_statements`, `flow_margin_trading`, `holder_event_risk`, and `full_research_data`. Real raw-first backfills stay behind explicit local gates: `--env-file .env.local --allow-network --require-token --rate-limit-per-minute 150 --resume --cache --audit --mode append`; use `--direct-append`, `--trade-days-only`, and `--financial-by-ts-code` / `--ts-code-split-datasets` for high-throughput capture and provider-required single-security requests.

## Point-In-Time And Leakage Governance

`point_in_time/` owns the as-of data governance layer. It defines availability contracts for current local datasets, builds `security_lifecycle.jsonl`, produces `active_security_mask.jsonl`, validates financial announcement dates, flags current-only security masters, and writes PIT plus survivorship reports. Daily end-of-day fields are explicitly governed by `feature_cutoff_mode`; `next_trade_day_open` prevents same-day after-close features from being considered available for same-day open decisions.

`leakage_audit/` is the future-data audit layer. It scans formula DSL tokens, checks factor values against `as_of_date` and active security masks, runs a small truncation consistency check, inspects backtest artifacts for signal/execution timing warnings, and writes structured leakage reports. Formula search, batch research, backtest, research suite, lifecycle review, monitoring, dashboard, schema validation, release inventory, and local CI can all read these artifacts.

Tushare security sync now supports configurable `L,D,P` stock status requests through `ASHARE_SECURITY_LIST_STATUSES` or `--security-list-statuses`. Sample data stays deterministic and local; a current-only security master is warning-level by default so historical tests and demos remain stable.

## Formula Corpus And Offline Pretraining

`formula_corpus/` turns local research artifacts into reusable AlphaGPT training data. It reads default candidates, seed formulas, factor store records, search outputs, neural outputs, batch reports, and suite catalogs; validates each formula with StackVM; deduplicates by stable formula hash; and writes corpus records, next-token sequence records, preference pairs, stats, and a human-readable report.

`formula_batch_eval/` evaluates formula batches through one shared data loader. It supports JSONL or matrix-cache data access, chunking, deterministic shard selection, shard manifests, shard output merge, optional resource reports, optional eval cache, transforms, split metrics, gate decisions, correlation checks, and optional approved-factor registration. It is used directly by `formula_batch_eval.run_batch_eval` and can be enabled inside `research.BatchFactorResearchRunner`.

`neural_search.run_pretrain` trains AlphaGPT offline from `formula_sequences.jsonl` and optional `formula_preferences.jsonl`. It writes checkpoint manifests, distributed training metadata, resource reports, and training history; it can feed `formula_search.run_search --search-mode neural|hybrid` through `--neural-checkpoint`. CPU fallback remains the default unless strict CUDA is requested.

`research_suite.run_suite` now supports:

- `--run-alpha-factory`
- `--alpha-feature-set-name ashare_features_v2`
- `--alpha-build-feature-set`
- `--use-alpha-shortlist-for-search`
- `--build-formula-corpus`
- `--pretrain-alphagpt`
- `--use-batch-eval`
- `--use-eval-cache`
- `--use-matrix-cache`
- `--use-compute-scheduler`
- `--formula-shards`
- `--compute-output-dir`
- `--experiment-output-dir`

These stages run before formula search and their artifacts are registered in the suite catalog.

## Release And Artifact Governance

`artifact_schema/` defines a local schema registry for platform artifacts. JSON reports can carry top-level `artifact_type`, `schema_version`, `producer`, `created_at`, and `artifact_metadata`. JSONL rows keep their business schema stable; schema details are captured through sidecars and manifests. Legacy unversioned artifacts are validated in compatible mode with warnings.

`artifact_schema.run_validate` scans artifact directories and suite catalogs, validates known JSON/JSONL files, writes `artifact_validation_report.json/md`, `artifact_validation_issues.jsonl`, and optionally `artifact_schema_manifest.json/md` with size, sha256, inferred artifact type, schema version, compatibility mode, and JSONL record counts.

`release_manager/` builds the local release view:

- `dependency_inventory.json`
- `module_inventory.json`
- `cli_inventory.json`
- `release_manifest.json/md`
- `release_gate_report.json/md`
- `release_notes_draft.md`

Release gates are local by default: import smoke, dashboard import, artifact schema validation, package build, optional pytest, token redaction checks, and a governed-module old-term scan. Skipped online Tushare checks are not release failures. `uv build` now produces a local wheel and sdist with only A-share platform packages included.

`ci/` provides `python -m ci.run_local_ci --quick|--full`. Quick mode runs import smoke, offline data-source smoke, artifact schema validation, and release dry-run. Full mode can add suite smoke, package build, and optional pytest.

GitHub Actions are split by risk boundary:

- `ci.yml`: offline default push/pull-request CI, no real Tushare network.
- `release-smoke.yml`: manual offline release gate and package build.
- `tushare-online-smoke.yml`: manual gated real Tushare smoke, using `secrets.TUSHARE_TOKEN` only when explicitly dispatched.

## Feature Promotion

`feature_promotion/` governs expanded `ashare_features_v3` feature eligibility. It builds `feature_promotion_policy.json`, per-feature evidence, a review package, local approval-compatible decisions, `feature_promotion_allowlist.json`, and `feature_promotion_denylist.json`. Weak-PIT and disabled expanded features remain excluded from Alpha Factory by default.

When Alpha Factory or formula search is run with `--require-feature-promotion`, only allowlisted `alpha_eligible` features can be sampled as alpha inputs. Risk/filter-only features remain usable for risk/filter workflows unless explicitly allowed as alpha. Validation Lab records unapproved or blocked feature usage as issues, and factor certification adds a `feature_promotion_check`.

The promotion layer is a PIT/leakage and human-review control. It does not certify expected returns.

## Pre-Live Review Layer

`program_trading_compliance/` creates local evidence packs for program-trading review. It inventories the software build, active strategy/model/portfolio artifacts, risk controls, data evidence, operations evidence, incident evidence, monitoring evidence, release evidence, checklist gaps, and secret-scan findings. The pack is evidence organization only; it is not legal advice, regulatory filing, broker approval, or trading permission.

`broker_uat_lab/` is the offline BrokerAdapter contract lab. It provides a deterministic mock broker and sample/strict scenario profiles for submit idempotency, status transitions, full/partial/rejected fills, cancel/replace, duplicate and out-of-order callbacks, replay, file-outbox placeholders, EOD/settlement checks, and kill-switch blocking. It does not use broker credentials or network access.

`broker_connectivity/` is the safe broker UAT connectivity shell. It defines read-only profiles, redacted credential references, network guards, connectivity sessions, and probe reports. Default smoke uses `mock_readonly`; real network UAT is gated by explicit CLI/env/approval checks and still exposes only read-only methods.

`broker_readonly_mirror/` normalizes read-only account, cash, position, order, fill, and statement payloads into local external mirror artifacts and reconciliation reports. It can feed broker statement and EOD reconciliation workflows without adding submit/cancel/replace capability.

`go_live_gate/` evaluates compliance, secret scan, BrokerAdapter UAT, dry-run file gateway, mapping certification, handoff, readiness, replay, risk, settlement, incident, monitoring, and release artifacts into local pre-live stages: `not_ready`, `insufficient_data`, `ready_for_broker_uat`, `ready_for_file_outbox_dry_run`, or `ready_for_manual_pilot_review`. These statuses are review milestones only and do not enable an external execution path.

`approval/` now supports `compliance_review`, `broker_uat_review`, `broker_connectivity_review`, and `go_live_review` batches. The batches can contain no orders and are used as local human-review evidence.

## 历史 PIT CSI300 与研究防火墙

`universe/historical.py` 是历史指数成分的统一证明层：显式隔离 canonical `index_code`，按自然月审计完整快照，以完整集合替换生成日频 membership/weight，并使用 usable period 全部历史成员的 union 作为股票轴。首个完整快照前、快照过期、缺月、成员数或权重和不合规时保持 unknown/blocked，禁止逐股票 ffill 或当前 300 股回填历史。

`research_firewall/` 是 generation、proxy、full eval、shortlist、matrix、scheduler 与 resume 共用的日期边界。`research_end_date` 必须在原始日期轴、feature 和 target 计算前生效；只有 eligible-date hash、label horizon、pre-compute truncation 和零越界 access audit 全部通过，才能声明防火墙启用。

正式 matrix/feature/validation 路径要求历史 universe proof、动态轴、交易与字段 validity masks、v3 feature validity、promotion allow/deny 和不可变 freeze 全部存在且 hash 一致。日频 OHLCV/daily basic 不跨日 ffill；invalid 可紧凑存储为 0，但 validity 必须为 false，Evaluator 与 StackVM 不得把该 0 当观测。

证据状态分为 engineering evidence、contaminated/sealed retrospective replay 与 future untouched holdout。旧候选的历史重放最多得到 `historical_replay_passed`，不能升级为 `clean_holdout_passed`；没有真实 fills/equity curve 时 cost、capacity、stress、drawdown 均为 unsupported/null，certification、portfolio、paper/live queue 必须为空。

## Dashboard

`dashboard/` is a Streamlit artifact viewer. It reads local data, sync plans, request audit, dataset statistics, snapshot summaries, data-source smoke reports, provider probes, field coverage, audit summaries, incremental recovery reports, baseline summaries, backfill plans/runs/coverage, running backfill observer reports, backfill ETA/repair/postprocess plans, raw landing QA, raw freeze-readiness decisions, raw data index manifests/dataset indexes/partitions/validation reports, semantic data-quality scorecards/issues/repair suggestions/freeze gates, research data readiness reports, feature readiness catalogs, post-download plans/run reports, data lake version/freeze/lineage reports, matrix cache metadata, matrix validation reports, benchmark reports, feature factory manifests/coverage/value summaries, Alpha Factory campaign manifests/reports/candidates/static checks/proxy/full eval/shortlists/diversity reports, compute resource snapshots, compute jobs/runs/events/leases, experiment plans/graphs/shards/merge reports, data-source comparison reports, factor store, factor reports, batch reports, search reports, neural search reports, neural training history, checkpoint lists, suite reports, artifact catalog, promotion decisions, model registry reports, model deployments, lifecycle events, factor lifecycle reports, health checks, review packages, lineage graphs, risk reports, risk model reports, risk exposures, risk decomposition, return attribution, capacity reports, execution plans, parent orders, child orders, child fills, execution quality, broker reports, broker order states, broker events, broker fills, broker reconciliation reports, broker-file gateway reports/manifests/roundtrip checks, mapping certification decisions, operator handoff reports, file outbox manifests, broker statement imports, external account mirrors, EOD reconciliation breaks, adjustment proposals/applications, settlement reports, cash buckets, position lots, position availability, realized PnL, account NAV, fee/tax reports, optimization results, backtest outputs, target positions, orders, paper fills, production runs, production orchestrator reports/plans/gates/phases, production replay reports/days/events, shadow trading reports/orders/fills/drift, shadow lab reports/drift/suggestions, live readiness scorecards/decisions, program-trading compliance packs, BrokerAdapter UAT reports, Go/No-Go scorecards, incident reports/records/runbooks, approvals, paper account state, account ledgers, monitoring reports, artifact schema validation reports, release gate reports, release manifests, dependency/module/CLI inventories, local CI reports, and alerts. Missing artifacts produce empty states instead of errors.

## Research Suite Outputs

`research_suite.run_suite` writes:

- `suite_result.json`
- `suite_report.md`
- `walk_forward_result.json`
- `promotion_decision.json`
- `artifact_catalog.json`
- `artifact_catalog.md`

The artifact catalog indexes data manifest, quality report, pipeline state, universe summary, optional matrix metadata, optional benchmark reports, search reports, factor store files, selected factor values, backtest outputs, risk reports, optimization results, order outputs, suite report, and promotion decision.
When lifecycle governance is enabled, it also indexes model registry files, lifecycle reports, model review packages, approval ids, and lineage graph artifacts.

## Engineering Robustness Validation

Formal real-campaign validation resolves freeze, matrix cache, feature tensor/manifest, promotion policy, factor store, and candidate pool from the source campaign manifest. Metadata-only factors are materialized once into compact NPY values and validity masks; JSONL factor values remain read-compatible only. Validation artifacts include per-factor lineage and hashes, long-history rolling windows, explicit policy thresholds, multiple-testing/PBO summaries, GPU shard resource reports, an engineering-only report, and a clean-holdout campaign plan. Retrospective selection reuse and fixed-as-of constituent evidence block certification and portfolio queues.

## Development Notes

The platform is local-first and deterministic by default. Production sync now has a local plan/cache/audit/resume/snapshot/statistics skeleton, governed backfill jobs, read-only running-backfill observation, explicit repair batches, raw landing QA, raw sidecar indexes, semantic data-quality gates, freeze-readiness gates, research-readiness gates, post-download state-machine orchestration, freeze candidate packages, dataset versioning, research freezes, real-data readiness/runbook/SLA/size reports, and a data-source smoke validator for offline fake Tushare scenarios and gated real-token diagnostics. Artifact schema versioning, release gate reports, local CI, and package build artifacts are available. Matrix cache, raw index-aware incremental matrix refresh, local benchmark, data-source comparison skeletons, CPU/GPU resource probing, local GPU leases, compute job scheduling, experiment sharding, and shard merge reports are available. Feature Factory v2/v3, v3 PIT-alignment reporting, feature-readiness cataloging, feature promotion policy/evidence gates, Alpha Factory campaign funnels, formula corpus construction, matrix-aware batch formula evaluation, and offline AlphaGPT pretraining are available. Barra-like risk model v1 and benchmark-aware portfolio optimization now have a local implementation. Capacity-aware execution planning, broker adapter state, safe read-only broker UAT connectivity, read-only account mirroring, dry-run broker-file gateway, mapping certification, operator handoff packages, program-trading compliance evidence packs, BrokerAdapter UAT, Go/No-Go review gates, file instruction export, paper child-order simulation, settlement-aware paper accounting, lot-cost PnL, NAV reconciliation, broker statement import, external account mirroring, and EOD break management are available. Neural-guided formula search now has a local AlphaGPT policy-search implementation. Daily production now has local approvals, model registry activation gates, paper account ledger, settlement reports, broker reconciliation, EOD reconciliation, production-day orchestration, multi-day replay, shadow lab aggregation, live readiness gates, shadow-only simulation, incident response, and monitoring reports. Real order submission/cancel/replace, full-market token/quota operation, full-market 4-GPU stress runs, finer raw-index random access, richer semantic QA calibration on frozen full-market data, richer DDP training, richer provider comparisons, production Barra definitions, robust full-market covariance calibration, a professional optimizer, stronger reinforcement learning, larger offline corpora, richer lifecycle policies, external review workflow integrations, broader neural training stability validation, Alpha Factory proxy-score calibration on long histories, richer v3 feature calibration and promotion policy calibration on governed freezes, finer matching realism, minute-level volume modeling, verified broker statement mappings, legal/regulatory workflow integrations, and production broker connectivity certification are future work.

## Task 053-A：保守停牌工程重放

正式日频政策为 `conservative_event_day_open_exclusion_v1`：已覆盖且无事件表示事件已知不存在；任意 S/R 事件日均禁止作为 next-open 入口或出口，但原始空 `suspend_timing` 保持空值，绝不冒充“已证明全日停牌”。验证状态严格区分受污染历史工程重放与未来 untouched holdout，后者及 certification/portfolio/paper/live 本轮始终为 false。

唯一编排入口按 governed source → immutable freeze → lagged historical universe → strict matrix → v3 values/validity → Research Firewall → conditional four-GPU replay 的顺序验证不可变证据。工程完成状态只能命名为 `engineering_replay_completed_certification_blocked`，存在真实工程 blocker 时为 `engineering_chain_built_replay_blocked`。

## Task 054 evidence boundary

The Task 054 baseline is contaminated retrospective engineering evidence. The production verifier binds candidate identity, research eligibility, matrix/tensor/policy/code lineage, shard completeness, CUDA evidence, and replay hashes. `certification_ready`, `portfolio_ready`, `paper_ready`, and `live_ready` remain false even when an engineering replay completes.

## Task 054-B：95 特征语义、selection forensic 与生产门控

- `feature_factory.semantics` 为 `ashare_features_v3` 全部 95 个特征提供唯一 canonical 递归语义：原始依赖、内部运算、外层变换、价格口径、PIT 可得性、有效性/min-period、实现源码哈希、最长依赖路径、`max_raw_lag` 与 `required_observations=max_raw_lag+1` 均可机器校验；公式窗口继续递归叠加算子 lookback，不接受旧存量 lookback 作为真值。
- `task_054_b.forensics` 对冻结 campaign 的完整候选池做 selection-impact 取证，而不是只检查历史 shortlist。流程按 formula hash 去重并核对预期唯一候选数，逐候选验证 tokens/names/formula hash/factor id，重算 canonical lookback、静态资格、lookback penalty、分数、排名及 shortlist membership，并以内容寻址 normalized overlay 保存修正后的工程元数据，不改写历史记录。取证仅使用冻结 selection artifacts，明确 `target_or_outcome_read=false`。
- `real_production` sentinel 固定执行 3 种 mutation × 4 条生产路径的 12 次运行：`baseline`、`post_cutoff`、`inside_cutoff` × `raw_local`、`raw_scheduler`、`matrix_local`、`matrix_scheduler`。它必须核验 audited-read ledger、公共生产入口 receipt、source generation、scheduler job/run/heartbeat/device state、cutoff 外研究输出不变、cutoff 内 cache miss 且研究输出变化，以及 raw/matrix、local/scheduler 结果一致。
- Task 054-B DAG 使用分阶段 validator，顺序固定为 governed source → strict matrix → v3 tensor → production firewall sentinel → identity forensic → four-GPU replay。每一阶段都必须通过状态、schema、文件 SHA256、content hash 和上游 lineage 校验；四卡只能在前五阶段 gate 全部通过后执行，并继续要求精确 20 候选、四份 scheduler state、真实 CUDA replay evidence、uncached sibling 一致和 immutable resume `4/4`。
- 即使工程链全部验证，终态仍为 `task054b_engineering_baseline_completed_historical_selection_contaminated_certification_blocked`；certification/portfolio/paper/live readiness 始终为 false，queue 始终为 0，不得宣称 clean OOS。
- Git 仅允许提交 scrubbed 相对身份、哈希、计数、状态与验证摘要；不得提交真实服务器路径、NPY 或原始物理 GPU UUID。

`task_054_c/` 是 Task 054-C 的唯一生产合同：原生 matrix/tensor/overlay validator、exact-20 normalized factor store、content-addressed canonical engineering bundle、受治理 mutation generation、物理 research/diagnostic projection、连续 receipt/read-ledger、固定 12 路 runner、pre-GPU seal 和独立 final verifier。研究进程只能映射 cutoff 边界内的 projection；证据威胁模型明确为 supervisor-attested/tamper-evident，历史选择污染和全部认证/部署 blocker 继续保留。

`task_055_a/` 建立 prospective holdout 封存和真实事件账本组合模拟基线：先以 append-only observation-boundary seal 固定项目已观察范围，再从只读 Task 054-C canonical bundle 发布严格 Simulation Bundle。正式模拟状态由订单、成交、现金桶、整数 lot、T+1 结算、公司行动和 open-to-open NAV 驱动；容量与冲击只能标记为日线代理模型。Validation Lab 的旧 synthetic stress 正式路径已 fail closed，只有真实 simulator 独立重跑才能产生 stress 指标。该任务不构造新组合、不挑选因子，certification、portfolio、paper、live 始终阻断且 queue 必须通过物理扫描为空。

## Task 055-A：前瞻封存与事件账本模拟

`task_055_a` 先封存项目级 observation boundary，再从 Task 054-C 只读证据发布物理隔离的 Simulation Bundle。正式模拟以订单、成交、现金桶、整数 lot、T+1、结算、公司行动和 open-to-open NAV 为真值；成交 open 与停牌估值 carry 分离。真实重放若遇到 active/member 持仓的 unexplained bar gap，会为每个因子/场景发布可验证的 `data_blocked` artifact，绝不以前值、复权价或零收益补齐。

## Task 055-B：全量 Security-Date 证据与估值闭环

`task_055_b/` 对 Task 055-A 的 first-blocker 删失样本做全量扩展：从只读 seal、freeze、strict matrix、Simulation Bundle 和 blocked run 重建 security-date inventory，按交易日轴合并 episode，并在任何请求前发布内容寻址的 exact-date × security-window 双几何计划。行情 null/零价不再 silent-zero；停牌事件本身不再授权 carry；membership 只限制选股/买入，不限制调出后持仓估值与合法卖出。

估值使用独立 evidence overlay，状态严格区分官方成交、受治理无交易、来源冲突、生命周期终止和数据缺口。Verifier 从 quote、状态证据、公司行动和 immutable fee schedule 重建 mark、费用与账本，禁止回退 raw open/close。只有 valuation closure unresolved/conflict 为 0 才允许新 Simulation Bundle 和 exact-20×5 重放；否则终态为 `task055b_security_date_evidence_remediation_blocked`，所有认证与部署 readiness/queue 继续为 false/0。

Task 055-C adds a fail-closed security-date evidence and valuation closure path. It distinguishes transport cache identity from episode-specific evidence use, scans all cache entries at zero request budget, reconstructs raw suspension semantics, limits targeted historical repair, refreshes valuation marks on the complete trading axis, and requires physical exact-20 × five-scenario simulator artifacts before reporting replay completion.

Task 055-D 将 Tushare 正式网络固定为经 TLS/hostname 验证的 `https://api.tushare.pro`，禁止明文降级、跨主机跳转和凭据派生指纹落盘。L0 必须全扫 v2/v3 cache，L1 计划封存后才可凭 `--allow-network` 与 plan hash 执行；无凭据时明确以 `credential_unavailable` 阻断。全轴 valuation v2 和 Fee Schedule v2 均由独立 verifier 从源 bytes、partition、axis、规则区间重新核验，未闭合时绝不创建 simulator replay。

Task 055-E 的 Offline Source Salvage 不读取凭据、不联网。它从 governed root 自动重验 freeze/raw-index/matrix/Task052+ envelope/cache/normalized rows，发布逐行 byte-offset provenance、六类离线 reconciliation、严格 raw-repair delta、直接向过去查找合法 close 的 anchor 重投影，以及 full-history/static-axis/实际持仓因果域三层 readiness。只有实际 exact-20×五场景持仓前缀中的 mark 缺口阻断 Simulator；轴外缺口只阻断 future research。该阶段绝不生成伪 Simulator 成功证据。

`task_055_f/` 将 Task 055-E 的 provisional frontier 收敛为独立 `truth_v2`、实际读取 ledger、Fee Schedule v2、紧凑估值投影和可恢复网络状态机。正式语义严格区分 S/R、同日冲突与 timing，truth 本身永不授权 stale price；只有真实 held-position observer 使用且存在合法历史 close 的 modeled mark 才计数。L1 为 exact security-date，canary 单请求后强制退出，L2 只能在 L1 应用和全量重投影后动态生成。工程 gate 全绿时原生执行 exact-20×5 primary/sibling/resume；历史选择污染、modeled execution 与全部 certification/deployment blocker 始终保留。

真实离线 hardening 对 35,844 个 security-date 完成守恒重建，并由独立 verifier 从源 bytes 和矩阵分区复核；旧 16-key 仅保留为 Task 055-E lineage，不再被描述为总缺口。正式 frontier 必须在完整官方 Fee Schedule v2 下重新产生。

`task_055_g/` 收敛 Task 055-F 的验收边界：任何 governed payload 在打开前必须进入 immutable Access Plan，实际读取由 attempted-access ledger 记录；独立 verifier 使用自己的计划与 ledger，从父 manifests、矩阵、原始 envelope、Fee 和持仓路径重建语义，不能复用 producer 自报结果。Fee Schedule v2 使用官方文档 acquisition→verification→extraction→publish→independent-verify 原生链，法定费用与未校准 modeled 成本分层。Authoritative operational seal 扫描真实 writer roots 和 legacy roots，拒绝影子空目录、自报 record count、未知 schema 与 symlink escape。

Task 055-G 本轮只允许封存 fee-aware exact-date frontier，动态 L1/L2 状态机保持离线且不读取凭据、不发送 Tushare 请求。顶层只能是等待网络授权或离线阻断；两者均属于 retrospective engineering evidence，不是 clean OOS、认证、组合批准或实盘许可，certification/portfolio/paper/live readiness 始终为 false。

真实 v3 sibling 运行完成 35,844 个 security-date 的独立守恒复核、官方 Fee Schedule v2 的 7 份文档/40 条规则验证，以及 exact-20×五场景的 producer/independent 100 路因果重放。Fee-aware round-1 frontier 为 17 个 exact daily key；本轮 Tushare physical attempts 为 0，prospective holdout 未触碰，七类 operational queue/registry 的物理记录数均为 0。

`task_055_h/` 是 Task 055-G 之后的纯离线 canary authorization plane。它重新验证父报告和 verifier，封存有序的 17 个 exact security-date key、首个 canary、预算和独立 resume gate；本阶段 `credential_read_count`、Tushare/其他网络请求及 prospective holdout access 必须全部为 0。Fee attestation 将 28 条官方费率或法定区间证据与 12 条未校准 modeled 规则分层展示，不把 modeled commission/slippage/impact 描述成官方费用或认证证据。

Task 055-H 的 authoritative operational seal 同时扫描实际 runtime roots 与 governed historical roots。`operational_state_unproven` 表示根身份、schema、内容或物理空状态尚未得到独立证明，而不是“已知为空”；该状态必须阻断 canary authorization。Task 055-H 顶层只允许 offline ready-no-network 或 offline blocked-no-network，certification/portfolio/paper/live readiness 始终为 false。

Git 仅保存 `evidence/task_055_h/scrubbed_authorization_evidence.json` 脱敏副本；它保留完整 17-key 顺序、plan/frontier/root/artifact hashes，不包含价格、凭据、绝对服务器路径或原始数据。standalone verifier 只能证明包内 hash/lineage 自洽，完整验证仍需服务器原生 artifacts。

`task_055_i/` 将 Task 055-H 的 ready 状态降级为父证据，并提供唯一未来单笔 canary 入口。生产 CLI 只接受 sealed runtime authority、完整 reviewed hash、owner-only 绝对 credential file 和显式 `--allow-network`，不提供 resume/batch，也不允许 executor/client/credential-loader 注入。

全局 Network Authority 位于 governed root，固定 17-key 顺序、首个 `daily / 000413.SZ / 20160726`、canonical roots、append-only network/spend 双账本、single-flight lock 与跨 output 不可重置的 `64/128/160` 预算。Task055-F/G 的旧 canary/resume 路径统一 fail closed 为 `superseded_by_task055i`。

本轮仍完全离线。隔离 rehearsal 仅替换最底层 synthetic HTTP response，其余实际调用 raw repair、immutable freeze、strict matrix、v3 tensor、StackVM/FactorMaterializer、Research Firewall sentinel 和 EventLedgerSimulator 20×5。Synthetic evidence 永不进入 production seal。由于所有历史 writer CLI 尚未完全绑定唯一 operational root，`operational_state_unproven` 继续保留，certification/portfolio/paper/live readiness 始终为 false。

Git-safe 证据位于 `evidence/task_055_i/task055i_scrubbed_evidence.json`；可用 `python task_055_i/verifier.py evidence/task_055_i/task055i_scrubbed_evidence.json` 做标准库离线自洽校验，但不能替代服务器原生 artifact 重验。

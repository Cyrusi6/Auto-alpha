# Framework Update

This file records only the current A-share architecture and recent governed milestones. Detailed historical implementation is available through Git history rather than duplicated task-by-task prose.

## 2026-08-11 — Deletion-first architecture closure

### Outcome

- One public package: `auto_alpha`.
- Six domains: `data`, `research`, `validation`, `portfolio`, `execution`, `platform`.
- 25 visible subsystems.
- 56 Python package directories, below the hard budget of 65.
- 664 Python source files, below the hard budget of 665.
- Four committed evidence files, at the hard evidence budget.
- Tests mirror the same six domains.

### Research consolidation

- Deleted `BatchFactorResearchRunner`, `BatchResearchConfig`, the `research batch` CLI, and all `studies_*` runner/report/store modules.
- `FormulaBatchEvaluator` is the sole formula batch evaluation implementation.
- `FormulaEvalRequest` is the canonical candidate request model for defaults, imported candidates, random search, and neural search.
- Formula search and neural search call the same evaluator instead of selecting between legacy and batch paths.
- Composite construction moved to `research/factors/composite.py`; composite factors remain unvalidated until independent OOS evidence exists.
- Split, metric, bounded multi-objective scoring, and factor report logic were merged into `research/discovery/evaluation.py`.
- Removed unused formula/runtime facades and the duplicate research CLI.

### Data truth and artifact storage

- `data/pit/truth.py` is the sole security-date truth builder, publisher, successor builder, and validator.
- The source reconstruction stage now consumes `AccessBroker`; the old `AuditedReader` execution path is not a formal truth implementation.
- Deleted the separate `truth_builder.py`, historical audit runner, unused availability module, and governed replay CLI.
- Generic content-addressed storage moved from network authority to `platform/artifacts/storage.py`.
- Network authority imports the shared artifact primitive; standalone release verifiers retain their intentionally independent standard-library hashing where required by their threat model.

### Fee and simulation contracts

- `portfolio/simulation/fees.py` is the sole Fee Schedule v2 workflow, calculator, and independent verifier.
- Deleted `fee_evidence.py` and the unused `ledger_fees.py` producer/calculator.
- A legacy schedule can only be inspected through explicit read-only validation; production simulation cannot execute it.
- Simulation artifact publication now requires explicit valuation evidence and a validated Fee Schedule v2 path.
- Removed implicit raw-quote valuation construction and arbitrary fee-mapping fallback.

### Validation consolidation

- Deleted Task054-A truth-evidence DAG, scrubbed-evidence producer, synthetic sentinel fixture, CLI, and tests.
- Removed the obsolete subprocess sentinel from `core_sentinel.py`; that module now owns only the deterministic in-process Research Firewall sentinel.
- Kept `production_sentinel_sentinel.py` as the production 12-path sentinel.
- Deleted Task054-B forensic/evidence/DAG facades that were no longer production dependencies.
- Deleted Task054-C runner, worker, receipt recorder, mutation producer, lookback audit, and final report producer.
- Task054-C bundle, normalized store, research projection, matrix/tensor, sentinel seal, and pre-GPU seal remain only where current code must validate historical immutable artifacts.
- Leakage audits were merged from five micro-modules into `validation/firewall/leakage.py`.

### Network and evidence boundary

- `platform/network_authority` remains the only network-authority implementation.
- Parent rehearsal read-only validation was merged into `rehearsal.py`.
- Historical Task055 H/I/J/K/KR Git evidence was removed from the working tree; Git history remains the archive.
- Current committed network evidence is limited to the KR2 candidate anchor, candidate evidence, and supersession marker.
- `evidence/research_current_baseline.json` is the sole committed research readiness summary.
- Runtime source changes invalidate prior execution authorization. No network authorization, credential read, canary, or market-data request is claimed by this cleanup.

### Safety state

- `alpha_search_authorized=false` for the current governed freeze baseline.
- `certification_ready=false`.
- `portfolio_ready=false`.
- `paper_ready=false`.
- `live_ready=false`.
- Historical selection contamination and the absence of a future untouched holdout remain blockers.

## 2026-07 — Task 056 research correctness baseline

### 056-A: research and backtest correctness

- Next-open execution uses the actual execution open and open-limit masks rather than close-limit aliases.
- Holdings are ledger state; weights drift with price and turnover can be independently recomputed.
- Target tail endpoints remain unavailable rather than becoming zero returns.
- Production research requires strict target validity, PIT signal eligibility, canonical tensor validity, a strict device, and an OOS gate.
- Factor lifecycle distinguishes generated, evaluated, validation candidate, certified, and downstream states.
- Neural reward does not add a fixed bonus for loosely approved records.
- Composite factors do not become approved automatically.
- Embargo includes formula lookback and label horizon.

### 056-B: four-GPU workflow

- The governed workflow uses independent deterministic shards rather than DDP.
- GPU leases, strict device enforcement, no CPU fallback, immutable manifests, resume fingerprints, and resource telemetry are part of the workflow contract.
- Campaign size progresses through correctness, throughput, and governed research pilots; no million-formula default is implied.

### 056-C: canonical freeze

- A content-addressed canonical freeze was built from governed artifacts without committing market data.
- The freeze retains historical securities and CSI300 snapshot proof but remains research-gate blocked.
- Missing strict matrix/tensor validity lineage, incomplete historical ST/suspension/name-change proof, industry transition proof, and corporate-action availability remain explicit blockers.
- The historically observed 2025-01-01 through 2026-06-30 interval is not an untouched holdout.

### 056-D: two-stage Alpha Factory

- Cheap proxy evaluation precedes full purged rolling evaluation.
- Ranking uses bounded or cohort-normalized objectives rather than adding raw ICIR, spread, monotonicity, and turnover in incompatible units.
- Trial lineage and multiple-testing evidence are preserved; search does not imply certification.

### 056-E: sealed holdout

- Candidate identity, formula/value hashes, research metrics, selection order, trial count, and policy hash must freeze before a holdout capability can be issued.
- The currently available period was already observed, so holdout execution remains blocked and no clean-OOS claim exists.

### 056-F: factor-certified portfolio research

- Only `factor_certified` records may enter portfolio research.
- Portfolio research remains blocked because the certified factor pool is empty.
- No factor combination, paper campaign, or live deployment was started.

## 2026-08 — First six-domain cleanup

- Migrated all production code into `src/auto_alpha` and mirrored tests by domain.
- Removed legacy peer packages and task-number package trees.
- Deleted broker connectivity/mirror/UAT, go-live, incident, production replay, and shadow-lab history.
- Deleted redundant research suite, benchmark, orchestration, and factor-lifecycle packages.
- Introduced the unified `auto-alpha <domain> <command>` surface and repository layout audit.
- Reduced package directories from 118 to 57 before this second deletion pass.

## Architecture rules

1. Delete obsolete behavior instead of hiding it behind compatibility adapters.
2. Each capability has one production owner.
3. Infrastructure cannot redefine data, factor, validation, or portfolio truth.
4. Missing source, validity, target, PIT, fee, valuation, or lineage evidence fails closed.
5. Retrospective evidence never becomes untouched holdout evidence through metadata.
6. Real data, NPY tensors, caches, checkpoints, credentials, machine paths, and raw GPU UUIDs never enter Git.
7. New task numbers, dates, campaigns, runners, stores, and reports do not create packages.
8. Every meaningful architecture change updates this file and passes `dev_tools.repository_layout`.

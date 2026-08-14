# Framework Update

This file records only the current A-share architecture and recent governed milestones. Detailed historical implementation is available through Git history rather than duplicated task-by-task prose.

## 2026-08-14 — Profile-scoped governed data admission contract

### Outcome

- Replaced the ambiguous global `41 required / 15 hard-gated` governance model with immutable, content-addressed Data Admission Profiles referenced by Research Contracts.
- Classified the first profile into 11 base-required, 23 feature-family-conditional, and 7 inactive dataset contracts; inactive or unactivated data cannot influence the admitted scope or trigger its campaign.
- Distinguished immutable physical Source Freeze Generations from Canonical Data Freezes. Only a profile/view/span scope with an independent admitted Data Admission Verdict is canonical research data; blocked generations remain audit evidence without authorization.
- Required provider-neutral Coverage Plans, atomic Coverage Obligations, immutable per-attempt Coverage Receipts, strict empty/cap/retry semantics, and verifier-recomputed Coverage Roots instead of producer-supplied `complete` or `coverage_root` declarations.
- Locked full A-share lifecycle coverage, including delisted securities, while limiting obligations to each security's `[list_date, delist_date)` interval.
- Locked PIT semantics for authoritative daily ST state, conservative suspension/resumption timing, immutable name-change revisions, multi-stage corporate actions, publication-proven index membership, and event-reconciled adjustment factors.
- Made universe, lifecycle, ST, suspension, price-limit, benchmark, corporate-action, and raw adjustment-factor fields control-only; they cannot be exploited as formula inputs or missingness signals.
- Required zero unexplained structural, PIT, coverage, lineage, parsing, duplicate, and conflict errors. Ordinary Alpha-field gaps remain explicit validity rather than semantic zero and are governed by preregistered per-date breadth thresholds.
- Bound each Data Scope Root to its active source-to-derived-to-consumer closure, axes, feature values/validity, frozen target values/availability/formula, code and toolchain; identical replay must be byte-deterministic.
- Allowed incremental reuse only by exact content identity while requiring full exact-cover and root verification for every new verdict. Permanent source evidence includes plans, all terminal receipts, journals, successful raw envelopes, admitted partitions, manifests, roots, and replay verdicts.
- Kept profile activation and threshold changes behind human approval. The autonomous loop may update and verify data under an activated profile but cannot relax its own evidence requirements.
- Confirmed that the current real lake remains blocked: ST and governed suspension evidence are absent, corporate-action and adjustment-factor causality is unresolved, CSI300 membership lacks complete publication-proven history, and no unified strict target/validity lineage bundle exists.
- Added the detailed contract at `docs/DATA_ADMISSION_CONTRACT.md` and recorded the architectural choice in ADR 0001. No production authorization or market-data acquisition was performed.

## 2026-08-13 — Autonomous research loop vocabulary

### Outcome

- Added the root domain glossary and defined the Autonomous Research Loop as a resumable, fail-closed A-share research cycle whose atomic and composite outputs stop at `Validation Candidate`.
- Defined `Validation Candidate` as a research promotion state governed by locked rolling-OOS, cost, capacity, stability, and redundancy gates; it is not factor certification.
- Kept sealed-holdout access, shadow or paper operation, and live trading behind an explicit Human Authorization Boundary.
- Required all research trials and evidence, including rejected and evidence-blocked outcomes, to remain in the Research Evidence Archive.
- Defined each Research Campaign as one budgeted execution of an immutable Research Contract against one Canonical Data Freeze; a valid zero-promotion result is a successful run.
- Set the initial operating boundary to a single controlled four-GPU host, local content-addressed storage, one campaign at a time, and small post-close campaigns triggered only by a newly admitted freeze.
- Prioritized existing local data; missing governed coverage must be reported before requesting access to Tushare Pro as the first provider behind provider-neutral contracts.
- Set both atomic and composite research outputs to stop at `Validation Candidate`; formal `Shadow Candidate` creation remains beyond this research-loop effort.
- Required template, random, mutation, crossover, and candidate-combination discovery in the first loop; neural guidance shares the same trial contract but is not an acceptance blocker.
- Made trial exposure cumulative across the Research Contract lineage and candidate promotion history immutable, with current `active`, `stale`, or `quarantined` eligibility derived from later evidence.
- Limited permanent trial storage to lightweight Evidence Envelopes and promoted value/validity materializations; replay-verified caches, proxy tensors, and expired checkpoints may be garbage-collected under locked retention rules.
- Required one active campaign per Research Contract, identity-preserving recovery, and auditable `superseded_without_run` outcomes when newer admissible freezes replace unstarted backlog entries.
- Published the planning route as the GitHub Wayfinder map `Map the autonomous A-share factor research loop`, with decision tickets and native blocking relationships; production implementation remains unchanged until that map is resolved.

## 2026-08-13 — Engineering skill repository configuration

### Outcome

- Selected GitHub Issues in `Cyrusi6/Auto-alpha` as the issue and specification tracker; pull requests remain outside the triage request surface.
- Mapped the five canonical triage roles directly to `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, and `wontfix`.
- Adopted the single-context domain documentation layout: root `CONTEXT.md` plus repository-wide ADRs under `docs/adr/`, both created lazily when domain modeling resolves terminology or decisions.
- Added concise discovery pointers to `AGENTS.md` and detailed consumer rules under `docs/agents/`; production runtime, factor research, validation, portfolio, execution, and GPU behavior are unchanged.

## 2026-08-12 — Repository-scoped engineering skills

### Outcome

- Installed the 25 stable engineering and productivity skills from `mattpocock/skills` at upstream commit `84fdeffd12f2ee307994d1eb6feb48173b6e0502`.
- Scoped the skills to this repository under `.agents/skills`; no Python, GPU, data, research, or production runtime dependency changed.
- Excluded upstream `in-progress` and optional `misc` skills from the governed project baseline.
- Recorded the upstream MIT license, immutable source revision, installed inventory, invocation examples, and update guidance beside the skills.
- Added a minimal `AGENTS.md` precedence and safety block. Discovery still comes from `.agents/skills`; the block keeps the repository's one-question clarification rule authoritative and requires confirmation before governance, external-write, branch, merge/rebase, browser, credential, or secret workflows.
- Left issue-tracker, triage-label, and domain-doc configuration unset. The optional `$setup-matt-pocock-skills` workflow may propose those settings later, but only after the user chooses and approves them.

## 2026-08-11 — Six-domain file-layout closure

### Outcome

- One public package: `auto_alpha`.
- Six domains: `data`, `research`, `validation`, `portfolio`, `execution`, `platform`.
- 23 visible subsystems.
- 54 Python package directories, below the hard budget of 55.
- 436 Python source files, below the hard budget of 450.
- Four committed evidence files, at the hard evidence budget.
- Tests mirror the same six domains.

### Research consolidation

- Deleted `BatchFactorResearchRunner`, `BatchResearchConfig`, the `research batch` CLI, and all `studies_*` runner/report/store modules.
- `FormulaBatchEvaluator` is the sole formula batch evaluation implementation.
- `FormulaEvalRequest` is the canonical candidate request model for defaults, imported candidates, random search, and neural search.
- Formula search and neural search call the same evaluator instead of selecting between legacy and batch paths.
- Composite construction moved to `research/factors/composite.py`; composite factors remain unvalidated until independent OOS evidence exists.
- `research/discovery` and the separate neural package were replaced by the single `research/search` subsystem.
- Formula batch, corpus, search, runtime semantics, factors, features, and experiment stores now use responsibility names instead of prefix chains.
- Removed unused formula/runtime facades and the duplicate research CLI.

### Data truth and artifact storage

- `data/pit/truth.py` is the sole security-date truth builder, publisher, successor builder, and validator.
- The source reconstruction stage now consumes `AccessBroker`; the old `AuditedReader` execution path is not a formal truth implementation.
- Deleted the separate `truth_builder.py`, historical audit runner, unused availability module, and governed replay CLI.
- Generic content-addressed storage moved from network authority to `platform/artifacts/storage.py`.
- Network authority imports the shared artifact primitive; standalone release verifiers retain their intentionally independent standard-library hashing where required by their threat model.

### Fee and simulation contracts

- `portfolio/simulator/fees.py` is the sole Fee Schedule v2 workflow, calculator, and independent verifier.
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

- `platform/governance/network` remains the only network-authority implementation.
- Parent rehearsal read-only validation was merged into `rehearsal.py`.
- Historical Task055 H/I/J/K/KR Git evidence was removed from the working tree; Git history remains the archive.
- Current committed network evidence is limited to the KR2 candidate anchor, candidate evidence, and supersession marker.
- `evidence/research_current_baseline.json` is the sole committed research readiness summary.
- Runtime source changes invalidate prior execution authorization. No network authorization, credential read, canary, or market-data request is claimed by this cleanup.

### Safety state

- `alpha_search_authorized=false` for the current governed Source Freeze Generation baseline.
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

### 056-C: source freeze generation

- A content-addressed Source Freeze Generation was built from governed artifacts without committing market data.
- The generation retains historical securities and CSI300 snapshot material but remains research-gate blocked; later adjudication determined that the snapshots do not prove PIT publication availability.
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

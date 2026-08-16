# Framework Update

This file records only the current A-share architecture and recent governed milestones. Detailed historical implementation is available through Git history rather than duplicated task-by-task prose.

## 2026-08-16 — Bounded free-provider capability probe

### Outcome

- Added a dedicated `data provider-probe` seam for Baostock, the CSI official announcement service, and CNINFO. It owns a finite credential-free request plan, host allowlist, time/response/page bounds, single-flight official HTTP access, a safer Baostock socket transport, and explicit positive/empty/error terminal semantics; it does not reuse the governed Tushare canary or the canonical-value provider interface.
- Persisted the exact contract and request plan before transport, then fsync'd attempt intents and every provider-observable response. Published generations bind raw bytes, response and file hashes, request semantics, parser checks, endpoint dispositions, implementation identity, and the append-only journal; validation independently recomputes expectation results and journal-to-plan/raw bindings.
- Added identity-preserving recovery for interrupted runs, including Baostock calendar/repeat state and CNINFO cross-page state. Verified succeeded generations are cacheable without transport, blocked generations remain retryable, corrupt current evidence and output symlinks fail before network, and archived replay cannot upgrade a handoff disposition.
- Locked the formal handoff vocabulary to `local_repair`, `bounded_backfill`, `permission_missing`, and `provider_cannot_prove`. Seven authorization flags are structurally fixed to `false`; a capability success cannot create an Admission Receipt, activate a Profile, run a bulk backfill, open a holdout, start Alpha search, or authorize paper/live trading.
- Executed the bounded plan against real public sources and retained the complete raw evidence outside Git. Final succeeded generations are Baostock `provider_probe_a42ce345190d186195e58901` (68 logical requests/72 wire exchanges), CNINFO `provider_probe_6e2996cbc39647342f586c99` (25 requests), and CSI `provider_probe_23792b43c34646306a4868d7` (18 requests), all bound to implementation root `edd8c99731f87bb792885e163757f642e5016e7254df40310462ef453a54b3e5`; offline validation and no-network cache replays passed. Baostock daily state/calendar and bounded CNINFO/CSI announcement-list paths are eligible for a separately specified `bounded_backfill`; Baostock historical CSI300/dividend/adjust-factor paths, CNINFO security-filtered suspension lookup, and CSI detail remain `provider_cannot_prove`. CSI details succeeded after cooldown, but the prior WAF HTTP 403 evidence and unfinished full event chain prevent an upgrade.
- Kept formal data admission blocked. The probe does not establish full-market exact cover, CSI300 publication/effective event closure, security-specific suspension coverage, corporate-action version causality, or adjustment-factor revision vintages; those require an approved Provider Acquisition Contract and independently verified coverage evidence.

## 2026-08-16 — Fixed-factor vertical development replay

### Outcome

- Added the validated `LocalDevelopmentBundleLoader` and completed the vertical chain from the immutable local bundle through one locked StackVM factor and the next-open `EventLedgerSimulator` into immutable replay evidence. The development matrix is not disguised as a Canonical Data Freeze or legacy strict matrix.
- Locked `volume_ratio_cs_rank_v1 = CS_RANK(volume_ratio)`, the membership-known PIT proxy cross-section, close-to-next-open timing, daily long-only Top-20 equal-weight refresh, T+1 shares, board lots, modeled costs, a zero-cost comparison scenario, 20-day ADV with an explicit one-observation minimum, and stable security-code tie breaking. The operator has no formula/search/generator CLI input.
- Materialized factor values and independent validity before any target read. The frozen target is used only for an in-sample development diagnostic; signal eligibility never reads target or target availability.
- Preserved complete development evidence: factor and eligibility arrays, observed execution proxies, input lineage, a cross-checked legacy unit-assumption receipt, full orders/fills/rejections/settlements/NAV/event ledgers for both scenarios, cost/capacity summaries, and peak/trough/recovery plus the full underwater series.
- Published with the prepared-directory immutable generation owner and a dedicated semantic validator. It locks exact factor/policy/governance contracts, validates read-only closure and governed 2012–2019 date bounds, recomputes summaries/drawdown, rejects self-consistently resealed blocker or policy forgeries, and can rematerialize the factor and both ledgers from a caller-supplied trusted bundle.
- Proved on controlled data that close-`t` decisions fill only at the next open, non-members cannot enter the rank cross-section, repeated runs and sibling output roots have one content/truth identity, cache resume is validated, output symlinks and artifact tampering fail closed, and CLI errors remain structured blockers.
- Published the real `469 × 1,945` replay as generation `fixed_factor_replay_75a0a210b1cda5bb92ad2994` (content `75a0a210b1cda5bb92ad29942e09110ba3ce639e39e044d32f438b4516052dcc`, artifact root `daf4ef4388dc8b48ed33b966c10fc4b16eb133a81ea072f557d9c193e04ba4e6`, simulation truth `c2e4bd79d5628bba20d3d304c328d4fbd56c697ef23b428c187c419d9969607a`). It retained 275,188 metric observations across 953 dates; rank IC mean was `-0.000505`. The modeled-cost scenario returned `-71.62%` with CNY 891,588 cumulative modeled costs and `74.42%` maximum drawdown, while the non-comparable zero-cost path returned `+33.70%`. This is a successful diagnostic with no promotion, not evidence of Alpha.
- Cross-checked 783,670 amount/price/volume observations (median normalized unit ratio `0.999904`), recorded 3,968 unresolved adjustment-factor transitions, preserved the exact input bundle manifest hash, and completed a trusted repeated build with `cache_hit=true`, the same content/truth identity, and only one 105 MB generation.
- Kept the result permanently non-admissible: `data_admission_eligible=false`, `alpha_search_authorized=false`, `validation_candidate_eligible=false`, `lifecycle_publication_allowed=false`, `holdout_accessed=false`, and `network_accessed=false`. No factor store, candidate pool, holdout, shadow, paper, or live state is touched.
- Retained the upstream blockers for provider receipts, PIT constituent publication, ST/suspension authority, adjustment revision history, and corporate-action lineage. Modeled costs, capacity, observed price bands, stale marks, and the unbenchmarked raw-price ledger are engineering proxies rather than formal Research Backtest evidence.

## 2026-08-16 — Offline local-data rehabilitation

### Outcome

- Added the provider-independent `Local Development Replay Bundle` for rehabilitating an immutable Source Freeze research view while Tushare access is unavailable. The command performs no network or token access and never writes into the source generation.
- Added explicit adapters for current Source Freeze Generations and historical `canonical_ashare_research_freeze_v1` evidence. The legacy adapter verifies the bound physical research view and partition identities but permanently preserves the `legacy_unproven` evidence grade.
- Materialized a self-contained `development_matrix/` with stock/date/feature axes, accepted retrospective CSI300 snapshots, 45-calendar-day membership expiry, membership/known/weight arrays, all raw values and validity, ten Alpha feature channels, observed open-to-open target values/availability, quality, reconciliation, and source lineage.
- Kept 2012–2015 membership unknown rather than projecting later constituents backward. Snapshot publication time, ST state, suspension state, adjustment revision history, corporate-action causal lineage, and provider request coverage remain explicit blockers.
- Made build identity bind the Source Freeze and search roots, locked scope, builder plus Source Freeze/storage source hashes, Python/NumPy/PyArrow runtime, artifact closure, and all evidence flags. One-worker and four-worker builds are byte-identical; compatible reruns replay trusted source semantics without republishing or creating a new matrix generation.
- Strengthened validation to reject mutable generations, any leaf or ancestor symlink, special or extra files, missing raw controls, wrong generation prefixes, negative index weights, duplicated calendars, source drift, incoherent price bands, and self-consistently re-signed feature or target arrays. Membership, feature values/validity, observed target, quality, reconciliation, and lineage are independently reconstructed from the frozen bundle evidence.
- Moved large prepared-directory publication behind the platform immutable-generation owner. Publication validates before and after atomic rename, uses per-generation locks, requires exact byte closure for same-identity reuse, and recovers safely when a process stops before the mutable current pointer advances.
- Registered the manifest schema and unified CLI command. Domain errors now return structured `blocked` JSON with exit code 2 instead of an internal traceback.
- Rebuilt the actual legacy lake offline into generation `local_development_bundle_d3012bcc31b7f37fab62106f` (content `d3012bcc31b7f37fab62106fa00a0342ebf39ede813b70e8ad0e8f8de3609dc1`, artifact root `e1c906408dbeec543a8b83b795d56068c12d98f29e2e164797f7bca9d820a53`): shape `469 × 1,945` over `20120104`–`20191231`, 48 retrospective snapshots, 10 features, 2,767,930 valid feature slots, and 275,471 observed targets. The source and research-view manifest hashes stayed unchanged, and a trusted repeat run was a validated cache hit.
- Replaced embedded raw Source Freeze manifests with a sanitized source-identity binding receipt; only the exact bootstrap/research view manifest is retained, and controlled-period locator strings are rejected. The source remains `legacy_unproven` and blocked for admission.
- Preserved the governance boundary: every local bundle has `mode=development_replay`, `data_admission_eligible=false`, `alpha_search_authorized=false`, and `lifecycle_publication_allowed=false`. No holdout, autonomous search, candidate promotion, shadow, paper, or live action was opened.

## 2026-08-14 — Data admission verifier foundation

### Outcome

- Implemented the first provider-neutral Data Admission Profile as a stable content identity with the locked 11 base-required, 23 feature-family-conditional, and 7 inactive classifications. Active contracts carry approved fields, consumer roles, evidence grade, and coverage geometry.
- Implemented deterministic Coverage Plan compilation over profile, access view, date span, As-of Market Date, full lifecycle population, locked SSE/SZSE and CSI300 subjects, and trading dates. The verifier reconstructs the plan identity and obligation set instead of trusting a caller-supplied plan; security obligations respect the half-open `[list_date, delist_date)` interval.
- Implemented independent verification of a durable attempt-start/post-transport journal over manifest and hash-chain integrity, profile-approved acquisition identities, raw response envelopes, observed-empty semantics, pagination caps, retry lineage, RSA capture signatures, and exact obligation cover. A started request without a terminal receipt remains ambiguous; producer `complete` and `coverage_root` claims are ignored.
- Locked every acquisition to the profile's exact approved fields, provider/adapter/endpoint/API/schema/permission contract, capture-public-key identity, read-only policy, retry ceiling, and deterministic split tree. A failed page has one causal retry chain; multiple root attempts, forks, backward time, unconsumed cursors, split drift, overlaps, and gaps block.
- Added authoritative population reconciliation: locked `L`/`D`/`P` securities-master partitions must reproduce the complete lifecycle population, while SSE/SZSE calendar spans must cover every calendar date through As-of and reproduce the plan's open trading dates. Omitting a listed or delisted security or an open day cannot shrink downstream obligations silently.
- Extended as-of coverage to the verdict watermark and required a pre-span suspension-state seed, so an apparently complete research span cannot hide stale base data or assume an unsuspended initial state.
- Made `not_applicable` a derived verdict rather than a producer assertion: each reason is profile-locked and must reference a positive, already satisfied lifecycle or tradability authority obligation with matching subject/date geometry. Suspension authority is reconstructed from valid `S`/`R` events and conservative timing; an arbitrary non-empty response, a same-day pre-open `R`, or a same-day after-close `S` cannot prove full-day suspension.
- Closed the first-profile source-field contracts to the approved price, volume, lifecycle, tradability, size, PIT CSI300, target, and strict-backtest inputs. Financial, industry, holder, event, and other conditional families remain inactive unless a future approved profile activates them.
- Implemented content-addressed Data Admission Verdict publication and validation. Verdict identity binds the profile, Source Freeze Generation, scope, active field/consumer closure, verifier-recomputed Coverage Root, Data Scope Root evidence, zero-tolerance metrics, deterministic replay evidence, and canonical blockers.
- Kept final Data Admission fail-closed at the human profile-approval, activated provider-acquisition, real Coverage evidence, and `canonical_bundle_contract_unresolved` boundaries. V1 can prove the controlled exact-cover path and compute a provisional active-scope root, but cannot issue an admitted Canonical Data Freeze until those authorities and the self-contained replayed matrix bundle exist.
- Registered the Data Admission Profile, Coverage Plan, Coverage Evidence, attempt journal events, and Data Admission Verdict artifacts with the platform schema registry.
- Removed producer authorization and canonical identity from the Source Freeze builder and physical research view. The capability module, CLI module, types, errors, generation schema, manifest, validation codes, and holdout lineage now consistently use Source Freeze language; no canonical-name compatibility facade remains. Their `alpha_search_authorized` value is always false, and historical canonical-labelled manifests remain read-only, legacy-unproven evidence.
- Bound `admission_evidence` and its root into the Source Freeze content identity, so lifecycle, coverage, scope, or replay references cannot be swapped while retaining a generation ID. Data Scope identity independently derives active per-dataset roots from validated Source Freeze partitions and excludes inactive roots, so invented lineage is blocked while inactive-family changes do not create false matching-scope drift.
- Bound each verdict to the actual admission, artifact-storage, and receipt-signing source hashes plus the Python toolchain identity; an implementation change can no longer reuse the same verifier identity merely because schema labels stayed constant.
- Kept profile activation fail-closed: a mutable `activation_status=active` declaration cannot forge human approval, and v1 has no trusted approval root, so every current profile remains blocked pending a later explicit authorization seam.
- Separated development replay from governed research validation. Governed Alpha Factory configuration and production formula loading require a Source Freeze plus an independent admitted verdict and cannot fall back to a legacy freeze; the loader remains blocked behind `admitted_bundle_resolver_required` until every target/value/validity/axis locator is resolved from the verdict-bound canonical bundle.
- Added `data freeze verify-admission`; it returns success only for an independently admitted verdict and returns the governed blocked exit code for missing evidence. Structural Source Freeze validation no longer doubles as research authorization.
- Preserved the current real-lake outcome as blocked. No Tushare call, market-data write, holdout opening, research campaign, candidate promotion, shadow, paper, or live action was performed.

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
- 437 Python source files, below the hard budget of 450.
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

## 2026-08 — Free domestic source feasibility

- Recorded the official-source review in `docs/research/FREE_DOMESTIC_ASHARE_DATA_SOURCES.md`.
- A bounded live probe confirmed that Baostock can return 2012 daily `isST`, `tradestatus`, and historical CSI300 snapshots without credentials; this is capability evidence only, not governed admission evidence.
- The preferred free acquisition topology is Baostock for daily control-state values, CSI official announcements for membership publication/effective events, and CNINFO announcements for ST, suspension, corporate-action, and correction versions. AkShare is an adapter and reconciliation aid, not an authority contract.
- No free source was found that exposes historical adjustment-factor revision vintages. The viable governed design is to derive versioned factors from PIT corporate-action events under a separately approved profile and use provider factors only for reconciliation.
- No bulk acquisition, profile activation, holdout access, autonomous search, paper trading, or live trading was authorized by this research.

## 2026-08 — Signed free-provider backfill and raw coverage replay

- Added a bounded, resumable physical-capture engine for Baostock, CNINFO and CSI official
  archives. Every attempt signs and fsyncs its start before transport, archives raw HTTP/socket
  bytes, then signs and fsyncs the terminal event.
- Added persistent owner-only RSA capture keys, immutable publication, crash/torn-tail recovery,
  strict request/page geometry, resource budgets and deterministic raw replay.
- Capture v2 signs the publication manifest itself. Historical v1 normalized files are explicitly
  untrusted; consumers replay the signed raw bytes under the current locked parser.
- Added provider×host circuit breakers. A 403, 429 or WAF response opens a signed governance
  breaker before stopping; CLI strings cannot authorize resume and changing output cannot bypass
  the breaker.
- Production capture CLIs load only the approved existing key and fixed staging namespaces.
  Capture and coverage writers reject lake data, canonical-freeze, local-bundle and lake-root
  targets. Official HTTP ignores environment proxies.
- Baostock v2 archives anonymous login and query protocol request/response bytes, socket peer and
  package RECORD identity. Validation recomputes exchange counts and binds actual code/date/fields/
  year tokens to the sealed request.
- Added raw-derived state coverage projection: one physical security-span response can prove many
  logical security-day obligations without weakening exact-day gap detection. Gap files are bound
  by hash, size, count and content root, and all research/trading safety flags are revalidated.
- Unified lifecycle semantics across Admission, PIT, universe and strict matrices: `delist_date` is
  the final valid listing day, not the first invalid day.
- Real full-market evidence remains fail closed: Baostock returned three missing security-days and
  one post-delist extra row after lifecycle correction; CNINFO closed 66,881 unique announcement
  identities, but document parsing, event-state reconstruction, factor vintages and historical
  CSI300 weights remain incomplete.
- No Data Admission Profile, alpha search, holdout, paper, shadow or live capability was activated.

## Architecture rules

1. Delete obsolete behavior instead of hiding it behind compatibility adapters.
2. Each capability has one production owner.
3. Infrastructure cannot redefine data, factor, validation, or portfolio truth.
4. Missing source, validity, target, PIT, fee, valuation, or lineage evidence fails closed.
5. Retrospective evidence never becomes untouched holdout evidence through metadata.
6. Real data, NPY tensors, caches, checkpoints, credentials, machine paths, and raw GPU UUIDs never enter Git.
7. New task numbers, dates, campaigns, runners, stores, and reports do not create packages.
8. Every meaningful architecture change updates this file and passes `dev_tools.repository_layout`.

# Auto-alpha Domain Language

Auto-alpha is a governed A-share quantitative research platform. Its language separates research evidence and candidate promotion from certification, shadow operation, paper trading, and live trading.

## Autonomous research

**Autonomous Research Loop**:
A repeatable, resumable cycle that updates governed A-share data, generates and combines factors, evaluates them out of sample under a locked policy, and archives the resulting candidates and evidence. It stops when required evidence is missing and does not cross a human authorization boundary.
_Avoid_: autonomous trading, unattended live trading

**Research Trial**:
One attempted factor or factor-combination evaluation together with its identity, lineage, policy, inputs, results, and terminal outcome. Rejected and evidence-blocked trials remain part of the research record.
_Avoid_: disposable run, successful factor

**Research Evidence Archive**:
The complete retained record of research trials and their evidence, including rejected and evidence-blocked outcomes. Admission to the archive does not imply candidate promotion.
_Avoid_: winner store, approved-factor registry

**Evidence Envelope**:
The lightweight, durable evidence needed to audit and deterministically replay a Research Trial: identities and hashes, formula, lineage, policy and budget, seed and environment, stage metrics, gate decisions, trial exposure, and value/validity hashes. Recomputable proxy tensors, caches, and expired checkpoints are not part of the permanent envelope.
_Avoid_: every intermediate byte, disposable log

**Trial Exposure Ledger**:
The append-only record of every generated hypothesis exposure within a Research Contract lineage. A repeated formula, new Canonical Data Freeze, restart, or policy execution does not erase an exposure, although duplicate evaluation may be skipped.
_Avoid_: per-day trial counter, resettable experiment count

**Locked Research Policy**:
The preregistered rules that determine evaluation windows, costs, capacity, stability, redundancy, and promotion without being changed in response to trial results.
_Avoid_: tunable acceptance criteria, post-result policy

**Research Contract**:
The immutable identity of one research scope, including its governed universe, holding period, rebalance frequency, target, and Locked Research Policy. Expanding the scope creates a new contract rather than silently changing an existing one.
_Avoid_: mutable campaign settings, universal strategy

**Research Campaign**:
One bounded execution of a Research Contract against one Canonical Data Freeze. It may create a limited number of new Research Trials and has a terminal outcome independent of whether any candidate is promoted.
_Avoid_: endless search loop, daily job

**Zero-Promotion Success**:
A successful Research Campaign that completes with valid evidence but promotes no Validation Candidate. It proves the loop operated correctly, not that market alpha exists.
_Avoid_: failed campaign, empty failure

**Locked Resource Budget**:
The preregistered limits for trials, accelerator time, storage, network access, retries, and related campaign resources. Exhaustion stops the Research Campaign and cannot be overridden by the loop itself.
_Avoid_: adaptive unlimited budget, best-effort cap

**Data Admission Profile**:
An immutable, content-addressed declaration that classifies each provider-neutral dataset contract as base-required, feature-family-conditional, or inactive and locks its field and evidence obligations. A Research Contract references one profile; changing the profile creates a new identity.
_Avoid_: global required-dataset list, per-campaign data switches

**Provider Acquisition Contract**:
A profile-activated, content-addressed evidence contract that locks one provider adapter's endpoint, API and schema versions, non-secret permission context, approved capture-key identity, exact canonical output fields, read-only rule, row cap, retryable failure kinds, retry ceiling, and deterministic split-tree limit. It governs how a provider-neutral dataset may be acquired without making vendor field names part of the canonical data contract.
_Avoid_: self-declared request metadata, mutable adapter defaults

**Data Admission Verdict**:
The deterministic admit-or-block result for one Source Freeze Generation scope under a Data Admission Profile, access view, date span, and As-of Market Date. An admitted verdict creates a Canonical Data Freeze; admission never grants access to another view or period.
_Avoid_: global readiness flag, reusable warning

**Coverage Plan**:
The immutable, provider-neutral set of Coverage Obligations generated for a Data Admission Profile, access view, date span, and point-in-time A-share lifecycle population. Provider request batching is an execution detail and cannot change the plan.
_Avoid_: API call list, aggregate coverage claim

**Coverage Obligation**:
One atomic expectation that a dataset-subject-date or dataset-subject-span fact is positively observed, validly absent, or not applicable; a subject may be a security, exchange, index, lifecycle version, or other provider-neutral partition key. Every active obligation needs one satisfying terminal disposition before its Data Admission Verdict can be admitted.
_Avoid_: inferred completeness, row-count target

**Coverage Receipt**:
The immutable signed evidence for one source-request attempt, binding its Provider Acquisition Contract, normalized request, response hash, row count, pagination state, attempt outcome, and journal lineage. A retry creates another receipt identity and never rewrites an earlier attempt.
_Avoid_: API log line, mutable request status

**Coverage Root**:
The verifier-recomputed SHA-256 content root over a Coverage Plan, its canonically ordered obligations, every attempt Coverage Receipt and journal link, and one terminal disposition per obligation. Completeness and the root are derived results, never producer attestations.
_Avoid_: caller-supplied root, aggregate completeness hash

**Observed Empty**:
A zero-row provider response whose Coverage Receipt proves successful exhaustive execution without permission, schema, pagination, truncation, or transport ambiguity. It is single-provider absence evidence, not a claim that the provider is infallible.
_Avoid_: any empty response, universal negative fact

**PIT Event Version**:
An immutable lifecycle or corporate-action event version with separate observation and economic-effect times. A correction appends a version instead of rewriting history or exposing final fields at an earlier announcement.
_Avoid_: mutable interval row, final-record backfill

**Control-Only Field**:
A point-in-time universe, lifecycle, ST, suspension, price-limit, benchmark, corporate-action, or raw adjustment state that may constrain eligibility, evaluation, target validity, or execution but cannot be used as a formula input. Listing age and causally validated adjusted prices are research features rather than Control-Only Fields.
_Avoid_: tradability alpha, missingness signal

**Legacy-Unproven Source Evidence**:
Retained source data or audit material whose file integrity is known but whose request, pagination, or valid-absence completeness cannot be replayed. It may support repair and reconciliation but cannot satisfy a Data Admission Verdict.
_Avoid_: grandfathered coverage, trusted old cache

**Data Scope Root**:
The content identity of the active source-to-derived-to-consumer closure admitted for one Data Admission Profile, access view, date span, and as-of market date. Changes outside that closure may change the lake generation but cannot trigger its Research Campaign.
_Avoid_: whole-lake change flag, file timestamp trigger

**As-of Market Date**:
The latest market date whose required observations, events, and coverage evidence are complete for a Data Admission Verdict. Carrying an older required value forward does not advance this date.
_Avoid_: newest file date, mixed dataset watermark

**Source Freeze Generation**:
An immutable, content-addressed physical snapshot of source and derived data plus its evidence, whether admitted or blocked. A blocked generation remains auditable but carries no research authorization.
_Avoid_: blocked Canonical Data Freeze, mutable lake snapshot

**Canonical Data Freeze**:
The admitted Data Scope Root within a Source Freeze Generation whose independent Data Admission Verdict passed for a specific profile, view, date span, and As-of Market Date. Only a new matching scope root, never an in-place mutation or unrelated lake change, can trigger a Research Campaign.
_Avoid_: latest data folder, mutable dataset

**Admission Verifier**:
The independent authority that reconstructs coverage, validity, lineage, and content identities from underlying evidence and issues a content-addressed Data Admission Verdict. Producer readiness flags and human-edited statuses cannot authorize research.
_Avoid_: producer self-attestation, manual ready flag

**Deterministic Freeze Replay**:
Rebuilding a data scope from the same immutable inputs and locked toolchain to obtain byte-identical artifacts, roots, and identities. A changed code or toolchain identity creates a new scope even when observed values appear equal.
_Avoid_: approximate rebuild, matching row count

## Candidate lifecycle

**Validation Candidate**:
A factor that passes the Locked Research Policy's rolling out-of-sample, cost, capacity, stability, and redundancy gates. This is a research promotion state, not factor certification or permission for shadow, paper, or live operation.
_Avoid_: good factor, approved factor, certified factor

**Composite Candidate**:
A factor constructed only from currently eligible Validation Candidates using training-period information, then evaluated as a new Research Trial under an independent rolling out-of-sample boundary. Passing promotes it to Validation Candidate, never directly to Shadow Candidate.
_Avoid_: automatically approved ensemble, certified portfolio

**Candidate Eligibility**:
The current derived qualification of an immutable Validation Candidate version: `active`, `stale`, or `quarantined`. New data may change eligibility but never rewrites the historical promotion event; Composite Candidates may consume only active versions.
_Avoid_: mutable promotion status, deleted candidate history

**Shadow Candidate**:
A factor combination that passes governed portfolio research and may be proposed for a separately authorized shadow stage. It carries no automatic permission for shadow, paper, or live operation.
_Avoid_: deployable strategy, production-ready portfolio

**Evidence-Blocked Trial**:
A Research Trial that cannot proceed because required data, lineage, point-in-time proof, validity, compute, or evaluation evidence is absent. It terminates fail-closed and may resume only when the missing evidence is supplied without changing its identity or policy.
_Avoid_: failed factor, rejected factor

**Superseded Freeze**:
An admissible Canonical Data Freeze that is deliberately not assigned a Research Campaign because a newer admissible freeze replaced it while the same Research Contract was still active. The `superseded_without_run` outcome is retained as evidence rather than silently dropped.
_Avoid_: missed day, deleted backlog item

## Authorization boundaries

**Human Authorization Boundary**:
An explicit approval required before opening a new sealed holdout, starting shadow or paper operation, or enabling live trading. The Autonomous Research Loop cannot grant this approval to itself.
_Avoid_: automatic promotion, implicit approval

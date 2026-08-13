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

**Canonical Data Freeze**:
An immutable, content-addressed, provider-neutral A-share research dataset whose point-in-time and validity evidence passed the data gate. A new freeze, not an in-place mutation, is the only data change that can trigger a new Research Campaign.
_Avoid_: latest data folder, mutable dataset

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

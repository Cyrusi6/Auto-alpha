# Historical A-share Source Freeze Generation

> This document records the existing Task 056-C implementation. The accepted [Governed A-share Data Admission Contract](DATA_ADMISSION_CONTRACT.md) now distinguishes a physical Source Freeze Generation from an admitted Canonical Data Freeze. The real generation described here is blocked and therefore is not canonical research data under the accepted domain language.

The `data lake` subsystem is the only publisher for the historical immutable A-share source generation. It reads one reviewed raw-index manifest, re-hashes every materialized source, writes compact Parquet envelopes, records field availability/effective-date contracts, computes quality evidence, and atomically publishes a content-addressed generation.

## Physical access classes

| View | Availability dates | Search access |
| --- | --- | --- |
| bootstrap | through 2011-12-31 | read-only lookback input |
| research | 2012-01-01 through 2019-12-31 | candidate search |
| validation | 2020-01-01 through 2022-12-31 | unavailable before candidate identity freeze |
| retrospective test | 2023-01-01 through 2024-12-31 | unavailable before candidate identity freeze |
| sealed holdout | 2025-01-01 through 2026-06-30 | unavailable to search |

The dates are versioned policy inputs, not a claim of untouched evidence. Existing project artifacts have already observed data through 2026-06-30, so the sealed period is `historically_observed=true` and `untouched=false`. It cannot support certification.

The physical research view contains only bootstrap/research Parquet partitions. It removes `raw_json`, exposes only PIT-observable fields, carries compact default-plus-override field availability/effective-date maps, and has no path or locator for controlled or sealed partitions. Governed Alpha Factory mode requires this validated view plus an independently admitted Data Admission Verdict and rejects legacy/manifest-only freezes or derived artifacts outside the verdict-bound scope.

## Historical fail-closed gates

The Task 056-C implementation declares 41 required datasets but promotes blockers from only 15 core datasets. This global/core split and its aggregate source-coverage proof are superseded as governance semantics by profile-scoped dataset roles, provider-neutral Coverage Obligations, leaf-recomputed Coverage Roots, and independent Data Admission Verdicts. The current real generation remains blocked because existing blockers happen to fire; the old gate does not satisfy the accepted fail-closed contract and cannot issue an admitted verdict.

The real preflight currently remains blocked. The server lake has no governed `st_status_daily`, contains the legacy 623-row suspension schema, has incomplete/post-cutoff-mixed name-change data, lacks historical industry transition proof, lacks full-market event coverage attestations, and has no admitted bounded strict matrix/tensor/target bundle. The publisher may preserve eligible sources in an immutable blocked generation, but it must not set `alpha_search_authorized=true`.

## Commands

```bash
auto-alpha data freeze preflight \
  --governed-root <validated-ashare-lake> \
  --output-root <new-sibling-output>

auto-alpha data freeze build \
  --governed-root <validated-ashare-lake> \
  --output-root <new-sibling-output> \
  --workers 4

auto-alpha data freeze validate \
  --manifest <generation>/source_freeze_manifest.json

auto-alpha data freeze validate-research-view \
  --manifest <generation>/search_view/research_view_manifest.json
```

No command downloads data, mutates the raw lake, or silently falls back to a mutable source directory.

## Real 2026-07-30 Source Freeze Generation

The real immutable generation is structurally valid but research-gate blocked. Its freeze content hash is `125cb21b...143c0a`, with 41 declared datasets, 37 materialized datasets, 105,208,161 in-scope rows, 1,176 Parquet partitions, and 134 post-cutoff rows represented only by exclusion hashes. Later adjudication found that CSI300 membership is absent for 2012-2015 and lacks real publication-time proof thereafter, so the historical snapshot count is not governed PIT evidence. The source also lacks governed ST/suspension coverage, corporate-action and adjustment-factor causal proof, and the strict values/validity/target lineage bundle. It remains a blocked Source Freeze Generation with `alpha_search_authorized=false`. The Git-safe summary is `evidence/task_056c/task056c_canonical_freeze_summary.json`.

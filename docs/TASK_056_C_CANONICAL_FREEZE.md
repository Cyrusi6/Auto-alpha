# Task 056-C Canonical A-share Research Freeze

The `data lake` subsystem is the only publisher for the canonical immutable A-share research source generation. It reads one reviewed raw-index manifest, re-hashes every materialized source, writes compact Parquet envelopes, records field availability/effective-date contracts, computes quality evidence, and atomically publishes a content-addressed generation.

## Physical access classes

| View | Availability dates | Search access |
| --- | --- | --- |
| bootstrap | through 2011-12-31 | read-only lookback input |
| research | 2012-01-01 through 2019-12-31 | candidate search |
| validation | 2020-01-01 through 2022-12-31 | unavailable before candidate identity freeze |
| retrospective test | 2023-01-01 through 2024-12-31 | unavailable before candidate identity freeze |
| sealed holdout | 2025-01-01 through 2026-06-30 | unavailable to search |

The dates are versioned policy inputs, not a claim of untouched evidence. Existing project artifacts have already observed data through 2026-06-30, so the sealed period is `historically_observed=true` and `untouched=false`. It cannot support certification.

The physical research view contains only bootstrap/research Parquet partitions. It removes `raw_json`, exposes only PIT-observable fields, carries compact default-plus-override field availability/effective-date maps, and has no path or locator for controlled or sealed partitions. Production Alpha Factory mode requires this validated view and rejects legacy/manifest-only freezes or derived artifacts outside the view.

## Fail-closed gates

The canonical research gate requires all governed source contracts, full-market coverage proofs for ST/name-change/suspension event absence, canonical CSI300 snapshots, immutable strict matrix/axes, float32 feature values, bool feature validity, bool target availability, and matching hashes. Missing, partial, legacy, post-cutoff-mixed, duplicate-key, unknown-availability, axis-drift, or lineage-drift evidence blocks search.

The real preflight currently remains blocked. The server lake has no governed `st_status_daily`, contains the legacy 623-row suspension schema, has incomplete/post-cutoff-mixed name-change data, lacks historical industry transition proof, lacks full-market event coverage attestations, and has no canonical bounded strict matrix/tensor/target bundle. The publisher may preserve eligible sources in an immutable blocked generation, but it must not set `alpha_search_authorized=true`.

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
  --manifest <generation>/canonical_freeze_manifest.json

auto-alpha data freeze validate-research-view \
  --manifest <generation>/search_view/research_view_manifest.json \
  --require-research-ready
```

No command downloads data, mutates the raw lake, or silently falls back to a mutable source directory.

## Real 2026-07-30 generation

The real immutable generation is structurally valid but research-gate blocked. Its freeze content hash is `125cb21b...143c0a`, with 41 required datasets, 37 materialized datasets, 105,208,161 in-scope rows, 1,176 Parquet partitions, and 134 post-cutoff rows represented only by exclusion hashes. The canonical CSI300 source proves 206 accepted snapshots across 126 months with 300 members per snapshot and no rejected snapshot. The source still lacks governed full-market ST/suspension/name-change coverage and the bounded strict matrix/v3 values/validity/target bundle, so `alpha_search_authorized=false`. The Git-safe summary is `evidence/task_056c/task056c_canonical_freeze_summary.json`.

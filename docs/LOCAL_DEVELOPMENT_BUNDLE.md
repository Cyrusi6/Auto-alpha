# Local Development Replay Bundle

## Purpose

The local bundle rehabilitates the existing immutable A-share lake while Tushare or another governed provider is unavailable. It turns one validated Source Freeze research view into a deterministic matrix that can support data-path diagnosis and later fixed-factor development replay.

It does not repair missing provider evidence. Every generation is permanently marked:

```text
mode=development_replay
data_admission_eligible=false
alpha_search_authorized=false
lifecycle_publication_allowed=false
```

## Locked first scope

- CSI300 retrospective snapshots, 2012-01-01 through 2019-12-31;
- close-of-`t` signal, observed `t+1 open` entry and `t+2 open` exit target;
- price, volume, turnover, volume ratio, and total market value as the ten development feature channels;
- membership, weights, limits, and raw adjustment factors as controls only;
- one immutable Source Freeze and its physical research-view partition root;
- no network access, token access, controlled/holdout view, search, or lifecycle publication.

Snapshot-date membership becomes effective on the next open trading day, expires after 45 calendar days, and is always labelled a retrospective publication-time-unproven proxy. The 2012–2015 universe remains unknown rather than being filled with later constituents.

## Evidence and validation

The generation is content-addressed by source/search roots, scope, builder and runtime identity, every artifact hash, evidence flags, and blockers. It freezes:

- stock, date, and feature axes;
- accepted constituent snapshots plus membership, known, and weight arrays;
- every raw field and independent validity array;
- feature values/validity;
- target values/availability and target contract;
- a sanitized Source Freeze identity binding receipt (the raw Source Freeze manifest is deliberately not copied), the exact physical research-view manifest, selected source partition lineage, quality, reconciliation, and matrix manifests.

The validator requires an exact read-only file closure with no symlink or special file. It verifies the sanitized Source Freeze identity receipt, derives the selected six-dataset partition catalog from the embedded research-view manifest and its anchored partition root, and rejects controlled-period locator strings in the embedded evidence. It reconstructs membership from the frozen snapshots, reconstructs feature values/validity from raw arrays, reconstructs target values/availability from observed prices, adjustment factors, provider price-band proxies, and membership, and cross-checks the matrix partition manifest. The band check is a retrospective observation filter, not proof that historical limit, ST, or suspension evidence is governed. The identity receipt is a provenance-consistency binding, not a cryptographic signature; a caller that needs source authenticity must revalidate the original immutable Source Freeze (the builder does this before publication). Recomputing every outer hash after changing a derived array, diagnostic mask, or lineage list is therefore insufficient to forge a valid bundle within the checked evidence contract.

Publication uses the platform's prepared-directory immutable generation owner. Validation occurs before and after atomic rename; same-identity concurrent writers are idempotent; a crash before the mutable current pointer advances can be resumed from the exact immutable generation.

## Commands

```bash
auto-alpha data local-bundle build \
  --source-freeze-manifest <source-manifest> \
  --output-root <sibling-local-bundle-root> \
  --date-start 20120101 \
  --date-end 20191231 \
  --index-code 000300.SH \
  --workers 4

auto-alpha data local-bundle validate \
  --manifest <generation>/local_development_bundle.json

# Optional source-authenticated validation when the original lake is available:
auto-alpha data local-bundle validate \
  --manifest <generation>/local_development_bundle.json \
  --trusted-source-freeze-manifest <source-generation>/canonical_freeze_manifest.json
```

Repeated builds validate the immutable Source Freeze and replay the trusted source semantics before returning a compatible current generation; they do not republish or create a new matrix generation. A source, scope, code, runtime, or artifact change creates a different generation identity.

Use one output root per locked scope and contract (for example `csi300_2012_2019_v1`). The mutable `current.json` is only a locator inside that root; it is not a cross-scope scheduler or evidence identity.

## Fixed-factor development replay

`LocalDevelopmentBundleLoader` is the only adapter from this bundle into the
first vertical replay. It validates the complete immutable generation before it
maps any role and preserves the stock × feature × date layout; it does not
rename the development matrix into a strict or canonical research matrix.

The first replay contract is deliberately not configurable:

- `volume_ratio_cs_rank_v1 = CS_RANK(volume_ratio)`, evaluated by
  `StackVM.execute_with_validity` over the membership-known PIT proxy domain;
- factor values and independent validity are materialized before target values
  are read; the target is used only for an explicitly in-sample diagnostic and
  never for signal eligibility;
- the decision is made at close `t`, execution occurs at the next observed
  open, and a long-only equal-weight Top-20 target is refreshed each trading
  day with stable security-code tie breaking;
- baseline modeled-cost and zero-cost scenarios both use the event ledger and
  retain orders, fills, rejections, settlements, NAV, events, and the complete
  underwater series;
- legacy volume is converted with `volume × 100 = shares` and amount with
  `amount × 1000 = CNY`; the ratio
  `amount_CNY / (close × volume_shares)` is cross-checked before the 20-day ADV
  proxy is used.

The execution masks remain retrospective observed-bar and price-band proxies.
Historical ST and suspension authority has not been reconstructed, corporate
actions are not applied without their causal lineage, the CSI300 benchmark is
not frozen in this bundle, and fee/capacity assumptions are not governed
evidence. Accordingly, every replay evidence generation inherits the bundle's
blockers and fixes all of these fields to false:

```text
data_admission_eligible
alpha_search_authorized
validation_candidate_eligible
lifecycle_publication_allowed
holdout_accessed
network_accessed
```

`terminal_status=diagnostic_completed` means only that the engineering chain
`bundle → loader → fixed factor → next-open ledger → immutable evidence`
completed. It is not OOS evidence, an Alpha conclusion, or a lifecycle event.

```bash
auto-alpha portfolio fixed-replay build \
  --bundle-manifest <local-bundle-manifest> \
  --trusted-source-freeze-manifest <source-generation>/canonical_freeze_manifest.json \
  --output-root <fixed-replay-root>

auto-alpha portfolio fixed-replay validate \
  --manifest <generation>/fixed_factor_replay_evidence.json \
  --trusted-bundle-manifest <local-bundle-manifest>
```

The dedicated validator checks immutable closure, exact formula/policy and
governance semantics, recomputes diagnostics, cost/capacity summaries and
drawdown evidence, and—with a trusted bundle—rematerializes the factor and both
ledger scenarios. Integrity-only CLI validation is labelled separately from a
trusted-bundle replay.

## Offline run on the existing lake

The first real local replay was built from the existing legacy freeze at
`/home/lijunsi/data/auto-alpha/ashare_lake/canonical_freezes/task_056c_v1`, without
Tushare or any network call. The published development-only output is intended to
live at:

```text
/home/lijunsi/data/auto-alpha/ashare_lake/local_development_bundles/csi300_2012_2019_v1
```

The deterministic generation is `local_development_bundle_d3012bcc31b7f37fab62106f`,
with content hash
`d3012bcc31b7f37fab62106fa00a0342ebf39ede813b70e8ad0e8f8de3609dc1` and artifact
root `e1c906408dbeec543a8b83b795d56068c12d98f29e2e164797f7bca9d820a53`.
It has shape `469 × 1,945` (dates `20120104`–`20191231`), 48 accepted retrospective
snapshots (47 effective within the date span), 10 feature channels, 2,767,930
valid feature slots, and 275,471 available observed targets. The 2012–2015 union axis has no complete PIT
membership proof and remains entirely unknown; this is a diagnostic matrix, not an
admitted research freeze.

## Offline fixed-factor run on the existing bundle

The locked replay completed against the real `469 × 1,945` bundle and was
published at:

```text
/home/lijunsi/data/auto-alpha/ashare_lake/fixed_factor_replays/csi300_2012_2019_volume_ratio_cs_rank_v1
```

Its immutable identity is:

- generation `fixed_factor_replay_75a0a210b1cda5bb92ad2994`;
- content hash
  `75a0a210b1cda5bb92ad29942e09110ba3ce639e39e044d32f438b4516052dcc`;
- artifact root
  `daf4ef4388dc8b48ed33b966c10fc4b16eb133a81ea072f557d9c193e04ba4e6`;
- simulation truth hash
  `c2e4bd79d5628bba20d3d304c328d4fbd56c697ef23b428c187c419d9969607a`.

The factor diagnostic has 275,188 valid observations over 953 evaluable dates,
rank IC mean `-0.000505`, ICIR `-0.00399`, and Top-minus-Bottom diagnostic
spread `0.000568`. These are in-sample plumbing diagnostics, not OOS results.
The modeled-cost ledger issued 28,442 orders, recorded 19,965 fills and 8,477
rejections, filled 61.20% of requested shares, incurred CNY 891,588 in modeled
cumulative costs, returned `-71.62%`, and reached a `74.42%` maximum drawdown
from 2016-02-25 to 2019-12-06 without recovery by the end of the view. The
zero-cost scenario returned `+33.70%`, but its orders and holdings can differ,
so the scenario difference is not an isolated transaction-cost estimate.

The unit receipt cross-checked 783,670 observations: the median normalized
amount/price/volume ratio is `0.999904` (p01 `0.966794`, p99 `1.037658`). It
also records 3,968 adjustment-factor transitions while keeping
`corporate_action_lineage_proven=false`. A trusted repeated build returned
`cache_hit=true` with the same content and truth hashes; there is exactly one
published generation, about 105 MB. The input bundle manifest SHA256 remains
`48dd4aba1c445593b3c171251c64623b6ea216a733bf88eebb59c02e50732e6b`.

The poor modeled-cost outcome is a valid zero-promotion engineering result. It
does not change any blocker or authorize autonomous search.

## Remaining governed blockers

The current local lake still lacks replayable provider request/pagination receipts, publication-time proof for CSI300 membership, authoritative historical ST state, governed suspension state, adjustment-factor revision history, corporate-action causal lineage, and a human-approved acquisition/profile activation chain. These are not missing-rate issues and cannot be inferred from prices.

Until those proofs exist and an independent Data Admission Verdict admits the matching scope, the next permitted consumer is a fixed-factor `development_replay` diagnostic only. Autonomous search and `Validation Candidate` publication remain blocked.

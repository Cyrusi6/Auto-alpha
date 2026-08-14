# Governed A-share Data Admission Contract

Status: accepted design, not yet implemented or activated

Decision date: 2026-08-14

This contract defines when real A-share data may enter governed factor research. It governs data identity, source coverage, point-in-time semantics, validity, lineage, deterministic replay, and admission authority. It does not authorize a Research Campaign, open a controlled or sealed view, certify a factor, or start shadow, paper, or live operation.

## 1. Identity and authority

A **Source Freeze Generation** is an immutable physical snapshot and may be retained in either admitted or blocked form. A **Canonical Data Freeze** is only the Data Scope Root inside a Source Freeze Generation for which an independent **Admission Verifier** has issued an admitted **Data Admission Verdict**.

A Research Contract references exactly one immutable, content-addressed **Data Admission Profile**. Changing any dataset role, field, coverage rule, validity rule, evidence grade, numerical threshold, or consumer closure creates a new profile identity. The autonomous loop may acquire and verify data under an activated profile, but it cannot create or activate a relaxed profile. Activation requires human approval.

Admission is scoped by:

```text
Data Admission Profile × access view × date span × As-of Market Date
```

Passing one scope grants no access to another scope. Search, controlled revalidation, retrospective test, and sealed views therefore receive separate verdicts even when they share one physical Source Freeze Generation.

Producer fields such as `complete`, `status`, or `alpha_search_authorized` are untrusted declarations. Only the Admission Verifier can authorize an admitted scope after reconstructing all required evidence.

## 2. Dataset roles

Every provider-neutral dataset contract has exactly one role in a profile:

- `base-required`: required for every use of the profile;
- `feature-family-conditional`: required when the Research Contract activates its named feature family or consumer role;
- `inactive`: unavailable to the profile and unable to block or influence its admitted scope.

Activation occurs when the Research Contract is created. Formula proposals, observed values, target outcomes, and campaign results cannot change the active closure.

### 2.1 First profile classification

| Dataset | Role | Family or reason |
| --- | --- | --- |
| `securities` | base-required | A-share identity and lifecycle |
| `trade_calendar` | base-required | session and date axes |
| `daily_bars` | base-required | stock price, volume, target, execution and capacity |
| `daily_basic` | base-required | approved turnover, volume-ratio and size fields |
| `financial_features` | feature-family-conditional | quality and growth |
| `daily_limits` | base-required | price-limit and fill eligibility |
| `adjustment_factors` | base-required | causally validated adjusted prices and target |
| `index_members` | base-required | PIT CSI300 universe |
| `corporate_actions` | base-required | EventLedger dividends and share transformations |
| `index_basic` | inactive | no production research consumer |
| `index_daily_bars` | base-required | benchmark and evaluation only |
| `index_daily_basic` | feature-family-conditional | benchmark valuation |
| `industry_classification` | inactive | no production feature consumer and weak PIT |
| `industry_members` | feature-family-conditional | industry; declaration and implementation must be repaired before activation |
| `suspensions` | base-required | S/R state and conservative fill exclusion |
| `st_status_daily` | base-required | authoritative daily ST state |
| `name_changes` | feature-family-conditional | name/ST reconciliation, never authoritative ST state |
| `new_shares` | feature-family-conditional | IPO and new-share events |
| `income_statements` | feature-family-conditional | financial statements |
| `balance_sheets` | feature-family-conditional | financial statements |
| `cashflow_statements` | feature-family-conditional | financial statements |
| `earnings_forecasts` | feature-family-conditional | earnings events |
| `earnings_express` | feature-family-conditional | earnings events |
| `disclosure_calendar` | feature-family-conditional | disclosure events |
| `financial_audit` | inactive | no production feature consumer |
| `main_business` | inactive | no consumer and no reliable availability time |
| `moneyflow` | feature-family-conditional | order-size money flow |
| `margin_summary` | feature-family-conditional | margin fallback |
| `margin_detail` | feature-family-conditional | security-level margin |
| `top_list` | feature-family-conditional | abnormal trading |
| `top_inst` | feature-family-conditional | abnormal trading |
| `block_trades` | feature-family-conditional | block trading |
| `holder_number` | feature-family-conditional | holder structure |
| `holder_trades` | inactive | no production feature consumer |
| `top10_holders` | feature-family-conditional | holder structure |
| `top10_float_holders` | feature-family-conditional | holder structure |
| `pledge_detail` | inactive | no production feature consumer |
| `pledge_stat` | inactive | no reliable PIT availability |
| `repurchases` | feature-family-conditional | shareholder events |
| `share_unlocks` | feature-family-conditional | shareholder events and risk |
| `hk_holdings` | feature-family-conditional | northbound holdings |

The first profile therefore contains 11 base-required, 23 feature-family-conditional, and 7 inactive contracts.

### 2.2 Base fields and consumer closure

| Dataset | Approved source fields | Required closure | Research role |
| --- | --- | --- | --- |
| `securities` | `ts_code`, symbol, exchange, board, `list_date`, `delist_date`, `list_status` | stock axis, listed/active state, exchange and board mapping | control-only; current status is reconciliation evidence only |
| `trade_calendar` | exchange, `trade_date`, `is_open`, `prev_trade_date` | date axis, previous/next session, target shift and rebalance dates | control-only |
| `daily_bars` | `ts_code`, `trade_date`, OHLC, `pre_close`, volume, amount | independent values/validity, price-volume features, target endpoints, ADV and ledger prices | stock price and volume may be Alpha-eligible; execution derivatives are control-only |
| `daily_basic` | `ts_code`, `trade_date`, `turnover_rate`, `volume_ratio`, `total_mv` | independent validity, turnover, volume ratio and `log_mkt_cap` | Alpha-eligible; valuation and float-cap fields are not exposed |
| `daily_limits` | `ts_code`, `trade_date`, `up_limit`, `down_limit`, `pre_close` | limit validity and buyable/sellable state | control-only |
| `adjustment_factors` | `ts_code`, `trade_date`, `adj_factor` | event-reconciled adjusted prices, price-basis validity and target | raw factor is control-only; validated adjusted prices may be Alpha-eligible |
| `index_members` | `index_code`, `trade_date`, `ts_code`, `weight` | publication-proven membership, source date and known mask | control-only |
| `index_daily_bars` | index code, trade date, OHLC, `pre_close`, volume, amount | benchmark values/validity and relative evaluation | control-only in the first profile |
| `suspensions` | `ts_code`, `trade_date`, `suspend_type`, `suspend_timing` | S/R state, coverage-known and conservative fill masks | control-only |
| `st_status_daily` | `ts_code`, `trade_date`, type and type name | daily ST state and known mask | control-only; provider name is non-semantic |
| `corporate_actions` | security, report period, announcement/implementation/record/ex/pay/list dates, process state, base shares, cash and stock terms | immutable event versions, economic effects, ledger transformations and adjustment reconciliation | control-only |

Stock price/volume, turnover, volume ratio, size, listing age, and causally valid adjusted prices are the first Alpha inputs. Membership, listed/active state, ST, suspension, price limits, index data, raw adjustment factors, and corporate actions are **Control-Only Fields** and must not appear in the formula vocabulary or proxy tensor.

Every active dataset must declare the complete `source → normalized event/value → derived artifact → consumer` dependency closure. An existing file or matrix is not evidence that the production consumer used it.

## 3. Coverage population and time

The first profile covers every A-share security whose lifecycle intersects the verdict date span, including delisted securities. A security has obligations only during its half-open lifecycle interval:

```text
[list_date, delist_date)
```

Current `list_status`, name, industry, and ST fields cannot be projected backward. A missing or contradictory lifecycle boundary blocks the affected scope.

Each verdict has one **As-of Market Date**. Base market, benchmark, ST, suspension, limit, and all published corporate-action evidence required by the scope must reach that date. Mixed latest dates and silent carry-forward do not advance the watermark. A versioned publication lag is allowed only for an explicitly conditional dataset whose consumer does not require same-day evidence.

A Source Freeze Generation may be published while blocked, but it cannot trigger a Research Campaign. Search and later controlled views use separate scope verdicts and physical access controls.

## 4. Point-in-time event contracts

### 4.1 ST status

`stock_st` daily status is authoritative. The current `securities.is_st` snapshot is never historical evidence, and `name_changes` is only optional reconciliation. Without a more precise publication timestamp, a same-date ST value is observable no earlier than that trading day's close. Every lifecycle-day obligation requires terminal positive or negative evidence.

### 4.2 Suspensions and resumptions

An S event starts a suspended state that persists until an R event. Reliable timing is applied when present. When timing is absent, the whole event day is untradable; an R event with unknown timing restores tradability on the next trading day. The Coverage Plan must establish the state before the evaluated span begins rather than assume an unsuspended initial state.

### 4.3 Name changes

Provider `end_date` is a closed endpoint and is normalized to `[start_date, end_date + 1 day)`. The business identity does not include nullable `end_date`. A later closure or correction appends a **PIT Event Version** and retains the superseded source version. A record with unknown `ann_date` cannot enter governed research.

### 4.4 Corporate actions

Proposal facts become observable at `ann_date`; final implementation parameters at `imp_ann_date`; share and price effects at `ex_date`; and cash at `pay_date`. Later source states append immutable revisions. Final implementation fields are never exposed at the initial announcement.

A row with no `ann_date` may form an implementation version when `imp_ann_date` is proved. An implementation announcement later than its economic effect is a causal conflict and blocks the matching scope until reacquired or reconciled.

### 4.5 Index membership

Historical membership must have proved availability/publication time. A snapshot-date plus one-trading-day proxy is development replay only and cannot support a Validation Candidate. Unknown membership on any required open day blocks the governed scope.

### 4.6 Adjustment factors

Every factor change must reconcile with a corporate-action version observable and effective no later than that date. A later source correction creates a new source and data-scope identity. An unexplained jump or prematurely changed factor blocks the affected scope.

## 5. Coverage proof

### 5.1 Coverage Plan and obligations

A **Coverage Plan** is provider-neutral and immutable. It contains the profile, access view, date span, As-of Market Date, lifecycle-population root, dataset contracts, and canonically ordered **Coverage Obligations**. An obligation uses `(dataset, subject, date-or-span)`, where the subject can be a security, exchange, index, lifecycle version, or other provider-neutral partition key. Security-day and security-span obligations are the common event-data cases, not the only allowed geometry.

An adapter may batch, paginate, or split obligations into provider calls, but request geometry cannot change the logical plan.

### 5.2 Coverage Receipt

Every request attempt produces an immutable receipt that binds at least:

- receipt, attempt, previous-journal and evidence-use identities;
- dataset contract, provider adapter, endpoint, API and schema versions;
- a non-secret permission-context identity;
- normalized parameters, fields and request fingerprint;
- obligation mapping and page/cursor or split-tree position;
- locked row cap and terminal pagination evidence;
- transport/provider outcome and local capture signature;
- returned count, records hash, payload hash and raw-envelope locator/hash;
- start/end capture times, retry predecessor and terminal state.

The local signature proves that the system captured the provider payload over the governed transport and that the evidence was not rewritten. It is not a provider signature and does not prove that the provider never revises history.

### 5.3 Empty, cap and retry semantics

Only these obligation outcomes can admit:

- `satisfied_nonempty`;
- `satisfied_empty`, backed by an **Observed Empty** receipt;
- `not_applicable`, derived from separately proved lifecycle or calendar evidence.

Permission failure, schema mismatch, coverage gap, `cap_suspected`, unresolved conflict, or terminal transport ambiguity blocks.

An empty provider response is valid only when request identity, permission, schema, success, pagination termination, and absence of truncation are proved. A count equal to the endpoint cap is never terminal without a provider cursor/end marker; otherwise the adapter must create a deterministic, gap-free and overlap-free split tree until every leaf is below the cap.

Network and rate-limit failures may receive bounded retries only for an endpoint declared read-only. Each retry receives a new attempt identity and retains earlier outcomes. An attempt missing its post-transport receipt remains `ambiguous_transport`; it is never rewritten as a later success.

### 5.4 Coverage Root

The Admission Verifier recomputes SHA-256 leaves and an ordered content tree over:

1. the Coverage Plan;
2. every Coverage Obligation;
3. every attempt Coverage Receipt, including success, empty, failure and ambiguity, plus every journal link;
4. the attempt-to-obligation mapping and exactly one terminal disposition for each obligation;
5. the resulting coverage completeness and evidence grade.

The verifier must prove exact cover: no missing obligation, duplicate satisfying disposition, orphaned attempt, gap, overlap, broken journal link, or cap ambiguity. Failed and ambiguous attempts remain in the root but do not satisfy an obligation; a later governed retry may be its single satisfying disposition. `complete` and `coverage_root` are verifier outputs and cannot be accepted as producer inputs.

Existing flat files, caches, aggregate audits, and self-declared coverage manifests are **Legacy-Unproven Source Evidence**. They may guide repair or reconciliation but cannot satisfy an obligation.

## 6. Validity and quality

Identity, primary-key, PIT availability, coverage, lineage, parsing, and control evidence are zero-tolerance. A scope must have:

```text
unexplained_unknown = 0
conflicting_primary_key = 0
unexplained_duplicate = 0
parse_error = 0
pit_availability_gap = 0
coverage_gap = 0
lineage_gap = 0
```

These metrics are recomputed over complete admitted partitions rather than estimated from samples. Semantically identical duplicates may be deterministically collapsed only when every source-row identity is retained. Corrections use PIT Event Versions. Conflicting values for one event version block.

Every invalid control or target cell must carry a reason such as `not_applicable`, `proven_suspension`, or `limit_blocked`. `unexplained_target_unknown` must be zero. Target availability is not expected to be universally true, but every false value must be causally explained.

Deterministic value rules include positive coherent OHLC prices, non-negative volume and amount, positive adjustment factors, coherent price limits, complete benchmark sessions, and fully known required membership state. A causally explained value violation makes that cell untradable and its target unavailable; it does not by itself block the whole scope. An unexplained, structural, PIT, coverage, or lineage violation blocks the scope, and explained cell loss may still block when preregistered breadth requirements are no longer met.

Ordinary Alpha fields may use explicit `validity=false`; they may never be imputed to semantic zero. Each feature/date requires both a minimum valid security count `N` and a minimum PIT-universe fraction `X`. Exact `N` and `X` values cannot be selected from the current legacy matrix. They must be set from a validity-only diagnostic view, without opening target values or observing trial/OOS outcomes, and human-approved into the activated profile. The Research Contract separately locks target/common evaluation breadth.

## 7. Data Scope Root and deterministic replay

The **Data Scope Root** binds only the active closure for its profile and scope:

- profile, access view, date span and As-of Market Date;
- active dataset, approved field and consumer-role contracts;
- source artifact roots and Coverage Root;
- provider adapter/schema and normalization/PIT transform identities;
- source-to-derived parent lineage;
- stock, date, feature and target axes;
- feature values and validity;
- target values, availability and formula/timing identity;
- quality and reconciliation roots;
- producer code and locked toolchain identity.

Paths, wall-clock build times, mutable pointers, scheduling order, and worker completion order are metadata, not semantic identity. A different code or toolchain identity produces a different scope even if observed values appear equal.

**Deterministic Freeze Replay** requires byte-identical artifacts, roots, and identities from the same immutable inputs and toolchain. The verifier performs replay before admission and records its result in the verdict.

An inactive or unactivated dataset may change the containing Source Freeze Generation without changing the matching Data Scope Root. It therefore cannot trigger a campaign for that Research Contract. A campaign is triggered only by a new admitted matching scope root.

## 8. Incremental reuse and retention

An unchanged obligation, receipt, raw envelope, normalized partition, or derived artifact may be reused only by its complete content identity. A new plan may append obligations, but the verifier recomputes exact cover and the complete Coverage and Data Scope roots. A changed profile, schema, adapter evidence-use identity, PIT rule, producer, or toolchain invalidates the affected reuse boundary.

Permanent evidence includes:

- activated profiles and all Data Admission Verdicts;
- Coverage Plans, obligations, every success/empty/failure/ambiguous receipt and the durable journal;
- successful content-addressed raw response envelopes;
- every governed normalized source partition referenced by a retained Source Freeze Generation or Data Admission Verdict, including blocked evidence;
- transform, quality, reconciliation, lineage and derived manifests;
- Coverage Roots, Data Scope Roots and deterministic replay evidence.

Temporary transfer fragments, transport buffers, duplicate materializations, and recomputable intermediate tensors may be removed only after a locked replay-verification and retention rule succeeds.

## 9. Verdict

A Data Admission Verdict minimally records:

- verdict identity and terminal `admitted` or `blocked` outcome;
- profile, source generation, access view, date span and As-of Market Date;
- active dataset/field/consumer closure;
- Coverage Plan and Coverage Root identities;
- Data Scope Root and all parent roots;
- structural, PIT, validity, quality, reconciliation and breadth metrics;
- deterministic replay identity and result;
- every blocker/reason code and evidence locator;
- verifier code/toolchain and signature identity.

Any blocker from a base-required or activated conditional contract remains a blocker. It cannot be demoted to a warning. A blocked verdict and its Source Freeze Generation remain immutable evidence and can be superseded only by a new generation/verdict identity.

## 10. Current real-lake adjudication

The current real lake cannot produce an admitted first-profile verdict:

- `suspensions` is a legacy incompatible schema without governed receipts;
- `st_status_daily` is absent;
- corporate actions contain unresolved availability/identity and causal conflicts;
- adjustment-factor revision semantics are unproved;
- CSI300 membership is absent for 2012-2015 and lacks real publication evidence thereafter;
- no canonical strict target, membership-known mask, feature validity tensor, or unified source-to-derived bundle exists;
- the legacy matrix mixes index memberships and cannot support quality thresholds;
- no production coverage-proof producer or leaf-recomputing verifier exists.

CSI300 index bars do cover all 1,945 open sessions in 2012-2019 with no observed required-field gap, but benchmark coverage cannot substitute for PIT membership proof.

The correct current outcome is `blocked`, not a warning and not permission to run a governed search.

## 11. Bounded provider-probe handoff

The first provider remains Tushare Pro behind provider-neutral contracts. A probe validates permission and evidence geometry; it never claims full historical coverage and never performs a bulk backfill.

| Endpoint | Required returned fields | Logical admission geometry | Mandatory bounded probe cases |
| --- | --- | --- | --- |
| `stock_basic` | security code, symbol, market/exchange, list status, list and delist dates | each A-share identity and lifecycle version, including delisted statuses | each list-status query, delisted sample, listing-boundary sample, terminal page/cap |
| `trade_cal` | request-scope exchange; returned calendar date, open flag and previous trade date | exchange × calendar span | open day, weekend/holiday span, year boundary, terminal page |
| `daily` | security, trade date, OHLC, pre-close, volume and amount | active security × open trading day | normal, IPO boundary, suspended/absent, delisted and cap-pressure samples |
| `daily_basic` | security, trade date, turnover rate, volume ratio and total market value | active security × open trading day | populated and valid-empty/field-null samples plus cap/page termination |
| `stk_limit` | security, trade date, pre-close, upper and lower limit | active security × open trading day | main board, STAR/ChiNext, ST, IPO/no-limit and historical rule-boundary samples |
| `adj_factor` | security, trade date and adjustment factor | active security × open trading day | no-action span, cash/stock action span, repeated retrieval/revision comparison |
| `index_daily` | index code, trade date, OHLC, pre-close, volume and amount | CSI300 benchmark × open trading day | 2012 boundary, normal span, current boundary and terminal page |
| `index_weight` | index code, constituent code, source date and weight | CSI300 publication/version × member | pre-2016 request, known 2016-2019 snapshot, publication metadata, index isolation and cap/page termination |
| `stock_st` | security, name, trade date, type and type name | active security × trading day | known ST, known non-ST/empty, historical boundary, delisted security and permission/cap cases |
| `suspend_d` | security, trade date, S/R type and timing | active security × trading day plus carried S/R state | known S, known R, missing timing, known empty span, leading-state and cap/page cases |
| `dividend` | security, report period, proposal/implementation/record/ex/pay/list dates, process, base shares, stock and cash terms | active security × event-version span | proposal-to-implementation revision, missing initial announcement, implementation-after-ex conflict, empty span and cap/page cases |

Before transport, the probe task must publish the exact finite request set, normalized parameters, requested fields, expected cap/page behavior, retry budget, and evidence-use identity. GitHub issues and committed artifacts retain only desensitized parameters, receipt identities/hashes and bounded summaries; the governed evidence store permanently retains successful raw response envelopes and full receipts under Section 8. Credentials never enter either surface. `namechange` is probed only if name/ST reconciliation is activated, and `index_member_all` only if an industry family is activated.

Every endpoint probe ends in exactly one handoff disposition: `local_repair`, `bounded_backfill`, `permission_missing`, or `provider_cannot_prove`. The disposition identifies the exact full acquisition or local normalization work that may be specified next.

## 12. Deferred activation inputs

The contract is decision-complete, but a concrete first profile cannot be activated until later mapped work supplies:

- a Research Contract date span and publication-proven PIT CSI300 universe;
- validity-only evidence from the first honest strict bundle and human-approved `N`/`X` breadth values;
- the self-contained canonical bundle/loader handoff;
- bounded provider permission and coverage probes for the exact base gaps;
- implementation and independent verification of the plan/receipt/root protocol.

No target values, factor metrics, OOS outcomes, or trial results may be opened to choose these activation inputs.

## 13. Acceptance evidence

Implementation acceptance must demonstrate at least:

1. a controlled complete fixture admits and the current real lake remains blocked;
2. missing receipts, cap hits, page omissions, broken journal links, permission/schema errors and payload tampering block;
3. lifecycle exclusions and valid empty responses produce explicit terminal reason codes;
4. current-status backfill, index mixing, NaN-to-zero and post-announcement leakage are rejected;
5. inactive dataset changes do not change the matching Data Scope Root;
6. active source, profile, code or toolchain changes do change the matching identity;
7. identical rebuilds across worker counts and output locations are byte-identical;
8. interrupted acquisition/rebuild resumes to the same receipts, roots and verdict as uninterrupted execution;
9. producer self-attestation cannot issue an admitted verdict.

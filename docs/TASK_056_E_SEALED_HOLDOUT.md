# Task 056-E: one-shot sealed holdout

Task 056-E adds a one-shot Validation Red-Team boundary after Alpha Factory research. It does not discover formulas, modify the frozen selection order, certify factors, or build portfolios.

## Freeze before access

`freeze-candidates` resolves the successful Alpha Factory report and freezes the shortlist together with its canonical identities, compact research values and validity hashes, research metrics, exact selection order, generated-trial count, and locked selection-policy hash. Every source file and JSONL schema sidecar is hashed and revalidated before a holdout capability can be issued.

The holdout view is candidate-bound and physically enumerates feature values and validity, strict targets and target availability, signal/membership/lifecycle masks, regimes, governed universes, and optional certified-factor references. The final `label_horizon` signal dates are never evaluable, unavailable targets must remain `NaN`, and the maximum endpoint is derived from the complete trading-day axis.

## One-shot capability

Only the `validation_red_team` principal may receive `single_sealed_holdout_evaluation`. A capability binds one candidate pool, one untouched future view, one stratum-specific policy, and one isolated output root. The registry is append-only and hash-chained. A holdout view can be registered only once, including attempts to present an adjusted formula or a different candidate pool.

Alpha Factory checks every configured input/output path for `holdout_feedback_forbidden.json` before creating its output directory. Red-Team results therefore cannot be configured as formula corpus, prior experiment, factor store, data, cache, or campaign output. Rejected and data-blocked formulas are archived with `next_generation_holdout_or_shadow_observation`; they cannot be edited and retried against the same holdout.

## Calibrated gates

Policies are locked to an exact tuple of universe, holding period, neutralization method, and rebalance frequency. The initial directional contract checks:

- median OOS RankIC above the calibrated zero boundary;
- at least 60% positive RankIC windows;
- at least 60% walk-forward window passes;
- positive modeled-cost net top-minus-bottom spread;
- no sign reversal at twice modeled cost;
- maximum correlation to certified factors no greater than 0.70;
- no PIT, leakage, or placebo blocker;
- direction consistency across preregistered regimes and universes.

These are not return promises and are not universal IC/ICIR constants. Every numeric threshold belongs to the calibration profile and cannot be silently reused for another universe or signal contract. Terminal states are `sealed_holdout_passed`, `sealed_holdout_rejected`, and `data_blocked`; none alone supports certification.

## Commands

```bash
auto-alpha validation red-team freeze-candidates \
  --campaign-report <alpha_factory_report.json> \
  --materialization-manifest <materialization_manifest.json> \
  --output-root <candidate-freeze-root>

auto-alpha validation red-team publish-policy \
  --policy-spec <locked-profile.json> \
  --output-root <policy-root>

auto-alpha validation red-team issue-capability \
  --registry-root <capability-registry> \
  --candidate-pool-manifest <candidate_pool_manifest.json> \
  --holdout-view-manifest <sealed_holdout_view_manifest.json> \
  --holdout-policy <sealed_holdout_policy.json> \
  --red-team-output-root <isolated-red-team-root>

auto-alpha validation red-team evaluate \
  --capability <holdout_capability.json> \
  --reviewed-capability-hash <sha256>
```

The current governed 2025-01-01 through 2026-06-30 partition is historically observed and has `untouched=false`. The native Task 056-E preflight therefore returns `blocked`, creates no capability, and executes no holdout evaluation. It remains retrospective research/development evidence and cannot be renamed as a clean holdout.

# Task 056-F: Factor-certified portfolio research

`portfolio_research` is the only formal factor-combination path. It accepts a content-addressed bundle containing compact factor values and independent validity, strict next-open market and valuation evidence, multiple PIT universes, multiple governed benchmarks, and a validated Fee Schedule v2.

## Admission boundary

Every input factor must have lifecycle status `factor_certified`. The bundle validator also requires a passed sealed-holdout status, an independent-audit attestation, a 64-character certification evidence hash, a canonical formula hash, family metadata, and effective lookback. Legacy `approved`, `conditional`, `sealed_holdout_passed`, composite, and latest-factor fallbacks are rejected.

Factor certification now maps a fully certified decision to `factor_certified`; conditional decisions are not certification passes and never enter the certified pool.

## Combination contract

For each governed universe and rolling window, the production policy fixes `756/126/126/126`. Embargo is the maximum of label horizon, policy minimum, and the longest certified factor lookback. Weights use training dates only:

1. validity-aware daily cross-sectional standardization;
2. absolute-correlation clustering;
3. train-fitted within-cluster residualization;
4. rolling RankIC/ICIR weighting;
5. shrinkage toward equal factor weights;
6. factor, cluster, and family caps;
7. bounded window-to-window weight changes.

Invalid cells never enter standardization, correlation, residualization, IC, or the combined signal. A missing positive training IC, insufficient breadth, missing strict mask, insufficient OOS window, or target-tail violation is a blocker rather than an equal-weight fallback.

## Portfolio walk-forward

Every OOS window is rerun with the Task 055 event-ledger simulator and an external governed fee calculator. The simulator consumes next-open buy/sell masks, explicit valuation marks, lagged ADV, lots, T+1 settlement, cash, fills, rejections, and corporate actions. The required scenarios are baseline, 2× modeled costs, 50% lagged volume, and an extreme-volatility slice with conservative costs and capacity.

The gate evaluates cost-adjusted return, drawdown, positive-window ratio, multi-universe consistency, and active return versus every governed benchmark. Historical Sharpe maximization and post-result policy selection are not used.

## State boundary

Successful engineering evidence produces only `shadow_candidate` and one `portfolio_shadow_queue` row. `portfolio_ready`, `paper_ready`, `live_ready`, and certification readiness remain false. Independent shadow audit is mandatory before any paper transition. The legacy optimizer registration and activation commands are fail closed.

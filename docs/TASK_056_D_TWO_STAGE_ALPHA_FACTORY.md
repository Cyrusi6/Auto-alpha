# Task 056-D: Two-stage Alpha Factory research

Task 056-D replaces the former mixed-unit factor score with a governed two-stage research contract. It does not certify factors, build portfolios, or relax the canonical data gate.

## Stage 1: cheap proxy

Every generated candidate first passes canonical syntax, recursive semantics, promotion, lookback, and PIT checks. The proxy then evaluates only deterministic research-eligible dates and records:

- eligible-cell coverage, variance, and nonzero coverage;
- industry/size-neutralized RankIC, hit rate, and IC stability;
- turnover proxy, complexity, and canonical lookback;
- maximum correlation with already materialized factors;
- family novelty and direction consistency across governed universes.

Hard failures remain blockers. The remaining objectives are converted to average-tie empirical percentiles within the candidate cohort, honoring each objective direction and policy weight. Raw return spread is retained as a diagnostic and never added directly to ICIR, monotonicity, or turnover.

## Stage 2: governed full research

Only the proxy shortlist enters full research. The production policy `alpha_factory_two_stage_oos_v1` fixes rolling windows at `756/126/126/126`; effective embargo is canonical formula lookback plus label horizon. Evaluation consumes formula validity, active/lifecycle state, PIT membership, target availability, and the campaign common eligible-date mask.

The evaluator records rolling OOS stability, regime slices, validity-aware placebo trials, signal-time and parameter perturbations, modeled daily-bar cost/capacity stress, and industry/size/as-of-beta/liquidity exposure. Benjamini-Hochberg and Holm corrections are calculated across full-research trials, while a generated-trial adjusted p-value and append-only trial ledger expose selection reuse. The rolling PBO field is explicitly an approximation and cannot support certification.

Only explicit positive OOS evidence under the locked policy can produce `validation_candidate`. Other terminal research states are `research_rejected` and `data_blocked`; an absent or empty OOS evaluation cannot pass.

## Artifacts and boundary

The campaign publishes the versioned research policy, proxy shortlist, governed full-evaluation rows, cohort normalization references, trial ledger, and selection-bias report. Monitoring rejects successful campaign reports that use the former unscaled linear score.

Production execution remains fail closed behind Task 056-C. The current Source Freeze Generation reports `alpha_search_authorized=false` because required historical/PIT source proofs and strict derived artifacts are incomplete, so it is not a Canonical Data Freeze under the accepted data-admission contract. Therefore Task 056-D validates the production implementation and synthetic/focused integration paths but does not start a real candidate search or GPU campaign.

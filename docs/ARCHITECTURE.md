# Repository architecture

## Layout contract

The repository uses a standard `src/` layout. Every installable production package is a direct child of `src/`; task numbers, campaign IDs, dates, and machine paths are not architectural package names.

`dev_tools/repository_layout.py` enforces the physical layout and prevents legacy task imports or packaging entries from returning.

## Dependency direction

The intended high-level dependency direction is:

```text
data foundation
  -> PIT matrix and feature validity
  -> formula and factor research
  -> validation and certification
  -> portfolio research
  -> shadow and operational evidence
```

Lower layers must not import portfolio, execution, or deployment state. Research components must consume governed artifacts rather than operational mutable state. Live-readiness components may verify research artifacts, but they cannot change formula identity or research results.

## Durable domains

### Data foundation

Owns source contracts, immutable freezes, lifecycle, historical membership, corporate actions, raw validity, strict matrices, and feature tensors.

Primary packages: `data_pipeline`, `data_lake`, `point_in_time`, `universe`, `matrix_store`, and `feature_factory`.

### Research and validation

Owns formula semantics, search, factor materialization, research firewall, walk-forward evaluation, multiple-testing evidence, and factor certification.

Primary packages: `alpha_factory`, `formula_search`, `formula_batch_eval`, `factor_store`, `research_firewall`, `validation_lab`, and `factor_certification`.

### Portfolio research

Owns admission of exact `factor_certified` records, correlation clustering, residualization, rolling IC weights, shrinkage, stability constraints, event-ledger simulation, governed fees, and shadow-only publication.

Primary packages: `portfolio_research`, `backtest`, `risk_model`, `capacity_model`, and `settlement_engine`.

### Operations

Owns artifact schemas, scheduling, monitoring, release checks, approvals, broker abstractions, and explicitly governed readiness evidence.

Primary packages: `artifact_schema`, `compute_cluster`, `monitoring`, `release_manager`, `execution`, `broker_adapter`, and `live_readiness`.

## Historical engineering code

Historical implementations remain available where they still protect correctness, but are nested under their durable domain. They must not be imported through task-numbered package names.

No compatibility packages are retained at the repository root. Import drift is a blocker rather than an alias fallback.

## Adding code

- Extend an existing domain before creating a package.
- Create a new top-level `src/` package only for a genuinely independent bounded context.
- Never create a package named after a task, date, campaign, or ticket.
- Keep real data and generated binaries outside Git.
- Add a focused test and update the artifact schema when output contracts change.

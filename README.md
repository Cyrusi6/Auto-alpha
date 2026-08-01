# Auto-alpha

Auto-alpha is a local-first A-share factor research platform. It covers governed data freezes, point-in-time feature computation, formula research, out-of-sample validation, factor certification, portfolio research, and shadow execution evidence.

The repository is intentionally fail-closed. Missing lineage, validity, target availability, PIT proof, execution masks, or governed fees block the affected workflow rather than substituting zero values or permissive fallbacks.

## Repository layout

Production Python packages live under a single `src/` root. Import names remain stable, for example `alpha_factory`, `data_pipeline`, and `portfolio_research`.

```text
src/                 production packages
tests/               unit, integration, regression, and security tests
docs/                architecture and task-specific operator documentation
evidence/            scrubbed, Git-safe evidence summaries only
dev_tools/           repository maintenance and synthetic rehearsal helpers
.github/              CI workflows
```

Task-numbered source roots are prohibited. Historical engineering implementations are grouped by durable responsibility:

- `src/point_in_time/historical_audit/`
- `src/backfill_repair/governed_replay/`
- `src/feature_factory/engineering_replay/`
- `src/research_firewall/{truth_evidence,production_sentinel,engineering_closure}/`
- `src/live_readiness/` for holdout simulation, evidence remediation, governed network authorization, and correctness closure

The layout contract is enforced by `python -m dev_tools.repository_layout audit` and `tests/test_repository_layout.py`.

## Architecture

### Data foundation

- `data_pipeline`, `data_backfill`, `raw_data_landing`, `raw_data_index`
- `data_lake`, `data_quality_lab`, `data_source_validation`
- `point_in_time`, `universe`, `corporate_actions`
- `matrix_store`, `matrix_refresh`, `research_data_readiness`

### Factor research

- `feature_factory`, `feature_promotion`, `factor_engine`, `factor_store`
- `formula_search`, `formula_batch_eval`, `formula_corpus`, `neural_search`
- `alpha_factory`, `alpha_experiment_store`, `experiment_orchestrator`
- `research_firewall`, `validation_lab`, `validation_campaign_store`
- `factor_certification`, `certification_campaign_store`

### Portfolio research

- `portfolio_research` is the formal factor-certified combination path.
- `backtest`, `risk_model`, `capacity_model`, and `settlement_engine` provide supporting simulation contracts.
- A successful portfolio research result is shadow-only. Paper and live require separate independent approval.

### Operations and execution

- `compute_cluster`, `monitoring`, `artifact_schema`, `release_manager`, `ci`
- `execution`, `execution_plan`, `broker_adapter`, `paper_account`, `shadow_trading`
- `live_readiness`, `risk_controls`, `incident_response`, `go_live_gate`

See `docs/ARCHITECTURE.md` for package boundaries and dependency rules.

## Environment

- Python `3.11`
- dependencies managed by `uv`
- production code packaged from `src/`

```bash
uv sync
uv run pytest
uv run python -m ci.run_local_ci --full --output-dir /tmp/auto-alpha-ci --pretty
uv build
```

For direct interpreter use without installation:

```bash
PYTHONPATH=src:. python -m pytest
```

## Common workflows

Offline sample data validation:

```bash
uv run python -m data_source_validation.run_smoke \
  --provider sample \
  --data-dir /tmp/auto-alpha-sample \
  --output-dir /tmp/auto-alpha-validation \
  --start-date 20240102 \
  --end-date 20240131 \
  --validate --stats --pretty
```

Canonical research freeze:

```bash
uv run python -m data_lake.run_canonical_freeze --config /path/to/config.json
```

Governed Alpha Factory research:

```bash
uv run python -m alpha_factory.run_factory --help
```

Factor-certified portfolio research:

```bash
uv run python -m portfolio_research.run_portfolio_research \
  --bundle-manifest /governed/portfolio_bundle/generations/<id>/portfolio_research_bundle_manifest.json \
  --output-dir /governed/portfolio_research/run
```

Strict artifact validation:

```bash
uv run python -m artifact_schema.run_validate \
  --artifact-dir /path/to/artifacts \
  --output-dir /tmp/artifact-schema \
  --strict --fail-on-error --include-unknown --write-manifest --pretty
```

## Safety boundaries

- Network access is disabled unless a governed command explicitly requires and authorizes it.
- Credentials never belong in commands, source files, artifacts, logs, or Git.
- Raw market data, NPY tensors, caches, checkpoints, and server-specific paths are not committed.
- Research, validation, certification, portfolio, paper, and live states are distinct.
- Retrospective or reused evidence never becomes clean holdout evidence by renaming metadata.
- CPU, JSONL, universe, target, fee, or PIT fallbacks are forbidden in strict production research paths.

## Development rules

1. Add production code under an existing `src/` domain package.
2. Do not create top-level task-numbered packages.
3. Add tests under `tests/` and keep fixtures synthetic and bounded.
4. Register new artifact types in `artifact_schema`.
5. Update `FRAMEWORK_UPDATE.md` for meaningful architecture changes.
6. Run focused tests, full pytest, local CI, package build, schema checks, and `git diff --check` before publication.

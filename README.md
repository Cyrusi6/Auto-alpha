# Auto-alpha

Auto-alpha is a fail-closed A-share factor research platform. It owns governed data, PIT-safe research, out-of-sample validation, portfolio simulation, execution evidence, and platform operations.

## Structure

Production code has one public package and six domains:

```text
src/auto_alpha/
├── data/          ingestion, lake, PIT, universe, matrix, quality
├── research/      features, formulas, factors, governed search
├── validation/    firewall, walk-forward lab, certification
├── portfolio/     construction, risk, event-ledger simulator
├── execution/     broker, trading, settlement
└── platform/      artifacts, compute, governance, observability

tests/
├── data/
├── research/
├── validation/
├── portfolio/
├── execution/
└── platform/
```

There are 23 visible subsystems, 54 Python package directories, and 436 Python source files. `research`, `validation`, `portfolio`, and `execution` are flat below their subsystem boundary. Cohesive capability files replace runner/store/report prefix chains. Task numbers, campaign names, dates, and machine paths are prohibited as public package boundaries.

The largest nested areas are deliberately narrow:

```text
data/ingestion/                  pipeline, repair
data/lake/                       catalog, operations, store
data/matrix/                     refresh, store
data/pit/                        corporate actions, engine, readiness
data/quality/                    cross-source, lab, source validation
platform/governance/             approval, CI, network, readiness, release
platform/observability/          dashboard, monitoring
```

`platform/governance/network` is the only retained formal network-authority implementation. Earlier Task 055 generations and the historical `_internal` tree are deleted. Provider response truth lives in `data/pit`; valuation, fees, and ledger simulation live in `portfolio/simulator`.

The repository also enforces one owner per high-risk capability: `FormulaBatchEvaluator`, `data.pit.truth`, `portfolio.simulator.fees`, the production firewall sentinel, and `platform.artifacts.storage`. Reintroducing their deleted predecessor modules fails the layout audit.

## CLI

Use one command surface:

```bash
auto-alpha list
auto-alpha data validate --help
auto-alpha data freeze --help
auto-alpha research alpha --help
auto-alpha validation run --help
auto-alpha portfolio research --help
auto-alpha platform schema --help
```

Without installation:

```bash
PYTHONPATH=src:. python -m auto_alpha list
```

The internal `run_*.py` modules are implementation details. New documentation, automation, and operator runbooks must use `auto-alpha <domain> <command>`.

## Safety

- Missing lineage, validity, target availability, PIT proof, or execution evidence blocks the affected workflow.
- Strict research forbids JSONL materialization, CPU fallback, sample profiles, and non-PIT universe fallback.
- Research, validation, certification, portfolio, paper, and live states are separate.
- Retrospective or reused evidence never becomes untouched holdout evidence through metadata.
- Credentials, real market data, NPY tensors, caches, checkpoints, and server paths never enter Git.

## Development

```bash
uv sync
uv run pytest
uv run auto-alpha platform ci --full --output-dir /tmp/auto-alpha-ci --pretty
uv build
```

Architecture rules are enforced by:

```bash
python -m dev_tools.repository_layout audit
```

The audit enforces ceilings of 55 package directories, 450 Python source files, and four committed evidence files. It also rejects nested micro-packages in `research`, `validation`, `portfolio`, and `execution`.

See `docs/ARCHITECTURE.md` for ownership and dependency rules. Every meaningful change must update `FRAMEWORK_UPDATE.md`.

# Auto-alpha

Auto-alpha is a fail-closed A-share factor research platform. It owns governed data, PIT-safe research, out-of-sample validation, portfolio simulation, execution evidence, and platform operations.

## Structure

Production code has one public package and six domains:

```text
src/auto_alpha/
├── data/          ingestion, lake, PIT, universe, matrix, quality
├── research/      features, formulas, factors, discovery, neural search
├── validation/    firewall, walk-forward lab, certification
├── portfolio/     construction, risk, event-ledger simulation
├── execution/     broker, trading, settlement, operations
└── platform/      artifacts, compute, governance, observability, network authority

tests/
├── data/
├── research/
├── validation/
├── portfolio/
├── execution/
└── platform/
```

There are 25 visible subsystems and 57 Python package directories in total. `research`, `validation`, `portfolio`, and `execution` are flat below their subsystem boundary: implementation files use capability prefixes instead of creating one package per runner, store, or report. Task numbers, campaign names, dates, and machine paths are prohibited as public package boundaries.

The largest nested areas are deliberately narrow:

```text
data/ingestion/                  pipeline, repair
data/lake/                       catalog, operations, store
platform/observability/          dashboard, monitoring
platform/network_authority/      one flat production implementation
```

`platform/network_authority` is the only retained formal network-authority implementation. Earlier Task 055 generations and the historical `_internal` tree are deleted. Provider response truth lives in `data/pit`; valuation, fees, and ledger simulation live in `portfolio/simulation` rather than inside platform infrastructure.

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

The audit enforces a hard ceiling of 65 package directories and rejects nested micro-packages in `research`, `validation`, `portfolio`, and `execution`.

See `docs/ARCHITECTURE.md` for ownership and dependency rules. Every meaningful change must update `FRAMEWORK_UPDATE.md`.

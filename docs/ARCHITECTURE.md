# Architecture

## Contract

The repository exposes one production package, six domains, and 25 visible subsystems.

```text
auto_alpha
├── data
│   ├── ingestion
│   ├── lake
│   ├── matrix
│   ├── pit
│   └── quality
├── research
│   ├── discovery
│   ├── factors
│   ├── features
│   ├── formulas
│   └── neural
├── validation
│   ├── certification
│   ├── firewall
│   └── lab
├── portfolio
│   ├── construction
│   ├── risk
│   └── simulation
├── execution
│   ├── broker
│   ├── operations
│   ├── settlement
│   └── trading
└── platform
    ├── artifacts
    ├── compute
    ├── governance
    ├── network_authority
    └── observability
```

## Ownership

| Domain | Owns | Must not own |
| --- | --- | --- |
| `data` | source contracts, immutable freezes, PIT availability, universe, matrices | factor admission or portfolio state |
| `research` | feature semantics, StackVM, formulas, factor identity, search | holdout access or certification |
| `validation` | eligibility, firewall, walk-forward, red-team, certification | formula mutation or portfolio optimization |
| `portfolio` | certified-factor combination, risk, capacity, ledger simulation | factor generation or broker connectivity |
| `execution` | orders, fills, settlement, reconciliation, paper/shadow operations | research scoring |
| `platform` | schemas, compute, monitoring, release, approvals, network authority | market or factor semantics |

## Dependency direction

```text
data → research → validation → portfolio → execution
  └──────────────── platform services ────────────────┘
```

Dependencies may point left-to-right through the research lifecycle. `platform` provides infrastructure but cannot redefine domain truth. Reverse dependencies require an explicit interface and must not import higher-level mutable state.

## Public surface

- Operators use `auto-alpha <domain> <command>`.
- Python callers import through `auto_alpha.<domain>...`.
- Internal `run_*.py` files and packages named `_internal` are not public capabilities.
- A new top-level domain or visible subsystem requires an architecture decision; normal work extends an existing subsystem.

## Network authority

`auto_alpha.platform.network_authority` is the single formal implementation. Earlier Task 055 generations were removed as public packages. The private `_internal` tree contains only contracts still consumed by the final implementation and cannot be invoked through the unified CLI.

## Enforcement

`dev_tools.repository_layout` and `tests/platform/test_repository_layout.py` fail when:

- a peer package reappears beside `src/auto_alpha`;
- the six-domain or 25-subsystem set drifts;
- a removed top-level import or task package returns;
- tests stop mirroring the six domains;
- a registered unified CLI command cannot resolve.

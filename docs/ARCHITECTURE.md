# Architecture

## Repository Shape

Auto-alpha exposes one production package, six domains, and 23 visible subsystems.

```text
auto_alpha/
├── data/
│   ├── ingestion/
│   ├── lake/
│   ├── matrix/
│   ├── pit/
│   ├── quality/
│   └── universe/
├── research/
│   ├── factors/
│   ├── features/
│   ├── formulas/
│   └── search/
├── validation/
│   ├── certification/
│   ├── firewall/
│   └── walk_forward/
├── portfolio/
│   ├── construction/
│   ├── risk/
│   └── simulator/
├── execution/
│   ├── broker/
│   ├── settlement/
│   └── trading/
└── platform/
    ├── artifacts/
    ├── compute/
    ├── governance/
    └── observability/
```

The tree contains 54 Python package directories and 440 Python source files. Hard ceilings are 55 and 450. Committed evidence remains capped at four current files.

## File Convention

A visible subsystem owns cohesive capability files rather than one file per model, report, runner, and store. Public examples are:

- `execution/broker/{adapter,file_gateway,mapping,statements}.py`
- `execution/trading/{engine,plan,paper,shadow,strategy,daily,handoff}.py`
- `research/formulas/{evaluator,corpus,semantics,operators,vm,data_loader,engine}.py`
- `research/search/{models,generation,evaluation,workflow,experiments,formulas,neural}.py`
- `portfolio/construction/{campaigns,certification,lab,optimizer,research}.py`
- `portfolio/risk/{controls,model}.py`
- `portfolio/simulator/{backtest,capacity,fees,valuation,ledger}.py`

Files named after task numbers, dates, campaigns, or historical implementation generations are prohibited. `__init__.py` marks a boundary; it is not a compatibility facade.

## Ownership

| Domain | Owns | Must not own |
| --- | --- | --- |
| `data` | source contracts, immutable freezes, PIT availability, universe, matrices | factor admission or portfolio state |
| `research` | feature semantics, StackVM, formulas, factor identity, search | holdout access or certification |
| `validation` | eligibility, firewall, walk-forward, red-team, certification | formula mutation or portfolio optimization |
| `portfolio` | certified-factor combination, risk, capacity, ledger simulation | factor generation or broker connectivity |
| `execution` | orders, fills, settlement, reconciliation, paper/shadow operations | research scoring |
| `platform` | schemas, compute, monitoring, release, approvals, network authority | market or factor semantics |

## Dependency Direction

```text
data → research → validation → portfolio → execution
  └──────────────── platform services ────────────────┘
```

Dependencies follow the research lifecycle. Platform services may support every domain but cannot redefine domain truth. Reverse dependencies require an explicit immutable contract.

The fixed-factor development replay is a portfolio-owned diagnostic
orchestrator. It consumes the data-owned validated local-bundle loader, the
research-owned StackVM, and the portfolio-owned event ledger, but it creates no
Research Campaign, Factor Record, candidate, or downstream lifecycle state.

## Nested Infrastructure

Research, validation, portfolio, and execution remain flat below their visible subsystem boundary. Data and platform may use nested packages only for real adapter or infrastructure boundaries:

- `data.ingestion`: `pipeline`, `repair`
- `data.lake`: `catalog`, `operations`, `store`
- `data.matrix`: `refresh`, `store`
- `data.pit`: `corporate_actions`, `engine`, `readiness`
- `data.quality`: `cross_source`, `lab`, `source_validation`
- `platform.artifacts`: `schema`
- `platform.compute`: `scheduler`
- `platform.governance`: `approval`, `ci`, `network`, `readiness`, `release`
- `platform.observability`: `dashboard`, `monitoring`

`platform.governance.network` is the sole network-authority implementation. Data truth belongs to `data.pit`; fee, valuation, causal, and ledger semantics belong to `portfolio.simulator`.

## Public Surface

- Operators use `auto-alpha <domain> <command>`.
- The first vertical diagnostic is exposed as
  `auto-alpha portfolio fixed-replay {build,validate}`.
- Python callers import canonical capability modules directly.
- Internal module names are not operator contracts.
- A new domain or visible subsystem requires an architecture decision.
- Tests mirror the six domains; integration behavior is identified by test scope rather than another package tree.

## Capability Owners

| Capability | Sole implementation |
| --- | --- |
| Formula batch evaluation | `research/formulas/evaluator.py` |
| Formula candidate requests | `research/formulas/candidates.py` |
| Composite factors | `research/factors/composite.py` |
| Security-date truth | `data/pit/truth.py` |
| Offline local-data rehabilitation | `data/lake/store/local_development_bundle.py` |
| Local development bundle loading | `data/lake/store/local_development_bundle.py` |
| Fixed-factor development replay evidence | `portfolio/simulator/fixed_factor_replay.py` |
| Fee workflow and calculator | `portfolio/simulator/fees.py` |
| Production firewall sentinel | `validation/firewall/production_sentinel_sentinel.py` |
| Immutable generation storage | `platform/artifacts/storage.py` |
| Network transport authority | `platform/governance/network/gateway.py` |

Historical Task054/055 artifacts are read-only inputs. Their old producers, synthetic workflows, compatibility modules, and task-number packages are not production capabilities.

`fixed_factor_replay_evidence` is non-admissible development evidence. It is
neither a Data Admission Verdict nor a promotable Research Evidence Envelope.

## Enforcement

`dev_tools.repository_layout` and `tests/platform/test_repository_layout.py` reject:

- packages beside `src/auto_alpha`;
- drift from the six domains or 23 subsystem set;
- nested micro-packages below research, validation, portfolio, or execution;
- missing capability owners or reintroduced predecessor modules;
- more than 55 package directories, 450 Python files, or four evidence files;
- legacy peer packages, task-number packages, or obsolete imports;
- a unified CLI command whose canonical module cannot resolve.

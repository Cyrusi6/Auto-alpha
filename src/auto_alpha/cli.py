"""Unified command surface for the Auto-alpha platform."""

from __future__ import annotations

import importlib
import json
import sys
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class CommandSpec:
    module: str
    summary: str


COMMANDS = {
    ("data", "sync"): CommandSpec("auto_alpha.data.ingestion.pipeline.run_pipeline", "Synchronize governed A-share data"),
    ("data", "validate"): CommandSpec("auto_alpha.data.quality.source_validation.run_smoke", "Validate a governed data source"),
    ("data", "land"): CommandSpec("auto_alpha.data.lake.catalog.run_landing", "Publish raw landing evidence"),
    ("data", "index"): CommandSpec("auto_alpha.data.lake.catalog.run_index", "Build the raw-data index"),
    ("data", "backfill"): CommandSpec("auto_alpha.data.ingestion.repair.run_backfill", "Run governed backfill"),
    ("data", "observe"): CommandSpec("auto_alpha.platform.observability.monitoring.run_backfill_observer", "Observe backfill progress"),
    ("data", "repair"): CommandSpec("auto_alpha.data.ingestion.repair.run_repair", "Apply a governed repair plan"),
    ("data", "post-download"): CommandSpec("auto_alpha.data.ingestion.repair.run_post_download", "Validate and publish downloaded data"),
    ("data", "lake"): CommandSpec("auto_alpha.data.lake.store.run_lake", "Manage immutable lake generations"),
    ("data", "freeze"): CommandSpec(
        "auto_alpha.data.lake.store.run_source_freeze",
        "Build a Source Freeze and verify data admission",
    ),
    ("data", "local-bundle"): CommandSpec(
        "auto_alpha.data.lake.store.run_local_development_bundle",
        "Build or validate an offline development-replay bundle",
    ),
    ("data", "operate"): CommandSpec("auto_alpha.data.lake.operations.run_real_data", "Run governed real-data operations"),
    ("data", "quality"): CommandSpec("auto_alpha.data.quality.lab.run_quality_lab", "Run data-quality diagnostics"),
    ("data", "compare"): CommandSpec("auto_alpha.data.quality.cross_source.run_compare", "Compare governed sources"),
    ("data", "pit"): CommandSpec("auto_alpha.data.pit.engine.run_pit", "Validate point-in-time contracts"),
    ("data", "universe"): CommandSpec("auto_alpha.data.universe.run_universe", "Build the historical universe"),
    ("data", "actions"): CommandSpec("auto_alpha.data.pit.corporate_actions.run_actions", "Normalize corporate actions"),
    ("data", "readiness"): CommandSpec("auto_alpha.data.pit.readiness.run_readiness", "Assess research-data readiness"),
    ("data", "matrix-build"): CommandSpec("auto_alpha.data.matrix.store.run_build_matrix", "Build a strict PIT matrix"),
    ("data", "matrix-refresh"): CommandSpec("auto_alpha.data.matrix.refresh.run_matrix_refresh", "Refresh a strict matrix"),
    ("research", "features"): CommandSpec("auto_alpha.research.features.factory", "Build feature values and validity"),
    ("research", "promote-features"): CommandSpec("auto_alpha.research.features.promotion", "Apply feature promotion policy"),
    ("research", "formula-search"): CommandSpec("auto_alpha.research.search.formulas", "Search canonical formulas"),
    ("research", "formula-batch"): CommandSpec("auto_alpha.research.formulas.evaluator", "Evaluate a formula batch"),
    ("research", "formula-corpus"): CommandSpec("auto_alpha.research.formulas.corpus", "Build the formula corpus"),
    ("research", "registry"): CommandSpec("auto_alpha.research.factors.registry", "Inspect the model registry"),
    ("research", "alpha"): CommandSpec("auto_alpha.research.search.workflow", "Run governed Alpha Factory research"),
    ("research", "experiments"): CommandSpec("auto_alpha.research.search.experiments", "Manage research experiments"),
    ("research", "neural-search"): CommandSpec("auto_alpha.research.search.neural_cli", "Run neural-guided search"),
    ("research", "neural-pretrain"): CommandSpec("auto_alpha.research.search.pretrain_cli", "Pretrain the formula model"),
    ("research", "runtime"): CommandSpec("auto_alpha.research.formulas.engine", "Run the formula research runtime"),
    ("validation", "firewall"): CommandSpec("auto_alpha.validation.firewall.leakage_run_audit", "Audit PIT and research leakage"),
    ("validation", "run"): CommandSpec("auto_alpha.validation.walk_forward.engine_run_validation", "Run walk-forward validation"),
    ("validation", "red-team"): CommandSpec("auto_alpha.validation.walk_forward.red_team_run_holdout", "Run sealed holdout red-team validation"),
    ("validation", "campaign"): CommandSpec("auto_alpha.validation.walk_forward.campaigns_run_validation_store", "Manage validation campaigns"),
    ("validation", "certify-factor"): CommandSpec("auto_alpha.validation.certification.factors_run_certify", "Certify an eligible factor"),
    ("validation", "certification-campaign"): CommandSpec("auto_alpha.validation.certification.campaigns_run_certification_campaign", "Manage certification campaigns"),
    ("portfolio", "research"): CommandSpec("auto_alpha.portfolio.construction.research", "Run factor-certified portfolio research"),
    ("portfolio", "optimize"): CommandSpec("auto_alpha.portfolio.construction.optimizer", "Optimize a governed portfolio"),
    ("portfolio", "lab"): CommandSpec("auto_alpha.portfolio.construction.lab", "Run portfolio diagnostics"),
    ("portfolio", "campaign"): CommandSpec("auto_alpha.portfolio.construction.campaigns", "Manage portfolio campaigns"),
    ("portfolio", "certify"): CommandSpec("auto_alpha.portfolio.construction.certification", "Certify an eligible portfolio"),
    ("portfolio", "backtest"): CommandSpec("auto_alpha.portfolio.simulator.backtest", "Run event-ledger backtests"),
    ("portfolio", "fixed-replay"): CommandSpec(
        "auto_alpha.portfolio.simulator.fixed_factor_replay",
        "Run the locked development-only factor replay",
    ),
    ("portfolio", "capacity"): CommandSpec("auto_alpha.portfolio.simulator.capacity", "Estimate modeled capacity"),
    ("portfolio", "risk"): CommandSpec("auto_alpha.portfolio.risk.controls", "Run portfolio risk controls"),
    ("execution", "broker"): CommandSpec("auto_alpha.execution.broker.adapter", "Run the broker adapter"),
    ("execution", "file-gateway"): CommandSpec("auto_alpha.execution.broker.file_gateway", "Run the broker file gateway"),
    ("execution", "mapping"): CommandSpec("auto_alpha.execution.broker.mapping", "Validate broker mappings"),
    ("execution", "statements"): CommandSpec("auto_alpha.execution.broker.statements", "Import broker statements"),
    ("execution", "plan"): CommandSpec("auto_alpha.execution.trading.plan", "Build an execution plan"),
    ("execution", "paper-account"): CommandSpec("auto_alpha.execution.trading.paper", "Manage the paper account"),
    ("execution", "shadow"): CommandSpec("auto_alpha.execution.trading.shadow", "Run shadow execution"),
    ("execution", "daily"): CommandSpec("auto_alpha.execution.trading.daily", "Run daily operations"),
    ("execution", "handoff"): CommandSpec("auto_alpha.execution.trading.handoff", "Build operator handoff"),
    ("execution", "settle"): CommandSpec("auto_alpha.execution.settlement.engine", "Run settlement"),
    ("execution", "reconcile"): CommandSpec("auto_alpha.execution.settlement.reconciliation", "Reconcile accounts"),
    ("execution", "compliance"): CommandSpec("auto_alpha.execution.settlement.compliance", "Run trading compliance checks"),
    ("platform", "schema"): CommandSpec("auto_alpha.platform.artifacts.schema.run_validate", "Validate artifact schemas"),
    ("platform", "compute"): CommandSpec("auto_alpha.platform.compute.scheduler.run_compute", "Manage compute jobs"),
    ("platform", "approve"): CommandSpec("auto_alpha.platform.governance.approval.run_approval", "Manage approvals"),
    ("platform", "readiness"): CommandSpec("auto_alpha.platform.governance.readiness.run_readiness", "Assess operational readiness"),
    ("platform", "release"): CommandSpec("auto_alpha.platform.governance.release.run_release", "Build a release report"),
    ("platform", "ci"): CommandSpec("auto_alpha.platform.governance.ci.run_local_ci", "Run local CI"),
    ("platform", "monitor"): CommandSpec("auto_alpha.platform.observability.monitoring.run_monitor", "Run monitoring checks"),
    ("platform", "network-authority"): CommandSpec("auto_alpha.platform.governance.network.run", "Verify network authority evidence"),
    ("platform", "network-canary"): CommandSpec("auto_alpha.platform.governance.network.network_cli", "Execute the sealed single-canary boundary"),
    ("platform", "network-apply"): CommandSpec("auto_alpha.platform.governance.network.application_cli", "Apply an accepted canary response"),
}


MODULE_COMMANDS = {spec.module: key for key, spec in COMMANDS.items()}


def normalize_python_module_command(command: Sequence[str]) -> list[str]:
    normalized = list(command)
    if len(normalized) < 3 or normalized[1] != "-m":
        return normalized
    key = MODULE_COMMANDS.get(normalized[2])
    if key is None:
        return normalized
    return [normalized[0], "-m", "auto_alpha", key[0], key[1], *normalized[3:]]


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(argv if argv is not None else sys.argv[1:])
    if not arguments or arguments[0] in {"-h", "--help", "help", "list"}:
        _print_help(arguments[1] if len(arguments) > 1 else None)
        return 0
    if len(arguments) == 1:
        _print_help(arguments[0])
        return 0
    key = (arguments[0], arguments[1])
    spec = COMMANDS.get(key)
    if spec is None:
        print(f"unknown auto-alpha command: {' '.join(key)}", file=sys.stderr)
        _print_help(arguments[0])
        return 2
    module = importlib.import_module(spec.module)
    entrypoint = getattr(module, "main", None)
    if entrypoint is None:
        raise RuntimeError(f"command module has no main(): {spec.module}")
    result = entrypoint(arguments[2:])
    return int(result or 0)


def _print_help(domain: str | None) -> None:
    rows = [
        {"domain": key[0], "command": key[1], "summary": spec.summary}
        for key, spec in sorted(COMMANDS.items())
        if domain is None or key[0] == domain
    ]
    if domain is not None and not rows:
        print(f"unknown domain: {domain}", file=sys.stderr)
        return
    print("Usage: auto-alpha <domain> <command> [arguments]")
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())

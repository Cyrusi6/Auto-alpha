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
    ("data", "freeze"): CommandSpec("auto_alpha.data.lake.store.run_canonical_freeze", "Build the canonical research freeze"),
    ("data", "operate"): CommandSpec("auto_alpha.data.lake.operations.run_real_data", "Run governed real-data operations"),
    ("data", "quality"): CommandSpec("auto_alpha.data.quality.lab.run_quality_lab", "Run data-quality diagnostics"),
    ("data", "compare"): CommandSpec("auto_alpha.data.quality.cross_source.run_compare", "Compare governed sources"),
    ("data", "pit"): CommandSpec("auto_alpha.data.pit.engine.run_pit", "Validate point-in-time contracts"),
    ("data", "universe"): CommandSpec("auto_alpha.data.pit.universe.run_universe", "Build the historical universe"),
    ("data", "actions"): CommandSpec("auto_alpha.data.pit.corporate_actions.run_actions", "Normalize corporate actions"),
    ("data", "readiness"): CommandSpec("auto_alpha.data.pit.readiness.run_readiness", "Assess research-data readiness"),
    ("data", "matrix-build"): CommandSpec("auto_alpha.data.matrix.store.run_build_matrix", "Build a strict PIT matrix"),
    ("data", "matrix-refresh"): CommandSpec("auto_alpha.data.matrix.refresh.run_matrix_refresh", "Refresh a strict matrix"),
    ("research", "features"): CommandSpec("auto_alpha.research.features.factory_run_features", "Build feature values and validity"),
    ("research", "promote-features"): CommandSpec("auto_alpha.research.features.promotion_run_promotion", "Apply feature promotion policy"),
    ("research", "formula-search"): CommandSpec("auto_alpha.research.formulas.search_run_search", "Search canonical formulas"),
    ("research", "formula-batch"): CommandSpec("auto_alpha.research.formulas.batch_run_batch_eval", "Evaluate a formula batch"),
    ("research", "formula-corpus"): CommandSpec("auto_alpha.research.formulas.corpus_run_corpus", "Build the formula corpus"),
    ("research", "registry"): CommandSpec("auto_alpha.research.factors.registry_run_registry", "Inspect the model registry"),
    ("research", "alpha"): CommandSpec("auto_alpha.research.discovery.factory_run_factory", "Run governed Alpha Factory research"),
    ("research", "experiments"): CommandSpec("auto_alpha.research.discovery.experiments_run_store", "Manage research experiments"),
    ("research", "neural-search"): CommandSpec("auto_alpha.research.neural.search_run_neural_search", "Run neural-guided search"),
    ("research", "neural-pretrain"): CommandSpec("auto_alpha.research.neural.search_run_pretrain", "Pretrain the formula model"),
    ("research", "runtime"): CommandSpec("auto_alpha.research.formulas.runtime_engine", "Run the formula research runtime"),
    ("validation", "firewall"): CommandSpec("auto_alpha.validation.firewall.leakage_run_audit", "Audit PIT and research leakage"),
    ("validation", "run"): CommandSpec("auto_alpha.validation.lab.engine_run_validation", "Run walk-forward validation"),
    ("validation", "red-team"): CommandSpec("auto_alpha.validation.lab.red_team_run_holdout", "Run sealed holdout red-team validation"),
    ("validation", "campaign"): CommandSpec("auto_alpha.validation.lab.campaigns_run_validation_store", "Manage validation campaigns"),
    ("validation", "certify-factor"): CommandSpec("auto_alpha.validation.certification.factors_run_certify", "Certify an eligible factor"),
    ("validation", "certification-campaign"): CommandSpec("auto_alpha.validation.certification.campaigns_run_certification_campaign", "Manage certification campaigns"),
    ("portfolio", "research"): CommandSpec("auto_alpha.portfolio.construction.research_run_portfolio_research", "Run factor-certified portfolio research"),
    ("portfolio", "optimize"): CommandSpec("auto_alpha.portfolio.construction.optimizer_run_optimize", "Optimize a governed portfolio"),
    ("portfolio", "lab"): CommandSpec("auto_alpha.portfolio.construction.lab_run_portfolio_lab", "Run portfolio diagnostics"),
    ("portfolio", "campaign"): CommandSpec("auto_alpha.portfolio.construction.campaigns_run_portfolio_campaign", "Manage portfolio campaigns"),
    ("portfolio", "certify"): CommandSpec("auto_alpha.portfolio.construction.certification_run_portfolio_certify", "Certify an eligible portfolio"),
    ("portfolio", "backtest"): CommandSpec("auto_alpha.portfolio.simulation.backtest_run_backtest", "Run event-ledger backtests"),
    ("portfolio", "capacity"): CommandSpec("auto_alpha.portfolio.simulation.capacity_run_capacity", "Estimate modeled capacity"),
    ("portfolio", "risk"): CommandSpec("auto_alpha.portfolio.risk.controls_run_controls", "Run portfolio risk controls"),
    ("execution", "broker"): CommandSpec("auto_alpha.execution.broker.adapter_run_broker", "Run the broker adapter"),
    ("execution", "file-gateway"): CommandSpec("auto_alpha.execution.broker.file_gateway_run_gateway", "Run the broker file gateway"),
    ("execution", "mapping"): CommandSpec("auto_alpha.execution.broker.mapping_run_mapping_certify", "Validate broker mappings"),
    ("execution", "statements"): CommandSpec("auto_alpha.execution.broker.statements_run_statement", "Import broker statements"),
    ("execution", "plan"): CommandSpec("auto_alpha.execution.trading.plan_run_plan", "Build an execution plan"),
    ("execution", "paper-account"): CommandSpec("auto_alpha.execution.trading.paper_run_account", "Manage the paper account"),
    ("execution", "shadow"): CommandSpec("auto_alpha.execution.trading.shadow_run_shadow", "Run shadow execution"),
    ("execution", "daily"): CommandSpec("auto_alpha.execution.operations.daily_run_daily", "Run daily operations"),
    ("execution", "handoff"): CommandSpec("auto_alpha.execution.operations.handoff_run_handoff", "Build operator handoff"),
    ("execution", "settle"): CommandSpec("auto_alpha.execution.settlement.engine_run_settlement", "Run settlement"),
    ("execution", "reconcile"): CommandSpec("auto_alpha.execution.settlement.reconciliation_run_reconcile", "Reconcile accounts"),
    ("execution", "compliance"): CommandSpec("auto_alpha.execution.settlement.compliance_run_compliance", "Run trading compliance checks"),
    ("platform", "schema"): CommandSpec("auto_alpha.platform.artifacts.schema.run_validate", "Validate artifact schemas"),
    ("platform", "compute"): CommandSpec("auto_alpha.platform.compute.scheduler.run_compute", "Manage compute jobs"),
    ("platform", "approve"): CommandSpec("auto_alpha.platform.governance.approval.run_approval", "Manage approvals"),
    ("platform", "readiness"): CommandSpec("auto_alpha.platform.governance.readiness.run_readiness", "Assess operational readiness"),
    ("platform", "release"): CommandSpec("auto_alpha.platform.governance.release.run_release", "Build a release report"),
    ("platform", "ci"): CommandSpec("auto_alpha.platform.governance.ci.run_local_ci", "Run local CI"),
    ("platform", "monitor"): CommandSpec("auto_alpha.platform.observability.monitoring.run_monitor", "Run monitoring checks"),
    ("platform", "network-authority"): CommandSpec("auto_alpha.platform.network_authority.run", "Verify network authority evidence"),
    ("platform", "network-canary"): CommandSpec("auto_alpha.platform.network_authority.network_cli", "Execute the sealed single-canary boundary"),
    ("platform", "network-apply"): CommandSpec("auto_alpha.platform.network_authority.application_cli", "Apply an accepted canary response"),
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

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
    ("data", "land"): CommandSpec("auto_alpha.data.ingestion.landing.run_landing", "Publish raw landing evidence"),
    ("data", "index"): CommandSpec("auto_alpha.data.ingestion.index.run_index", "Build the raw-data index"),
    ("data", "backfill"): CommandSpec("auto_alpha.data.ingestion.backfill.run_backfill", "Run governed backfill"),
    ("data", "observe"): CommandSpec("auto_alpha.data.ingestion.observer.run_observer", "Observe backfill progress"),
    ("data", "repair"): CommandSpec("auto_alpha.data.ingestion.repair.run_repair", "Apply a governed repair plan"),
    ("data", "post-download"): CommandSpec("auto_alpha.data.ingestion.post_download.run_post_download", "Validate and publish downloaded data"),
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
    ("research", "features"): CommandSpec("auto_alpha.research.features.factory.run_features", "Build feature values and validity"),
    ("research", "promote-features"): CommandSpec("auto_alpha.research.features.promotion.run_promotion", "Apply feature promotion policy"),
    ("research", "formula-search"): CommandSpec("auto_alpha.research.formulas.search.run_search", "Search canonical formulas"),
    ("research", "formula-batch"): CommandSpec("auto_alpha.research.formulas.batch.run_batch_eval", "Evaluate a formula batch"),
    ("research", "formula-corpus"): CommandSpec("auto_alpha.research.formulas.corpus.run_corpus", "Build the formula corpus"),
    ("research", "factor-lifecycle"): CommandSpec("auto_alpha.research.factors.lifecycle.run_lifecycle", "Manage factor lifecycle"),
    ("research", "registry"): CommandSpec("auto_alpha.research.factors.registry.run_registry", "Inspect the model registry"),
    ("research", "alpha"): CommandSpec("auto_alpha.research.discovery.factory.run_factory", "Run governed Alpha Factory research"),
    ("research", "experiments"): CommandSpec("auto_alpha.research.discovery.experiments.run_store", "Manage research experiments"),
    ("research", "orchestrate"): CommandSpec("auto_alpha.research.discovery.orchestrator.run_experiment", "Orchestrate research shards"),
    ("research", "batch"): CommandSpec("auto_alpha.research.discovery.studies.run_batch", "Run a research batch"),
    ("research", "suite"): CommandSpec("auto_alpha.research.discovery.suite.run_suite", "Run the research suite"),
    ("research", "benchmark"): CommandSpec("auto_alpha.research.discovery.benchmark.run_benchmark", "Benchmark research throughput"),
    ("research", "neural-search"): CommandSpec("auto_alpha.research.neural.search.run_neural_search", "Run neural-guided search"),
    ("research", "neural-pretrain"): CommandSpec("auto_alpha.research.neural.search.run_pretrain", "Pretrain the formula model"),
    ("research", "runtime"): CommandSpec("auto_alpha.research.formulas.runtime.engine", "Run the formula research runtime"),
    ("validation", "firewall"): CommandSpec("auto_alpha.validation.firewall.leakage.run_audit", "Audit PIT and research leakage"),
    ("validation", "run"): CommandSpec("auto_alpha.validation.lab.engine.run_validation", "Run walk-forward validation"),
    ("validation", "red-team"): CommandSpec("auto_alpha.validation.lab.red_team.run_holdout", "Run sealed holdout red-team validation"),
    ("validation", "campaign"): CommandSpec("auto_alpha.validation.lab.campaigns.run_validation_store", "Manage validation campaigns"),
    ("validation", "certify-factor"): CommandSpec("auto_alpha.validation.certification.factors.run_certify", "Certify an eligible factor"),
    ("validation", "certification-campaign"): CommandSpec("auto_alpha.validation.certification.campaigns.run_certification_campaign", "Manage certification campaigns"),
    ("portfolio", "research"): CommandSpec("auto_alpha.portfolio.construction.research.run_portfolio_research", "Run factor-certified portfolio research"),
    ("portfolio", "optimize"): CommandSpec("auto_alpha.portfolio.construction.optimizer.run_optimize", "Optimize a governed portfolio"),
    ("portfolio", "lab"): CommandSpec("auto_alpha.portfolio.construction.lab.run_portfolio_lab", "Run portfolio diagnostics"),
    ("portfolio", "campaign"): CommandSpec("auto_alpha.portfolio.construction.campaigns.run_portfolio_campaign", "Manage portfolio campaigns"),
    ("portfolio", "certify"): CommandSpec("auto_alpha.portfolio.construction.certification.run_portfolio_certify", "Certify an eligible portfolio"),
    ("portfolio", "backtest"): CommandSpec("auto_alpha.portfolio.simulation.backtest.run_backtest", "Run event-ledger backtests"),
    ("portfolio", "capacity"): CommandSpec("auto_alpha.portfolio.simulation.capacity.run_capacity", "Estimate modeled capacity"),
    ("portfolio", "risk"): CommandSpec("auto_alpha.portfolio.risk.controls.run_controls", "Run portfolio risk controls"),
    ("execution", "broker"): CommandSpec("auto_alpha.execution.broker.adapter.run_broker", "Run the broker adapter"),
    ("execution", "connectivity"): CommandSpec("auto_alpha.execution.broker.connectivity.run_connectivity", "Probe broker connectivity"),
    ("execution", "file-gateway"): CommandSpec("auto_alpha.execution.broker.file_gateway.run_gateway", "Run the broker file gateway"),
    ("execution", "mapping"): CommandSpec("auto_alpha.execution.broker.mapping.run_mapping_certify", "Validate broker mappings"),
    ("execution", "mirror"): CommandSpec("auto_alpha.execution.broker.mirror.run_readonly_mirror", "Build a read-only broker mirror"),
    ("execution", "statements"): CommandSpec("auto_alpha.execution.broker.statements.run_statement", "Import broker statements"),
    ("execution", "uat"): CommandSpec("auto_alpha.execution.broker.uat.run_uat", "Run broker UAT"),
    ("execution", "plan"): CommandSpec("auto_alpha.execution.trading.plan.run_plan", "Build an execution plan"),
    ("execution", "paper-account"): CommandSpec("auto_alpha.execution.trading.paper.run_account", "Manage the paper account"),
    ("execution", "shadow"): CommandSpec("auto_alpha.execution.trading.shadow.run_shadow", "Run shadow execution"),
    ("execution", "daily"): CommandSpec("auto_alpha.execution.operations.daily.run_daily", "Run daily operations"),
    ("execution", "production-plan"): CommandSpec("auto_alpha.execution.operations.production.run_production", "Plan production operations"),
    ("execution", "replay"): CommandSpec("auto_alpha.execution.operations.replay.run_replay", "Replay production evidence"),
    ("execution", "shadow-lab"): CommandSpec("auto_alpha.execution.operations.shadow_lab.run_shadow_lab", "Run shadow diagnostics"),
    ("execution", "handoff"): CommandSpec("auto_alpha.execution.operations.handoff.run_handoff", "Build operator handoff"),
    ("execution", "incident"): CommandSpec("auto_alpha.execution.operations.incidents.run_incident", "Manage incidents"),
    ("execution", "go-live"): CommandSpec("auto_alpha.execution.operations.go_live.run_go_live", "Evaluate the go-live gate"),
    ("execution", "settle"): CommandSpec("auto_alpha.execution.settlement.engine.run_settlement", "Run settlement"),
    ("execution", "reconcile"): CommandSpec("auto_alpha.execution.settlement.reconciliation.run_reconcile", "Reconcile accounts"),
    ("execution", "compliance"): CommandSpec("auto_alpha.execution.settlement.compliance.run_compliance", "Run trading compliance checks"),
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

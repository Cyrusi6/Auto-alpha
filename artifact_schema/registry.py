"""Artifact schema registry for local platform outputs."""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path

from .models import ArtifactSchemaDefinition


def _definition(
    artifact_type: str,
    required: list[str],
    patterns: list[str],
    kind: str = "json",
    optional: list[str] | None = None,
    allow_empty: bool = True,
) -> ArtifactSchemaDefinition:
    return ArtifactSchemaDefinition(
        artifact_type=artifact_type,
        schema_version="1.0",
        required_fields=required,
        optional_fields=optional or [
            "artifact_type",
            "schema_version",
            "producer",
            "created_at",
            "artifact_metadata",
        ],
        file_patterns=patterns,
        json_or_jsonl=kind,
        allow_empty=allow_empty,
    )


ARTIFACT_SCHEMA_REGISTRY: dict[str, ArtifactSchemaDefinition] = {
    "research_suite_result": _definition("research_suite_result", ["suite_name", "status", "stages"], ["suite_result.json"]),
    "artifact_catalog": _definition("artifact_catalog", ["suite_name", "created_at", "entries"], ["artifact_catalog.json"]),
    "promotion_decision": _definition("promotion_decision", [], ["promotion_decision.json"]),
    "data_source_smoke_report": _definition("data_source_smoke_report", ["provider", "status", "datasets"], ["data_source_smoke_report.json"]),
    "provider_probe": _definition("provider_probe", ["probes"], ["provider_probe.json"]),
    "field_coverage": _definition("field_coverage", ["datasets"], ["field_coverage.json"]),
    "audit_summary": _definition("audit_summary", [], ["audit_summary.json"]),
    "incremental_recovery_report": _definition("incremental_recovery_report", [], ["incremental_recovery_report.json"]),
    "baseline_compare_summary": _definition("baseline_compare_summary", [], ["baseline_compare_summary.json"]),
    "dataset_contracts": _definition("dataset_contracts", ["datasets"], ["dataset_contracts.json"]),
    "capacity_report": _definition("capacity_report", ["trade_date", "config", "portfolio"], ["capacity_report.json"]),
    "execution_plan": _definition("execution_plan", ["schedule", "fills", "quality"], ["execution_plan.json"]),
    "execution_quality": _definition("execution_quality", ["execution_fill_rate"], ["execution_quality.json"]),
    "parent_orders": _definition("parent_orders", ["parent_order_id"], ["parent_orders.jsonl"], kind="jsonl", allow_empty=True),
    "child_orders": _definition("child_orders", ["child_order_id"], ["child_orders.jsonl"], kind="jsonl", allow_empty=True),
    "child_fills": _definition("child_fills", ["trade_date", "ts_code", "status"], ["child_fills.jsonl"], kind="jsonl", allow_empty=True),
    "broker_report": _definition("broker_report", ["batch_id", "summary"], ["broker_report.json"]),
    "broker_reconciliation": _definition("broker_reconciliation", [], ["broker_reconciliation.json"]),
    "broker_orders": _definition("broker_orders", ["client_order_id"], ["broker_orders.jsonl"], kind="jsonl", allow_empty=True),
    "broker_events": _definition("broker_events", ["event_id"], ["broker_events.jsonl"], kind="jsonl", allow_empty=True),
    "broker_fills": _definition("broker_fills", ["broker_fill_id"], ["broker_fills.jsonl"], kind="jsonl", allow_empty=True),
    "broker_instruction_manifest": _definition("broker_instruction_manifest", ["batch_id"], ["broker_instruction_manifest.json"]),
    "monitoring_report": _definition("monitoring_report", ["as_of_date", "checks", "alerts"], ["monitoring_report.json"]),
    "alerts": _definition("alerts", ["severity", "check", "message"], ["alerts.jsonl"], kind="jsonl", allow_empty=True),
    "risk_model_report": _definition("risk_model_report", [], ["risk_model_report.json"]),
    "risk_report": _definition("risk_report", [], ["risk_report.json"]),
    "risk_exposures": _definition("risk_exposures", ["trade_date"], ["risk_exposures.jsonl"], kind="jsonl", allow_empty=True),
    "risk_decomposition": _definition("risk_decomposition", ["trade_date"], ["risk_decomposition.jsonl"], kind="jsonl", allow_empty=True),
    "return_attribution": _definition("return_attribution", ["trade_date"], ["return_attribution.jsonl"], kind="jsonl", allow_empty=True),
    "optimization_result": _definition("optimization_result", [], ["optimization_result.json"]),
    "backtest_result": _definition("backtest_result", ["snapshots", "fills", "metrics"], ["backtest_result.json"]),
    "orders": _definition("orders", ["trade_date", "ts_code", "side"], ["orders.jsonl"], kind="jsonl", allow_empty=True),
    "paper_fills": _definition("paper_fills", ["trade_date", "ts_code", "status"], ["paper_fills.jsonl"], kind="jsonl", allow_empty=True),
    "production_run": _definition("production_run", ["run_id", "status", "summary"], ["production_run.json"]),
    "approval_batch": _definition("approval_batch", ["approval_id", "status", "orders"], ["approvals/*.json", "*.approval.json"]),
    "paper_account_state": _definition("paper_account_state", ["account_id", "cash", "positions"], ["account_state.json"]),
    "artifact_schema_manifest": _definition("artifact_schema_manifest", ["entries"], ["artifact_schema_manifest.json"]),
    "artifact_validation_report": _definition("artifact_validation_report", ["results"], ["artifact_validation_report.json"]),
    "release_manifest": _definition("release_manifest", ["release_name", "created_at"], ["release_manifest.json"]),
    "release_gate_report": _definition("release_gate_report", ["checks"], ["release_gate_report.json"]),
    "dependency_inventory": _definition("dependency_inventory", ["files"], ["dependency_inventory.json"]),
    "module_inventory": _definition("module_inventory", ["modules"], ["module_inventory.json"]),
    "cli_inventory": _definition("cli_inventory", ["entries"], ["cli_inventory.json"]),
    "ci_report": _definition("ci_report", ["commands"], ["ci_report.json"]),
    "ci_command_results": _definition("ci_command_results", ["name", "returncode"], ["ci_command_results.jsonl"], kind="jsonl", allow_empty=True),
    "formula_corpus": _definition("formula_corpus", ["formula_hash", "formula_tokens"], ["formula_corpus.jsonl"], kind="jsonl", allow_empty=False),
    "formula_sequences": _definition("formula_sequences", ["formula_hash", "prefix_tokens", "target_token"], ["formula_sequences.jsonl"], kind="jsonl", allow_empty=False),
    "formula_preferences": _definition("formula_preferences", ["pair_id", "preferred_hash", "rejected_hash"], ["formula_preferences.jsonl"], kind="jsonl", allow_empty=True),
    "formula_corpus_stats": _definition("formula_corpus_stats", ["total_records", "valid_records"], ["formula_corpus_stats.json"]),
    "formula_corpus_build_result": _definition("formula_corpus_build_result", ["created_at", "stats", "paths"], ["formula_corpus_build_result.json"]),
    "formula_batch_eval_result": _definition("formula_batch_eval_result", ["batch_id", "results", "summary"], ["formula_batch_eval_result.json"]),
    "formula_eval_results": _definition("formula_eval_results", ["request", "status", "score"], ["formula_eval_results.jsonl"], kind="jsonl", allow_empty=True),
    "formula_eval_cache_manifest": _definition("formula_eval_cache_manifest", ["cache_dir", "enabled"], ["formula_eval_cache_manifest.json"]),
    "formula_batch_eval_benchmark": _definition("formula_batch_eval_benchmark", ["formulas_requested", "elapsed_seconds"], ["formula_batch_eval_benchmark.json"]),
    "alphagpt_pretrain_result": _definition("alphagpt_pretrain_result", ["status", "history", "checkpoint_manifest"], ["alphagpt_pretrain_result.json"]),
    "alphagpt_pretrain_history": _definition("alphagpt_pretrain_history", ["epoch", "loss"], ["alphagpt_pretrain_history.jsonl"], kind="jsonl", allow_empty=True),
    "alphagpt_checkpoint_manifest": _definition("alphagpt_checkpoint_manifest", ["latest_checkpoint_path", "checkpoints"], ["checkpoint_manifest.json"]),
    "model_versions": _definition("model_versions", ["model_version_id", "factor_id", "lifecycle_status"], ["model_versions.jsonl"], kind="jsonl", allow_empty=True),
    "model_deployments": _definition("model_deployments", ["deployment_id", "model_version_id", "status"], ["model_deployments.jsonl"], kind="jsonl", allow_empty=True),
    "lifecycle_events": _definition("lifecycle_events", ["event_id", "model_version_id", "action"], ["lifecycle_events.jsonl"], kind="jsonl", allow_empty=True),
    "model_registry_manifest": _definition("model_registry_manifest", ["model_versions", "deployments", "events"], ["model_registry_manifest.json"]),
    "model_registry_report": _definition("model_registry_report", ["manifest", "active_models", "deployments"], ["model_registry_report.json"]),
    "model_lineage_graph": _definition("model_lineage_graph", ["nodes", "edges"], ["model_lineage_graph.json"]),
    "factor_lifecycle_report": _definition("factor_lifecycle_report", ["evaluation"], ["factor_lifecycle_report.json"]),
    "factor_health_checks": _definition("factor_health_checks", ["name", "severity", "passed"], ["factor_health_checks.jsonl"], kind="jsonl", allow_empty=True),
    "lifecycle_decisions": _definition("lifecycle_decisions", ["factor_id", "recommended_action"], ["lifecycle_decisions.jsonl"], kind="jsonl", allow_empty=True),
    "model_review_package": _definition("model_review_package", ["factor_id", "lifecycle_status", "health_checks"], ["model_review_package.json"]),
}


def get_registry() -> dict[str, ArtifactSchemaDefinition]:
    return dict(ARTIFACT_SCHEMA_REGISTRY)


def infer_artifact_type(path: str | Path, registry: dict[str, ArtifactSchemaDefinition] | None = None) -> str | None:
    candidate = Path(path)
    relative = str(candidate).replace("\\", "/")
    name = candidate.name
    for artifact_type, definition in (registry or ARTIFACT_SCHEMA_REGISTRY).items():
        for pattern in definition.file_patterns:
            if fnmatch(name, pattern) or fnmatch(relative, f"*/{pattern}") or fnmatch(relative, pattern):
                return artifact_type
    return None


def get_definition(artifact_type: str | None) -> ArtifactSchemaDefinition | None:
    if artifact_type is None:
        return None
    return ARTIFACT_SCHEMA_REGISTRY.get(artifact_type)

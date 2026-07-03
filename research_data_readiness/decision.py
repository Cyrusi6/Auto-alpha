"""Research data readiness decision logic."""

from __future__ import annotations

from .models import DatasetReadinessCheck, DatasetResearchTier, FeatureFamilyReadiness, FeatureReadinessStatus, ResearchDataReadinessDecision, ResearchDataReadinessStatus


CORE_ALPHA_FAMILIES = {"price_volume", "liquidity", "valuation"}


def decide_research_readiness(
    dataset_checks: list[DatasetReadinessCheck],
    feature_readiness: list[FeatureFamilyReadiness],
    summary: dict,
    *,
    strict: bool = False,
) -> ResearchDataReadinessDecision:
    core_blockers = [
        f"{item.dataset}: {'; '.join(item.blockers) or item.status}"
        for item in dataset_checks
        if item.tier == DatasetResearchTier.core_required and item.status == "blocked"
    ]
    noncore_blockers = [
        f"{item.dataset}: {'; '.join(item.blockers) or item.status}"
        for item in dataset_checks
        if item.tier != DatasetResearchTier.core_required and item.status == "blocked"
    ]
    required_feature_blockers = [
        f"{item.feature_family}: {'; '.join(item.blockers)}"
        for item in feature_readiness
        if item.feature_family in CORE_ALPHA_FAMILIES and item.readiness_status == FeatureReadinessStatus.blocked
    ]
    warnings = sum(1 for item in dataset_checks if item.status == "warning") + sum(
        1 for item in feature_readiness if item.readiness_status == FeatureReadinessStatus.warning
    )
    pending_jobs = int(summary.get("pending_job_count", 0) or 0)
    failed_jobs = int(summary.get("failed_job_count", 0) or 0)
    quarantined_jobs = int(summary.get("quarantined_job_count", 0) or 0)
    has_runtime_artifacts = any(
        bool(summary.get(key))
        for key in ("observer_report_exists", "freeze_readiness_exists", "matrix_freshness_exists")
    )
    core_ready = not core_blockers
    expanded_ready = not noncore_blockers
    matrix_ready = core_ready and not required_feature_blockers
    alpha_ready = matrix_ready and not required_feature_blockers
    validation_ready = alpha_ready and str(summary.get("matrix_freshness_status")) == "fresh"
    blockers = list(core_blockers)
    if strict:
        blockers.extend(noncore_blockers)
    blockers.extend(required_feature_blockers)
    if has_runtime_artifacts and pending_jobs:
        status = ResearchDataReadinessStatus.raw_download_in_progress
    elif has_runtime_artifacts and (failed_jobs or quarantined_jobs) and core_blockers:
        status = ResearchDataReadinessStatus.raw_download_complete_but_needs_repair
    elif has_runtime_artifacts and validation_ready:
        status = ResearchDataReadinessStatus.validation_ready
    elif has_runtime_artifacts and alpha_ready and str(summary.get("matrix_freshness_status")) == "fresh":
        status = ResearchDataReadinessStatus.alpha_factory_ready
    elif has_runtime_artifacts and alpha_ready:
        status = ResearchDataReadinessStatus.raw_ready_for_freeze
    elif core_blockers:
        status = ResearchDataReadinessStatus.not_ready
    elif required_feature_blockers:
        status = ResearchDataReadinessStatus.insufficient_data
    elif validation_ready:
        status = ResearchDataReadinessStatus.ready_for_validation
    elif alpha_ready:
        status = ResearchDataReadinessStatus.ready_for_alpha_factory
    elif matrix_ready:
        status = ResearchDataReadinessStatus.ready_for_matrix
    elif core_ready:
        status = ResearchDataReadinessStatus.ready_for_freeze
    else:
        status = ResearchDataReadinessStatus.not_ready
    remediations = blockers or [item for item in noncore_blockers[:20]]
    commands = _recommended_commands(status, bool(core_blockers), bool(noncore_blockers), summary)
    blocked_reason = remediations[0] if remediations else None
    next_action = _next_required_action(status, bool(core_blockers), bool(required_feature_blockers), pending_jobs, failed_jobs + quarantined_jobs)
    return ResearchDataReadinessDecision(
        status=status,
        core_ready=core_ready,
        expanded_ready=expanded_ready,
        matrix_ready=matrix_ready,
        alpha_ready=alpha_ready,
        validation_ready=validation_ready,
        blocker_count=len(blockers),
        warning_count=warnings + len(noncore_blockers),
        required_remediations=remediations,
        recommended_next_commands=commands,
        can_create_freeze=core_ready and not pending_jobs and not (failed_jobs + quarantined_jobs),
        can_build_matrix=core_ready and not required_feature_blockers and str(summary.get("raw_freeze_readiness_status")) in {"ready", "ok", "missing"},
        can_run_core_alpha_factory=alpha_ready,
        can_run_expanded_alpha_factory=alpha_ready and expanded_ready,
        can_run_validation=validation_ready,
        blocked_reason=blocked_reason,
        next_required_action=next_action,
        recommended_codex_task=_recommended_codex_task(status),
    )


def _recommended_commands(status: str, has_core_blockers: bool, has_noncore_blockers: bool, summary: dict) -> list[str]:
    commands: list[str] = []
    if has_core_blockers or has_noncore_blockers:
        commands.append("uv run python -m backfill_observer.run_observer repair-plan --run-dir <run_dir> --data-dir <data_dir> --output-dir <reports_dir>")
    if status in {ResearchDataReadinessStatus.ready_for_freeze, ResearchDataReadinessStatus.ready_for_matrix, ResearchDataReadinessStatus.ready_for_alpha_factory}:
        commands.extend(
            [
                "uv run python -m raw_data_landing.run_landing report --data-dir <data_dir> --output-dir <raw_landing_dir> --validate",
                "uv run python -m data_lake.run_lake create-version --data-dir <data_dir> --registry-dir <registry_dir>",
                "uv run python -m data_lake.run_lake create-freeze --data-dir <data_dir> --registry-dir <registry_dir> --freeze-dir <freeze_dir>",
                "uv run python -m matrix_refresh.run_matrix_refresh refresh --data-dir <freeze_data_dir> --matrix-cache-dir <matrix_cache_dir>",
            ]
        )
    if status == ResearchDataReadinessStatus.ready_for_alpha_factory:
        commands.append("uv run python -m alpha_factory.run_alpha_factory --data-dir <freeze_data_dir> --matrix-cache-dir <matrix_cache_dir> --pretty")
    if summary.get("matrix_freshness_status") != "fresh":
        commands.append("uv run python -m matrix_refresh.run_matrix_refresh plan --data-dir <data_dir> --matrix-cache-dir <matrix_cache_dir> --pretty")
    return commands


def _next_required_action(status: str, has_core_blockers: bool, has_required_feature_blockers: bool, pending_jobs: int, failed_or_quarantined: int) -> str:
    if pending_jobs:
        return "wait_for_download_completion_and_observe"
    if failed_or_quarantined or has_core_blockers:
        return "run_backfill_repair_plan"
    if has_required_feature_blockers:
        return "complete_core_feature_datasets"
    if status in {ResearchDataReadinessStatus.raw_ready_for_freeze, ResearchDataReadinessStatus.ready_for_freeze}:
        return "create_freeze_candidate_then_freeze"
    if status in {ResearchDataReadinessStatus.alpha_factory_ready, ResearchDataReadinessStatus.ready_for_alpha_factory}:
        return "run_alpha_factory"
    if status == ResearchDataReadinessStatus.validation_ready:
        return "run_validation_suite"
    return "review_readiness_report"


def _recommended_codex_task(status: str) -> str:
    if status == ResearchDataReadinessStatus.raw_download_in_progress:
        return "observe_current_backfill_without_mutation"
    if status == ResearchDataReadinessStatus.raw_download_complete_but_needs_repair:
        return "prepare_and_execute_backfill_repair"
    if status in {ResearchDataReadinessStatus.raw_ready_for_freeze, ResearchDataReadinessStatus.ready_for_freeze}:
        return "create_research_freeze_and_matrix_cache"
    if status in {ResearchDataReadinessStatus.alpha_factory_ready, ResearchDataReadinessStatus.ready_for_alpha_factory}:
        return "start_real_data_alpha_factory"
    return "review_data_readiness_gate"

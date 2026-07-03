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
    core_ready = not core_blockers
    expanded_ready = not noncore_blockers
    matrix_ready = core_ready and not required_feature_blockers
    alpha_ready = matrix_ready and not required_feature_blockers
    validation_ready = alpha_ready and str(summary.get("matrix_freshness_status")) == "fresh"
    blockers = list(core_blockers)
    if strict:
        blockers.extend(noncore_blockers)
    blockers.extend(required_feature_blockers)
    if core_blockers:
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

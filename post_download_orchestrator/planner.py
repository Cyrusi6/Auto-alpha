"""Plan safe post-download processing steps."""

from __future__ import annotations

import json
from pathlib import Path

from artifact_schema.writer import utc_now

from .models import PostDownloadPlan, PostDownloadStep


READY_STATUSES = {
    "ready_for_freeze",
    "ready_for_matrix",
    "ready_for_alpha_factory",
    "ready_for_validation",
    "raw_ready_for_freeze",
    "freeze_ready",
    "matrix_ready",
    "alpha_factory_ready",
    "validation_ready",
    "ready_for_core_alpha",
}

DIAGNOSTIC_STEPS = {"refresh_observer", "raw_landing_qa", "research_readiness", "repair_required_check"}


def build_post_download_plan(
    *,
    data_dir: str | Path,
    run_dir: str | Path | None,
    staging_dir: str | Path | None,
    output_dir: str | Path,
    registry_dir: str | Path | None,
    freeze_dir: str | Path | None,
    matrix_cache_dir: str | Path | None,
    readiness_report_path: str | Path | None,
    profile_name: str | None,
    start_date: str | None,
    end_date: str | None,
    allow_incomplete: bool = False,
) -> PostDownloadPlan:
    readiness = _read_json(readiness_report_path)
    decision = readiness.get("decision", {}) if readiness else {}
    status = str(decision.get("status") or "missing_readiness")
    blockers = list(decision.get("required_remediations") or []) if isinstance(decision, dict) else []
    if status not in READY_STATUSES and not allow_incomplete:
        blockers = blockers or [f"readiness status is {status}"]
    root = Path(output_dir)
    registry = Path(registry_dir) if registry_dir else root / "registry"
    freeze = Path(freeze_dir) if freeze_dir else root / "freeze"
    matrix = Path(matrix_cache_dir) if matrix_cache_dir else root / "matrix_cache"
    observer_out = root / "observer_latest"
    landing_out = root / "raw_landing_latest"
    readiness_out = root / "research_readiness_latest"
    schema_out = root / "schema_validation"
    commands = [
        (
            "refresh_observer",
            "Refresh the read-only backfill observer artifacts.",
            f"uv run python -m backfill_observer.run_observer observe --run-dir {run_dir or '<run_dir>'} --data-dir {data_dir} --staging-dir {staging_dir or '<staging_dir>'} --output-dir {observer_out} --start-date {start_date or '<start_date>'} --end-date {end_date or '<end_date>'} --pretty",
        ),
        (
            "raw_landing_qa",
            "Scan raw JSONL landing files and freeze readiness.",
            f"uv run python -m raw_data_landing.run_landing report --data-dir {data_dir} --run-dir {run_dir or '<run_dir>'} --output-dir {landing_out} --expected-start-date {start_date or '<start_date>'} --expected-end-date {end_date or '<end_date>'} --pretty",
        ),
        (
            "research_readiness",
            "Refresh the research data readiness gate.",
            f"uv run python -m research_data_readiness.run_readiness assess --data-dir {data_dir} --run-dir {run_dir or '<run_dir>'} --raw-landing-report-path {landing_out / 'raw_data_landing_report.json'} --freeze-readiness-path {landing_out / 'raw_freeze_readiness_decision.json'} --output-dir {readiness_out} --profile-name {profile_name or 'research_data'} --expected-start-date {start_date or '<start_date>'} --expected-end-date {end_date or '<end_date>'} --pretty",
        ),
        (
            "repair_required_check",
            "Review repair plan before any mutation.",
            f"uv run python -m backfill_repair.run_repair plan --run-dir {run_dir or '<run_dir>'} --data-dir {data_dir} --output-dir {root / 'repair'} --repair-plan-path {observer_out / 'backfill_repair_plan.json'} --pretty",
        ),
        (
            "compact",
            "Compact datasets only after download completion.",
            f"uv run python -m data_pipeline.run_pipeline --data-dir {data_dir} --compact --pretty",
        ),
        (
            "validate",
            "Validate governed datasets.",
            f"uv run python -m data_pipeline.run_pipeline --data-dir {data_dir} --validate-only --pretty",
        ),
        (
            "stats",
            "Compute dataset statistics.",
            f"uv run python -m data_pipeline.run_pipeline --data-dir {data_dir} --stats --pretty",
        ),
        (
            "size_report",
            "Write real data size report.",
            f"uv run python -m data_lake.run_lake size-report --data-dir {data_dir} --registry-dir {registry} --output-dir {root / 'size'} --matrix-cache-dir {matrix} --pretty",
        ),
        (
            "data_lake_create_version",
            "Create a governed dataset version manifest.",
            f"uv run python -m data_lake.run_lake create-version --data-dir {data_dir} --registry-dir {registry} --output-dir {root / 'data_lake'} --pretty",
        ),
        (
            "data_lake_promote_candidate",
            "Promote candidate dataset version after review.",
            f"uv run python -m data_lake.run_lake promote-version --registry-dir {registry} --output-dir {root / 'data_lake'} --dataset-version-id <dataset_version_id> --status candidate --pretty",
        ),
        (
            "data_lake_create_freeze",
            "Create a research freeze from the governed data version.",
            f"uv run python -m data_lake.run_lake create-freeze --data-dir {data_dir} --registry-dir {registry} --output-dir {root / 'freeze'} --freeze-dir {freeze} --freeze-name {profile_name or 'research_data_freeze'} --pretty",
        ),
        (
            "freeze_validate",
            "Validate research freeze fingerprints.",
            f"uv run python -m data_lake.run_lake validate-freeze --registry-dir {registry} --output-dir {root / 'freeze_validation'} --freeze-dir {freeze} --pretty",
        ),
        (
            "point_in_time_validate",
            "Validate point-in-time contracts and leakage controls.",
            f"uv run python -m point_in_time.run_pit validate --data-dir {data_dir} --output-dir {root / 'pit'} --pretty",
        ),
        (
            "leakage_audit",
            "Run leakage audit on the frozen research input set.",
            f"uv run python -m leakage_audit.run_audit report --data-dir {data_dir} --output-dir {root / 'leakage'} --pretty",
        ),
        (
            "corporate_action_report",
            "Build corporate-action and total-return reports.",
            f"uv run python -m corporate_actions.run_actions report --data-dir {data_dir} --output-dir {root / 'corporate_actions'} --start-date {start_date or '<start_date>'} --end-date {end_date or '<end_date>'} --pretty",
        ),
        (
            "matrix_refresh",
            "Legacy matrix refresh compatibility step.",
            f"uv run python -m matrix_refresh.run_matrix_refresh refresh --data-dir {data_dir} --matrix-cache-dir {matrix} --output-dir {root / 'matrix_refresh'} --refresh-mode full_rebuild --pretty",
        ),
        (
            "matrix_refresh_plan",
            "Plan matrix cache refresh.",
            f"uv run python -m matrix_refresh.run_matrix_refresh plan --data-dir {data_dir} --matrix-cache-dir {matrix} --output-dir {root / 'matrix_refresh'} --pretty",
        ),
        (
            "matrix_refresh_execute",
            "Refresh matrix cache after freeze validation.",
            f"uv run python -m matrix_refresh.run_matrix_refresh refresh --data-dir {data_dir} --matrix-cache-dir {matrix} --output-dir {root / 'matrix_refresh'} --refresh-mode full_rebuild --pretty",
        ),
        (
            "matrix_freshness_validate",
            "Validate matrix cache freshness.",
            f"uv run python -m matrix_refresh.run_matrix_refresh validate --data-dir {data_dir} --matrix-cache-dir {matrix} --output-dir {root / 'matrix_refresh'} --pretty",
        ),
        (
            "real_data_sla",
            "Write real-data SLA review artifacts.",
            f"uv run python -m real_data_ops.run_real_data runbook --profile-name {profile_name or 'research_data'} --output-dir {root / 'sla'} --pretty",
        ),
        (
            "artifact_schema_validate",
            "Validate generated post-download artifacts.",
            f"uv run python -m artifact_schema.run_validate --artifact-dir {root} --output-dir {schema_out} --write-manifest --pretty",
        ),
        (
            "final_research_readiness",
            "Refresh final research readiness after post-download steps.",
            f"uv run python -m research_data_readiness.run_readiness assess --data-dir {data_dir} --output-dir {root / 'final_readiness'} --profile-name {profile_name or 'research_data'} --pretty",
        ),
        (
            "final_package",
            "Build freeze candidate and final research package.",
            f"uv run python -m post_download_orchestrator.run_post_download report --data-dir {data_dir} --output-dir {root} --readiness-report-path {readiness_report_path or readiness_out / 'research_data_readiness_report.json'} --pretty",
        ),
        (
            "research_suite_dry_smoke",
            "Run a real-data dry smoke only after readiness is green.",
            f"uv run python -m research_suite.run_suite --suite-name {profile_name or 'real_data_dry_smoke'} --skip-data-sync --data-dir {data_dir} --output-dir {root / 'suite'} --pretty",
        ),
    ]
    blocked = bool(blockers)
    steps = [
        PostDownloadStep(
            step_id=step_id,
            description=description,
            command=command,
            blocked=blocked and step_id not in DIAGNOSTIC_STEPS,
            reason=(
                "research readiness is not green; only diagnostic steps are allowed until repair/readiness clears"
                if blocked and step_id not in DIAGNOSTIC_STEPS
                else None
            ),
        )
        for step_id, description, command in commands
    ]
    next_step = next((step.step_id for step in steps if not step.blocked), None)
    now = utc_now()
    return PostDownloadPlan(
        plan_id=f"post_download_{now.replace(':', '').replace('-', '')}",
        created_at=now,
        profile_name=profile_name,
        readiness_status=status,
        allow_incomplete=allow_incomplete,
        steps=steps,
        blockers=[] if allow_incomplete else blockers,
        warnings=[] if status in READY_STATUSES else [f"readiness status is {status}"],
        next_step=next_step,
    )


def _read_json(path: str | Path | None) -> dict:
    if not path or not Path(path).exists():
        return {}
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}

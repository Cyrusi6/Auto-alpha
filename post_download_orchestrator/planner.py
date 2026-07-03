"""Plan safe post-download processing steps."""

from __future__ import annotations

import json
from pathlib import Path

from artifact_schema.writer import utc_now

from .models import PostDownloadPlan, PostDownloadStep


READY_STATUSES = {"ready_for_freeze", "ready_for_matrix", "ready_for_alpha_factory", "ready_for_validation"}


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
            "observer_latest",
            "Refresh the read-only backfill observer artifacts.",
            f"uv run python -m backfill_observer.run_observer observe --run-dir {run_dir or '<run_dir>'} --data-dir {data_dir} --staging-dir {staging_dir or '<staging_dir>'} --output-dir {observer_out} --start-date {start_date or '<start_date>'} --end-date {end_date or '<end_date>'} --pretty",
        ),
        (
            "raw_landing_latest",
            "Scan raw JSONL landing files and freeze readiness.",
            f"uv run python -m raw_data_landing.run_landing report --data-dir {data_dir} --run-dir {run_dir or '<run_dir>'} --output-dir {landing_out} --expected-start-date {start_date or '<start_date>'} --expected-end-date {end_date or '<end_date>'} --pretty",
        ),
        (
            "repair_check",
            "Review repair plan before any mutation.",
            f"uv run python -m backfill_observer.run_observer repair-plan --run-dir {run_dir or '<run_dir>'} --data-dir {data_dir} --output-dir {observer_out} --start-date {start_date or '<start_date>'} --end-date {end_date or '<end_date>'} --pretty",
        ),
        (
            "compact_validate_stats",
            "Compact, validate and compute stats only after download completion.",
            f"uv run python -m data_pipeline.run_pipeline --data-dir {data_dir} --compact --validate-only --stats --pretty",
        ),
        (
            "data_lake_version",
            "Create a governed dataset version manifest.",
            f"uv run python -m data_lake.run_lake create-version --data-dir {data_dir} --registry-dir {registry} --pretty",
        ),
        (
            "data_lake_freeze",
            "Create a research freeze from the governed data version.",
            f"uv run python -m data_lake.run_lake create-freeze --data-dir {data_dir} --registry-dir {registry} --freeze-dir {freeze} --freeze-name {profile_name or 'research_data_freeze'} --pretty",
        ),
        (
            "pit_validation",
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
            "Refresh matrix cache after freeze validation.",
            f"uv run python -m matrix_refresh.run_matrix_refresh refresh --data-dir {data_dir} --matrix-cache-dir {matrix} --output-dir {root / 'matrix_refresh'} --refresh-mode full_rebuild --pretty",
        ),
        (
            "artifact_schema_validate",
            "Validate generated post-download artifacts.",
            f"uv run python -m artifact_schema.run_validate --artifact-dir {root} --output-dir {schema_out} --write-manifest --pretty",
        ),
        (
            "research_suite_dry_smoke",
            "Run a real-data dry smoke only after readiness is green.",
            f"uv run python -m research_suite.run_suite --suite-name {profile_name or 'real_data_dry_smoke'} --skip-data-sync --data-dir {data_dir} --output-dir {root / 'suite'} --pretty",
        ),
    ]
    blocked = bool(blockers) and not allow_incomplete
    steps = [
        PostDownloadStep(
            step_id=step_id,
            description=description,
            command=command,
            blocked=blocked and step_id not in {"observer_latest", "raw_landing_latest", "repair_check"},
            reason="research readiness is not green; rerun plan with --allow-incomplete only for manual diagnostics" if blocked and step_id not in {"observer_latest", "raw_landing_latest", "repair_check"} else None,
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

"""Execute matrix refresh plans."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from matrix_store.builder import build_matrix_cache

from .diff import diff_matrix_source
from .models import MatrixRefreshResult
from .planner import build_matrix_refresh_plan
from .report import write_matrix_refresh_artifacts
from .validator import validate_matrix_freshness


def run_matrix_refresh(
    *,
    data_dir: str | Path,
    matrix_cache_dir: str | Path,
    output_dir: str | Path,
    data_freeze_dir: str | Path | None = None,
    data_version_manifest_path: str | Path | None = None,
    universe_name: str | None = None,
    universe_file: str | Path | None = None,
    refresh_mode: str = "skip_if_fresh",
    require_data_freeze: bool = False,
    point_in_time: bool = False,
    feature_cutoff_mode: str = "same_day_after_close",
    corporate_action_aware: bool = False,
    target_return_mode: str = "adjusted_close",
    feature_set_name: str = "ashare_features_v1",
    feature_set_manifest_path: str | Path | None = None,
    feature_promotion_policy_path: str | Path | None = None,
    pretty_config: dict[str, Any] | None = None,
) -> MatrixRefreshResult:
    plan = build_matrix_refresh_plan(
        data_dir=data_dir,
        matrix_cache_dir=matrix_cache_dir,
        data_freeze_dir=data_freeze_dir,
        data_version_manifest_path=data_version_manifest_path,
        refresh_mode=refresh_mode,
        feature_set_name=feature_set_name,
        feature_set_manifest_path=feature_set_manifest_path,
        feature_promotion_policy_path=feature_promotion_policy_path,
        config=pretty_config,
    )
    action = plan.recommendation
    warnings: list[str] = []
    if action == "full_rebuild":
        build_matrix_cache(
            data_dir=data_dir,
            output_dir=matrix_cache_dir,
            universe_name=universe_name,
            universe_file=universe_file,
            point_in_time=point_in_time,
            feature_cutoff_mode=feature_cutoff_mode,
            corporate_action_aware=corporate_action_aware,
            target_return_mode=target_return_mode,
            data_freeze_dir=data_freeze_dir,
            data_version_manifest_path=data_version_manifest_path,
            require_data_freeze=require_data_freeze,
            feature_set_name=feature_set_name,
            feature_set_manifest_path=feature_set_manifest_path,
        )
        status = "refreshed"
    elif action == "skip":
        status = "fresh"
    else:
        status = "validated"
    source_diff = diff_matrix_source(data_dir, matrix_cache_dir, data_version_manifest_path)
    freshness = validate_matrix_freshness(data_dir, matrix_cache_dir, data_version_manifest_path)
    if freshness.status == "error":
        status = "failed"
        warnings.append("matrix freshness validation failed")
    provisional = MatrixRefreshResult(
        status=status,
        action=action,
        refresh_mode=refresh_mode,
        matrix_cache_dir=str(matrix_cache_dir),
        source_diff=source_diff.to_dict(),
        freshness=freshness.to_dict(),
        paths={},
        warnings=warnings,
    )
    paths = write_matrix_refresh_artifacts(
        plan=plan,
        source_diff=source_diff,
        freshness=freshness,
        result=provisional,
        output_dir=output_dir,
    )
    return MatrixRefreshResult(
        status=provisional.status,
        action=provisional.action,
        refresh_mode=provisional.refresh_mode,
        matrix_cache_dir=provisional.matrix_cache_dir,
        source_diff=provisional.source_diff,
        freshness=provisional.freshness,
        paths=paths,
        warnings=warnings,
    )

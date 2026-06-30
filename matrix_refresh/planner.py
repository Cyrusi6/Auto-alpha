"""Build conservative matrix refresh plans."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .diff import diff_matrix_source
from .models import MatrixRefreshPlan


def build_matrix_refresh_plan(
    *,
    data_dir: str | Path,
    matrix_cache_dir: str | Path,
    data_freeze_dir: str | Path | None = None,
    data_version_manifest_path: str | Path | None = None,
    refresh_mode: str = "skip_if_fresh",
    config: dict[str, Any] | None = None,
) -> MatrixRefreshPlan:
    diff = diff_matrix_source(data_dir, matrix_cache_dir, data_version_manifest_path)
    metadata_exists = (Path(matrix_cache_dir) / "metadata.json").exists()
    reasons: list[str] = []
    if refresh_mode == "validate_only":
        recommendation = "validate_only"
    elif refresh_mode == "full_rebuild":
        recommendation = "full_rebuild"
        reasons.append("refresh_mode_full_rebuild")
    elif not metadata_exists:
        recommendation = "full_rebuild"
        reasons.append("missing_matrix_cache")
    elif diff.status == "fresh":
        recommendation = "skip"
        reasons.append("matrix_cache_fresh")
    else:
        recommendation = "full_rebuild"
        reasons.extend(item.get("code", "source_changed") for item in diff.issues)
    return MatrixRefreshPlan(
        refresh_mode=refresh_mode,
        data_dir=str(data_dir),
        data_freeze_dir=str(data_freeze_dir) if data_freeze_dir else None,
        matrix_cache_dir=str(matrix_cache_dir),
        source_hash=diff.source_hash,
        matrix_hash=diff.matrix_hash,
        recommendation=recommendation,
        reasons=reasons,
        config=dict(config or {}),
    )

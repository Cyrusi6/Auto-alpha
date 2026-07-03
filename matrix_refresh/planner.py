"""Build conservative matrix refresh plans."""

from __future__ import annotations

import json
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
    feature_set_name: str = "ashare_features_v1",
    feature_set_manifest_path: str | Path | None = None,
    config: dict[str, Any] | None = None,
) -> MatrixRefreshPlan:
    diff = diff_matrix_source(data_dir, matrix_cache_dir, data_version_manifest_path)
    metadata_exists = (Path(matrix_cache_dir) / "metadata.json").exists()
    feature_drift = _feature_set_drift(matrix_cache_dir, feature_set_name, feature_set_manifest_path)
    reasons: list[str] = []
    if refresh_mode == "validate_only":
        recommendation = "validate_only"
    elif refresh_mode == "full_rebuild":
        recommendation = "full_rebuild"
        reasons.append("refresh_mode_full_rebuild")
    elif not metadata_exists:
        recommendation = "full_rebuild"
        reasons.append("missing_matrix_cache")
    elif feature_drift.get("drift"):
        recommendation = "full_rebuild"
        reasons.append("feature_set_hash_drift")
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
        config=dict(config or {}) | {"feature_set": feature_drift},
    )


def _feature_set_drift(
    matrix_cache_dir: str | Path,
    feature_set_name: str,
    feature_set_manifest_path: str | Path | None,
) -> dict[str, Any]:
    metadata_path = Path(matrix_cache_dir) / "metadata.json"
    if not metadata_path.exists():
        return {"drift": False, "status": "missing_metadata", "expected_feature_set_name": feature_set_name}
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"drift": True, "status": "malformed_metadata", "expected_feature_set_name": feature_set_name}
    expected_hash = None
    expected_name = feature_set_name
    if feature_set_manifest_path is not None and Path(feature_set_manifest_path).exists():
        try:
            payload = json.loads(Path(feature_set_manifest_path).read_text(encoding="utf-8"))
            expected_hash = payload.get("content_hash")
            expected_name = str(payload.get("feature_set_name", feature_set_name))
        except json.JSONDecodeError:
            return {"drift": True, "status": "malformed_feature_manifest", "expected_feature_set_name": feature_set_name}
    stored_name = metadata.get("feature_set_name", "ashare_features_v1")
    stored_hash = metadata.get("feature_set_hash")
    drift = bool(stored_name != expected_name or (expected_hash and stored_hash and stored_hash != expected_hash))
    return {
        "drift": drift,
        "status": "drift" if drift else "fresh",
        "stored_feature_set_name": stored_name,
        "expected_feature_set_name": expected_name,
        "stored_feature_set_hash": stored_hash,
        "expected_feature_set_hash": expected_hash,
    }

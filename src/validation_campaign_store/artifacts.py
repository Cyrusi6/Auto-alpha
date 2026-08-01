"""Resolve governed inputs from an existing Alpha campaign manifest."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from validation_lab.materialization import MaterializationInputs


@dataclass(frozen=True)
class CampaignArtifacts:
    campaign_root: str
    campaign_manifest_path: str
    candidate_pool_path: str
    factor_store_dir: str
    data_dir: str
    data_freeze_dir: str
    matrix_cache_dir: str
    feature_manifest_path: str
    feature_tensor_path: str
    promotion_policy_path: str | None
    promotion_allowlist_path: str | None
    promotion_denylist_path: str | None
    target_return_mode: str
    feature_cutoff_mode: str
    point_in_time: bool

    def materialization_inputs(self) -> MaterializationInputs:
        return MaterializationInputs(
            data_freeze_dir=self.data_freeze_dir,
            matrix_cache_dir=self.matrix_cache_dir,
            feature_manifest_path=self.feature_manifest_path,
            feature_tensor_path=self.feature_tensor_path,
            promotion_policy_path=self.promotion_policy_path,
            target_return_mode=self.target_return_mode,
            feature_cutoff_mode=self.feature_cutoff_mode,
            point_in_time=self.point_in_time,
            campaign_manifest_path=self.campaign_manifest_path,
        )


def resolve_campaign_artifacts(campaign_root: str | Path) -> CampaignArtifacts:
    root = Path(campaign_root).resolve()
    report_path = root / "alpha_factory" / "alpha_factory_report.json"
    if not report_path.exists():
        raise FileNotFoundError(f"alpha factory report missing: {report_path}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    manifest_path = Path(str((report.get("paths") or {}).get("alpha_campaign_manifest_path") or root / "alpha_factory" / "alpha_campaign_manifest.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    config = manifest.get("config_snapshot") or {}
    paths = report.get("paths") or {}
    feature_manifest_path = Path(str(config.get("feature_set_manifest_path") or paths.get("feature_set_manifest_path") or ""))
    build_result_path = feature_manifest_path.parent / "feature_tensor_build_result.json"
    build_result = json.loads(build_result_path.read_text(encoding="utf-8")) if build_result_path.exists() else {}
    feature_tensor_path = Path(str(build_result.get("tensor_path") or feature_manifest_path.parent / "feature_tensor.npy"))
    candidate_pool = Path(str(paths.get("alpha_validation_candidate_pool_path") or root / "validation_pool" / "alpha_validation_candidate_pool.jsonl"))
    if not candidate_pool.exists():
        fallback = root / "validation_pool" / "alpha_validation_candidate_pool.jsonl"
        candidate_pool = fallback if fallback.exists() else candidate_pool
    artifacts = CampaignArtifacts(
        campaign_root=str(root),
        campaign_manifest_path=str(manifest_path),
        candidate_pool_path=str(candidate_pool),
        factor_store_dir=str(config.get("consolidated_factor_store_dir") or root / "consolidated_factor_store"),
        data_dir=str(config.get("data_dir") or ""),
        data_freeze_dir=str(config.get("data_freeze_dir") or ""),
        matrix_cache_dir=str(config.get("matrix_cache_dir") or ""),
        feature_manifest_path=str(feature_manifest_path),
        feature_tensor_path=str(feature_tensor_path),
        promotion_policy_path=_optional(config.get("feature_promotion_policy_path")),
        promotion_allowlist_path=_optional(config.get("feature_promotion_allowlist_path")),
        promotion_denylist_path=_optional(config.get("feature_promotion_denylist_path")),
        target_return_mode=str(config.get("target_return_mode") or "adjusted_close"),
        feature_cutoff_mode=str(config.get("feature_cutoff_mode") or "next_open"),
        point_in_time=bool(config.get("point_in_time")),
    )
    for name in ["candidate_pool_path", "factor_store_dir", "data_freeze_dir", "matrix_cache_dir", "feature_manifest_path", "feature_tensor_path"]:
        if not Path(getattr(artifacts, name)).exists():
            raise FileNotFoundError(f"resolved campaign artifact missing: {name}={getattr(artifacts, name)}")
    return artifacts


def _optional(value) -> str | None:
    return str(value) if value else None

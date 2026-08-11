"""Feature coverage, readiness, and reporting."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import torch

from auto_alpha.research.features.models import FeatureCoverageReport
from auto_alpha.research.features.models import FeatureSetManifest


def build_feature_coverage_report(
    manifest: FeatureSetManifest,
    tensor: torch.Tensor,
    warnings: list[str] | None = None,
    raw_data_index_summary: dict[str, Any] | None = None,
) -> FeatureCoverageReport:
    feature_warnings = warnings or []
    summaries: list[dict[str, Any]] = []
    for idx, definition in enumerate(manifest.feature_definitions):
        values = tensor[:, idx, :]
        finite = torch.isfinite(values)
        nonzero = torch.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0) != 0
        name = str(definition.get("feature_name"))
        summaries.append(
            {
                "feature_name": name,
                "family": definition.get("family"),
                "finite_ratio": float(finite.to(torch.float32).mean().item()),
                "nonzero_ratio": float(nonzero.to(torch.float32).mean().item()),
                "null_ratio": float((~finite).to(torch.float32).mean().item()),
                "warning": "; ".join(item for item in feature_warnings if name in item),
            }
        )
    return FeatureCoverageReport(
        feature_set_name=manifest.feature_set_name,
        feature_set_version=manifest.feature_set_version,
        feature_count=manifest.feature_count,
        rows=int(tensor.shape[0]),
        cols=int(tensor.shape[2]),
        warnings=feature_warnings,
        feature_summaries=summaries,
        created_at=datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        raw_data_index_used=bool((raw_data_index_summary or {}).get("raw_data_index_used", False)),
        dataset_index_status=dict(raw_data_index_summary or {}),
    )

from auto_alpha.data.pit.readiness.feature_readiness import FEATURE_FAMILY_POLICIES, build_feature_readiness_catalog

__all__ = ["FEATURE_FAMILY_POLICIES", "build_feature_readiness_catalog"]

from pathlib import Path
from typing import Any

from auto_alpha.platform.artifacts.schema.writer import write_json_artifact


def write_feature_factory_report(payload: dict[str, Any], output_dir: str | Path) -> tuple[Path, Path]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    json_path = write_json_artifact(
        target / "feature_factory_report.json",
        payload,
        "feature_tensor_build_result",
        "feature_factory",
    )
    md_path = target / "feature_factory_report.md"
    md_path.write_text(_markdown(payload), encoding="utf-8")
    return json_path, md_path


def _markdown(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"# Feature Factory: {payload.get('feature_set_name')}",
            "",
            f"- feature_count: {payload.get('feature_count')}",
            f"- n_stocks: {payload.get('n_stocks')}",
            f"- n_dates: {payload.get('n_dates')}",
            f"- warnings: {len(payload.get('warnings', []))}",
            "",
        ]
    )

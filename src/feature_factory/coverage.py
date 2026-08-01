"""Feature coverage diagnostics."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import torch

from .models import FeatureCoverageReport, FeatureSetManifest


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

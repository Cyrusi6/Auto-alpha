"""Cross-campaign comparison utilities."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact

from .registry import LocalAlphaExperimentStore


def compare_experiment_stores(current_store_dir: str | Path, previous_store_dirs: list[str | Path], output_dir: str | Path | None = None) -> dict[str, Any]:
    current = LocalAlphaExperimentStore(current_store_dir)
    current_rows = current.load_consolidated_factors()
    current_hashes = {str(row.get("formula_hash")) for row in current_rows if row.get("formula_hash")}
    previous_summary: list[dict[str, Any]] = []
    all_previous_hashes: set[str] = set()
    for store_dir in previous_store_dirs:
        store = LocalAlphaExperimentStore(store_dir)
        rows = store.load_consolidated_factors()
        hashes = {str(row.get("formula_hash")) for row in rows if row.get("formula_hash")}
        all_previous_hashes.update(hashes)
        previous_summary.append(
            {
                "store_dir": str(store_dir),
                "factor_count": len(rows),
                "overlap_count": len(current_hashes & hashes),
            }
        )
    payload = {
        "status": "success",
        "current_store_dir": str(current_store_dir),
        "previous_store_count": len(previous_store_dirs),
        "current_factor_count": len(current_rows),
        "previous_unique_formula_count": len(all_previous_hashes),
        "overlap_count": len(current_hashes & all_previous_hashes),
        "new_formula_count": len(current_hashes - all_previous_hashes),
        "previous": previous_summary,
    }
    if output_dir:
        write_json_artifact(Path(output_dir) / "alpha_campaign_comparison_report.json", payload, "alpha_campaign_comparison_report", "alpha_experiment_store")
    return payload

"""Feature factory report helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact


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

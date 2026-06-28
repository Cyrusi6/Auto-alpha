"""Experiment report writers."""

from __future__ import annotations

import json
from pathlib import Path

from artifact_schema.writer import write_json_artifact

from .models import ExperimentPlan, ExperimentRunReport
from .planner import write_experiment_plan


def write_experiment_run_report(report: ExperimentRunReport, output_dir: str | Path) -> tuple[Path, Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    json_path = write_json_artifact(
        output / "experiment_run_report.json",
        report.to_dict(),
        "experiment_run_report",
        "experiment_orchestrator",
    )
    md_path = output / "experiment_run_report.md"
    md_path.write_text(_render_run_report(report), encoding="utf-8")
    _write_catalog(report, output)
    return json_path, md_path


def write_plan_artifacts(plan: ExperimentPlan, output_dir: str | Path) -> dict[str, str]:
    return write_experiment_plan(plan, output_dir)


def _write_catalog(report: ExperimentRunReport, output: Path) -> None:
    entries = []
    for name, path in report.paths.items():
        if path:
            entries.append({"name": name, "path": path, "stage": "experiment", "kind": "json" if path.endswith(".json") else "other"})
    write_json_artifact(
        output / "experiment_artifact_catalog.json",
        {"experiment_id": report.experiment_id, "entries": entries},
        "experiment_artifact_catalog",
        "experiment_orchestrator",
    )


def _render_run_report(report: ExperimentRunReport) -> str:
    return "\n".join(
        [
            "# Experiment Run Report",
            "",
            f"- experiment_id: `{report.experiment_id}`",
            f"- workflow: `{report.workflow}`",
            f"- status: `{report.status}`",
            f"- shard_count: {report.shard_count}",
            f"- failed_shard_count: {report.failed_shard_count}",
            "",
            "```json",
            json.dumps(report.summary, ensure_ascii=False, indent=2),
            "```",
        ]
    ) + "\n"

"""Report writers for artifact schema validation."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .models import ArtifactValidationReport, ArtifactValidationResult
from .writer import write_json_artifact


def build_validation_report(results: list[ArtifactValidationResult]) -> ArtifactValidationReport:
    return ArtifactValidationReport(
        created_at=datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        results=results,
    )


def write_validation_report(report: ArtifactValidationReport, output_dir: str | Path) -> tuple[Path, Path, Path]:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / "artifact_validation_report.json"
    md_path = target / "artifact_validation_report.md"
    issues_path = target / "artifact_validation_issues.jsonl"
    payload = report.to_dict()
    write_json_artifact(json_path, payload, artifact_type="artifact_validation_report", producer="artifact_schema")
    md_path.write_text(_markdown(payload), encoding="utf-8")
    with issues_path.open("w", encoding="utf-8") as handle:
        for result in payload["results"]:
            for issue in result.get("issues", []):
                handle.write(json.dumps(issue, ensure_ascii=False, sort_keys=True))
                handle.write("\n")
    return json_path, md_path, issues_path


def _markdown(payload: dict) -> str:
    lines = [
        "# Artifact Validation Report",
        "",
        f"- artifact_count: `{payload.get('artifact_count', 0)}`",
        f"- error_count: `{payload.get('error_count', 0)}`",
        f"- warning_count: `{payload.get('warning_count', 0)}`",
        f"- legacy_artifact_count: `{payload.get('legacy_artifact_count', 0)}`",
        f"- unknown_artifact_count: `{payload.get('unknown_artifact_count', 0)}`",
        "",
        "| path | type | mode | valid | issues |",
        "| --- | --- | --- | --- | ---: |",
    ]
    for result in payload.get("results", []):
        lines.append(
            f"| `{result.get('path')}` | {result.get('artifact_type') or ''} | {result.get('compatibility_mode')} | {result.get('valid')} | {len(result.get('issues', []))} |"
        )
    return "\n".join(lines) + "\n"

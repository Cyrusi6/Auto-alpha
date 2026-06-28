"""Alpha Factory artifact writers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact


def write_campaign_report(report, output_dir: str | Path) -> tuple[Path, Path]:
    target = Path(output_dir)
    json_path = write_json_artifact(target / "alpha_factory_report.json", report.to_dict(), "alpha_factory_report", "alpha_factory")
    md_path = target / "alpha_factory_report.md"
    md_path.write_text(_markdown(report.to_dict()), encoding="utf-8")
    return json_path, md_path


def write_artifact_catalog(paths: dict[str, str], output_dir: str | Path, campaign_id: str) -> Path:
    entries = [
        {"name": name, "path": path, "kind": _kind(path), "stage": "alpha_factory"}
        for name, path in sorted(paths.items())
        if path
    ]
    return write_json_artifact(
        Path(output_dir) / "alpha_campaign_artifact_catalog.json",
        {"campaign_id": campaign_id, "entries": entries},
        "alpha_campaign_artifact_catalog",
        "alpha_factory",
    )


def write_generation_stats(candidates, warnings: list[str], output_dir: str | Path) -> Path:
    source_counts: dict[str, int] = {}
    family_counts: dict[str, int] = {}
    for item in candidates:
        source_counts[item.source] = source_counts.get(item.source, 0) + 1
        for family in item.family_tags:
            family_counts[family] = family_counts.get(family, 0) + 1
    return write_json_artifact(
        Path(output_dir) / "alpha_generation_stats.json",
        {
            "generated": len(candidates),
            "source_counts": source_counts,
            "family_counts": family_counts,
            "warning_count": len(warnings),
            "warnings": warnings,
        },
        "alpha_generation_stats",
        "alpha_factory",
    )


def write_jsonl(path: str | Path, records: list[dict[str, Any]], artifact_type: str) -> Path:
    return write_jsonl_artifact(path, records, artifact_type, "alpha_factory")


def _markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        f"# Alpha Factory Campaign: {payload.get('campaign_id')}",
        "",
        f"- status: {payload.get('status')}",
        f"- generated: {summary.get('candidates_generated')}",
        f"- static_passed: {summary.get('static_passed')}",
        f"- proxy_passed: {summary.get('proxy_passed')}",
        f"- full_eval_count: {summary.get('full_eval_count')}",
        f"- shortlist_count: {summary.get('shortlist_count')}",
        f"- best_score: {summary.get('best_score')}",
        "",
    ]
    return "\n".join(lines)


def _kind(path: str) -> str:
    if path.endswith(".jsonl"):
        return "jsonl"
    if path.endswith(".md"):
        return "markdown"
    return "json"

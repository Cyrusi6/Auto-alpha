"""Diversity-aware shortlist selection."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact


def select_shortlist(candidates, *, top_k: int, max_per_family: int, min_novelty_score: float) -> tuple[list, list, dict]:
    ranked = sorted(
        [item for item in candidates if item.status != "rejected" and item.novelty_score >= min_novelty_score],
        key=lambda item: item.final_score,
        reverse=True,
    )
    family_counts: dict[str, int] = {}
    shortlist = []
    rejected = []
    for candidate in ranked:
        family = (candidate.family_tags or ["general"])[0]
        if len(shortlist) >= top_k:
            rejected.append(replace(candidate, status="rejected", reject_reason="outside_top_k"))
            continue
        if family_counts.get(family, 0) >= max_per_family:
            rejected.append(replace(candidate, status="rejected", reject_reason="family_cap"))
            continue
        family_counts[family] = family_counts.get(family, 0) + 1
        shortlist.append(replace(candidate, status="shortlisted", diversity_group=family))
    selected_ids = {item.alpha_candidate_id for item in shortlist}
    for candidate in candidates:
        if candidate.alpha_candidate_id not in selected_ids and candidate.status == "rejected":
            rejected.append(candidate)
    report = {
        "shortlist_count": len(shortlist),
        "rejected_count": len(rejected),
        "family_counts": family_counts,
        "max_per_family": max_per_family,
        "min_novelty_score": min_novelty_score,
        "top_k": top_k,
        "warning_count": 0,
    }
    return shortlist, rejected, report


def write_diversity_outputs(shortlist, rejected, report: dict, output_dir: str | Path) -> dict[str, str]:
    target = Path(output_dir)
    short_path = write_jsonl_artifact(target / "alpha_shortlist.jsonl", [item.to_dict() for item in shortlist], "alpha_shortlist", "alpha_factory")
    rej_path = write_jsonl_artifact(target / "alpha_rejected.jsonl", [item.to_dict() for item in rejected], "alpha_rejected", "alpha_factory")
    div_path = write_json_artifact(target / "alpha_diversity_report.json", report, "alpha_diversity_report", "alpha_factory")
    md_path = target / "alpha_diversity_report.md"
    md_path.write_text(_markdown(report), encoding="utf-8")
    return {
        "alpha_shortlist_path": str(short_path),
        "alpha_rejected_path": str(rej_path),
        "alpha_diversity_report_path": str(div_path),
        "alpha_diversity_report_md_path": str(md_path),
    }


def _markdown(report: dict) -> str:
    lines = [
        "# Alpha Diversity Report",
        "",
        f"- shortlist_count: {report.get('shortlist_count')}",
        f"- rejected_count: {report.get('rejected_count')}",
        "",
        "| family | count |",
        "| --- | ---: |",
    ]
    for family, count in sorted((report.get("family_counts") or {}).items()):
        lines.append(f"| {family} | {count} |")
    return "\n".join(lines) + "\n"

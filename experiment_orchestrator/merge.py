"""Merge shard outputs from experiment jobs."""

from __future__ import annotations

import json
from pathlib import Path

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import ExperimentMergeReport


def merge_formula_batch_eval_results(shard_dirs: list[str | Path], output_dir: str | Path) -> ExperimentMergeReport:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    seen: set[str] = set()
    duplicates = 0
    missing = 0
    warnings: list[str] = []
    for shard_dir in shard_dirs:
        path = Path(shard_dir) / "formula_eval_results.jsonl"
        if not path.exists():
            alt = Path(shard_dir) / "formula_eval_results_shard_0.jsonl"
            path = alt if alt.exists() else path
        if not path.exists():
            missing += 1
            warnings.append(f"missing_shard:{shard_dir}")
            continue
        for row in _read_jsonl(path):
            request = row.get("request") if isinstance(row.get("request"), dict) else {}
            formula_hash = str(request.get("formula_hash") or row.get("formula_hash") or json.dumps(row, sort_keys=True))
            if formula_hash in seen:
                duplicates += 1
                continue
            seen.add(formula_hash)
            rows.append(row)
    result_payload = {
        "status": "success" if missing == 0 else "warning",
        "results": rows,
        "summary": {
            "merged_records": len(rows),
            "duplicate_formula_hash_count": duplicates,
            "missing_shard_count": missing,
        },
    }
    write_jsonl_artifact(output / "merged_formula_eval_results.jsonl", rows, "formula_eval_results", "experiment_orchestrator")
    write_json_artifact(output / "merged_formula_batch_eval_result.json", result_payload, "formula_batch_eval_result", "experiment_orchestrator")
    report = ExperimentMergeReport(
        status=result_payload["status"],
        shard_count=len(shard_dirs),
        merged_records=len(rows),
        duplicate_formula_hash_count=duplicates,
        missing_shard_count=missing,
        warnings=warnings,
        paths={
            "merged_formula_eval_results": str(output / "merged_formula_eval_results.jsonl"),
            "merged_formula_batch_eval_result": str(output / "merged_formula_batch_eval_result.json"),
            "experiment_merge_report": str(output / "experiment_merge_report.json"),
            "experiment_merge_report_md": str(output / "experiment_merge_report.md"),
        },
    )
    write_json_artifact(output / "experiment_merge_report.json", report.to_dict(), "experiment_merge_report", "experiment_orchestrator")
    (output / "experiment_merge_report.md").write_text(_render_merge_report(report), encoding="utf-8")
    return report


def merge_formula_search_results(shard_dirs: list[str | Path], output_dir: str | Path) -> ExperimentMergeReport:
    rows = []
    for shard_dir in shard_dirs:
        payload = _read_json(Path(shard_dir) / "search_result.json") or _read_json(Path(shard_dir) / "formula_search_result.json")
        if payload:
            rows.extend(payload.get("best_candidates", []) or payload.get("results", []))
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    write_json_artifact(output / "merged_search_result.json", {"best_candidates": rows}, "experiment_search_merge_result", "experiment_orchestrator")
    return ExperimentMergeReport(
        status="success",
        shard_count=len(shard_dirs),
        merged_records=len(rows),
        duplicate_formula_hash_count=0,
        missing_shard_count=0,
        warnings=[],
        paths={"merged_search_result": str(output / "merged_search_result.json")},
    )


def merge_factor_store_shards(shard_factor_store_dirs: list[str | Path], output_store_dir: str | Path) -> dict:
    output = Path(output_store_dir)
    output.mkdir(parents=True, exist_ok=True)
    copied = 0
    seen: set[str] = set()
    target = output / "factors.jsonl"
    with target.open("w", encoding="utf-8") as handle:
        for directory in shard_factor_store_dirs:
            for row in _read_jsonl(Path(directory) / "factors.jsonl"):
                factor_id = str(row.get("factor_id") or "")
                if factor_id in seen:
                    continue
                seen.add(factor_id)
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                copied += 1
    return {"output_store_dir": str(output), "factors": copied}


def merge_benchmark_results(shard_dirs: list[str | Path], output_dir: str | Path) -> dict:
    rows = [_read_json(Path(path) / "benchmark_result.json") for path in shard_dirs]
    rows = [row for row in rows if row]
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    payload = {"status": "success", "shards": rows}
    write_json_artifact(output / "merged_benchmark_result.json", payload, "gpu_benchmark_report", "experiment_orchestrator")
    return payload


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _render_merge_report(report: ExperimentMergeReport) -> str:
    return "\n".join(
        [
            "# Experiment Merge Report",
            "",
            f"- status: `{report.status}`",
            f"- shard_count: {report.shard_count}",
            f"- merged_records: {report.merged_records}",
            f"- missing_shard_count: {report.missing_shard_count}",
        ]
    ) + "\n"

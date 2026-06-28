"""Merge formula batch evaluation shard outputs."""

from __future__ import annotations

import json
from pathlib import Path

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact


def merge_shard_outputs(shard_dirs: list[str | Path], output_dir: str | Path) -> dict:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    seen: set[str] = set()
    duplicate_count = 0
    missing_count = 0
    for shard_dir in shard_dirs:
        path = Path(shard_dir) / "formula_eval_results.jsonl"
        if not path.exists():
            missing_count += 1
            continue
        for row in _read_jsonl(path):
            request = row.get("request") if isinstance(row.get("request"), dict) else {}
            formula_hash = str(request.get("formula_hash") or row.get("formula_hash") or json.dumps(row, sort_keys=True))
            if formula_hash in seen:
                duplicate_count += 1
                continue
            seen.add(formula_hash)
            rows.append(row)
    status_counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status", "unknown"))
        status_counts[status] = status_counts.get(status, 0) + 1
    payload = {
        "batch_id": "merged_formula_batch_eval",
        "status": "success" if missing_count == 0 else "warning",
        "results": rows,
        "summary": {
            "total": len(rows),
            "status_counts": status_counts,
            "duplicate_formula_hash_count": duplicate_count,
            "missing_shard_count": missing_count,
        },
        "paths": {
            "formula_batch_eval_result_path": str(output / "formula_batch_eval_result.json"),
            "formula_eval_results_path": str(output / "formula_eval_results.jsonl"),
        },
    }
    write_jsonl_artifact(output / "formula_eval_results.jsonl", rows, "formula_eval_results", "formula_batch_eval")
    write_json_artifact(output / "formula_batch_eval_result.json", payload, "formula_batch_eval_result", "formula_batch_eval")
    (output / "formula_batch_eval_report.md").write_text(_render(payload), encoding="utf-8")
    return payload


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _render(payload: dict) -> str:
    summary = payload.get("summary", {})
    return "\n".join(
        [
            "# Merged Formula Batch Evaluation",
            "",
            f"- status: `{payload.get('status')}`",
            f"- records: {summary.get('total', 0)}",
            f"- duplicate_formula_hash_count: {summary.get('duplicate_formula_hash_count', 0)}",
            f"- missing_shard_count: {summary.get('missing_shard_count', 0)}",
        ]
    ) + "\n"

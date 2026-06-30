"""Size reporting for governed real-data directories."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact

from .models import RealDataSizeReport


def compute_data_size_report(
    data_dir: str | Path,
    matrix_cache_dir: str | Path | None = None,
    freeze_dir: str | Path | None = None,
    staging_dir: str | Path | None = None,
) -> RealDataSizeReport:
    data_root = Path(data_dir)
    matrix_root = Path(matrix_cache_dir) if matrix_cache_dir else None
    freeze_root = Path(freeze_dir) if freeze_dir else None
    staging_root = Path(staging_dir) if staging_dir else None
    dataset_sizes: dict[str, int] = {}
    dataset_records: dict[str, int] = {}
    avg_record_sizes: dict[str, float] = {}
    if data_root.exists():
        for dataset_dir in sorted(path for path in data_root.iterdir() if path.is_dir()):
            records_path = dataset_dir / "records.jsonl"
            if not records_path.exists():
                continue
            size = records_path.stat().st_size
            records = _count_lines(records_path)
            dataset_sizes[dataset_dir.name] = size
            dataset_records[dataset_dir.name] = records
            avg_record_sizes[dataset_dir.name] = float(size / records) if records else 0.0

    total_size = _dir_size(data_root)
    matrix_size = _dir_size(matrix_root)
    freeze_size = _dir_size(freeze_root)
    staging_size = _dir_size(staging_root)
    cache_size = _dir_size(data_root / ".cache") if data_root.exists() else 0
    largest_files = _largest_files([data_root, matrix_root, freeze_root, staging_root], limit=20)
    return RealDataSizeReport(
        data_dir=str(data_root),
        matrix_cache_dir=str(matrix_root) if matrix_root else None,
        freeze_dir=str(freeze_root) if freeze_root else None,
        total_size_bytes=total_size,
        total_size_gb=float(total_size / (1024**3)),
        dataset_size_bytes=dataset_sizes,
        dataset_record_count=dataset_records,
        avg_record_size_bytes=avg_record_sizes,
        matrix_cache_size_bytes=matrix_size,
        freeze_size_bytes=freeze_size,
        staging_size_bytes=staging_size,
        cache_size_bytes=cache_size,
        largest_files=largest_files,
        estimated_full_size_if_partial=None,
    )


def write_size_report(report: RealDataSizeReport, output_dir: str | Path) -> tuple[Path, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = write_json_artifact(root / "real_data_size_report.json", report.to_dict(), "real_data_size_report", "real_data_ops")
    md_path = root / "real_data_size_report.md"
    payload = report.to_dict()
    lines = [
        "# Real Data Size Report",
        "",
        f"- total_size_gb: `{payload['total_size_gb']:.6f}`",
        f"- matrix_cache_size_bytes: `{payload['matrix_cache_size_bytes']}`",
        f"- freeze_size_bytes: `{payload['freeze_size_bytes']}`",
        f"- cache_size_bytes: `{payload['cache_size_bytes']}`",
        "",
        "| Dataset | Records | Size bytes | Avg record bytes |",
        "| --- | ---: | ---: | ---: |",
    ]
    for dataset, size in sorted(report.dataset_size_bytes.items()):
        lines.append(
            f"| {dataset} | {report.dataset_record_count.get(dataset, 0)} | {size} | {report.avg_record_size_bytes.get(dataset, 0.0):.2f} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def _count_lines(path: Path) -> int:
    count = 0
    with path.open("rb") as handle:
        for _ in handle:
            count += 1
    return count


def _dir_size(path: Path | None) -> int:
    if path is None or not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(child.stat().st_size for child in path.rglob("*") if child.is_file())


def _largest_files(roots: list[Path | None], limit: int) -> list[dict[str, Any]]:
    seen: set[Path] = set()
    files: list[tuple[int, Path]] = []
    for root in roots:
        if root is None or not root.exists():
            continue
        candidates = [root] if root.is_file() else list(root.rglob("*"))
        for path in candidates:
            if path.is_file() and path not in seen:
                seen.add(path)
                files.append((path.stat().st_size, path))
    return [
        {"path": str(path), "size_bytes": size}
        for size, path in sorted(files, reverse=True)[:limit]
    ]

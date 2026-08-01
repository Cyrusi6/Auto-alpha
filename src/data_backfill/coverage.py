"""Coverage analysis for governed backfill outputs."""

from __future__ import annotations

from pathlib import Path

from artifact_schema.writer import utc_now, write_json_artifact, write_jsonl_artifact
from data_pipeline.ashare.stats import compute_dataset_stats
from data_pipeline.ashare.storage import LocalAshareStorage

from .models import BackfillCoverageRecord, BackfillPlan, DatasetCoverageMatrix


def analyze_backfill_coverage(data_dir: str | Path, plan: BackfillPlan | None = None) -> DatasetCoverageMatrix:
    storage = LocalAshareStorage(data_dir)
    datasets = plan.scope.datasets if plan is not None else []
    if not datasets:
        datasets = sorted(path.parent.name for path in Path(data_dir).glob("*/records.jsonl"))
    records: list[BackfillCoverageRecord] = []
    gap_count = 0
    for dataset in datasets:
        gaps: list[str] = []
        if not storage.dataset_exists(dataset):
            gaps.append("missing_dataset")
            stats = None
        else:
            stats = compute_dataset_stats(storage, dataset)
            if stats.records == 0:
                gaps.append("empty_dataset")
            if stats.duplicate_keys > 0:
                gaps.append("duplicate_keys")
            if stats.first_trade_date is None and dataset != "securities":
                gaps.append("missing_date_range")
        gap_count += len(gaps)
        records.append(
            BackfillCoverageRecord(
                dataset=dataset,
                records=stats.records if stats else 0,
                unique_keys=stats.unique_keys if stats else 0,
                duplicate_keys=stats.duplicate_keys if stats else 0,
                first_date=stats.first_trade_date if stats else None,
                last_date=stats.last_trade_date if stats else None,
                ts_code_count=stats.ts_code_count if stats else 0,
                status="warning" if gaps else "ok",
                gaps=gaps,
            )
        )
    return DatasetCoverageMatrix(generated_at=utc_now(), records=records, gap_count=gap_count)


def write_backfill_coverage(matrix: DatasetCoverageMatrix, output_dir: str | Path) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    report_payload = {
        "generated_at": matrix.generated_at,
        "datasets": [record.to_dict() for record in matrix.records],
        "gap_count": matrix.gap_count,
        "status": "warning" if matrix.gap_count else "ok",
    }
    report_path = write_json_artifact(root / "backfill_coverage_report.json", report_payload, "backfill_coverage_report", "data_backfill")
    matrix_path = write_json_artifact(root / "backfill_coverage_matrix.json", matrix.to_dict(), "backfill_coverage_matrix", "data_backfill")
    gaps = [
        {"dataset": record.dataset, "gap": gap, "status": record.status}
        for record in matrix.records
        for gap in record.gaps
    ]
    gaps_path = write_jsonl_artifact(root / "backfill_coverage_gaps.jsonl", gaps, "backfill_coverage_gaps", "data_backfill")
    md_path = root / "backfill_coverage_report.md"
    lines = [
        "# Backfill Coverage Report",
        "",
        f"- Status: {report_payload['status']}",
        f"- Gap count: {matrix.gap_count}",
        "",
        "| Dataset | Records | Date Range | Gaps |",
        "| --- | ---: | --- | --- |",
    ]
    for record in matrix.records:
        lines.append(
            f"| {record.dataset} | {record.records} | {record.first_date or ''} - {record.last_date or ''} | {', '.join(record.gaps)} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "backfill_coverage_report_path": str(report_path),
        "backfill_coverage_matrix_path": str(matrix_path),
        "backfill_coverage_gaps_path": str(gaps_path),
        "backfill_coverage_report_md_path": str(md_path),
    }

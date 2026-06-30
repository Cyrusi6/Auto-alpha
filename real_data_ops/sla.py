"""SLA checks for real data backfills and matrix refreshes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from artifact_schema.writer import write_json_artifact, write_jsonl_artifact

from .models import RealDataSlaCheck, RealDataSlaReport


def build_real_data_sla_report(
    *,
    backfill_report: dict[str, Any],
    matrix_refresh_result: dict[str, Any] | None,
    required_datasets: list[str],
) -> RealDataSlaReport:
    checks: list[RealDataSlaCheck] = []
    backfill_status = str(backfill_report.get("status") or "")
    checks.append(
        RealDataSlaCheck(
            check_id="backfill_status",
            status="pass" if backfill_status == "success" else "warning" if backfill_status in {"warning", "blocked"} else "fail",
            value=backfill_status,
            threshold="success",
            message="backfill should complete successfully for production real data",
        )
    )
    summary = backfill_report.get("summary") if isinstance(backfill_report.get("summary"), dict) else {}
    failed_jobs = int(summary.get("failed_jobs", 0) or 0)
    checks.append(RealDataSlaCheck("failed_jobs", "pass" if failed_jobs == 0 else "fail", failed_jobs, 0))
    coverage = backfill_report.get("coverage") if isinstance(backfill_report.get("coverage"), dict) else {}
    missing = _missing_required_datasets(coverage, required_datasets)
    checks.append(RealDataSlaCheck("required_datasets", "pass" if not missing else "fail", missing, []))
    if matrix_refresh_result is not None:
        matrix_status = str(matrix_refresh_result.get("status") or "")
        checks.append(RealDataSlaCheck("matrix_refresh_status", "pass" if matrix_status in {"fresh", "refreshed", "success"} else "warning", matrix_status))
    fail_count = sum(check.status == "fail" for check in checks)
    warning_count = sum(check.status == "warning" for check in checks)
    status = "fail" if fail_count else "warning" if warning_count else "pass"
    return RealDataSlaReport(
        status=status,
        checks=checks,
        summary={"fail_count": fail_count, "warning_count": warning_count, "required_dataset_count": len(required_datasets)},
    )


def write_sla_report(report: RealDataSlaReport, output_dir: str | Path) -> tuple[Path, Path, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = write_json_artifact(root / "real_data_sla_report.json", report.to_dict(), "real_data_sla_report", "real_data_ops")
    checks_path = write_jsonl_artifact(
        root / "real_data_sla_checks.jsonl",
        [check.to_dict() for check in report.checks],
        "real_data_sla_checks",
        "real_data_ops",
    )
    md_path = root / "real_data_sla_report.md"
    lines = [
        "# Real Data SLA Report",
        "",
        f"- status: `{report.status}`",
        "",
        "| Check | Status | Value | Threshold |",
        "| --- | --- | --- | --- |",
    ]
    for check in report.checks:
        lines.append(f"| {check.check_id} | {check.status} | `{check.value}` | `{check.threshold}` |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path, checks_path


def _missing_required_datasets(coverage: dict[str, Any], required: list[str]) -> list[str]:
    records = coverage.get("records") if isinstance(coverage.get("records"), list) else []
    present = {str(item.get("dataset")) for item in records if int(item.get("records", 0) or 0) > 0}
    return [dataset for dataset in required if dataset not in present]

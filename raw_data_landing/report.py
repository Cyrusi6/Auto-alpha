"""Raw landing report builders and writers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from artifact_schema.writer import utc_now, write_json_artifact, write_jsonl_artifact
from data_pipeline.ashare.pipeline import ASHARE_DATASETS

from .coverage import build_coverage_matrix
from .gate import evaluate_freeze_readiness
from .models import RawDataLandingReport
from .quality import summarize_landing_checks
from .scanner import checks_from_raw_data_index, scan_datasets


def build_landing_report(
    data_dir: str | Path,
    datasets: Sequence[str] | None = None,
    profile_name: str | None = None,
    expected_trade_days: int | None = None,
    expected_security_count: int | None = None,
    index_codes: Sequence[str] | None = None,
    core_datasets: Sequence[str] | None = None,
    required_expanded_datasets: Sequence[str] | None = None,
    raw_data_index_manifest_path: str | Path | None = None,
    require_raw_data_index: bool = False,
) -> RawDataLandingReport:
    selected = list(datasets or ASHARE_DATASETS)
    checks, index_summary = _checks_with_optional_index(
        data_dir=data_dir,
        datasets=selected,
        raw_data_index_manifest_path=raw_data_index_manifest_path,
        require_raw_data_index=require_raw_data_index,
    )
    coverage = build_coverage_matrix(
        checks,
        expected_trade_days=expected_trade_days,
        expected_security_count=expected_security_count,
        expected_index_codes=len(index_codes or []),
    )
    decision = evaluate_freeze_readiness(checks, coverage, core_datasets=core_datasets, required_expanded_datasets=required_expanded_datasets)
    summary = summarize_landing_checks(checks)
    summary.update(
        {
            "raw_landing_status": "blocked" if decision.blocker_count else ("warning" if decision.warning_count else "ok"),
            "raw_freeze_readiness_status": decision.status,
            "raw_freeze_blocker_count": decision.blocker_count,
            "coverage_gap_count": sum(1 for row in coverage if row.status != "ok"),
            **index_summary,
        }
    )
    now = utc_now()
    return RawDataLandingReport(
        report_id=f"raw_landing_{now.replace(':', '').replace('-', '')}",
        generated_at=now,
        profile_name=profile_name,
        data_dir=str(data_dir),
        datasets=checks,
        coverage_matrix=coverage,
        freeze_readiness=decision,
        summary=summary,
    )


def _checks_with_optional_index(
    *,
    data_dir: str | Path,
    datasets: Sequence[str],
    raw_data_index_manifest_path: str | Path | None,
    require_raw_data_index: bool,
) -> tuple[list, dict]:
    summary = {
        "index_used": False,
        "index_status": "missing" if raw_data_index_manifest_path else "not_configured",
        "index_manifest_path": str(raw_data_index_manifest_path) if raw_data_index_manifest_path else None,
        "fallback_reason": "",
    }
    if raw_data_index_manifest_path:
        path = Path(raw_data_index_manifest_path)
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = {}
                summary["index_status"] = "malformed"
                summary["fallback_reason"] = "malformed_raw_data_index_manifest"
            if payload:
                summary["index_status"] = str(payload.get("status") or "unknown")
                indexed = checks_from_raw_data_index(payload, datasets)
                indexed_names = {item.dataset for item in indexed}
                missing = [dataset for dataset in datasets if dataset not in indexed_names]
                if payload.get("status") == "fresh" and not missing:
                    summary["index_used"] = True
                    summary["fallback_reason"] = ""
                    return indexed, summary
                summary["fallback_reason"] = "index_missing_selected_datasets" if missing else f"index_status_{summary['index_status']}"
        else:
            summary["fallback_reason"] = "raw_data_index_manifest_missing"
    if require_raw_data_index:
        checks = scan_datasets(data_dir, datasets)
        for check in checks:
            check.warnings.append(f"raw data index required but not used: {summary['fallback_reason'] or summary['index_status']}")
        summary["index_status"] = summary["index_status"] or "missing"
        return checks, summary
    return scan_datasets(data_dir, datasets), summary


def write_landing_artifacts(report: RawDataLandingReport, output_dir: str | Path) -> dict[str, str]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    report_path = write_json_artifact(root / "raw_data_landing_report.json", report.to_dict(), "raw_data_landing_report", "raw_data_landing")
    checks_path = write_jsonl_artifact(root / "raw_dataset_landing_checks.jsonl", [item.to_dict() for item in report.datasets], "raw_dataset_landing_checks", "raw_data_landing")
    coverage_path = write_json_artifact(
        root / "raw_dataset_coverage_matrix.json",
        {"datasets": [item.to_dict() for item in report.coverage_matrix], "summary": report.summary},
        "raw_dataset_coverage_matrix",
        "raw_data_landing",
    )
    decision_path = write_json_artifact(root / "raw_freeze_readiness_decision.json", report.freeze_readiness.to_dict(), "raw_freeze_readiness_decision", "raw_data_landing")
    freeze_checks_path = write_jsonl_artifact(root / "raw_freeze_readiness_checks.jsonl", report.freeze_readiness.checks, "raw_freeze_readiness_checks", "raw_data_landing")
    md_path = root / "raw_data_landing_report.md"
    md_path.write_text(_markdown(report), encoding="utf-8")
    return {
        "raw_data_landing_report_path": str(report_path),
        "raw_data_landing_report_md_path": str(md_path),
        "raw_dataset_landing_checks_path": str(checks_path),
        "raw_dataset_coverage_matrix_path": str(coverage_path),
        "raw_freeze_readiness_decision_path": str(decision_path),
        "raw_freeze_readiness_checks_path": str(freeze_checks_path),
    }


def _markdown(report: RawDataLandingReport) -> str:
    lines = [
        "# Raw Data Landing Report",
        "",
        f"- Data dir: `{report.data_dir}`",
        f"- Freeze readiness: `{report.freeze_readiness.status}`",
        f"- Blockers: {report.freeze_readiness.blocker_count}",
        f"- Warnings: {report.freeze_readiness.warning_count}",
        "",
        "| Dataset | Status | Records | Parse errors | Duplicates | First | Last |",
        "| --- | --- | ---: | ---: | ---: | --- | --- |",
    ]
    for item in report.datasets:
        lines.append(f"| {item.dataset} | {item.status} | {item.line_count} | {item.parse_error_count} | {item.duplicate_key_estimate} | {item.first_date or ''} | {item.last_date or ''} |")
    lines.extend(["", "## Freeze Blockers"])
    lines.extend(f"- {item}" for item in report.freeze_readiness.blockers)
    return "\n".join(lines) + "\n"


def dumps(payload: dict, pretty: bool = False) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None, sort_keys=pretty)

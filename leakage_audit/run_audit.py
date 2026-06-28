"""CLI for leakage audits."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from artifact_schema.writer import write_jsonl_artifact
from point_in_time.validator import validate_point_in_time_data

from .backtest_audit import audit_backtest_artifacts
from .factor_audit import audit_factor_values
from .models import LeakageAuditConfig, LeakageAuditReport, LeakageIssue, SurvivorshipAuditResult
from .report import write_leakage_audit_report
from .static_analysis import scan_formula_leakage
from .truncation import run_truncation_consistency_test


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit A-share formulas and artifacts for future-data leakage.")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--factor-store-dir")
    parser.add_argument("--formula-corpus-path")
    parser.add_argument("--candidates-json")
    parser.add_argument("--factor-id")
    parser.add_argument("--model-registry-dir")
    parser.add_argument("--model-version-id")
    parser.add_argument("--backtest-result-path")
    parser.add_argument("--orders-dir")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--as-of-date")
    parser.add_argument("--cutoff-date")
    parser.add_argument("--universe-name")
    parser.add_argument("--universe-file")
    parser.add_argument("--point-in-time", action="store_true")
    parser.add_argument("--feature-cutoff-mode", default="same_day_after_close")
    parser.add_argument("--min-listing-days", type=int, default=0)
    parser.add_argument("--exclude-st", action="store_true")
    parser.add_argument("--run-static-scan", action="store_true")
    parser.add_argument("--run-truncation-test", action="store_true")
    parser.add_argument("--max-formulas", type=int, default=5)
    parser.add_argument("--tolerance", type=float, default=1e-8)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--fail-on-blocker", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    formula_paths = [path for path in (args.formula_corpus_path, args.candidates_json) if path]
    formula_scan = scan_formula_leakage(formula_paths=formula_paths) if args.run_static_scan or formula_paths else scan_formula_leakage([])
    truncation = (
        run_truncation_consistency_test(args.data_dir, args.factor_store_dir, args.cutoff_date, args.max_formulas, args.tolerance)
        if args.run_truncation_test
        else run_truncation_consistency_test(args.data_dir, None, args.cutoff_date, args.max_formulas, args.tolerance)
    )
    active_mask_path = None
    survivorship = SurvivorshipAuditResult(False, 0, [])
    if args.point_in_time:
        pit_report, survivor, mask = validate_point_in_time_data(
            args.data_dir,
            as_of_date=args.as_of_date,
            min_listing_days=args.min_listing_days,
            exclude_st=args.exclude_st,
            feature_cutoff_mode=args.feature_cutoff_mode,
        )
        active_mask_path = Path(args.output_dir) / "active_security_mask.jsonl"
        write_jsonl_artifact(active_mask_path, [item.to_dict() for item in mask], "active_security_mask", "leakage_audit")
        survivorship = SurvivorshipAuditResult(
            current_only_security_master=survivor.current_only_security_master,
            warning_count=survivor.warning_count,
            issues=[LeakageIssue("warning", "current_only_security_master", msg, "securities") for msg in survivor.warnings],
        )
    factor_audit = audit_factor_values(args.factor_store_dir, args.factor_id, args.as_of_date, active_mask_path, args.point_in_time)
    backtest_audit = audit_backtest_artifacts(args.backtest_result_path, strict=args.strict)
    issues = [
        *formula_scan.issues,
        *truncation.issues,
        *factor_audit.issues,
        *backtest_audit.issues,
        *survivorship.issues,
    ]
    blocker_count = sum(issue.severity == "blocker" for issue in issues)
    error_count = sum(issue.severity == "error" for issue in issues)
    warning_count = sum(issue.severity == "warning" for issue in issues)
    config = LeakageAuditConfig(
        data_dir=args.data_dir,
        factor_store_dir=args.factor_store_dir,
        output_dir=args.output_dir,
        as_of_date=args.as_of_date,
        cutoff_date=args.cutoff_date,
        point_in_time=args.point_in_time,
        feature_cutoff_mode=args.feature_cutoff_mode,
        strict=args.strict,
    )
    report = LeakageAuditReport(
        created_at=_utc_now(),
        status="failed" if blocker_count or error_count else ("warning" if warning_count else "passed"),
        blocker_count=blocker_count,
        error_count=error_count,
        warning_count=warning_count,
        config=config,
        formula_scan=formula_scan,
        truncation_consistency=truncation,
        factor_value_leakage=factor_audit,
        backtest_leakage=backtest_audit,
        survivorship=survivorship,
        issues=issues,
    )
    paths = write_leakage_audit_report(report, args.output_dir)
    report = LeakageAuditReport(**{**report.__dict__, "paths": paths})
    write_leakage_audit_report(report, args.output_dir)
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    if args.fail_on_blocker and blocker_count:
        return 3
    return 0


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


if __name__ == "__main__":
    raise SystemExit(main())

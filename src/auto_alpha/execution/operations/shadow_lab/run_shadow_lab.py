"""CLI for shadow lab analysis."""

from __future__ import annotations

import argparse
import json
from datetime import datetime

from .calibration import build_calibration_suggestions
from .drift import summarize_shadow_drift
from .loader import load_shadow_inputs
from .models import ShadowLabConfig, ShadowLabIssue, ShadowLabReport
from .performance import summarize_shadow_performance
from .report import write_shadow_lab_report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze multi-day shadow replay artifacts.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["analyze", "compare", "calibrate", "report", "smoke"]:
        cmd = sub.add_parser(name)
        _add_args(cmd)
    return parser


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--replay-report-path")
    parser.add_argument("--replay-dir")
    parser.add_argument("--shadow-root-dir")
    parser.add_argument("--production-root-dir")
    parser.add_argument("--paper-account-dir")
    parser.add_argument("--settlement-dir")
    parser.add_argument("--portfolio-lab-report-path")
    parser.add_argument("--portfolio-certification-decision-path")
    parser.add_argument("--certified-portfolio-policy-path")
    parser.add_argument("--backtest-result-path")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--drift-threshold", type=float, default=0.05)
    parser.add_argument("--min-shadow-days", type=int, default=1)
    parser.add_argument("--pretty", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config = ShadowLabConfig(
        output_dir=args.output_dir,
        start_date=args.start_date,
        end_date=args.end_date,
        drift_threshold=args.drift_threshold,
        min_shadow_days=args.min_shadow_days,
        replay_report_path=args.replay_report_path,
        replay_dir=args.replay_dir,
        shadow_root_dir=args.shadow_root_dir,
        production_root_dir=args.production_root_dir,
        paper_account_dir=args.paper_account_dir,
        settlement_dir=args.settlement_dir,
        portfolio_lab_report_path=args.portfolio_lab_report_path,
        portfolio_certification_decision_path=args.portfolio_certification_decision_path,
        certified_portfolio_policy_path=args.certified_portfolio_policy_path,
        backtest_result_path=args.backtest_result_path,
    )
    days = load_shadow_inputs(args.replay_report_path, args.replay_dir, args.shadow_root_dir)
    if args.start_date:
        days = [day for day in days if day.trade_date >= args.start_date]
    if args.end_date:
        days = [day for day in days if day.trade_date <= args.end_date]
    performance = summarize_shadow_performance(days)
    drift = summarize_shadow_drift(days, threshold=args.drift_threshold)
    suggestions = build_calibration_suggestions(performance, drift)
    issues = []
    if len(days) < args.min_shadow_days:
        issues.append(
            ShadowLabIssue(
                "warning",
                "insufficient_shadow_days",
                f"shadow day count {len(days)} is below minimum {args.min_shadow_days}",
                {"shadow_day_count": len(days), "min_shadow_days": args.min_shadow_days},
            )
        )
    if not drift.get("passed", True):
        issues.append(ShadowLabIssue("warning", "drift_threshold_breached", "shadow drift exceeds threshold", drift))
    status = "warning" if issues or suggestions else "ok"
    report = ShadowLabReport(
        status=status,
        created_at=_utc_now(),
        config=config.to_dict(),
        day_summaries=days,
        performance_summary=performance,
        drift_summary=drift,
        calibration_suggestions=suggestions,
        issues=issues,
    )
    paths = write_shadow_lab_report(report, args.output_dir)
    payload = report.to_dict()
    payload["paths"] = paths
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    return 0


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


if __name__ == "__main__":
    raise SystemExit(main())

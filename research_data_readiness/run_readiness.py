"""CLI for read-only research data readiness assessment."""

from __future__ import annotations

import argparse

from .report import build_research_data_readiness_report, dumps, stdout_payload, write_research_data_readiness_artifacts


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Assess real-data research readiness without mutating data.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["assess", "feature-readiness", "decide", "report", "smoke"]:
        _add_common(sub.add_parser(name))
    return parser


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--run-dir")
    parser.add_argument("--observer-report-path")
    parser.add_argument("--dataset-progress-path")
    parser.add_argument("--raw-landing-report-path")
    parser.add_argument("--freeze-readiness-path")
    parser.add_argument("--repair-plan-path")
    parser.add_argument("--postprocess-plan-path")
    parser.add_argument("--real-data-sla-report-path")
    parser.add_argument("--matrix-freshness-report-path")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--profile-name")
    parser.add_argument("--expected-start-date")
    parser.add_argument("--expected-end-date")
    parser.add_argument("--expected-trade-days", type=int)
    parser.add_argument("--expected-security-count", type=int)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--fail-on-not-ready", action="store_true")
    parser.add_argument("--pretty", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = build_research_data_readiness_report(
        data_dir=args.data_dir,
        run_dir=args.run_dir,
        observer_report_path=args.observer_report_path,
        dataset_progress_path=args.dataset_progress_path,
        raw_landing_report_path=args.raw_landing_report_path,
        freeze_readiness_path=args.freeze_readiness_path,
        repair_plan_path=args.repair_plan_path,
        postprocess_plan_path=args.postprocess_plan_path,
        real_data_sla_report_path=args.real_data_sla_report_path,
        matrix_freshness_report_path=args.matrix_freshness_report_path,
        profile_name=args.profile_name,
        strict=args.strict,
    )
    paths = write_research_data_readiness_artifacts(report, args.output_dir)
    payload = stdout_payload(report, paths)
    if args.command == "feature-readiness":
        payload = {"feature_readiness": [item.to_dict() for item in report.feature_readiness], "paths": paths}
    elif args.command == "decide":
        payload = {"decision": report.decision.to_dict(), "paths": paths}
    elif args.command == "report":
        payload = {"report": report.to_dict(), "paths": paths}
    print(dumps(payload, args.pretty))
    if args.fail_on_not_ready and report.decision.status in {"not_ready", "insufficient_data"}:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""CLI for post-download orchestration planning."""

from __future__ import annotations

import argparse

from .planner import build_post_download_plan
from .executor import build_freeze_candidate_package, execute_post_download_plan
from .report import build_run_report, dumps, stdout_payload, write_post_download_artifacts


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan safe post-download processing for real A-share data.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["plan", "run", "report"]:
        _add_common(sub.add_parser(name))
    return parser


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--run-dir")
    parser.add_argument("--staging-dir")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--registry-dir")
    parser.add_argument("--freeze-dir")
    parser.add_argument("--matrix-cache-dir")
    parser.add_argument("--readiness-report-path")
    parser.add_argument("--profile-name")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--allow-real-data-path", action="store_true")
    parser.add_argument("--stop-after-step")
    parser.add_argument("--start-at-step")
    parser.add_argument("--refresh-step")
    parser.add_argument("--pretty", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    plan = build_post_download_plan(
        data_dir=args.data_dir,
        run_dir=args.run_dir,
        staging_dir=args.staging_dir,
        output_dir=args.output_dir,
        registry_dir=args.registry_dir,
        freeze_dir=args.freeze_dir,
        matrix_cache_dir=args.matrix_cache_dir,
        readiness_report_path=args.readiness_report_path,
        profile_name=args.profile_name,
        start_date=args.start_date,
        end_date=args.end_date,
        allow_incomplete=args.allow_incomplete,
    )
    mode = "execute" if args.execute else "plan_only"
    step_runs = []
    state = None
    package = None
    if args.command == "report":
        package = build_freeze_candidate_package(
            data_dir=args.data_dir,
            run_dir=args.run_dir,
            output_dir=args.output_dir,
            matrix_cache_dir=args.matrix_cache_dir,
            readiness_report_path=args.readiness_report_path,
            proposed_freeze_name=args.profile_name or "research_data_freeze",
        )
        status = "blocked" if package.status == "blocked_candidate" else "planned"
    elif args.execute:
        step_runs, state, package, _events = execute_post_download_plan(
            plan,
            data_dir=args.data_dir,
            run_dir=args.run_dir,
            output_dir=args.output_dir,
            matrix_cache_dir=args.matrix_cache_dir,
            readiness_report_path=args.readiness_report_path,
            execute=True,
            resume=args.resume,
            allow_incomplete=args.allow_incomplete,
            allow_real_data_path=args.allow_real_data_path,
            start_at_step=args.start_at_step,
            stop_after_step=args.stop_after_step,
            refresh_step=args.refresh_step,
        )
        status = "failed" if any(step.status == "failed" for step in step_runs) else ("blocked" if any(step.status == "blocked" for step in step_runs) else "success")
    else:
        status = "planned"
    report = build_run_report(plan, mode, status, step_runs, run_id=state.run_id if state is not None else None)
    paths = write_post_download_artifacts(plan, report, args.output_dir, step_runs=step_runs, state=state, freeze_candidate=package)
    print(dumps(stdout_payload(plan, report, paths), args.pretty))
    return 1 if args.execute and status in {"failed", "blocked"} else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""CLI for safe backfill repair batches."""

from __future__ import annotations

import argparse

from .planner import build_repair_batch_plan
from .report import dumps, stdout_payload, write_repair_artifacts
from .runner import run_repair_batch


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan and run explicit backfill repair batches.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "dry-run", "execute", "resume", "report"):
        _add_common(sub.add_parser(name))
    return parser


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--run-dir")
    parser.add_argument("--staging-dir")
    parser.add_argument("--repair-plan-path")
    parser.add_argument("--backfill-plan-path")
    parser.add_argument("--job-results-path")
    parser.add_argument("--state-path")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--run-commands", action="store_true")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--allow-real-data-path", action="store_true")
    parser.add_argument("--pretty", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    execute = args.execute or args.command == "execute"
    resume = args.resume or args.command == "resume"
    plan = build_repair_batch_plan(
        data_dir=args.data_dir,
        output_dir=args.output_dir,
        run_dir=args.run_dir,
        staging_dir=args.staging_dir,
        repair_plan_path=args.repair_plan_path,
        backfill_plan_path=args.backfill_plan_path,
        job_results_path=args.job_results_path,
        state_path=args.state_path,
        mode="execute" if execute else "dry_run",
    )
    report = run_repair_batch(
        plan,
        output_dir=args.output_dir,
        execute=execute,
        resume=resume,
        run_commands=args.run_commands,
        allow_network=args.allow_network,
        allow_real_data_path=args.allow_real_data_path,
    )
    paths = write_repair_artifacts(plan, report, args.output_dir)
    print(dumps(stdout_payload(plan, report, paths), args.pretty))
    return 1 if report.status in {"failed", "blocked"} and execute else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""CLI for local compute resource probing and job scheduling."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .gpu_probe import probe_compute_resources, write_resource_snapshot
from .job_store import LocalComputeJobStore
from .lease import GpuLeaseManager
from .models import ComputeDeviceType, ComputeJobKind, ComputeJobSpec, ComputeSchedulerConfig
from .report import write_compute_report
from .scheduler import LocalComputeScheduler


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run local compute plane commands.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["probe", "submit", "run", "resume", "list-jobs", "show-job", "cancel-job", "release-stale-leases", "report", "smoke"]:
        sp = sub.add_parser(name)
        sp.add_argument("--state-dir", required=True)
        sp.add_argument("--output-dir")
        sp.add_argument("--jobs-json")
        sp.add_argument("--job-spec-path")
        sp.add_argument("--job-id")
        sp.add_argument("--max-parallel-cpu-jobs", type=int, default=1)
        sp.add_argument("--max-parallel-gpu-jobs", type=int, default=1)
        sp.add_argument("--required-device-type", choices=["cpu", "cuda", "auto"], default="cpu")
        sp.add_argument("--preferred-devices")
        sp.add_argument("--min-free-memory-mb", type=float)
        sp.add_argument("--max-retries", type=int, default=0)
        sp.add_argument("--max-duration-seconds", type=float)
        sp.add_argument("--dry-run", action="store_true")
        sp.add_argument("--resume", action="store_true")
        sp.add_argument("--fail-fast", action="store_true")
        sp.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    output_dir = Path(args.output_dir or args.state_dir)
    if args.command == "probe":
        snapshot = probe_compute_resources()
        write_resource_snapshot(snapshot, output_dir)
        print(json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2 if args.pretty else None))
        return 0
    if args.command == "submit":
        jobs = _load_jobs(args)
        result = LocalComputeJobStore(args.state_dir).submit_jobs(jobs)
        print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
        return 0
    if args.command in {"run", "resume"}:
        scheduler = LocalComputeScheduler(
            ComputeSchedulerConfig(
                state_dir=args.state_dir,
                output_dir=str(output_dir),
                max_parallel_cpu_jobs=args.max_parallel_cpu_jobs,
                max_parallel_gpu_jobs=args.max_parallel_gpu_jobs,
                fail_fast=args.fail_fast,
                dry_run=args.dry_run,
                resume=args.resume or args.command == "resume",
            )
        )
        report = scheduler.run()
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2 if args.pretty else None))
        return 0 if report.status == "success" else 1
    if args.command == "list-jobs":
        store = LocalComputeJobStore(args.state_dir)
        state = store.load_state().get("jobs", {})
        rows = [job.to_dict() | state.get(job.job_id, {}) for job in store.list_jobs()]
        print(json.dumps({"jobs": rows}, ensure_ascii=False, indent=2 if args.pretty else None))
        return 0
    if args.command == "show-job":
        job = LocalComputeJobStore(args.state_dir).get_job(args.job_id or "")
        print(json.dumps(job.to_dict() if job else {}, ensure_ascii=False, indent=2 if args.pretty else None))
        return 0 if job else 1
    if args.command == "cancel-job":
        LocalComputeJobStore(args.state_dir).update_status(args.job_id or "", "cancelled")
        print(json.dumps({"job_id": args.job_id, "status": "cancelled"}, ensure_ascii=False, indent=2 if args.pretty else None))
        return 0
    if args.command == "release-stale-leases":
        count = GpuLeaseManager(args.state_dir).release_stale_leases()
        print(json.dumps({"released": count}, ensure_ascii=False, indent=2 if args.pretty else None))
        return 0
    if args.command == "report":
        report = write_compute_report("manual_report", args.state_dir, output_dir, probe_compute_resources())
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2 if args.pretty else None))
        return 0
    if args.command == "smoke":
        return _smoke(args, output_dir)
    return 1


def _smoke(args: argparse.Namespace, output_dir: Path) -> int:
    store = LocalComputeJobStore(args.state_dir)
    job = ComputeJobSpec(
        job_id="compute_smoke_cpu",
        job_kind=ComputeJobKind.SHELL_COMMAND,
        command=[sys.executable, "-c", "print('compute smoke ok')"],
        output_dir=str(output_dir / "jobs" / "compute_smoke_cpu"),
        required_device_type=ComputeDeviceType.CPU,
        max_retries=args.max_retries,
        max_duration_seconds=args.max_duration_seconds,
    )
    store.submit_jobs([job])
    report = LocalComputeScheduler(
        ComputeSchedulerConfig(
            state_dir=args.state_dir,
            output_dir=str(output_dir),
            max_parallel_cpu_jobs=args.max_parallel_cpu_jobs,
            max_parallel_gpu_jobs=args.max_parallel_gpu_jobs,
            dry_run=args.dry_run,
            resume=True,
        )
    ).run()
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if report.status == "success" else 1


def _load_jobs(args: argparse.Namespace) -> list[ComputeJobSpec]:
    path = args.jobs_json or args.job_spec_path
    if not path:
        raise SystemExit("--jobs-json or --job-spec-path is required")
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("jobs", payload) if isinstance(payload, dict) else payload
    jobs = []
    for row in rows:
        defaults = ComputeJobSpec(job_id="", job_kind=ComputeJobKind.SHELL_COMMAND, command=[]).to_dict()
        defaults.update(row)
        jobs.append(ComputeJobSpec(**defaults))
    return jobs


if __name__ == "__main__":
    raise SystemExit(main())

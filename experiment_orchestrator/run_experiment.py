"""CLI for local experiment planning, execution and merge."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from artifact_schema.writer import write_json_artifact

from .merge import merge_formula_batch_eval_results, merge_formula_search_results
from .planner import create_experiment_plan
from .workflows import run_workflow_smoke


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan and run local research compute experiments.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["plan", "run", "resume", "merge", "report", "smoke"]:
        sp = sub.add_parser(name)
        sp.add_argument("--workflow", default="full_research_compute_smoke")
        sp.add_argument("--data-freeze-dir")
        sp.add_argument("--data-version-manifest-path")
        sp.add_argument("--require-data-freeze", action="store_true")
        sp.add_argument("--data-dir")
        sp.add_argument("--factor-store-dir")
        sp.add_argument("--matrix-cache-dir")
        sp.add_argument("--formula-corpus-path")
        sp.add_argument("--candidates-json")
        sp.add_argument("--output-dir", required=True)
        sp.add_argument("--compute-state-dir")
        sp.add_argument("--gpu-count", type=int, default=0)
        sp.add_argument("--shard-count", type=int, default=1)
        sp.add_argument("--formulas-per-shard", type=int)
        sp.add_argument("--max-formulas", type=int)
        sp.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
        sp.add_argument("--use-ddp-pretrain", action="store_true")
        sp.add_argument("--pretrain-epochs", type=int, default=1)
        sp.add_argument("--pretrain-batch-size", type=int, default=8)
        sp.add_argument("--search-mode", choices=["random", "neural", "hybrid"], default="random")
        sp.add_argument("--search-generations", type=int, default=1)
        sp.add_argument("--search-population-size", type=int, default=8)
        sp.add_argument("--search-max-candidates", type=int)
        sp.add_argument("--batch-eval-chunk-size", type=int, default=4)
        sp.add_argument("--max-parallel-gpu-jobs", type=int, default=1)
        sp.add_argument("--max-parallel-cpu-jobs", type=int, default=1)
        sp.add_argument("--resume", action="store_true")
        sp.add_argument("--dry-run", action="store_true")
        sp.add_argument("--shard-dir", action="append", default=[])
        sp.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config = vars(args)
    if args.command == "plan":
        plan = create_experiment_plan(config)
        print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2 if args.pretty else None))
        return 0
    if args.command in {"run", "resume", "smoke"}:
        report = run_workflow_smoke(config | {"resume": args.resume or args.command == "resume"})
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2 if args.pretty else None))
        return 0 if report.status == "success" else 1
    if args.command == "merge":
        report = merge_formula_batch_eval_results(args.shard_dir, Path(args.output_dir) / "merged")
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2 if args.pretty else None))
        return 0 if report.status in {"success", "warning"} else 1
    if args.command == "report":
        payload = {"status": "ok", "output_dir": args.output_dir}
        write_json_artifact(Path(args.output_dir) / "experiment_run_report.json", payload, "experiment_run_report", "experiment_orchestrator")
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

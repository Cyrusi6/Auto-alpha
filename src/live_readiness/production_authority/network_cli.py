from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .application import apply_accepted_canary
from .executor import Task055JExecutionError, execute_single_canary, verify_and_accept_canary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Task 055-J single-canary production authority")
    subparsers = parser.add_subparsers(dest="command", required=True)

    canary = subparsers.add_parser("canary")
    canary.add_argument("--final-execution-seal", required=True)
    canary.add_argument("--reviewed-final-execution-seal-hash", required=True)
    canary.add_argument("--credential-file", required=True)
    canary.add_argument("--allow-network", action="store_true")

    acceptance = subparsers.add_parser("canary-verify")
    acceptance.add_argument("--final-execution-seal", required=True)
    acceptance.add_argument("--reviewed-final-execution-seal-hash", required=True)

    application = subparsers.add_parser("canary-apply")
    application.add_argument("--final-execution-seal", required=True)
    application.add_argument("--reviewed-final-execution-seal-hash", required=True)
    application.add_argument("--canary-acceptance", required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "canary":
            result = execute_single_canary(
                final_execution_seal=Path(args.final_execution_seal),
                reviewed_final_execution_seal_hash=str(args.reviewed_final_execution_seal_hash),
                credential_file=Path(args.credential_file),
                allow_network=bool(args.allow_network),
            )
        elif args.command == "canary-verify":
            result = verify_and_accept_canary(
                final_execution_seal=Path(args.final_execution_seal),
                reviewed_final_execution_seal_hash=str(args.reviewed_final_execution_seal_hash),
            )
        else:
            result = apply_accepted_canary(
                final_execution_seal=Path(args.final_execution_seal),
                reviewed_final_execution_seal_hash=str(args.reviewed_final_execution_seal_hash),
                canary_acceptance=Path(args.canary_acceptance),
            )
    except Exception as exc:
        print(json.dumps({"status": "blocked", "blocker": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    summary = {
        key: result[key]
        for key in (
            "status",
            "content_hash",
            "generation_id",
            "item_count",
            "physical_post_count",
            "action",
            "terminal_pair_count",
            "terminal_counts",
            "frontier_union_root",
            "resume_authorized",
            "batch_authorized",
        )
        if key in result
    }
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

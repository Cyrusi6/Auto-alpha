from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .executor import (
    Task055IExecutionError,
    execute_single_canary,
    verify_and_accept_canary,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Task 055-I single-canary production network authority"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    canary = subparsers.add_parser("canary")
    canary.add_argument("--runtime-authority", required=True)
    canary.add_argument("--reviewed-authority-hash", required=True)
    canary.add_argument("--credential-file", required=True)
    canary.add_argument("--allow-network", action="store_true")

    acceptance = subparsers.add_parser("canary-verify")
    acceptance.add_argument("--runtime-authority", required=True)
    acceptance.add_argument("--reviewed-authority-hash", required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "canary":
            result = execute_single_canary(
                runtime_authority=Path(args.runtime_authority),
                reviewed_authority_hash=str(args.reviewed_authority_hash),
                credential_file=Path(args.credential_file),
                allow_network=bool(args.allow_network),
            )
        else:
            result = verify_and_accept_canary(
                runtime_authority=Path(args.runtime_authority),
                reviewed_authority_hash=str(args.reviewed_authority_hash),
            )
    except Task055IExecutionError as exc:
        print(
            json.dumps({"status": "blocked", "blocker": str(exc)}, sort_keys=True),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

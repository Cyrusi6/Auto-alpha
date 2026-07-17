from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .network import publish_resume_authorization, verify_and_accept_canary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Task 055-H read-only canary acceptance plane")
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("canary-verify")
    verify.add_argument("--authorization-seal", required=True)
    verify.add_argument("--canary-execution", required=True)
    verify.add_argument("--output-root", required=True)
    resume = commands.add_parser("resume-authorize")
    resume.add_argument("--canary-acceptance", required=True)
    resume.add_argument("--reviewed-acceptance-hash", required=True)
    resume.add_argument("--output-root", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "canary-verify":
            result = verify_and_accept_canary(
                authorization_seal=args.authorization_seal,
                canary_execution_manifest=args.canary_execution,
                output_root=args.output_root,
            )
        else:
            result = publish_resume_authorization(
                canary_acceptance=args.canary_acceptance,
                reviewed_acceptance_hash=args.reviewed_acceptance_hash,
                output_root=args.output_root,
            )
    except Exception as exc:
        print(json.dumps({"status": "blocked", "blocker": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps({"status": result["status"], "content_hash": result["content_hash"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

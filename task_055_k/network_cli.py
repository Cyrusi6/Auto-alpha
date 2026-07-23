from __future__ import annotations

import argparse
import json
from typing import Sequence

from .gateway import execute_operator_authorized_single_canary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Task055-KR single-canary production gateway")
    subparsers = parser.add_subparsers(dest="command", required=True)
    canary = subparsers.add_parser("canary")
    canary.add_argument("--final-candidate-seal", required=True)
    canary.add_argument("--reviewed-final-candidate-seal-hash", required=True)
    canary.add_argument("--operator-authorization", required=True)
    canary.add_argument("--reviewed-operator-authorization-hash", required=True)
    canary.add_argument("--credential-file", required=True)
    canary.add_argument("--repository-root", required=True)
    canary.add_argument("--allow-network", action="store_true")
    args = parser.parse_args(argv)
    if not args.allow_network:
        print(json.dumps({"status": "blocked", "blocker": "explicit_allow_network_required"}))
        return 2
    try:
        accepted = execute_operator_authorized_single_canary(
            final_candidate_seal=args.final_candidate_seal,
            reviewed_final_candidate_seal_hash=args.reviewed_final_candidate_seal_hash,
            operator_authorization=args.operator_authorization,
            reviewed_operator_authorization_hash=args.reviewed_operator_authorization_hash,
            credential_file=args.credential_file,
            repository_root=args.repository_root,
        )
    except Exception as exc:
        print(json.dumps({"status": "blocked", "blocker": str(exc)}, sort_keys=True))
        return 2
    print(
        json.dumps(
            {
                "status": "single_canary_accepted",
                "acceptance_content_hash": accepted.acceptance["content_hash"],
                "transport_receipt_content_hash": accepted.receipt["content_hash"],
                "item_count": len(accepted.records),
                "resume_authorized": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

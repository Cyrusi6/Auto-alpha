from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .application import apply_accepted_response, production_context_from_parent
from .authority import validate_final_candidate_seal, validate_task055j_parent
from .broker import load_accepted_response
from .contracts import TASK055J_AUTHORITY_RELATIVE_ROOT, TASK055J_FINAL_SEAL_HASH


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply one native Task055-KR accepted response")
    parser.add_argument("--acceptance", required=True)
    parser.add_argument("--final-candidate-seal", required=True)
    parser.add_argument("--reviewed-final-candidate-seal-hash", required=True)
    parser.add_argument("--repository-root", required=True)
    args = parser.parse_args(argv)
    try:
        final_seal = validate_final_candidate_seal(
            args.final_candidate_seal,
            repository_root=args.repository_root,
            reviewed_hash=args.reviewed_final_candidate_seal_hash,
        )
        governed = Path(final_seal["governed_root"])
        parent = validate_task055j_parent(
            final_seal_path=_parent_final_seal(governed),
            repository_root=args.repository_root,
        )
        accepted = load_accepted_response(
            acceptance_path=args.acceptance,
            repository_root=args.repository_root,
            final_candidate_seal_path=args.final_candidate_seal,
        )
        result = apply_accepted_response(
            accepted=accepted,
            context=production_context_from_parent(parent),
            output_root=Path(final_seal["task_root"]) / "applications/real_single_canary",
            evidence_scope="real_production",
        )
    except Exception as exc:
        print(json.dumps({"status": "blocked", "blocker": str(exc)}, sort_keys=True))
        return 2
    print(
        json.dumps(
            {
                "status": result["status"],
                "application_content_hash": result["content_hash"],
                "terminal_pair_count": result["terminal_pair_count"],
            },
            sort_keys=True,
        )
    )
    return 0


def _parent_final_seal(governed: Path) -> Path:
    root = governed / TASK055J_AUTHORITY_RELATIVE_ROOT / "final_execution_seal/generations"
    matches = []
    for path in root.glob("*/final_execution_seal.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("content_hash") == TASK055J_FINAL_SEAL_HASH:
            matches.append(path)
    if len(matches) != 1:
        raise RuntimeError("task055k_parent_final_seal_resolution_invalid")
    return matches[0]


if __name__ == "__main__":
    raise SystemExit(main())

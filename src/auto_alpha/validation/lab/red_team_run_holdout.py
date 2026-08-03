"""CLI for candidate freezing and one-shot sealed-holdout validation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from auto_alpha.validation.lab.red_team_candidate_pool import freeze_candidate_pool
from auto_alpha.validation.lab.red_team_capability import HoldoutCapabilityRegistry
from auto_alpha.validation.lab.red_team_contracts import HoldoutCalibrationProfile
from auto_alpha.validation.lab.red_team_contracts import SealedHoldoutPolicy
from auto_alpha.validation.lab.red_team_contracts import publish_holdout_policy
from auto_alpha.validation.lab.red_team_evaluator import ValidationRedTeamAgent
from auto_alpha.validation.lab.red_team_preflight import preflight_canonical_holdout
from auto_alpha.validation.lab.red_team_verifier import verify_holdout_result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Governed one-shot sealed holdout")
    subparsers = parser.add_subparsers(dest="command", required=True)

    freeze = subparsers.add_parser("freeze-candidates")
    freeze.add_argument("--campaign-report", required=True)
    freeze.add_argument("--materialization-manifest", action="append", required=True)
    freeze.add_argument("--output-root", required=True)

    policy = subparsers.add_parser("publish-policy")
    policy.add_argument("--policy-spec", required=True)
    policy.add_argument("--output-root", required=True)

    issue = subparsers.add_parser("issue-capability")
    issue.add_argument("--registry-root", required=True)
    issue.add_argument("--candidate-pool-manifest", required=True)
    issue.add_argument("--holdout-view-manifest", required=True)
    issue.add_argument("--holdout-policy", required=True)
    issue.add_argument("--red-team-output-root", required=True)

    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("--capability", required=True)
    evaluate.add_argument("--reviewed-capability-hash", required=True)
    evaluate.add_argument("--device", default="cpu")

    verify = subparsers.add_parser("verify")
    verify.add_argument("--result-manifest", required=True)

    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--canonical-freeze-manifest", required=True)
    preflight.add_argument("--candidate-pool-manifest")
    preflight.add_argument("--output-root", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "freeze-candidates":
        path, payload = freeze_candidate_pool(
            args.campaign_report,
            args.materialization_manifest,
            args.output_root,
        )
    elif args.command == "publish-policy":
        spec = json.loads(Path(args.policy_spec).read_text(encoding="utf-8"))
        if not isinstance(spec, dict) or not isinstance(spec.get("profile"), dict):
            raise ValueError("policy spec must contain a profile object")
        policy = SealedHoldoutPolicy(
            policy_id=str(spec["policy_id"]),
            profile=HoldoutCalibrationProfile(**spec["profile"]),
        )
        path, payload = publish_holdout_policy(policy, args.output_root)
    elif args.command == "issue-capability":
        path, payload = HoldoutCapabilityRegistry(args.registry_root).issue(
            candidate_pool_manifest_path=args.candidate_pool_manifest,
            holdout_view_manifest_path=args.holdout_view_manifest,
            holdout_policy_path=args.holdout_policy,
            red_team_output_root=args.red_team_output_root,
        )
    elif args.command == "evaluate":
        path, payload = ValidationRedTeamAgent(
            args.capability,
            args.reviewed_capability_hash,
            device=args.device,
        ).evaluate()
    elif args.command == "verify":
        path = Path(args.result_manifest).resolve()
        payload = verify_holdout_result(path)
    else:
        path, payload = preflight_canonical_holdout(
            args.canonical_freeze_manifest,
            args.output_root,
            candidate_pool_manifest_path=args.candidate_pool_manifest,
        )
    print(json.dumps({"artifact_path": str(path), **payload}, ensure_ascii=False, sort_keys=True))
    return 2 if args.command == "preflight" and payload.get("status") == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())

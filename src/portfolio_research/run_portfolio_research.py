"""Production CLI for certified-factor portfolio research."""

from __future__ import annotations

import argparse
import json

from live_readiness.production_hardening.fees import FeeScheduleCalculator

from .bundle import load_portfolio_research_bundle
from .contracts import DATA_BLOCKED_STATUS, PortfolioResearchPolicy
from .engine import evaluate_portfolio_research
from .report import publish_portfolio_research_result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run locked factor-certified portfolio walk-forward research.")
    parser.add_argument("--bundle-manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--policy-id", default="factor_certified_portfolio_walk_forward_v1")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        policy = PortfolioResearchPolicy(policy_id=args.policy_id)
        data, fee_path, bundle = load_portfolio_research_bundle(args.bundle_manifest)
        calculator = FeeScheduleCalculator(fee_path)
        result = evaluate_portfolio_research(data, policy, fee_calculator=calculator)
        published = publish_portfolio_research_result(result, args.output_dir)
        payload = {
            "status": result["status"],
            "bundle_content_hash": bundle["content_hash"],
            "policy_hash": policy.policy_hash,
            "factor_certified_count": int(result.get("factor_certified_count") or 0),
            "walk_forward_window_count": int(result.get("walk_forward_window_count") or 0),
            "shadow_ready": bool(result.get("shadow_ready")),
            "paper_ready": False,
            "live_ready": False,
            "paths": published,
        }
    except Exception as exc:
        payload = {"status": DATA_BLOCKED_STATUS, "error": f"{type(exc).__name__}:{exc}"}
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    return 2 if result["status"] == DATA_BLOCKED_STATUS else 0


if __name__ == "__main__":
    raise SystemExit(main())

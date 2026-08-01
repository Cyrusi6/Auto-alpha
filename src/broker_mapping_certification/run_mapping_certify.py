"""CLI for broker mapping certification."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .certifier import certify_broker_file_mapping
from .policy import load_certification_policy


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Certify broker file mapping profiles for dry-run outbox.")
    parser.add_argument("command", nargs="?", choices=["init-policy", "run", "scorecard", "decide", "report", "smoke"])
    parser.add_argument("--profile-name", default="generic_broker_csv")
    parser.add_argument("--profile-config")
    parser.add_argument("--policy", dest="policy_name", default="dry_run_standard")
    parser.add_argument("--policy-profile", dest="policy_name")
    parser.add_argument("--policy-config", dest="policy_config")
    parser.add_argument("--policy-path", dest="policy_config")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--gateway-store-dir")
    parser.add_argument("--handoff-store-dir")
    parser.add_argument("--approval-store-dir")
    parser.add_argument("--work-dir")
    parser.add_argument("--trade-date", default="20240104")
    parser.add_argument("--run-roundtrip", action="store_true")
    parser.add_argument("--run-eod-reconciliation", action="store_true")
    parser.add_argument("--handoff-package-path")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    command = args.command or ("smoke" if args.smoke else "run")
    if command == "init-policy":
        policy = load_certification_policy(args.policy_name, args.policy_config)
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / "broker_mapping_certification_policy.json"
        path.write_text(json.dumps(policy.to_dict(), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        payload = {"status": "success", "policy": policy.to_dict(), "policy_path": str(path)}
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
        return 0

    package = certify_broker_file_mapping(
        profile_name=args.profile_name,
        profile_config=args.profile_config,
        policy_name=args.policy_name,
        policy_config=args.policy_config,
        output_dir=args.output_dir,
        gateway_store_dir=args.gateway_store_dir,
        trade_date=args.trade_date,
    )
    payload = package.to_dict()
    payload["command"] = command
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    return 0 if package.decision.status in {"certified_for_dry_run", "conditional"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

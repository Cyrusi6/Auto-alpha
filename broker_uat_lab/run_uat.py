"""CLI for local BrokerAdapter UAT."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from broker_adapter import FileInstructionBrokerAdapter, SimulatedBrokerAdapter

from .contract import run_broker_adapter_contract_suite
from .mock_broker import DeterministicMockBrokerAdapter
from .models import BrokerUatPlan
from .replay import replay_broker_events
from .report import write_broker_uat_artifacts
from .scenarios import build_default_uat_scenarios


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run local BrokerAdapter UAT scenarios.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["plan", "run", "run-contract", "replay", "report", "smoke"]:
        cmd = sub.add_parser(name)
        _add_args(cmd)
    return parser


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--broker-store-dir")
    parser.add_argument("--gateway-store-dir")
    parser.add_argument("--paper-account-dir")
    parser.add_argument("--settlement-dir")
    parser.add_argument("--risk-control-state-dir")
    parser.add_argument("--profile", choices=["sample", "strict"], default="sample")
    parser.add_argument("--adapter", choices=["mock", "simulated", "file"], default="mock")
    parser.add_argument("--file-profile", default="generic_broker_csv")
    parser.add_argument("--scenario-config")
    parser.add_argument("--run-file-roundtrip", action="store_true")
    parser.add_argument("--run-eod-reconciliation", action="store_true")
    parser.add_argument("--run-settlement-reconciliation", action="store_true")
    parser.add_argument("--run-kill-switch-test", action="store_true")
    parser.add_argument("--pretty", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    scenarios = build_default_uat_scenarios(args.profile)
    plan = BrokerUatPlan(plan_id=f"broker_uat_plan_{args.profile}_{args.adapter}", profile=args.profile, adapter=args.adapter, scenarios=scenarios)
    broker_store = Path(args.broker_store_dir or Path(args.output_dir) / "broker_store")
    adapter = _build_adapter(args, broker_store)
    if args.command == "plan":
        payload = plan.to_dict()
        print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
        return 0
    if args.command == "replay":
        replay = replay_broker_events(broker_store)
        print(json.dumps(replay, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
        return 0
    contract = run_broker_adapter_contract_suite(adapter, scenarios, adapter_name=args.adapter)
    replay = replay_broker_events(broker_store)
    paths = write_broker_uat_artifacts(output_dir=args.output_dir, plan=plan, contract_report=contract, replay_report=replay)
    payload = {"status": contract.status, "summary": {"failed_count": contract.failed_count, "warning_count": contract.warning_count}, "paths": paths}
    print(json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=args.pretty))
    return 1 if contract.failed_count else 0


def _build_adapter(args: argparse.Namespace, broker_store: Path):
    if args.adapter == "mock":
        return DeterministicMockBrokerAdapter(broker_store)
    if args.adapter == "simulated":
        return SimulatedBrokerAdapter(broker_store, prices={"000001.SZ": 10.0}, volumes={"000001.SZ": 100000.0}, auto_fill=True, risk_control_state_dir=args.risk_control_state_dir)
    outbox = Path(args.gateway_store_dir or args.output_dir) / "outbox"
    inbox = Path(args.gateway_store_dir or args.output_dir) / "inbox"
    return FileInstructionBrokerAdapter(broker_store, outbox, inbox)


if __name__ == "__main__":
    raise SystemExit(main())

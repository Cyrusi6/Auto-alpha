"""CLI for local BrokerAdapter UAT."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from broker_adapter import FileInstructionBrokerAdapter, SimulatedBrokerAdapter
from broker_connectivity.network_guard import build_network_guard
from broker_connectivity.probe import run_connectivity_probe
from broker_connectivity.profiles import build_broker_connection_profile, profile_hash
from broker_connectivity.readonly_client import build_readonly_client
from broker_connectivity.report import write_connectivity_artifacts
from broker_connectivity.session_store import LocalBrokerConnectivityStore, build_session
from broker_readonly_mirror.mirror_store import LocalBrokerReadonlyMirrorStore
from broker_readonly_mirror.models import BrokerReadonlySnapshot, BrokerReadonlySnapshotStatus
from broker_readonly_mirror.normalizer import normalize_readonly_payload
from broker_readonly_mirror.reconciliation import reconcile_readonly_mirror
from broker_readonly_mirror.report import write_readonly_mirror_artifacts

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
    parser.add_argument("--connectivity-profile", default="mock_readonly")
    parser.add_argument("--connectivity-store-dir")
    parser.add_argument("--run-readonly-connectivity", action="store_true")
    parser.add_argument("--run-readonly-mirror", action="store_true")
    parser.add_argument("--pretty", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    readonly_paths = _build_readonly_evidence(args) if args.run_readonly_connectivity or args.run_readonly_mirror else {}
    scenarios = build_default_uat_scenarios(args.profile, include_readonly=bool(readonly_paths), readonly_metadata=readonly_paths)
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
    if readonly_paths:
        payload["paths"].update(readonly_paths)
        payload["summary"].update(
            {
                "readonly_connectivity_scenario_count": sum(1 for item in scenarios if item.scenario_type.startswith("readonly") or item.scenario_type in {"credential_redaction", "network_guard"}),
                "real_submit_supported": False,
            }
        )
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


def _build_readonly_evidence(args: argparse.Namespace) -> dict[str, str]:
    output = Path(args.output_dir)
    conn_dir = output / "broker_connectivity"
    mirror_dir = output / "broker_readonly_mirror"
    profile = build_broker_connection_profile(args.connectivity_profile)
    guard = build_network_guard(profile, allow_network=False)
    client = build_readonly_client(profile, guard, "paper_account", "20240104", "20240104")
    probe = run_connectivity_probe(profile, guard, client, account_id="paper_account", trade_date="20240104", as_of_date="20240104")
    store = LocalBrokerConnectivityStore(args.connectivity_store_dir or conn_dir / "store")
    p_hash = profile_hash(profile)
    session = build_session(p_hash, profile.profile_name, profile.broker_name, "paper_account", "20240104", "20240104", None, probe.status, str(conn_dir / "broker_connectivity_probe_report.json"))
    session = store.save_session(session, refresh=True)
    paths = write_connectivity_artifacts(output_dir=conn_dir, profile=profile, guard=guard, probe_result=probe, session=session)
    if args.run_readonly_mirror:
        metadata = probe.metadata
        normalized = normalize_readonly_payload(
            {
                "account_snapshot": probe.account_snapshot,
                "positions": metadata.get("positions", []),
                "orders": metadata.get("orders", []),
                "fills": metadata.get("fills", []),
                "statements": metadata.get("statements", []),
            },
            account_id=probe.account_id,
            broker_name=probe.broker_name,
            trade_date=probe.trade_date,
            as_of_date=probe.as_of_date,
        )
        issues = [issue.to_dict() for issue in normalized.get("issues", [])]
        snapshot = BrokerReadonlySnapshot(
            snapshot_id=f"readonly_snapshot_{session.session_id}",
            connectivity_session_id=session.session_id,
            account_id=probe.account_id,
            broker_name=probe.broker_name,
            trade_date=probe.trade_date,
            as_of_date=probe.as_of_date,
            status=BrokerReadonlySnapshotStatus.warning if issues else BrokerReadonlySnapshotStatus.success,
            cash=normalized["cash"].to_dict() if normalized.get("cash") else {},
            positions=[item.to_dict() for item in normalized.get("positions", [])],
            orders=[item.to_dict() for item in normalized.get("orders", [])],
            fills=[item.to_dict() for item in normalized.get("fills", [])],
            statements=[item.to_dict() for item in normalized.get("statements", [])],
            source_hash=str(normalized.get("source_hash") or ""),
            created_at=session.created_at,
            issues=issues,
            metadata={"source": "broker_uat_lab", "real_submit_supported": False},
        )
        snapshot = LocalBrokerReadonlyMirrorStore(mirror_dir / "store").save_snapshot(snapshot, refresh=True)
        reconciliation = reconcile_readonly_mirror(snapshot, paper_account_dir=args.paper_account_dir, broker_store_dir=args.broker_store_dir, settlement_dir=args.settlement_dir)
        paths.update(write_readonly_mirror_artifacts(output_dir=mirror_dir, snapshot=snapshot, reconciliation_report=reconciliation))
    return paths


if __name__ == "__main__":
    raise SystemExit(main())

"""CLI for local read-only broker connectivity UAT."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from approval import ApprovalBatch, LocalApprovalStore
from artifact_schema.writer import write_json_artifact

from .credentials import write_credential_ref_manifest
from .network_guard import build_network_guard, write_network_guard_report
from .probe import run_connectivity_probe
from .profiles import build_broker_connection_profile, load_broker_connection_profile, profile_hash
from .readonly_client import build_readonly_client
from .report import write_connectivity_artifacts
from .session_store import LocalBrokerConnectivityStore, build_session


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only broker connectivity UAT tools.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["init-profile", "validate-profile", "create-review", "probe", "show-session", "report", "smoke"]:
        cmd = sub.add_parser(name)
        _add_args(cmd)
    return parser


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile-name", default="mock_readonly")
    parser.add_argument("--profile-config")
    parser.add_argument("--connectivity-store-dir")
    parser.add_argument("--approval-store-dir")
    parser.add_argument("--approval-id")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--trade-date", default="20240104")
    parser.add_argument("--as-of-date", default="20240104")
    parser.add_argument("--account-id", default="paper_account")
    parser.add_argument("--broker-name")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--require-approval", action="store_true")
    parser.add_argument("--require-credentials", action="store_true")
    parser.add_argument("--fail-on-blocked", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--reviewer")
    parser.add_argument("--comment")
    parser.add_argument("--pretty", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    profile = _load_profile(args)
    root = Path(args.output_dir)
    root.mkdir(parents=True, exist_ok=True)
    if args.command in {"init-profile", "validate-profile"}:
        paths = _write_profile_outputs(root, profile, args)
        payload = {"status": "passed", "profile": profile.to_dict(), "profile_hash": profile_hash(profile), "paths": paths}
        print(_json(payload, args.pretty))
        return 0
    if args.command == "create-review":
        paths = _write_profile_outputs(root, profile, args)
        approval = _create_review(args, profile, paths)
        payload = {"status": "pending_review", "approval": approval.to_dict(), "approval_id": approval.approval_id, "paths": paths}
        print(_json(payload, args.pretty))
        return 0
    if args.command == "show-session":
        store = LocalBrokerConnectivityStore(args.connectivity_store_dir or root)
        payload = {"sessions": [session.to_dict() for session in store.load_sessions()]}
        print(_json(payload, args.pretty))
        return 0
    paths, status = _run_probe(args, profile)
    payload = {"status": status, "paths": paths}
    print(_json(payload, args.pretty))
    return 1 if args.fail_on_blocked and status == "blocked" else 0


def _run_probe(args: argparse.Namespace, profile) -> tuple[dict[str, str], str]:
    guard = build_network_guard(
        profile,
        allow_network=args.allow_network,
        approval_store_dir=args.approval_store_dir,
        approval_id=args.approval_id,
        require_approval=args.require_approval,
    )
    client = build_readonly_client(profile, guard, args.account_id, args.trade_date, args.as_of_date)
    probe = run_connectivity_probe(
        profile,
        guard,
        client,
        account_id=args.account_id,
        trade_date=args.trade_date,
        as_of_date=args.as_of_date,
        require_credentials=args.require_credentials,
    )
    p_hash = profile_hash(profile)
    store = LocalBrokerConnectivityStore(args.connectivity_store_dir or args.output_dir)
    session = build_session(
        p_hash,
        profile.profile_name,
        profile.broker_name,
        args.account_id,
        args.trade_date,
        args.as_of_date,
        args.approval_id,
        probe.status,
        probe_report_path=str(Path(args.output_dir) / "broker_connectivity_probe_report.json"),
    )
    session = store.save_session(session, refresh=args.refresh)
    paths = write_connectivity_artifacts(output_dir=args.output_dir, profile=profile, guard=guard, probe_result=probe, session=session)
    return paths, probe.status


def _create_review(args: argparse.Namespace, profile, paths: dict[str, str]) -> ApprovalBatch:
    if not args.approval_store_dir:
        raise ValueError("--approval-store-dir is required for create-review")
    approval = ApprovalBatch(
        approval_id=f"broker_connectivity_review_{_utc_id()}",
        created_at=_utc_now(),
        factor_id="broker_connectivity",
        factor_type="review",
        rebalance_date="",
        portfolio_method="not_applicable",
        orders=[],
        approval_type="broker_connectivity_review",
        broker_connectivity_profile_path=paths.get("broker_connectivity_profile_path"),
        broker_connectivity_summary={
            "broker_name": profile.broker_name,
            "profile_name": profile.profile_name,
            "connectivity_mode": profile.connectivity_mode,
            "readonly_only": True,
            "real_submit_supported": False,
        },
        metadata={"reviewer": args.reviewer or "", "comment": args.comment or ""},
    )
    LocalApprovalStore(args.approval_store_dir).save_batch(approval)
    return approval


def _write_profile_outputs(root: Path, profile, args: argparse.Namespace) -> dict[str, str]:
    guard = build_network_guard(profile, allow_network=args.allow_network, approval_store_dir=args.approval_store_dir, approval_id=args.approval_id, require_approval=args.require_approval)
    paths = {
        "broker_connectivity_profile_path": str(write_json_artifact(root / "broker_connectivity_profile.json", profile.to_dict(), "broker_connectivity_profile", "broker_connectivity")),
        "broker_credential_ref_manifest_path": str(write_credential_ref_manifest(root / "broker_credential_ref_manifest.json", profile)),
        "broker_network_guard_report_path": str(write_network_guard_report(root / "broker_network_guard_report.json", profile, guard)),
    }
    return paths


def _load_profile(args: argparse.Namespace):
    if args.profile_config:
        return load_broker_connection_profile(args.profile_config)
    return build_broker_connection_profile(args.profile_name, broker_name=args.broker_name, account_id=args.account_id)


def _json(payload: dict, pretty: bool) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None, sort_keys=pretty)


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _utc_id() -> str:
    return _utc_now().replace("-", "").replace(":", "").replace("Z", "")


if __name__ == "__main__":
    raise SystemExit(main())


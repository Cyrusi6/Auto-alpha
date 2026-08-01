"""CLI for local pre-trade risk controls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from approval import LocalApprovalStore

from .kill_switch import activate_kill_switch, deactivate_kill_switch, load_kill_switch
from .order_gate import evaluate_orders_file
from .overrides import apply_approved_override, create_override_approval
from .policy import default_policy, load_policy, validate_policy, write_policy, write_policy_manifest
from .state import LocalRiskControlState


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run local A-share pre-trade risk controls.")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init-policy")
    init.add_argument("--output-dir", required=True)
    init.add_argument("--profile", default="cn_ashare_paper_default")
    init.add_argument("--pretty", action="store_true")

    validate = sub.add_parser("validate-policy")
    validate.add_argument("--policy-path", required=True)
    validate.add_argument("--output-dir")
    validate.add_argument("--pretty", action="store_true")

    for name, scope in [
        ("evaluate-orders", "order"),
        ("evaluate-child-orders", "child_order"),
        ("evaluate-broker-requests", "broker_request"),
    ]:
        cmd = sub.add_parser(name)
        cmd.set_defaults(scope=scope)
        cmd.add_argument("--orders-path", required=True)
        cmd.add_argument("--policy-path")
        cmd.add_argument("--policy-profile", default="cn_ashare_paper_default")
        cmd.add_argument("--state-dir", required=True)
        cmd.add_argument("--output-dir", required=True)
        cmd.add_argument("--batch-id", default="")
        cmd.add_argument("--trade-date")
        cmd.add_argument("--allow-clipping", action="store_true")
        cmd.add_argument("--fail-on-breach", action="store_true")
        cmd.add_argument("--pretty", action="store_true")

    activate = sub.add_parser("activate-kill-switch")
    activate.add_argument("--state-dir", required=True)
    activate.add_argument("--reason", required=True)
    activate.add_argument("--actor", default="local_user")
    activate.add_argument("--pretty", action="store_true")

    deactivate = sub.add_parser("deactivate-kill-switch")
    deactivate.add_argument("--state-dir", required=True)
    deactivate.add_argument("--reason", required=True)
    deactivate.add_argument("--actor", default="local_user")
    deactivate.add_argument("--approval-id")
    deactivate.add_argument("--pretty", action="store_true")

    show = sub.add_parser("show-kill-switch")
    show.add_argument("--state-dir", required=True)
    show.add_argument("--pretty", action="store_true")

    create = sub.add_parser("create-override-approval")
    create.add_argument("--approval-store-dir", required=True)
    create.add_argument("--state-dir", required=True)
    create.add_argument("--output-dir", required=True)
    create.add_argument("--scope", default="global")
    create.add_argument("--reason", required=True)
    create.add_argument("--requested-by", default="local_user")
    create.add_argument("--expires-at")
    create.add_argument("--max-usage-count", type=int)
    create.add_argument("--pretty", action="store_true")

    apply = sub.add_parser("apply-approved-override")
    apply.add_argument("--approval-store-dir", required=True)
    apply.add_argument("--approval-id", required=True)
    apply.add_argument("--state-dir", required=True)
    apply.add_argument("--actor", default="local_user")
    apply.add_argument("--deactivate-kill-switch", action="store_true")
    apply.add_argument("--pretty", action="store_true")

    usage = sub.add_parser("show-usage")
    usage.add_argument("--state-dir", required=True)
    usage.add_argument("--pretty", action="store_true")

    report = sub.add_parser("report")
    report.add_argument("--state-dir", required=True)
    report.add_argument("--pretty", action="store_true")

    smoke = sub.add_parser("smoke")
    smoke.add_argument("--output-dir", required=True)
    smoke.add_argument("--state-dir")
    smoke.add_argument("--policy-profile", default="strict_paper_gate")
    smoke.add_argument("--pretty", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    command = args.command
    if command == "init-policy":
        output = Path(args.output_dir)
        output.mkdir(parents=True, exist_ok=True)
        policy = default_policy(args.profile)
        policy_path = write_policy(policy, output / "risk_control_policy.json")
        manifest_path = write_policy_manifest(policy, output / "risk_control_policy_manifest.json", policy_path=policy_path)
        payload = {"policy_path": str(policy_path), "manifest_path": str(manifest_path), "policy": policy.to_dict()}
        _print(payload, args.pretty)
        return 0
    if command == "validate-policy":
        policy = load_policy(args.policy_path)
        manifest = validate_policy(policy, policy_path=args.policy_path)
        if args.output_dir:
            write_policy_manifest(policy, Path(args.output_dir) / "risk_control_policy_manifest.json", policy_path=args.policy_path)
        _print(manifest.to_dict(), args.pretty)
        return 0 if manifest.status == "valid" else 1
    if command.startswith("evaluate-"):
        report, _orders, paths = evaluate_orders_file(
            args.orders_path,
            policy_path=args.policy_path,
            policy_profile=args.policy_profile,
            state_dir=args.state_dir,
            output_dir=args.output_dir,
            batch_id=args.batch_id,
            trade_date=args.trade_date,
            scope=args.scope,
            allow_clipping=args.allow_clipping,
        )
        payload = report.to_dict() | {"paths": {key: str(value) for key, value in paths.items()}}
        _print(payload, args.pretty)
        return 1 if args.fail_on_breach and report.rejected_orders > 0 else 0
    if command == "activate-kill-switch":
        state = activate_kill_switch(args.state_dir, args.reason, args.actor)
        _print(state.to_dict(), args.pretty)
        return 0
    if command == "deactivate-kill-switch":
        state = deactivate_kill_switch(args.state_dir, args.reason, args.actor, approval_id=args.approval_id)
        _print(state.to_dict(), args.pretty)
        return 0
    if command == "show-kill-switch":
        _print(load_kill_switch(args.state_dir).to_dict(), args.pretty)
        return 0
    if command == "create-override-approval":
        request, batch, request_path = create_override_approval(
            approval_store_dir=args.approval_store_dir,
            state_dir=args.state_dir,
            output_dir=args.output_dir,
            scope=args.scope,
            reason=args.reason,
            requested_by=args.requested_by,
            expires_at=args.expires_at,
            max_usage_count=args.max_usage_count,
        )
        _print({"override_request": request.to_dict(), "approval_id": batch.approval_id, "request_path": str(request_path)}, args.pretty)
        return 0
    if command == "apply-approved-override":
        summary = apply_approved_override(
            approval_store_dir=args.approval_store_dir,
            approval_id=args.approval_id,
            state_dir=args.state_dir,
            actor=args.actor,
            deactivate_kill_switch=args.deactivate_kill_switch,
        )
        _print(summary.to_dict(), args.pretty)
        return 0
    if command == "show-usage":
        usage = LocalRiskControlState(args.state_dir).load_usage()
        _print({"records": len(usage), "usage": usage}, args.pretty)
        return 0
    if command == "report":
        path = LocalRiskControlState(args.state_dir).write_state_summary()
        _print({"risk_control_state_path": str(path), "state": json.loads(path.read_text(encoding="utf-8"))}, args.pretty)
        return 0
    if command == "smoke":
        output = Path(args.output_dir)
        state_dir = Path(args.state_dir) if args.state_dir else output / "state"
        orders_path = output / "smoke_orders.jsonl"
        output.mkdir(parents=True, exist_ok=True)
        orders_path.write_text(
            "\n".join(
                [
                    json.dumps({"trade_date": "20240104", "ts_code": "000001.SZ", "side": "BUY", "order_value": 1000.0, "shares": 100}),
                    json.dumps({"trade_date": "20240104", "ts_code": "688999.SH", "side": "BUY", "order_value": 2_000_000.0, "shares": 200000}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        report, _orders, paths = evaluate_orders_file(
            orders_path,
            policy_profile=args.policy_profile,
            state_dir=state_dir,
            output_dir=output,
            batch_id="risk_controls_smoke",
            trade_date="20240104",
        )
        _print(report.to_dict() | {"paths": {key: str(value) for key, value in paths.items()}}, args.pretty)
        return 0
    return 1


def _print(payload: dict, pretty: bool) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None, sort_keys=pretty))


if __name__ == "__main__":
    raise SystemExit(main())
